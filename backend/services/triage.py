"""Triage orchestration (FR-8/11/13).

Ties memory + agent together and powers the before/after (cold vs warm) story that is
the demo centerpiece. Recall similar incidents, run the agent, stamp provenance, and
persist a timeline event.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..llm.agent import TriageAgent
from ..memory import MemoryStore
from ..models import Brief, Incident, RecallResult, now_ms


class TriageService:
    def __init__(self, memory: MemoryStore, agent: TriageAgent, repo: Any,
                 top_k: int = 5) -> None:
        self.memory = memory
        self.agent = agent
        self.repo = repo
        self.top_k = top_k

    def triage(self, incident: Incident, *, use_memory: bool = True,
               stream: Optional[Callable[[str], None]] = None,
               persist: bool = True) -> dict[str, Any]:
        t0 = now_ms()
        recalled: Optional[RecallResult] = None
        if use_memory:
            recalled = self.memory.recall(incident.signature_text(), top_k=self.top_k)

        brief: Brief = self.agent.triage(
            incident, use_memory=use_memory, recalled=recalled, on_token=stream)

        # provenance / honest backend badges
        brief.memory_used = use_memory
        brief.memory_backend = getattr(self.memory, "backend_name", "local")
        brief.llm_backend = getattr(self.agent.reasoner, "backend_name", "local")
        brief.latency_ms = now_ms() - t0
        if use_memory and not brief.reflection:
            brief.reflection = self.memory.reflect(incident.signature_text())

        if persist and getattr(incident, "id", None):
            mode = "warm (memory ON)" if use_memory else "cold (memory OFF)"
            self.repo.add_timeline(
                incident.id, "triaged",
                f"{mode}: {brief.summary[:120]}",
                payload={"memory_used": use_memory,
                         "citations": len(brief.citations),
                         "confidence": brief.confidence})
        return {"brief": brief, "recall": recalled}

    def compare(self, incident: Incident) -> dict[str, Any]:
        """Run both briefs side by side for the demo toggle (no timeline noise)."""
        cold = self.triage(incident, use_memory=False, persist=False)["brief"]
        warm = self.triage(incident, use_memory=True, persist=False)["brief"]
        return {"cold": cold, "warm": warm}
