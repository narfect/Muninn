"""Analytics: MTTR, learning curve, memory coverage (FR-14..16).

All metrics are computed over the clearly-labeled synthetic demo dataset — never
fabricated. The learning curve replays resolved incidents chronologically and measures
how recall quality rises as the bank accumulates memories.
"""
from __future__ import annotations

from typing import Any, Optional

from ..db import Repository
from ..memory import MemoryStore
from ..models import RESOLVED
from ..memory import retrieval
from .incidents import build_retention_doc

_HIT_THRESHOLD = 0.35  # top-1 score above which a recall counts as a useful match


class MetricsService:
    def __init__(self, repo: Repository, memory: MemoryStore) -> None:
        self.repo = repo
        self.memory = memory

    def mttr(self, service: Optional[str] = None) -> dict[str, Any]:
        incidents = [i for i in self.repo.list_incidents(status=RESOLVED, limit=1000)
                     if i.mttr_minutes is not None]
        if service:
            incidents = [i for i in incidents if i.service == service]
        overall = round(sum(i.mttr_minutes for i in incidents) / len(incidents), 1) if incidents else 0.0
        by_service: dict[str, list[float]] = {}
        for i in incidents:
            by_service.setdefault(i.service, []).append(i.mttr_minutes)
        return {
            "overall_min": overall,
            "by_service": {k: round(sum(v) / len(v), 1) for k, v in by_service.items()},
            "n": len(incidents),
        }

    def learning_curve(self) -> list[dict[str, Any]]:
        """Replay resolved incidents oldest-first; recall each against only the memories
        that existed *before* it, and record the top-1 similarity + whether it hit."""
        resolved = sorted(
            [i for i in self.repo.list_incidents(status=RESOLVED, limit=1000)],
            key=lambda x: x.created_at,
        )
        series: list[dict[str, Any]] = []
        prior_docs: list[dict[str, Any]] = []
        hits = 0
        for idx, inc in enumerate(resolved):
            if prior_docs:
                ranked = retrieval.rank(inc.signature_text(), prior_docs,
                                        now_ms=inc.created_at, top_k=1)
                top = ranked[0]["score"] if ranked else 0.0
            else:
                top = 0.0
            hit = top >= _HIT_THRESHOLD
            hits += 1 if hit else 0
            series.append({
                "n_memories": len(prior_docs),
                "avg_top_score": round(top, 4),
                "coverage": round(hits / (idx + 1), 4),
                "hit": hit,
                "incident": inc.external_id,
                "mttr_minutes": inc.mttr_minutes,
                "ts": inc.created_at,
            })
            prior_docs.append({"content": build_retention_doc(inc, repo=self.repo),
                               "created_at": inc.created_at, "source": inc.external_id})
        return series

    def coverage(self, signature_text: str) -> dict[str, Any]:
        rc = self.memory.recall(signature_text, top_k=self.memory.count() or 5)
        best = max((m.score for m in rc.memories), default=0.0)
        n_similar = sum(1 for m in rc.memories if m.score >= _HIT_THRESHOLD)
        return {"n_similar": n_similar, "best_score": round(best, 4),
                "n_memories": self.memory.count()}

    def summary(self) -> dict[str, Any]:
        incidents = self.repo.list_incidents(limit=1000)
        by_sev: dict[str, int] = {}
        by_service: dict[str, int] = {}
        open_n = 0
        for i in incidents:
            by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
            by_service[i.service] = by_service.get(i.service, 0) + 1
            if i.status != RESOLVED:
                open_n += 1
        curve = self.learning_curve()
        return {
            "mttr": self.mttr(),
            "by_severity": by_sev,
            "by_service": by_service,
            "n_incidents": len(incidents),
            "n_open": open_n,
            "n_resolved": self.repo.count_resolved(),
            "n_memories": self.memory.count(),
            "memory_backend": getattr(self.memory, "backend_name", "local"),
            "learning_curve": curve,
        }
