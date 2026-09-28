"""Typed domain models (standard-library dataclasses).

These are the shapes that flow through the API, the services, and the memory
layer. ``as_dict`` helpers give stable JSON for the frontend.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# --- incident status lifecycle ---
OPEN = "open"
TRIAGING = "triaging"
MITIGATED = "mitigated"
RESOLVED = "resolved"
STATUSES = (OPEN, TRIAGING, MITIGATED, RESOLVED)

# --- severities ---
SEV1 = "SEV1"  # critical, customer-facing outage
SEV2 = "SEV2"  # major degradation
SEV3 = "SEV3"  # minor / partial
SEVERITIES = (SEV1, SEV2, SEV3)

# --- auth roles (viewer < responder < admin; each inherits the levels below) ---
VIEWER = "viewer"
RESPONDER = "responder"
ADMIN = "admin"
ROLES = (VIEWER, RESPONDER, ADMIN)
ROLE_RANK = {VIEWER: 0, RESPONDER: 1, ADMIN: 2}


def now_ms() -> int:
    """Current wall-clock time in milliseconds (used for ordering + MTTR)."""
    return int(time.time() * 1000)


@dataclass
class Service:
    """A monitored service in the fictional 'Northwind' platform."""

    name: str
    tier: int
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    oncall: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Runbook:
    """An operational runbook keyed by service + symptom class."""

    id: str
    title: str
    service: str
    symptoms: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Incident:
    """A production incident and everything learned about it."""

    external_id: str            # e.g. INC-2041
    title: str
    service: str
    severity: str
    symptom: str                # short human symptom, e.g. "elevated 5xx"
    error_signature: str        # normalized fingerprint for retrieval
    id: Optional[int] = None    # DB primary key
    tags: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    log_excerpt: str = ""
    status: str = OPEN
    created_at: int = field(default_factory=now_ms)
    resolved_at: Optional[int] = None
    root_cause: str = ""
    remediation_steps: list[str] = field(default_factory=list)
    resolver: str = ""
    mttr_minutes: Optional[float] = None
    feedback: dict[str, Any] = field(default_factory=dict)

    def signature_text(self) -> str:
        """A compact natural-language query used for memory recall."""
        tag_str = ", ".join(self.tags)
        return (
            f"{self.service}: {self.symptom} ({self.error_signature}). "
            f"{self.title}. tags: {tag_str}"
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class User:
    """An authenticated account. ``password_hash`` is never serialized to clients."""

    email: str
    role: str = VIEWER
    id: Optional[int] = None
    name: str = ""
    password_hash: str = ""
    created_at: int = field(default_factory=now_ms)
    failed_attempts: int = 0
    locked_until: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        # Deliberately omit password_hash / lockout internals from any API payload.
        return {"id": self.id, "email": self.email, "name": self.name,
                "role": self.role, "created_at": self.created_at}


@dataclass
class Memory:
    """A single recalled memory unit from the memory layer."""

    content: str
    type: str = "experience"          # world | experience | observation
    score: float = 0.0                # relevance in [0, 1]
    id: str = ""
    source: str = ""                  # provenance, e.g. incident external_id
    created_at: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecallResult:
    """The outcome of a recall call, plus which backend served it."""

    query: str
    memories: list[Memory] = field(default_factory=list)
    backend: str = "local"
    strategies: dict[str, float] = field(default_factory=dict)  # explainability

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "backend": self.backend,
            "strategies": self.strategies,
            "memories": [m.as_dict() for m in self.memories],
        }


@dataclass
class Brief:
    """The triage brief the agent produces for an incident."""

    summary: str = ""
    root_cause_hypothesis: str = ""
    confidence: float = 0.0
    remediation_steps: list[str] = field(default_factory=list)
    runbook_ref: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)  # past incidents
    reflection: str = ""            # Hindsight reflect synthesis
    memory_used: bool = True
    memory_backend: str = "local"
    llm_backend: str = "local"
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Compatibility alias: the canonical field is ``root_cause_hypothesis`` but the
        # API contract (and external clients) expect a ``root_cause`` key. Expose both so
        # raw-JSON consumers don't read ``root_cause`` as null (S1).
        d["root_cause"] = d["root_cause_hypothesis"]
        return d
