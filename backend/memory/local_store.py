"""``LocalMemoryStore`` — offline hybrid retriever (the demo default).

Used when Hindsight is not configured, so the app runs and is testable anywhere with
no network and no keys. It ALWAYS reports ``backend_name = "local"`` to the UI so a
demo never misrepresents a local result as a live Hindsight call.

Persistence: a JSON file per bank (``data/local_memory_<bank>.json``) so retained
memories survive across the seeder process and server restarts — keeping the
learning-curve demo consistent. The corpus is small (demo scale) so we read fresh per
op and cache embeddings implicitly via ``retrieval``. Writes are atomic (temp + rename).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import Memory, RecallResult, now_ms
from . import retrieval
from .base import MemoryStore

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "bank").lower()).strip("-") or "bank"


class LocalMemoryStore(MemoryStore):
    backend_name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bank = settings.hindsight_bank
        data_dir = Path(settings.db_path).resolve().parent
        self.path = data_dir / f"local_memory_{_slug(self.bank)}.json"
        self._lock = threading.RLock()
        self.ensure_bank()

    # --- persistence helpers ------------------------------------------------
    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        mems = raw.get("memories", []) if isinstance(raw, dict) else []
        return mems if isinstance(mems, list) else []

    def _save(self, memories: list[dict[str, Any]]) -> None:
        payload = {
            "bank": self.bank,
            "backend": "local",
            "note": "SYNTHETIC demo memory — offline local store, not a live Hindsight bank.",
            "updated_at": now_ms(),
            "memories": memories,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)  # atomic
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # --- MemoryStore interface ---------------------------------------------
    def ensure_bank(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._save([])

    def retain(self, content: str, *, mem_type: str = "experience", source: str = "",
               metadata: Optional[dict[str, Any]] = None) -> str:
        meta = dict(metadata or {})
        # allow the seeder to backdate a memory so the learning curve replays in order
        created_at = int(meta.pop("created_at", None) or now_ms())
        with self._lock:
            memories = self._load()
            mem_id = f"loc-{len(memories) + 1}-{int(time.time() * 1000)}"
            memories.append({
                "id": mem_id,
                "content": content,
                "type": mem_type,
                "source": source,
                "created_at": created_at,
                "metadata": meta,
            })
            self._save(memories)
        return mem_id

    def recall(self, query: str, *, top_k: int = 5) -> RecallResult:
        memories = self._load()
        result = RecallResult(query=query, backend=self.backend_name,
                              strategies=dict(retrieval.WEIGHTS))
        if not memories:
            return result
        ranked = retrieval.rank(query, memories, now_ms=now_ms(), top_k=top_k)
        for r in ranked:
            m = memories[r["index"]]
            meta = dict(m.get("metadata", {}))
            meta["strategy_breakdown"] = r["breakdown"]
            meta["signals"] = {k: round(v, 4) for k, v in r["signals"].items()}
            result.memories.append(Memory(
                content=m.get("content", ""),
                type=m.get("type", "experience"),
                score=round(r["score"], 4),
                id=m.get("id", ""),
                source=m.get("source", ""),
                created_at=m.get("created_at"),
                metadata=meta,
            ))
        return result

    def reflect(self, query: str) -> str:
        """Deterministic, offline evidence synthesis over the top recalls.

        This is a documented local heuristic (NOT an LLM call): it recalls the most
        similar past incidents and composes a short pattern/root-cause/fix summary from
        their structured content. The real Hindsight backend answers ``reflect`` with an
        agentic synthesis shaped by the bank mission.
        """
        rc = self.recall(query, top_k=5)
        if not rc.memories:
            return ("No prior incidents match this signature yet — the local memory bank "
                    "has nothing to reflect on. (offline heuristic)")
        top = rc.memories[:3]
        families = [m.metadata.get("family") for m in top if m.metadata.get("family")]
        family = _most_common(families)
        best = top[0]
        root = _extract_line(best.content, "Root cause") or "not recorded"
        fix = _extract_line(best.content, "Fix") or "see runbook"
        sources = ", ".join(m.source for m in top if m.source)
        fam_line = (f" These cluster in incident family '{family}'." if family else "")
        return (
            f"Reflecting over {len(rc.memories)} recalled incident(s) ({sources}):"
            f"{fam_line} The closest prior match is {best.source or 'a past incident'} "
            f"(match {best.score:.0%}). Most likely root cause: {root} "
            f"Fix that worked before: {fix} "
            f"[local offline synthesis — deterministic, not an LLM]"
        )

    def count(self) -> int:
        return len(self._load())

    def reset(self) -> None:
        """Clear the bank (used by the demo reset control)."""
        with self._lock:
            self._save([])

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "ok": True,
                "detail": "in-process hybrid retriever (offline)", "n": self.count()}


def _extract_line(content: str, prefix: str) -> str:
    for line in content.splitlines():
        s = line.strip()
        if s.lower().startswith(prefix.lower()):
            _, _, rest = s.partition(":")
            return rest.strip() or s
    return ""


def _most_common(items: list[str]) -> str:
    if not items:
        return ""
    counts: dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    return max(counts, key=lambda k: counts[k])
