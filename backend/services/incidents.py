"""Incident lifecycle service (FR-1/4/6/7/9/14..16).

Owns creation, state transitions, timeline, and — crucially — RETAINING resolved
incidents into the memory layer so the agent learns (the institutional-memory loop).
"""
from __future__ import annotations

import re
from typing import Any

from ..db import Repository
from ..memory import MemoryStore
from ..models import (
    MITIGATED,
    OPEN,
    RESOLVED,
    SEVERITIES,
    STATUSES,
    TRIAGING,
    Incident,
    now_ms,
)

_ORDER = {OPEN: 0, TRIAGING: 1, MITIGATED: 2, RESOLVED: 3}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class IncidentService:
    def __init__(self, repo: Repository, memory: MemoryStore) -> None:
        self.repo = repo
        self.memory = memory

    # --- fingerprint --------------------------------------------------------
    def signature(self, service: str, symptom: str, tags: list[str],
                  log_excerpt: str = "") -> str:
        """A normalized, pipe-joined fingerprint for retrieval when none is supplied."""
        toks: list[str] = []
        for source in (symptom, " ".join(tags or [])):
            toks.extend(_TOKEN_RE.findall((source or "").lower()))
        seen, uniq = set(), []
        for t in toks:
            if t not in seen and len(t) > 1:
                seen.add(t)
                uniq.append(t)
        base = "|".join(uniq[:6]) or "unknown"
        return f"{(service or 'svc').lower()}|{base}"

    # --- lifecycle ----------------------------------------------------------
    def create_incident(self, payload: dict[str, Any]) -> Incident:
        if not isinstance(payload, dict):
            raise ValueError("incident payload must be an object")
        title = str(payload.get("title", "")).strip()
        service = str(payload.get("service", "")).strip()
        if not title:
            raise ValueError("title is required")
        if not service:
            raise ValueError("service is required")
        severity = str(payload.get("severity", "SEV3")).strip().upper()
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("tags must be a list")
        symptom = str(payload.get("symptom", "")).strip()
        log_excerpt = str(payload.get("log_excerpt", ""))
        error_signature = str(payload.get("error_signature", "")).strip() or \
            self.signature(service, symptom, tags, log_excerpt)
        external_id = str(payload.get("external_id", "")).strip() or f"INC-{now_ms() % 1_000_000}"

        inc = Incident(
            external_id=external_id, title=title, service=service, severity=severity,
            symptom=symptom or title, error_signature=error_signature,
            tags=[str(t) for t in tags], metrics=payload.get("metrics") or {},
            log_excerpt=log_excerpt, status=OPEN, created_at=int(payload.get("created_at") or now_ms()),
        )
        inc = self.repo.add_incident(inc)
        self.repo.add_timeline(inc.id, "opened", f"{severity} opened for {service}", ts=inc.created_at)
        return inc

    def transition(self, incident_id: int, status: str) -> Incident:
        status = str(status or "").strip().lower()
        if status not in STATUSES:
            raise ValueError(f"unknown status '{status}'")
        inc = self.repo.get_incident(incident_id)
        if inc is None:
            raise ValueError(f"incident {incident_id} not found")
        if _ORDER[status] <= _ORDER.get(inc.status, 0):
            raise ValueError(f"illegal transition {inc.status} -> {status}")
        inc.status = status
        if status == RESOLVED and inc.resolved_at is None:
            inc.resolved_at = now_ms()
        self.repo.update_incident(inc)
        self.repo.add_timeline(inc.id, "transition", f"{inc.status}")
        return inc

    def resolve(self, incident_id: int, *, root_cause: str,
                remediation_steps: list[str], resolver: str) -> Incident:
        inc = self.repo.get_incident(incident_id)
        if inc is None:
            raise ValueError(f"incident {incident_id} not found")
        inc.status = RESOLVED
        inc.resolved_at = now_ms()
        inc.root_cause = str(root_cause or "")
        inc.remediation_steps = [str(s) for s in (remediation_steps or [])]
        inc.resolver = str(resolver or "")
        if inc.created_at and inc.resolved_at:
            inc.mttr_minutes = round((inc.resolved_at - inc.created_at) / 60_000.0, 1)
        self.repo.update_incident(inc)

        # --- the learning loop: RETAIN a rich experience memory --------------
        doc = build_retention_doc(inc, repo=self.repo)
        self.memory.retain(
            doc, mem_type="experience", source=inc.external_id,
            metadata={
                "service": inc.service, "severity": inc.severity,
                "signature": inc.error_signature,
                "mttr_minutes": inc.mttr_minutes,
                "family": inc.feedback.get("family", "") if isinstance(inc.feedback, dict) else "",
            },
        )
        self.repo.add_timeline(inc.id, "resolved",
                               f"resolved in {inc.mttr_minutes}m; retained to memory")
        return inc

    def record_feedback(self, incident_id: int, *, helpful: bool,
                        root_cause_correct: bool, note: str = "") -> Incident:
        inc = self.repo.get_incident(incident_id)
        if inc is None:
            raise ValueError(f"incident {incident_id} not found")
        inc.feedback = {
            "helpful": bool(helpful),
            "root_cause_correct": bool(root_cause_correct),
            "note": str(note or ""),
            "ts": now_ms(),
        }
        self.repo.update_incident(inc)
        self.repo.add_timeline(inc.id, "feedback",
                               "helpful" if helpful else "not helpful")
        # FR-9: positive feedback reinforces memory with a confirming observation.
        if helpful and root_cause_correct:
            self.memory.retain(
                f"[FEEDBACK {inc.external_id}] Confirmed: the recalled diagnosis and fix "
                f"for '{inc.error_signature}' on {inc.service} resolved the incident. {note}".strip(),
                mem_type="observation", source=f"{inc.external_id}-fb",
                metadata={"service": inc.service, "reinforces": inc.external_id},
            )
        return inc

    # --- demo seeding -------------------------------------------------------
    def seed(self, data: dict[str, Any]) -> dict[str, int]:
        """Load the labeled synthetic dataset: catalog + incidents, retaining resolved
        'memory' incidents into the bank with backdated timestamps so the learning curve
        replays chronologically. Idempotent: resets structured + memory state first."""
        from ..models import Runbook, Service

        self.repo.reset()
        if hasattr(self.memory, "reset"):
            self.memory.reset()

        counts = {"services": 0, "runbooks": 0, "incidents": 0, "memories": 0}
        for s in data.get("services", []):
            self.repo.upsert_service(Service(
                name=s["name"], tier=int(s.get("tier", 3)),
                description=s.get("description", ""),
                dependencies=s.get("dependencies", []), oncall=s.get("oncall", "")))
            counts["services"] += 1
        for r in data.get("runbooks", []):
            self.repo.upsert_runbook(Runbook(
                id=r["id"], title=r["title"], service=r["service"],
                symptoms=r.get("symptoms", []), steps=r.get("steps", []),
                tags=r.get("tags", [])))
            counts["runbooks"] += 1

        day = 86_400_000
        for i in data.get("incidents", []):
            created_at = now_ms() - int(i.get("created_days_ago", 0)) * day
            resolved_at = None
            mttr = i.get("mttr_minutes")
            status = i.get("status", OPEN)
            if status == RESOLVED and mttr is not None:
                resolved_at = created_at + int(float(mttr) * 60_000)
            inc = Incident(
                external_id=i["external_id"], title=i["title"], service=i["service"],
                severity=i["severity"], symptom=i.get("symptom", ""),
                error_signature=i.get("error_signature", ""),
                tags=i.get("tags", []), metrics=i.get("metrics", {}),
                log_excerpt=i.get("log_excerpt", ""), status=status,
                created_at=created_at, resolved_at=resolved_at,
                root_cause=i.get("root_cause", ""),
                remediation_steps=i.get("remediation_steps", []),
                resolver=i.get("resolver", ""),
                mttr_minutes=float(mttr) if mttr is not None else None,
                feedback={"family": i.get("family", "")},
            )
            inc = self.repo.add_incident(inc)
            self.repo.add_timeline(inc.id, "opened", f"{inc.severity} opened", ts=created_at)
            counts["incidents"] += 1
            # Retain only resolved incidents flagged as institutional 'memory'.
            if i.get("seed_role") == "memory" and status == RESOLVED:
                doc = build_retention_doc(inc, repo=self.repo)
                self.memory.retain(
                    doc, mem_type="experience", source=inc.external_id,
                    metadata={
                        "service": inc.service, "severity": inc.severity,
                        "signature": inc.error_signature, "family": i.get("family", ""),
                        "mttr_minutes": inc.mttr_minutes, "created_at": created_at,
                    })
                if resolved_at:
                    self.repo.add_timeline(inc.id, "resolved",
                                           f"resolved in {inc.mttr_minutes}m", ts=resolved_at)
                counts["memories"] += 1
        return counts


def build_retention_doc(inc: Incident, *, repo: Repository = None) -> str:
    """Compose the rich experience document retained to memory (per docs/DATASET.md)."""
    runbook_ref = ""
    if repo is not None:
        rbs = repo.find_runbooks_for_service(inc.service)
        if rbs:
            runbook_ref = rbs[0].id
    fix = "; ".join(inc.remediation_steps) if inc.remediation_steps else "not recorded"
    mttr = f"{inc.mttr_minutes}m" if inc.mttr_minutes is not None else "unknown"
    lines = [
        f"[INCIDENT {inc.external_id} · {inc.service} · {inc.severity}]",
        f"Symptom: {inc.symptom}. Signature: {inc.error_signature}.",
        f"Root cause: {inc.root_cause or 'not recorded'}.",
        f"Fix: {fix}.",
        f"Runbook: {runbook_ref or 'n/a'}. Resolver: {inc.resolver or 'n/a'}. "
        f"MTTR: {mttr}. Outcome: resolved.",
    ]
    if inc.log_excerpt:
        lines.append(f"Logs: {inc.log_excerpt.splitlines()[0] if inc.log_excerpt else ''}")
    return "\n".join(lines)
