"""Security hardening — per-IP fixed-window rate limiting on the public auth endpoints.

Two layers, both offline + deterministic:
  * ``RateLimiter`` unit tests — a stdlib fixed-window counter that allows up to ``limit``
    hits per window per key, blocks the overflow, isolates distinct keys, rolls the window
    over (via an injected ``now=``), and is fully disabled when ``limit``/``window`` is 0.
  * Endpoint tests — signup/login throttle by *client IP only* (never the email, so the
    limiter can't be used to probe which accounts exist): the (N+1)th rapid call from one
    IP is 429, a different IP keeps its own budget, and a disabled limiter never blocks.

Requests go through the real router with ``req.client_ip`` set exactly as the HTTP
transport sets it (from the socket peer, never a client-supplied X-Forwarded-For).
"""
import dataclasses
import json
import os
import tempfile
import unittest

from backend import server
from backend.config import settings
from backend.ratelimit import RateLimiter
from backend.router import Request

_ORIGIN = {"Origin": "http://127.0.0.1", "Host": "127.0.0.1"}
_PW = "muninn-pw-good-1"


class TestRateLimiterUnit(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        rl = RateLimiter(limit=3, window_seconds=60)
        self.assertTrue(all(rl.allow("ip", now=0.0) for _ in range(3)))
        self.assertFalse(rl.allow("ip", now=0.0))       # 4th within window -> blocked

    def test_distinct_keys_are_independent(self):
        rl = RateLimiter(limit=1, window_seconds=60)
        self.assertTrue(rl.allow("a", now=0.0))
        self.assertTrue(rl.allow("b", now=0.0))          # different key, its own budget
        self.assertFalse(rl.allow("a", now=0.0))         # "a" already spent

    def test_window_rolls_over(self):
        rl = RateLimiter(limit=1, window_seconds=60)
        self.assertTrue(rl.allow("ip", now=0.0))
        self.assertFalse(rl.allow("ip", now=59.0))       # still inside the same window
        self.assertTrue(rl.allow("ip", now=60.0))        # window elapsed -> fresh budget

    def test_disabled_when_limit_zero(self):
        rl = RateLimiter(limit=0)
        self.assertFalse(rl.enabled)
        self.assertTrue(all(rl.allow("ip", now=0.0) for _ in range(1000)))

    def test_disabled_when_window_zero(self):
        rl = RateLimiter(limit=5, window_seconds=0)
        self.assertFalse(rl.enabled)
        self.assertTrue(all(rl.allow("ip") for _ in range(50)))

    def test_empty_key_is_tolerated(self):
        rl = RateLimiter(limit=1, window_seconds=60)
        self.assertTrue(rl.allow("", now=0.0))
        self.assertFalse(rl.allow("", now=0.0))          # None/"" collapse to one bucket


def _app(**over):
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="rl-test", memory_backend="local",
                             llm_backend="local", **over)
    ctx = server.build_context(st)
    return ctx, server.build_router(ctx)


def _post(router, path, body, ip):
    """Dispatch a POST with the client IP populated the way the transport does (socket
    peer), so the per-IP limiter sees the same key the real server would."""
    req = Request.build("POST", path, dict(_ORIGIN), json.dumps(body).encode("utf-8"))
    req.client_ip = ip
    return router.dispatch(req)


class TestSignupRateLimit(unittest.TestCase):
    def test_n_plus_one_from_one_ip_is_429(self):
        _, router = _app(rl_signup=3)
        for i in range(3):                                   # 3 allowed by the limiter
            r = _post(router, "/api/auth/signup",
                      {"email": f"u{i}@x.io", "password": _PW}, "10.0.0.1")
            self.assertEqual(r.status, 201)
        blocked = _post(router, "/api/auth/signup",
                        {"email": "u3@x.io", "password": _PW}, "10.0.0.1")
        self.assertEqual(blocked.status, 429)                # (N+1)th from same IP -> 429

    def test_a_different_ip_is_unaffected(self):
        _, router = _app(rl_signup=1)
        self.assertEqual(_post(router, "/api/auth/signup",
                               {"email": "a@x.io", "password": _PW}, "10.0.0.1").status, 201)
        self.assertEqual(_post(router, "/api/auth/signup",
                               {"email": "b@x.io", "password": _PW}, "10.0.0.1").status, 429)
        # A fresh IP has its own budget and is untouched by the throttled one.
        self.assertEqual(_post(router, "/api/auth/signup",
                               {"email": "c@x.io", "password": _PW}, "10.0.0.2").status, 201)

    def test_disabled_limiter_never_blocks(self):
        _, router = _app(rl_signup=0)                        # limit=0 -> disabled
        for i in range(8):
            r = _post(router, "/api/auth/signup",
                      {"email": f"u{i}@x.io", "password": _PW}, "10.0.0.1")
            self.assertNotEqual(r.status, 429)

    def test_limiter_message_is_generic(self):
        # The 429 must not reveal whether the email exists — same body regardless.
        _, router = _app(rl_signup=1)
        _post(router, "/api/auth/signup", {"email": "a@x.io", "password": _PW}, "10.0.0.1")
        blocked = _post(router, "/api/auth/signup",
                        {"email": "a@x.io", "password": _PW}, "10.0.0.1")
        self.assertEqual(blocked.status, 429)
        body = json.loads(blocked.body.decode("utf-8"))
        self.assertNotIn("a@x.io", json.dumps(body))         # no email echo / existence hint


class TestLoginRateLimit(unittest.TestCase):
    def test_n_plus_one_logins_from_one_ip_is_429(self):
        # A high account-lockout threshold guarantees the 429 we assert is the per-IP RATE
        # LIMIT, not the per-account failed-login lockout (both map to 429 otherwise).
        _, router = _app(rl_login=3, login_max_attempts=99)
        _post(router, "/api/auth/signup",
              {"email": "a@x.io", "password": _PW}, "10.9.9.9")   # setup on a separate IP
        creds = {"email": "a@x.io", "password": _PW}
        for _ in range(3):
            self.assertEqual(_post(router, "/api/auth/login", creds, "10.0.0.5").status, 200)
        self.assertEqual(_post(router, "/api/auth/login", creds, "10.0.0.5").status, 429)

    def test_signup_and_login_limiters_are_independent(self):
        # Exhausting the signup bucket must not consume the login bucket (separate limiters).
        _, router = _app(rl_signup=1, rl_login=5, login_max_attempts=99)
        self.assertEqual(_post(router, "/api/auth/signup",
                               {"email": "a@x.io", "password": _PW}, "10.0.0.7").status, 201)
        self.assertEqual(_post(router, "/api/auth/signup",
                               {"email": "b@x.io", "password": _PW}, "10.0.0.7").status, 429)
        self.assertEqual(_post(router, "/api/auth/login",
                               {"email": "a@x.io", "password": _PW}, "10.0.0.7").status, 200)


if __name__ == "__main__":
    unittest.main()
