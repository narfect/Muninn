"""Authentication & session service (stdlib-only — hashlib/secrets/hmac).

Implements the auth design in docs/PRODUCTION_PLAN.md §5:

* Password hashing: ``scrypt`` (n=2**14, r=8, p=1) with a ``pbkdf2_hmac`` fallback where
  scrypt's ``maxmem`` is constrained; per-user 16-byte salt; upgradable stored string
  ``algo$params$salt$hash``; constant-time verify via ``hmac.compare_digest``.
* Sessions: opaque ``secrets.token_urlsafe(32)`` token; only ``sha256(token)`` is stored,
  so a DB read cannot resurrect live sessions. Absolute + idle expiry, both enforced.
* CSRF: a per-session token derived as ``HMAC(server_secret, token_hash)`` — recomputable,
  never stored, and unguessable without the server secret.
* Anti-abuse: failed-login lockout on the user row; uniform errors to resist enumeration;
  server-side password policy. First account created becomes ``admin`` (bootstrap).

No third-party dependency; safe under the threaded HTTP server (all state in SQLite).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from typing import Optional

from ..config import Settings
from ..db import Repository
from ..errors import AuthError, ForbiddenError, LockedError
from ..models import ADMIN, ROLES, VIEWER, User, now_ms

log = logging.getLogger("muninn.auth")

# scrypt cost parameters (see §5). maxmem must exceed 128*n*r bytes.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_PBKDF2_ROUNDS = 600_000

# A tiny denylist of the most common passwords (server-side policy). Not exhaustive by
# design — the length + not-all-numeric rules do the heavy lifting.
_COMMON = {
    "password", "password1", "12345678", "123456789", "qwerty123", "letmein1",
    "changeme", "admin123", "welcome1", "iloveyou", "passw0rd", "muninn123",
}
_MIN_PASSWORD_LEN = 8


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


class AuthService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    # --- password hashing -------------------------------------------------
    def hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        try:
            digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                                    r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
                                    maxmem=_SCRYPT_MAXMEM)
            return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"
        except (ValueError, MemoryError):
            # scrypt maxmem constrained on this host — fall back to PBKDF2.
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                         _PBKDF2_ROUNDS)
            return f"pbkdf2${_PBKDF2_ROUNDS}${_b64e(salt)}${_b64e(digest)}"

    def verify_password(self, password: str, stored: str) -> bool:
        try:
            parts = stored.split("$")
            algo = parts[0]
            if algo == "scrypt":
                _, n, r, p, salt_b64, hash_b64 = parts
                calc = hashlib.scrypt(password.encode("utf-8"), salt=_b64d(salt_b64),
                                      n=int(n), r=int(r), p=int(p), dklen=32,
                                      maxmem=_SCRYPT_MAXMEM)
            elif algo == "pbkdf2":
                _, rounds, salt_b64, hash_b64 = parts
                calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                           _b64d(salt_b64), int(rounds))
            else:
                return False
            return hmac.compare_digest(calc, _b64d(hash_b64))
        except (ValueError, IndexError):
            return False

    # --- password policy --------------------------------------------------
    @staticmethod
    def validate_password(password: str) -> None:
        if len(password) < _MIN_PASSWORD_LEN:
            raise ValueError(f"password must be at least {_MIN_PASSWORD_LEN} characters")
        if password.isdigit():
            raise ValueError("password must not be all digits")
        if password.lower() in _COMMON:
            raise ValueError("password is too common")

    @staticmethod
    def _normalize_email(email: str) -> str:
        email = (email or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1] or len(email) < 3:
            raise ValueError("a valid email is required")
        return email

    # --- signup / login / logout -----------------------------------------
    def signup(self, email: str, password: str, name: str = "") -> tuple[User, str, str]:
        email = self._normalize_email(email)
        self.validate_password(password)
        # Bootstrap: the very first account becomes admin; everyone else defaults to viewer.
        role = ADMIN if self.repo.count_users() == 0 else VIEWER
        user = self.repo.create_user(User(
            email=email, name=name.strip(), role=role,
            password_hash=self.hash_password(password), created_at=now_ms(),
        ))  # raises ConflictError on duplicate email
        token, csrf = self._start_session(user)
        log.info("signup: %s as %s", email, role)
        return user, token, csrf

    def login(self, email: str, password: str) -> tuple[User, str, str]:
        try:
            email = self._normalize_email(email)
        except ValueError:
            raise AuthError("invalid email or password")
        user = self.repo.get_user_by_email(email)
        now = now_ms()
        # Uniform failure path: whether the user exists or not, a wrong password looks the
        # same (still run a hash to keep timing roughly uniform / resist enumeration).
        if user is None:
            self.hash_password(password)  # burn ~equivalent time
            raise AuthError("invalid email or password")
        if user.locked_until and user.locked_until > now:
            raise LockedError("too many failed attempts; try again later")
        if not self.verify_password(password, user.password_hash):
            self._register_failed_login(user, now)
            raise AuthError("invalid email or password")
        # success — clear any failure state and open a session.
        if user.failed_attempts or user.locked_until:
            self.repo.set_login_state(user.id, 0, None)
        token, csrf = self._start_session(user)
        return user, token, csrf

    def logout(self, token: Optional[str]) -> None:
        if token:
            self.repo.delete_session(self._hash_token(token))

    def _register_failed_login(self, user: User, now: int) -> None:
        attempts = (user.failed_attempts or 0) + 1
        locked_until = None
        if attempts >= self.settings.login_max_attempts:
            locked_until = now + self.settings.login_lockout_seconds * 1000
            attempts = 0  # reset the counter; the lock window is the penalty now
        self.repo.set_login_state(user.id, attempts, locked_until)

    # --- sessions ---------------------------------------------------------
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def csrf_token(self, token_hash: str) -> str:
        return hmac.new(self.settings.server_secret.encode("utf-8"),
                        token_hash.encode("utf-8"), hashlib.sha256).hexdigest()

    def _start_session(self, user: User) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = now_ms()
        expires = now + self.settings.session_ttl_seconds * 1000
        self.repo.create_session(token_hash, user.id, now, now, expires)
        return token, self.csrf_token(token_hash)

    def authenticate(self, token: Optional[str]) -> Optional[tuple[User, str]]:
        """Resolve a session token to (user, token_hash), or None. Enforces absolute and
        idle expiry, refreshes ``last_seen``, and deletes expired sessions."""
        if not token:
            return None
        token_hash = self._hash_token(token)
        sess = self.repo.get_session(token_hash)
        if not sess:
            return None
        now = now_ms()
        idle_deadline = sess["last_seen"] + self.settings.session_idle_seconds * 1000
        if now >= sess["expires_at"] or now >= idle_deadline:
            self.repo.delete_session(token_hash)
            return None
        user = self.repo.get_user_by_id(sess["user_id"])
        if user is None:
            self.repo.delete_session(token_hash)
            return None
        self.repo.touch_session(token_hash, now)
        return user, token_hash

    # --- admin: role management ------------------------------------------
    def set_role(self, user_id: int, role: str) -> User:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        user = self.repo.update_user_role(user_id, role)
        if user is None:
            from ..errors import NotFoundError
            raise NotFoundError(f"user {user_id} not found")
        # Force a re-auth so the new role takes effect immediately everywhere.
        self.repo.delete_sessions_for_user(user_id)
        return user

    # --- cookie helpers ---------------------------------------------------
    SESSION_COOKIE = "muninn_session"
    CSRF_COOKIE = "muninn_csrf"

    def session_cookie(self, token: str) -> str:
        attrs = [f"{self.SESSION_COOKIE}={token}", "HttpOnly", "SameSite=Strict",
                 "Path=/", f"Max-Age={self.settings.session_ttl_seconds}"]
        if self.settings.cookie_secure:
            attrs.append("Secure")
        return "; ".join(attrs)

    def csrf_cookie(self, csrf: str) -> str:
        # Readable by JS (NOT HttpOnly) so the SPA can echo it in the X-CSRF-Token header.
        attrs = [f"{self.CSRF_COOKIE}={csrf}", "SameSite=Strict", "Path=/",
                 f"Max-Age={self.settings.session_ttl_seconds}"]
        if self.settings.cookie_secure:
            attrs.append("Secure")
        return "; ".join(attrs)

    def clear_cookies(self) -> list[str]:
        base = ["Path=/", "Max-Age=0", "SameSite=Strict"]
        return [f"{self.SESSION_COOKIE}=; HttpOnly; " + "; ".join(base),
                f"{self.CSRF_COOKIE}=; " + "; ".join(base)]
