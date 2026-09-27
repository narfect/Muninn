"""``MemoryStore`` — the single memory-layer contract Muninn depends on.

Both backends implement this interface:
  * ``HindsightStore``   (real Hindsight REST: retain / recall / reflect)
  * ``LocalMemoryStore`` (offline hybrid retriever, clearly labeled "local")

The application (services + agent) only ever talks to this interface, so the
backend can be swapped by configuration without touching business logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..config import Settings
from ..models import RecallResult


class MemoryStore(ABC):
    """Abstract memory backend.

    Implementations MUST be safe to call from multiple threads and MUST NOT raise
    on transient backend errors — recall/reflect should degrade to an empty/partial
    result and log, so the incident workflow never hard-fails (see NFR-2).
    """

    #: short identifier surfaced to the UI, e.g. "hindsight" or "local"
    backend_name: str = "abstract"

    @abstractmethod
    def ensure_bank(self) -> None:
        """Create the configured memory bank if it does not already exist (idempotent)."""

    @abstractmethod
    def retain(
        self,
        content: str,
        *,
        mem_type: str = "experience",       # world | experience | observation
        source: str = "",                    # provenance (e.g. incident external id)
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Store a memory. Returns a memory/operation id (may be empty for local)."""

    @abstractmethod
    def recall(self, query: str, *, top_k: int = 5) -> RecallResult:
        """Return the top-k most relevant memories for ``query`` with scores + provenance."""

    @abstractmethod
    def reflect(self, query: str) -> str:
        """Synthesize an answer by reasoning over memories (agentic for Hindsight;
        deterministic evidence-synthesis for local). Returns prose."""

    @abstractmethod
    def count(self) -> int:
        """Number of retained memories/units (used by the learning-curve metric)."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return ``{"backend": name, "ok": bool, "detail": str}`` without raising."""


def build_memory_store(settings: Settings) -> MemoryStore:
    """Factory: pick the memory backend from settings.

    ``settings.resolved_memory_backend()`` returns "hindsight" only when a base URL
    and API key are configured (or explicitly forced); otherwise "local".
    """
    # Imported here to avoid a circular import at module load.
    from .hindsight_store import HindsightStore
    from .local_store import LocalMemoryStore

    if settings.resolved_memory_backend() == "hindsight":
        return HindsightStore(settings)
    return LocalMemoryStore(settings)
