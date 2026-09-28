"""Domain error types with distinct HTTP semantics (CONCRETE — small, dependency-free).

The router maps these to status codes so handlers can raise a precise error instead of a
generic ``ValueError`` (which maps to 400). Keeping them here avoids a circular import
between ``router`` and the service/db layers that raise them.

  * ``NotFoundError``  -> HTTP 404 (a referenced resource does not exist)
  * ``ConflictError``  -> HTTP 409 (a uniqueness/state constraint was violated)
  * ``AuthError``      -> HTTP 401 (missing/invalid credentials or session)
  * ``ForbiddenError`` -> HTTP 403 (authenticated but insufficient role / CSRF)
  * ``LockedError``    -> HTTP 429 (too many failed logins; temporarily locked)

Both are plain ``Exception`` subclasses (NOT ``ValueError``) so the router's ordered
``except`` clauses can distinguish them from bad-input 400s.
"""
from __future__ import annotations


class NotFoundError(Exception):
    """A referenced resource (e.g. an incident) does not exist -> HTTP 404."""


class ConflictError(Exception):
    """A uniqueness or state constraint was violated (e.g. duplicate id) -> HTTP 409."""


class AuthError(Exception):
    """Authentication failed or is required -> HTTP 401."""


class ForbiddenError(Exception):
    """Authenticated but not permitted (role too low, or CSRF check failed) -> HTTP 403."""


class LockedError(Exception):
    """Account temporarily locked after too many failed logins -> HTTP 429."""
