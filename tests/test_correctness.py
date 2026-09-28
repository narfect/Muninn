"""Phase 1 regression tests — correctness fixes B1, S1, S2, S3, S4.

Offline + deterministic: tempfile SQLite path (NOT :memory:, because the code sets
PRAGMA journal_mode=WAL) + local memory/LLM backends, requests dispatched through the
router. Each test fails against the pre-Phase-1 code and passes after the fix.
"""
import dataclasses
import json
import os
import tempfile
import unittest

from backend import server
from backend.config import settings
from backend.errors import ConflictError, NotFoundError
from backend.memory.local_store import LocalMemoryStore
from backend.models import RESOLVED, now_ms
from backend.router import Request
from backend.services.incidents import IncidentService


def _svc():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="correctness-test")
    from backend.db import Repository
    repo = Repository(st.db_path)
    mem = LocalMemoryStore(st)
    return IncidentService(repo, mem), repo, mem


def _admin_headers(router):
    """Bootstrap the first account (becomes admin) and return headers carrying the session
    cookie + CSRF token + same-origin Origin, so the Phase 1 API regressions run as admin
    through the Phase 3 auth middleware."""
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
                             hindsight_bank="correctness-api",
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


class TestB1TransitionResolvesLikeResolve(unittest.TestCase):
    """B1: transition(status='resolved') must compute MTTR AND retain to memory."""

    def test_transition_to_resolved_retains_and_computes_mttr(self):
        inc_svc, _, mem = _svc()
        n0 = mem.count()
        inc = inc_svc.create_incident({
            "title": "x", "service": "checkout-api", "severity": "SEV1",
            "symptom": "5xx after deploy",
            "created_at": now_ms() - 10 * 60_000,  # 10 minutes ago
        })
        resolved = inc_svc.transition(inc.id, "resolved")
        self.assertEqual(resolved.status, RESOLVED)
        self.assertIsNotNone(resolved.resolved_at)
        self.assertIsNotNone(resolved.mttr_minutes)         # was None pre-fix
        self.assertGreaterEqual(resolved.mttr_minutes, 9.0)
        self.assertLessEqual(resolved.mttr_minutes, 11.0)
        self.assertEqual(mem.count(), n0 + 1)               # retained (learning loop)

    def test_transition_to_resolved_via_api_retains(self):
        ctx, router = _app()
        _, created = _req(router, "POST", "/api/incidents",
                          {"title": "x", "service": "checkout-api", "severity": "SEV1",
                           "symptom": "5xx"})
        iid = created["incident"]["id"]
        n0 = ctx.memory.count()
        resp, body = _req(router, "POST", f"/api/incidents/{iid}/transition",
                          {"status": "resolved"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["incident"]["status"], RESOLVED)
        self.assertIsNotNone(body["incident"]["mttr_minutes"])
        self.assertEqual(ctx.memory.count(), n0 + 1)


class TestS1RootCauseAlias(unittest.TestCase):
    """S1: Brief.as_dict() exposes a root_cause alias for raw-JSON clients."""

    def test_compare_json_has_non_null_root_cause(self):
        ctx, router = _app()
        _req(router, "POST", "/api/demo/seed")
        inc = ctx.repo.get_incident_by_external("INC-0058")
        resp, body = _req(router, "POST", "/api/compare", {"incident_id": inc.id})
        self.assertEqual(resp.status, 200)
        self.assertIn("root_cause", body["warm"])
        self.assertIsNotNone(body["warm"]["root_cause"])
        self.assertEqual(body["warm"]["root_cause"],
                         body["warm"]["root_cause_hypothesis"])


class TestS2RemediationStepsGuard(unittest.TestCase):
    """S2: a non-list remediation_steps is rejected, not exploded into characters."""

    def test_resolve_rejects_string_remediation_steps(self):
        inc_svc, _, _ = _svc()
        inc = inc_svc.create_incident({"title": "x", "service": "checkout-api",
                                       "severity": "SEV1"})
        with self.assertRaises(ValueError):
            inc_svc.resolve(inc.id, root_cause="rc",
                            remediation_steps="rollback", resolver="o")

    def test_resolve_rejects_string_remediation_steps_via_api(self):
        _, router = _app()
        _, created = _req(router, "POST", "/api/incidents",
                          {"title": "x", "service": "checkout-api", "severity": "SEV1"})
        iid = created["incident"]["id"]
        resp, _ = _req(router, "POST", f"/api/incidents/{iid}/resolve",
                       {"root_cause": "rc", "remediation_steps": "rollback",
                        "resolver": "o"})
        self.assertEqual(resp.status, 400)


class TestS3DuplicateExternalId(unittest.TestCase):
    """S3: a duplicate external_id is a clean 409 conflict, not a 500."""

    def test_duplicate_external_id_raises_conflict(self):
        inc_svc, _, _ = _svc()
        inc_svc.create_incident({"external_id": "INC-DUP", "title": "a",
                                 "service": "checkout-api", "severity": "SEV1"})
        with self.assertRaises(ConflictError):
            inc_svc.create_incident({"external_id": "INC-DUP", "title": "b",
                                     "service": "checkout-api", "severity": "SEV1"})

    def test_duplicate_external_id_via_api_is_409(self):
        _, router = _app()
        body = {"external_id": "INC-DUP", "title": "a", "service": "checkout-api",
                "severity": "SEV1"}
        first, _ = _req(router, "POST", "/api/incidents", body)
        self.assertEqual(first.status, 201)
        second, _ = _req(router, "POST", "/api/incidents", dict(body, title="b"))
        self.assertEqual(second.status, 409)

    def test_auto_external_id_is_collision_resistant(self):
        inc_svc, _, _ = _svc()
        ids = {inc_svc.create_incident(
            {"title": "x", "service": "checkout-api", "severity": "SEV3"}).external_id
            for _ in range(50)}
        self.assertEqual(len(ids), 50)  # all unique


class TestS4NotFoundIs404(unittest.TestCase):
    """S4: not-found on transition/resolve/feedback returns 404, not 400."""

    def test_service_raises_not_found(self):
        inc_svc, _, _ = _svc()
        with self.assertRaises(NotFoundError):
            inc_svc.transition(999999, "mitigated")
        with self.assertRaises(NotFoundError):
            inc_svc.resolve(999999, root_cause="rc", remediation_steps=["fix"],
                            resolver="o")
        with self.assertRaises(NotFoundError):
            inc_svc.record_feedback(999999, helpful=True, root_cause_correct=True)

    def test_api_not_found_maps_to_404(self):
        _, router = _app()
        for method, path, body in (
            ("POST", "/api/incidents/999999/transition", {"status": "mitigated"}),
            ("POST", "/api/incidents/999999/resolve",
             {"root_cause": "rc", "remediation_steps": ["fix"], "resolver": "o"}),
            ("POST", "/api/incidents/999999/feedback",
             {"helpful": True, "root_cause_correct": True}),
        ):
            resp, _ = _req(router, method, path, body)
            self.assertEqual(resp.status, 404, f"{method} {path}")

    def test_real_bad_input_still_400(self):
        _, router = _app()
        _, created = _req(router, "POST", "/api/incidents",
                          {"title": "x", "service": "checkout-api", "severity": "SEV2"})
        iid = created["incident"]["id"]
        # unknown status is bad input on an existing incident -> 400, not 404
        resp, _ = _req(router, "POST", f"/api/incidents/{iid}/transition",
                       {"status": "nonsense"})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
