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

# SRE-analyst disposition applied to the bank on create/update (traits are 1-5, default 3).
# Tuned so `reflect` reads as a calm senior SRE: question assumptions before blaming a
# cause (higher skepticism), stay anchored to the evidence in recalled incidents (higher
# literalism), and keep the voice technical and composed rather than reassuring (lower
# empathy). The mission string itself is sourced from settings, not hard-coded here.
_DISPOSITION = {"skepticism": 4, "literalism": 4, "empathy": 2}


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
            # Single idempotent create-or-update PUT carrying the reflect mission + SRE
            # disposition. The mission comes from settings (not hard-coded in the client).
            resp = self.client.create_bank(
                self.bank, name="Muninn incident memory",
                mission=self.settings.hindsight_bank_mission, disposition=_DISPOSITION,
            )
            # idempotent: a 409/"already exists" is success for our purposes
            if resp.get("ok") or resp.get("_status") in (200, 201, 409):
                self._bank_ready = True
            else:
                # Non-fatal: Hindsight auto-creates the bank on the first retain, so a
                # non-confirming PUT here does not block the loop (it only skips the
                # disposition update).
                log.warning("create_bank did not confirm: %s — bank will auto-create on "
                            "first retain (reflect uses default disposition until then)",
                            resp.get("error"))

    def retain(self, content: str, *, mem_type: str = "experience", source: str = "",
               metadata: Optional[dict[str, Any]] = None) -> str:
        meta = dict(metadata or {})
        if source:
            meta.setdefault("source", source)
        # source doubles as the upsert key (document_id): re-retaining the same incident
        # REPLACES its memory rather than duplicating it, so re-seeding is idempotent.
        resp = self.client.retain(self.bank, content, mem_type=mem_type, metadata=meta,
                                  document_id=source)
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
        items = self.client.extract_memories(resp)[:max(1, top_k)]
        n = len(items)
        for i, item in enumerate(items):
            meta = dict(item.get("metadata")) if isinstance(item.get("metadata"), dict) else {}
            raw = self.client.item_score(item)
            if raw is None:
                # Hindsight's recall response does not document a per-item numeric score;
                # the hits ARE genuinely ranked, so when no score is present we represent
                # rank with a descending value and flag it so nothing reads as a backend
                # score it isn't (honest provenance). First hit stays high; later hits decay.
                score = round(max(0.3, 1.0 - i * (0.6 / max(1, n))), 4)
                meta["score_source"] = "rank_heuristic"
            else:
                # Hindsight's raw relevance score is NOT on a 0..1 scale (observed ~1.0-1.1 for
                # strong matches), so passing it through saturates confidence and shows citation
                # percentages >100%. Normalize onto 0..1 against the configured strong-match
                # reference so downstream grading matches the local-cosine scale. Keep the raw
                # value in metadata for honest provenance / debugging.
                scale = getattr(self.settings, "hindsight_score_scale", 1.1) or 1.1
                score = round(max(0.0, min(1.0, float(raw) / scale)), 4)
                meta.setdefault("score_source", "backend")
                meta.setdefault("raw_score", round(float(raw), 4))
            result.memories.append(Memory(
                content=self.client.item_content(item),
                type=item.get("type") or meta.get("muninn_type") or "experience",
                score=score,
                id=str(item.get("id", "")),
                source=self.client.item_source(item),
                created_at=item.get("created_at"),
                metadata=meta,
            ))
        return result

    def reflect(self, query: str) -> str:
        resp = self.client.reflect(self.bank, query)
        if not resp.get("ok"):
            log.warning("reflect failed (%s)", resp.get("error"))
            return "Reflect is unavailable (memory backend unreachable)."
        # The documented reflect answer lives in ``text``; the others are version/compat
        # fallbacks. Returns "" when the backend answered but carried no prose.
        for key in ("text", "answer", "reflection", "content", "result"):
            v = resp.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    def count(self) -> int:
        """Session-local memory count.

        S8: the public Hindsight API reference documents no per-bank memory-count / stats
        endpoint, so rather than fabricate or guess a bank-wide total this reflects only the
        retains performed by THIS process (it starts at 0 on restart). It is an honest
        best-effort indicator for ``health.n_memories`` / metrics, NOT an authoritative
        server-side total. The restart caveat is documented in docs/HINDSIGHT.md; a demo
        that needs a stable count should keep the process alive after seeding.
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
