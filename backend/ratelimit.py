"""Tiny thread-safe fixed-window rate limiter (stdlib only).

Used to throttle the auth endpoints per client IP so a single host can't brute-force
logins or spam signups. Fixed-window (not token-bucket) on purpose: it's a handful of
lines, has no background sweeper, and is more than enough for the threat it addresses.

A limiter with ``limit <= 0`` or ``window <= 0`` is DISABLED — :meth:`allow` always
returns True. That's the config escape hatch (``MUNINN_RL_* = 0``) for single-user local
runs and tests, so the limiter never gets in the way when it isn't wanted.

Thread-safe under the threaded HTTP server via a single lock around the counter map.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    """Count hits per key within a rolling fixed window; allow up to ``limit`` per window."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = int(limit)
        self.window = float(window_seconds)
        self._lock = threading.Lock()
        # key -> (window_start_monotonic, hits_in_window)
        self._hits: dict[str, tuple[float, int]] = {}

    @property
    def enabled(self) -> bool:
        return self.limit > 0 and self.window > 0

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit for ``key`` and return whether it's within the limit for the current
        window. A disabled limiter always returns True. ``now`` (monotonic seconds) is
        injectable for deterministic tests."""
        if not self.enabled:
            return True
        t = time.monotonic() if now is None else now
        key = key or ""  # a missing IP collapses to one shared bucket rather than crashing
        with self._lock:
            start, count = self._hits.get(key, (t, 0))
            if t - start >= self.window:
                start, count = t, 0  # window elapsed — start a fresh one
            count += 1
            self._hits[key] = (start, count)
            return count <= self.limit
