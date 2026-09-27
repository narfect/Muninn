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


def _app():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="api-test",
                             memory_backend="local", llm_backend="local")
    ctx = server.build_context(st)
    return ctx, server.build_router(ctx)


def _req(router, method, path, body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    resp = router.dispatch(Request.build(method, path, {}, raw))
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
            "GET", f"/api/triage/stream?incident_id={inc.id}&use_memory=true", {}, b""))
        self.assertIsNotNone(resp.stream)
        frames = "".join(resp.stream())
        self.assertIn("event: token", frames)
        self.assertIn("event: done", frames)


if __name__ == "__main__":
    unittest.main()
