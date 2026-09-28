"""Phase 3 tests — authentication, sessions, RBAC, and CSRF (offline + deterministic).

Service-level tests drive ``AuthService`` against a tempfile SQLite repo; router-level
tests exercise the real endpoints through the auth middleware. A fixed ``server_secret``
keeps CSRF tokens reproducible across the process.
"""
import dataclasses
import json
import os
import tempfile
import unittest

from backend import server
from backend.config import settings
from backend.db import Repository, init_db
from backend.errors import AuthError, ConflictError, LockedError, NotFoundError
from backend.models import ADMIN, RESPONDER, VIEWER, now_ms
from backend.router import Request
from backend.services.auth import AuthService


def _settings(**over):
    d = tempfile.mkdtemp()
    base = dict(db_path=os.path.join(d, "t.db"), hindsight_bank="auth-test",
                memory_backend="local", llm_backend="local",
                server_secret="test-secret-fixed")
    base.update(over)
    return dataclasses.replace(settings, **base)


def _auth(**over):
    st = _settings(**over)
    repo = Repository(st.db_path)
    return AuthService(repo, st), repo, st


def _router(**over):
    st = _settings(**over)
    ctx = server.build_context(st)
    return ctx, server.build_router(ctx)


# PLACEHOLDER-APPEND


_ORIGIN = {"Origin": "http://127.0.0.1", "Host": "127.0.0.1"}


def _cookies_from(resp):
    token = csrf = ""
    for c in resp.cookies:
        name, _, rest = c.partition("=")
        value = rest.split(";", 1)[0]
        if name == "muninn_session":
            token = value
        elif name == "muninn_csrf":
            csrf = value
    return token, csrf


def _headers(token, csrf, origin="http://127.0.0.1"):
    return {"Cookie": f"muninn_session={token}", "X-CSRF-Token": csrf,
            "Origin": origin, "Host": "127.0.0.1"}


def _dispatch(router, method, path, body=None, headers=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    resp = router.dispatch(Request.build(method, path, headers or {}, raw))
    parsed = json.loads(resp.body.decode("utf-8")) if resp.body else None
    return resp, parsed


def _signup(router, email, pw, name=""):
    resp = router.dispatch(Request.build(
        "POST", "/api/auth/signup", dict(_ORIGIN),
        json.dumps({"email": email, "password": pw, "name": name}).encode("utf-8")))
    body = json.loads(resp.body.decode("utf-8")) if resp.body else None
    token, csrf = _cookies_from(resp)
    return resp, body, _headers(token, csrf)


def _login(router, email, pw):
    resp = router.dispatch(Request.build(
        "POST", "/api/auth/login", dict(_ORIGIN),
        json.dumps({"email": email, "password": pw}).encode("utf-8")))
    body = json.loads(resp.body.decode("utf-8")) if resp.body else None
    token, csrf = _cookies_from(resp)
    return resp, body, _headers(token, csrf)


# APPEND-2


class TestPasswordHashing(unittest.TestCase):
    def test_roundtrip_and_wrong_password(self):
        a, _, _ = _auth()
        h = a.hash_password("s3cret-passphrase")
        self.assertTrue(a.verify_password("s3cret-passphrase", h))
        self.assertFalse(a.verify_password("wrong-password", h))

    def test_salt_makes_hashes_unique(self):
        a, _, _ = _auth()
        self.assertNotEqual(a.hash_password("same-pass-1"), a.hash_password("same-pass-1"))

    def test_stored_format_is_recognized_algo(self):
        a, _, _ = _auth()
        h = a.hash_password("whatever-pw-1")
        self.assertTrue(h.startswith("scrypt$") or h.startswith("pbkdf2$"))

    def test_verify_bad_stored_string_is_false(self):
        a, _, _ = _auth()
        self.assertFalse(a.verify_password("x", "garbage"))
        self.assertFalse(a.verify_password("x", ""))


class TestPasswordPolicy(unittest.TestCase):
    def test_rejects_short_all_digits_and_common(self):
        a, _, _ = _auth()
        with self.assertRaises(ValueError):
            a.validate_password("short")
        with self.assertRaises(ValueError):
            a.validate_password("12345678")
        with self.assertRaises(ValueError):
            a.validate_password("password")
        a.validate_password("a-good-passphrase")  # must not raise


class TestSignup(unittest.TestCase):
    def test_first_is_admin_rest_are_viewers(self):
        a, _, _ = _auth()
        u1, _, _ = a.signup("a@x.com", "pw-one-good-1")
        u2, _, _ = a.signup("b@x.com", "pw-two-good-1")
        self.assertEqual(u1.role, ADMIN)
        self.assertEqual(u2.role, VIEWER)

    def test_duplicate_email_normalized_conflict(self):
        a, _, _ = _auth()
        a.signup("a@x.com", "pw-one-good-1")
        with self.assertRaises(ConflictError):
            a.signup("A@X.com", "pw-two-good-1")

    def test_weak_password_and_bad_email_rejected(self):
        a, _, _ = _auth()
        with self.assertRaises(ValueError):
            a.signup("a@x.com", "short")
        with self.assertRaises(ValueError):
            a.signup("not-an-email", "pw-good-strong-1")


# APPEND-3


class TestLoginAndLockout(unittest.TestCase):
    def test_login_success_opens_session(self):
        a, _, _ = _auth()
        a.signup("a@x.com", "pw-good-strong-1")
        user, token, csrf = a.login("a@x.com", "pw-good-strong-1")
        self.assertTrue(token)
        self.assertTrue(csrf)
        self.assertEqual(a.authenticate(token)[0].email, "a@x.com")

    def test_wrong_password_and_unknown_user_raise_autherror(self):
        a, _, _ = _auth()
        a.signup("a@x.com", "pw-good-strong-1")
        with self.assertRaises(AuthError):
            a.login("a@x.com", "nope-nope-nope-1")
        with self.assertRaises(AuthError):
            a.login("ghost@x.com", "whatever-good-1")

    def test_lockout_after_max_attempts(self):
        a, _, _ = _auth(login_max_attempts=3, login_lockout_seconds=900)
        a.signup("a@x.com", "pw-good-strong-1")
        for _ in range(3):
            with self.assertRaises(AuthError):
                a.login("a@x.com", "wrong-pass-nine-9")
        # Locked now — even the correct password is refused during the window.
        with self.assertRaises(LockedError):
            a.login("a@x.com", "pw-good-strong-1")


class TestSessions(unittest.TestCase):
    def test_valid_session_resolves_user(self):
        a, repo, _ = _auth()
        u, _, _ = a.signup("a@x.com", "pw-good-strong-1")
        tok = "tok-valid"
        th = a._hash_token(tok)
        now = now_ms()
        repo.create_session(th, u.id, now, now, now + 10_000_000)
        res = a.authenticate(tok)
        self.assertIsNotNone(res)
        self.assertEqual(res[0].id, u.id)

    def test_absolute_expiry_deletes_session(self):
        a, repo, _ = _auth()
        u, _, _ = a.signup("a@x.com", "pw-good-strong-1")
        tok = "tok-abs"
        th = a._hash_token(tok)
        now = now_ms()
        repo.create_session(th, u.id, now - 5000, now - 5000, now - 1000)
        self.assertIsNone(a.authenticate(tok))
        self.assertIsNone(repo.get_session(th))

    def test_idle_expiry(self):
        a, repo, st = _auth()
        u, _, _ = a.signup("a@x.com", "pw-good-strong-1")
        tok = "tok-idle"
        th = a._hash_token(tok)
        now = now_ms()
        stale = now - (st.session_idle_seconds * 1000 + 5000)
        repo.create_session(th, u.id, now - 10_000_000, stale, now + 10_000_000)
        self.assertIsNone(a.authenticate(tok))

    def test_unknown_and_empty_token(self):
        a, _, _ = _auth()
        self.assertIsNone(a.authenticate("does-not-exist"))
        self.assertIsNone(a.authenticate(None))

    def test_logout_invalidates(self):
        a, _, _ = _auth()
        _, token, _ = a.signup("a@x.com", "pw-good-strong-1")
        self.assertIsNotNone(a.authenticate(token))
        a.logout(token)
        self.assertIsNone(a.authenticate(token))


# APPEND-4


class TestCsrfTokenAndRoles(unittest.TestCase):
    def test_csrf_token_deterministic_and_binds_session(self):
        a, _, _ = _auth()
        self.assertEqual(a.csrf_token("abc"), a.csrf_token("abc"))
        self.assertNotEqual(a.csrf_token("abc"), a.csrf_token("xyz"))

    def test_set_role_updates_and_invalidates_sessions(self):
        a, _, _ = _auth()
        a.signup("admin@x.com", "pw-good-strong-1")           # admin
        u2, t2, _ = a.signup("b@x.com", "pw-good-strong-1")   # viewer
        self.assertIsNotNone(a.authenticate(t2))
        updated = a.set_role(u2.id, RESPONDER)
        self.assertEqual(updated.role, RESPONDER)
        self.assertIsNone(a.authenticate(t2))  # forced re-auth

    def test_set_role_rejects_bad_role_and_missing_user(self):
        a, _, _ = _auth()
        u, _, _ = a.signup("a@x.com", "pw-good-strong-1")
        with self.assertRaises(ValueError):
            a.set_role(u.id, "superuser")
        with self.assertRaises(NotFoundError):
            a.set_role(999999, RESPONDER)


class TestCookieHelpers(unittest.TestCase):
    def test_session_cookie_is_httponly_samesite(self):
        a, _, _ = _auth()
        c = a.session_cookie("abc")
        self.assertIn("muninn_session=abc", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Strict", c)
        self.assertIn("Path=/", c)
        self.assertNotIn("Secure", c)

    def test_secure_attribute_when_configured(self):
        a, _, _ = _auth(cookie_secure=True)
        self.assertIn("Secure", a.session_cookie("abc"))

    def test_csrf_cookie_is_readable_by_js(self):
        a, _, _ = _auth()
        self.assertNotIn("HttpOnly", a.csrf_cookie("z"))

    def test_clear_cookies_expire_immediately(self):
        a, _, _ = _auth()
        self.assertTrue(all("Max-Age=0" in c for c in a.clear_cookies()))


class TestSchemaIdempotent(unittest.TestCase):
    def test_init_db_is_idempotent(self):
        st = _settings()
        init_db(st.db_path)
        init_db(st.db_path)
        Repository(st.db_path)  # must not raise


# APPEND-5


class TestRbacThroughRouter(unittest.TestCase):
    def test_unauthenticated_read_is_401(self):
        _, router = _router()
        resp, _ = _dispatch(router, "GET", "/api/incidents")
        self.assertEqual(resp.status, 401)

    def test_health_is_public(self):
        _, router = _router()
        resp, body = _dispatch(router, "GET", "/api/health")
        self.assertEqual(resp.status, 200)
        # Anonymous callers get liveness ONLY — no backend/provenance fingerprinting.
        self.assertEqual(body, {"status": "ok"})
        self.assertNotIn("memory_backend", body)
        self.assertNotIn("n_memories", body)

    def test_health_detail_is_authenticated_only(self):
        _, router = _router()
        _, _, admin_h = _signup(router, "admin@x.com", "pw-good-strong-1")
        resp, body = _dispatch(router, "GET", "/api/health", headers=admin_h)
        self.assertEqual(resp.status, 200)
        # An authenticated caller (any role) sees the full backend/provenance detail.
        self.assertEqual(body["memory_backend"], "local")
        self.assertEqual(body["llm_backend"], "local")
        self.assertIn("n_memories", body)
        self.assertIn("llm", body)

    def test_role_matrix(self):
        _, router = _router()
        _, _, admin_h = _signup(router, "admin@x.com", "pw-good-strong-1")   # admin
        _, vbody, viewer_h = _signup(router, "viewer@x.com", "pw-good-strong-1")  # viewer
        vid = vbody["user"]["id"]
        # viewer can read, but cannot mutate incidents (responder) or reset (admin)
        self.assertEqual(_dispatch(router, "GET", "/api/incidents", headers=viewer_h)[0].status, 200)
        self.assertEqual(_dispatch(router, "POST", "/api/incidents",
                                   {"title": "x", "service": "checkout-api", "severity": "SEV1"},
                                   headers=viewer_h)[0].status, 403)
        self.assertEqual(_dispatch(router, "POST", "/api/demo/reset", headers=viewer_h)[0].status, 403)
        # admin can reset
        self.assertEqual(_dispatch(router, "POST", "/api/demo/reset", headers=admin_h)[0].status, 200)
        # admin promotes viewer -> responder; the old viewer session is invalidated
        self.assertEqual(_dispatch(router, "PATCH", f"/api/users/{vid}",
                                   {"role": "responder"}, headers=admin_h)[0].status, 200)
        _, _, resp_h = _login(router, "viewer@x.com", "pw-good-strong-1")
        self.assertEqual(_dispatch(router, "POST", "/api/incidents",
                                   {"title": "x", "service": "checkout-api", "severity": "SEV1"},
                                   headers=resp_h)[0].status, 201)
        # responder still cannot reach admin-only reset
        self.assertEqual(_dispatch(router, "POST", "/api/demo/reset", headers=resp_h)[0].status, 403)


class TestAuthEndpoints(unittest.TestCase):
    def test_login_sets_httponly_samesite_cookie(self):
        _, router = _router()
        _signup(router, "a@x.com", "pw-good-strong-1")
        resp, _ = _dispatch(router, "POST", "/api/auth/login",
                            {"email": "a@x.com", "password": "pw-good-strong-1"}, headers=dict(_ORIGIN))
        self.assertEqual(resp.status, 200)
        sess = [c for c in resp.cookies if c.startswith("muninn_session=")][0]
        self.assertIn("HttpOnly", sess)
        self.assertIn("SameSite=Strict", sess)

    def test_login_wrong_creds_is_401(self):
        _, router = _router()
        _signup(router, "a@x.com", "pw-good-strong-1")
        resp, _ = _dispatch(router, "POST", "/api/auth/login",
                            {"email": "a@x.com", "password": "wrong-pass-1"}, headers=dict(_ORIGIN))
        self.assertEqual(resp.status, 401)

    def test_lockout_via_api_is_429(self):
        _, router = _router(login_max_attempts=2)
        _signup(router, "a@x.com", "pw-good-strong-1")
        for _ in range(2):
            _dispatch(router, "POST", "/api/auth/login",
                      {"email": "a@x.com", "password": "wrong-pass-1"}, headers=dict(_ORIGIN))
        resp, _ = _dispatch(router, "POST", "/api/auth/login",
                            {"email": "a@x.com", "password": "pw-good-strong-1"}, headers=dict(_ORIGIN))
        self.assertEqual(resp.status, 429)

    def test_me_and_logout(self):
        _, router = _router()
        _, _, h = _signup(router, "a@x.com", "pw-good-strong-1")
        resp, body = _dispatch(router, "GET", "/api/auth/me", headers=h)
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["user"]["email"], "a@x.com")
        self.assertIn("csrf", body)
        self.assertEqual(_dispatch(router, "POST", "/api/auth/logout", headers=h)[0].status, 200)
        self.assertEqual(_dispatch(router, "GET", "/api/auth/me", headers=h)[0].status, 401)


# APPEND-6


class TestCsrfMiddleware(unittest.TestCase):
    def _admin(self, router):
        resp = router.dispatch(Request.build(
            "POST", "/api/auth/signup", dict(_ORIGIN),
            json.dumps({"email": "admin@x.com", "password": "pw-good-strong-1"}).encode("utf-8")))
        return _cookies_from(resp)

    def test_missing_csrf_token_is_403(self):
        _, router = _router()
        token, _csrf = self._admin(router)
        headers = {"Cookie": f"muninn_session={token}", **_ORIGIN}  # no X-CSRF-Token
        resp, _ = _dispatch(router, "POST", "/api/demo/reset", headers=headers)
        self.assertEqual(resp.status, 403)

    def test_foreign_origin_is_403(self):
        _, router = _router()
        token, csrf = self._admin(router)
        headers = _headers(token, csrf, origin="http://evil.example")
        resp, _ = _dispatch(router, "POST", "/api/demo/reset", headers=headers)
        self.assertEqual(resp.status, 403)

    def test_valid_same_origin_csrf_succeeds(self):
        _, router = _router()
        token, csrf = self._admin(router)
        resp, _ = _dispatch(router, "POST", "/api/demo/reset", headers=_headers(token, csrf))
        self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
