"""API endpoint behavior tests (FR-8/9/13 + happy path). Offline + deterministic:
temp SQLite DB, local memory + local reasoner, requests dispatched through the router.
"""
import dataclasses
import json
import os
import tempfile
import unittest

from backend import server
from backend.config import settings
from backend.router import Request


def _admin_headers(router):
    """Sign up the first account (bootstraps to admin) and return headers that carry the
    session cookie + CSRF token + a same-origin Origin, so the API tests exercise the real
    handlers through the auth middleware as an admin (which passes every role check)."""
    raw = json.dumps({"email": "admin@muninn.test", "password": "muninn-admin-pw1",
                      "name": "Admin"}).encode("utf-8")
    resp = router.dispatch(Request.build(
        "POST", "/api/auth/signup",
        {"Origin": "http://127.0.0.1", "Host": "127.0.0.1"}, raw))
    token = csrf = ""
    for c in resp.cookies:
        name, _, rest = c.partition("=")
        value = rest.split(";", 1)[0]
        if name == "muninn_session":
            token = value
        elif name == "muninn_csrf":
            csrf = value
    return {"Cookie": f"muninn_session={token}", "X-CSRF-Token": csrf,
            "Origin": "http://127.0.0.1", "Host": "127.0.0.1"}


def _app():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="api-test",
                             memory_backend="local", llm_backend="local")
    ctx = server.build_context(st)
    router = server.build_router(ctx)
    router._auth_headers = _admin_headers(router)
    return ctx, router


def _req(router, method, path, body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    headers = dict(getattr(router, "_auth_headers", {}))
    resp = router.dispatch(Request.build(method, path, headers, raw))
    parsed = json.loads(resp.body.decode("utf-8")) if resp.body else None
    return resp, parsed


class TestApiHappyPath(unittest.TestCase):
    def test_health_ok(self):
        _, router = _app()
        resp, body = _req(router, "GET", "/api/health")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["memory_backend"], "local")
        self.assertEqual(body["llm_backend"], "local")
        self.assertIn("n_memories", body)

    def test_seed_then_list(self):
        _, router = _app()
        resp, body = _req(router, "POST", "/api/demo/seed")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["seeded"]["incidents"] > 0)
        self.assertTrue(body["synthetic"])
        _, listing = _req(router, "GET", "/api/incidents")
        self.assertTrue(listing["incidents"])

    def test_create_incident_validation(self):
        _, router = _app()
        bad, _ = _req(router, "POST", "/api/incidents", {"title": "no service"})
        self.assertEqual(bad.status, 400)
        ok, body = _req(router, "POST", "/api/incidents",
                        {"title": "x", "service": "checkout-api", "severity": "SEV1"})
        self.assertEqual(ok.status, 201)
        self.assertIsNotNone(body["incident"]["id"])

    def test_get_missing_incident_404(self):
        _, router = _app()
        resp, _ = _req(router, "GET", "/api/incidents/999999")
        self.assertEqual(resp.status, 404)


class TestApiMemoryStory(unittest.TestCase):
    def test_compare_cold_vs_warm(self):
        ctx, router = _app()
        _req(router, "POST", "/api/demo/seed")
        inc = ctx.repo.get_incident_by_external("INC-0058")  # queue, family A
        resp, body = _req(router, "POST", "/api/compare", {"incident_id": inc.id})
        self.assertEqual(resp.status, 200)
        self.assertGreaterEqual(len(body["warm"]["citations"]), 1)
        self.assertEqual(body["cold"]["citations"], [])
        self.assertFalse(body["cold"]["memory_used"])

    def test_memory_recall_inspector(self):
        _, router = _app()
        _req(router, "POST", "/api/demo/seed")
        resp, body = _req(router, "POST", "/api/memory/recall",
                          {"query": "checkout 5xx after deploy conn_pool", "top_k": 3})
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["backend"], "local")
        self.assertTrue(body["memories"])
        self.assertIn("score", body["memories"][0])
        self.assertTrue(body["memories"][0]["source"])

    def test_recall_top_k_is_clamped_not_unbounded(self):
        # A huge top_k must be capped (<=50), not honoured verbatim, and must not error.
        _, router = _app()
        _req(router, "POST", "/api/demo/seed")
        resp, body = _req(router, "POST", "/api/memory/recall",
                          {"query": "checkout 5xx conn_pool", "top_k": 100000})
        self.assertEqual(resp.status, 200)
        self.assertLessEqual(len(body["memories"]), 50)

    def test_recall_non_numeric_top_k_is_400(self):
        # A non-numeric top_k is a client error (400), never an unhandled 500.
        _, router = _app()
        resp, _ = _req(router, "POST", "/api/memory/recall",
                       {"query": "x", "top_k": "abc"})
        self.assertEqual(resp.status, 400)

    def test_feedback(self):
        ctx, router = _app()
        _, created = _req(router, "POST", "/api/incidents",
                          {"title": "x", "service": "checkout-api", "severity": "SEV1",
                           "symptom": "5xx"})
        iid = created["incident"]["id"]
        _req(router, "POST", f"/api/incidents/{iid}/resolve",
             {"root_cause": "rc", "remediation_steps": ["fix"], "resolver": "o"})
        n_before = ctx.memory.count()
        resp, body = _req(router, "POST", f"/api/incidents/{iid}/feedback",
                          {"helpful": True, "root_cause_correct": True, "note": "good"})
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["incident"]["feedback"]["helpful"])
        self.assertEqual(ctx.memory.count(), n_before + 1)  # FR-9 reinforcement


class TestApiStreaming(unittest.TestCase):
    def test_sse_stream_shape(self):
        ctx, router = _app()
        _req(router, "POST", "/api/demo/seed")
        inc = ctx.repo.get_incident_by_external("INC-0058")
        resp = router.dispatch(Request.build(
            "GET", f"/api/triage/stream?incident_id={inc.id}&use_memory=true",
            dict(router._auth_headers), b""))
        self.assertIsNotNone(resp.stream)
        frames = "".join(resp.stream())
        self.assertIn("event: token", frames)
        self.assertIn("event: done", frames)


class TestCsrfSelfHeal(unittest.TestCase):
    """Regression: a session minted under one server secret must keep working for mutating
    requests after the secret rotates (e.g. a restart with the ephemeral default). The boot
    gate's GET /api/auth/me re-issues the muninn_csrf cookie from the CURRENT secret, so the
    double-submit token re-syncs instead of 403-ing every mutation forever."""

    @staticmethod
    def _csrf_cookie(resp):
        for c in resp.cookies:
            name, _, rest = c.partition("=")
            if name == "muninn_csrf":
                return rest.split(";", 1)[0]
        return None

    def test_me_reissues_csrf_after_secret_rotation(self):
        ctx, router = _app()
        _req(router, "POST", "/api/demo/seed")
        session_cookie = router._auth_headers["Cookie"]

        # Rotate the server secret out from under the live session (simulates a restart
        # with a fresh ephemeral MUNINN_SERVER_SECRET). The session still authenticates
        # (DB-backed), but the stale csrf token no longer matches.
        ctx.auth.settings = dataclasses.replace(ctx.auth.settings,
                                                server_secret="rotated-secret-xyz")

        # Bug repro: the stale double-submit token now fails CSRF.
        stale, _ = _req(router, "POST", "/api/memory/recall", {"query": "x", "top_k": 1})
        self.assertEqual(stale.status, 403)

        # Boot gate re-syncs: /api/auth/me returns the current token AND re-sets the cookie.
        me = router.dispatch(Request.build(
            "GET", "/api/auth/me",
            {"Cookie": session_cookie, "Origin": "http://127.0.0.1", "Host": "127.0.0.1"}, b""))
        self.assertEqual(me.status, 200)
        cookie_csrf = self._csrf_cookie(me)
        body_csrf = json.loads(me.body.decode("utf-8"))["csrf"]
        self.assertIsNotNone(cookie_csrf)
        self.assertEqual(cookie_csrf, body_csrf)

        # Self-healed: a mutating request echoing the refreshed token passes.
        healed = router.dispatch(Request.build(
            "POST", "/api/memory/recall",
            {"Cookie": session_cookie, "X-CSRF-Token": cookie_csrf,
             "Origin": "http://127.0.0.1", "Host": "127.0.0.1"},
            json.dumps({"query": "checkout 5xx", "top_k": 1}).encode("utf-8")))
        self.assertEqual(healed.status, 200)


if __name__ == "__main__":
    unittest.main()
