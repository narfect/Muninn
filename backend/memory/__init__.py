"""Memory layer package: a single ``MemoryStore`` interface with two backends.

* ``HindsightStore``  — the real integration (Hindsight REST: retain/recall/reflect).
* ``LocalMemoryStore`` — an in-process hybrid retriever used when Hindsight is not
  configured, so the app runs and is testable anywhere. It is always surfaced as
  "local" in the API/UI so nothing simulated is mistaken for a live Hindsight call.
"""
from __future__ import annotations

from .base import MemoryStore, build_memory_store

__all__ = ["MemoryStore", "build_memory_store"]
