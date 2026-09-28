"""``HindsightStore`` — ``MemoryStore`` backed by the real Hindsight REST API.

Wraps :class:`HindsightClient` and adapts it to the :class:`MemoryStore` interface the
rest of Muninn depends on, so business logic is identical whether memory is served by
Hindsight or the offline local store. Every method degrades gracefully on transient
errors (logs + safe default) and never raises (NFR-2).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from ..config import Settings
from ..models import Memory, RecallResult
from .base import MemoryStore
from .hindsight_client import HindsightClient

log = logging.getLogger("muninn.hindsight")

_MISSION = (
    "Help on-call engineers resolve incidents faster by recalling how similar past "
    "incidents were diagnosed and fixed. Prefer safe, reversible mitigations and always "
    "ground recommendations in prior incidents."
)


class HindsightStore(MemoryStore):
    backend_name = "hindsight"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bank = settings.hindsight_bank
        self.client = HindsightClient(
            base_url=settings.hindsight_base_url,
            api_key=settings.hindsight_api_key,
            namespace=settings.hindsight_namespace,
            timeout=settings.request_timeout,
        )
        self._lock = threading.Lock()
        self._count = 0
        self._bank_ready = False
        # S5: ensure the bank exists up front (idempotent, degrades gracefully if the
        # backend is unreachable) so the first retain/recall doesn't hit a missing bank.
        self.ensure_bank()

    def ensure_bank(self) -> None:
        with self._lock:
            if self._bank_ready:
                return
            resp = self.client.create_bank(self.bank, "Muninn incident memory", _MISSION)
            # idempotent: a 409/"already exists" is success for our purposes
            if resp.get("ok") or resp.get("_status") in (200, 201, 409):
                self._bank_ready = True
            else:
                log.warning("create_bank did not confirm: %s", resp.get("error"))

    def retain(self, content: str, *, mem_type: str = "experience", source: str = "",
               metadata: Optional[dict[str, Any]] = None) -> str:
        meta = dict(metadata or {})
        if source:
            meta.setdefault("source", source)
        resp = self.client.retain(self.bank, content, mem_type=mem_type, metadata=meta)
        if resp.get("ok"):
            with self._lock:
                self._count += 1
        else:
            log.warning("retain failed (%s) — memory not stored", resp.get("error"))
        return str(resp.get("operation_id") or resp.get("id") or source or "")

    def recall(self, query: str, *, top_k: int = 5) -> RecallResult:
        result = RecallResult(query=query, backend=self.backend_name, strategies={})
        resp = self.client.recall(self.bank, query, top_k=top_k)
        if not resp.get("ok"):
            log.warning("recall failed (%s) — returning empty result", resp.get("error"))
            return result
        for item in self.client.extract_memories(resp):
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            result.memories.append(Memory(
                content=self.client.item_content(item),
                type=item.get("type", "experience"),
                score=round(self.client.item_score(item), 4),
                id=str(item.get("id", "")),
                source=str((meta or {}).get("source") or item.get("source", "")),
                created_at=item.get("created_at"),
                metadata=meta or {},
            ))
        return result

    def reflect(self, query: str) -> str:
        resp = self.client.reflect(self.bank, query)
        if not resp.get("ok"):
            log.warning("reflect failed (%s)", resp.get("error"))
            return "Reflect is unavailable (memory backend unreachable)."
        for key in ("answer", "reflection", "content", "text", "result"):
            v = resp.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    def count(self) -> int:
        """Session-local memory count.

        S8: Hindsight exposes no verified stats endpoint in this client, so this reflects
        only retains performed by THIS process (it starts at 0 on restart). It is a
        best-effort indicator for ``health.n_memories``/metrics, NOT an authoritative
        bank-wide total. Documented as session-local rather than fabricating a count.
        """
        return self._count

    def reset(self) -> None:
        """Reset session-local tracking and re-ensure the bank (S7).

        The verified Hindsight REST client exposes no bulk-delete, so this does NOT purge
        memories already stored server-side — it clears the in-process counter and
        re-asserts the bank exists. Operators wanting a truly clean slate should point at
        a fresh ``HINDSIGHT_BANK``. This method exists so the seeder's ``hasattr(...,
        'reset')`` guard runs a real reset on the Hindsight path instead of silently
        skipping (which previously accumulated duplicates)."""
        with self._lock:
            self._count = 0
            self._bank_ready = False
        log.warning("HindsightStore.reset(): server-side memories are not purged "
                    "(no bulk-delete API); use a fresh bank id for a clean demo.")
        self.ensure_bank()

    def health(self) -> dict[str, Any]:
        try:
            resp = self.client.health()
            ok = bool(resp.get("ok"))
            detail = resp.get("detail") or ("reachable" if ok else resp.get("error", "unreachable"))
        except Exception as exc:  # noqa: BLE001 - health must never raise
            ok, detail = False, str(exc)
        return {"backend": self.backend_name, "ok": ok, "detail": str(detail), "n": self._count}
