"""Service-layer tests: lifecycle, learning loop, metrics (FR-1/4/6/7/9/14..16).
Offline: temp SQLite DB + local memory backend, deterministic.
"""
import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.config import settings
from backend.memory.local_store import LocalMemoryStore
from backend.models import RESOLVED, SEV1
from backend.services.incidents import IncidentService
from backend.services.metrics import MetricsService

_SAMPLE = Path(__file__).resolve().parents[1] / "data" / "seed_sample.json"


def _svc():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="svc-test")
    from backend.db import Repository
    repo = Repository(st.db_path)
    mem = LocalMemoryStore(st)
    return IncidentService(repo, mem), MetricsService(repo, mem), repo, mem


def _sample():
    return json.loads(_SAMPLE.read_text(encoding="utf-8"))


class TestIncidentLifecycle(unittest.TestCase):
    def test_create_incident(self):
        inc_svc, _, repo, _ = _svc()
        inc = inc_svc.create_incident({
            "title": "Checkout 5xx", "service": "checkout-api", "severity": "SEV1",
            "symptom": "5xx spike after deploy", "tags": ["deploy", "5xx"],
        })
        self.assertIsNotNone(inc.id)
        self.assertTrue(inc.error_signature)
        self.assertEqual(repo.get_incident(inc.id).title, "Checkout 5xx")
        kinds = [e["kind"] for e in repo.list_timeline(inc.id)]
        self.assertIn("opened", kinds)

    def test_illegal_transition_rejected(self):
        inc_svc, _, _, _ = _svc()
        inc = inc_svc.create_incident({"title": "x", "service": "checkout-api",
                                       "severity": "SEV2"})
        inc_svc.transition(inc.id, "mitigated")           # legal forward jump
        with self.assertRaises(ValueError):
            inc_svc.transition(inc.id, "triaging")        # backward -> illegal
        with self.assertRaises(ValueError):
            inc_svc.transition(inc.id, "nonsense")        # unknown status

    def test_resolve_computes_mttr(self):
        inc_svc, _, _, _ = _svc()
        from backend.models import now_ms
        inc = inc_svc.create_incident({
            "title": "x", "service": "checkout-api", "severity": "SEV1",
            "created_at": now_ms() - 12 * 60_000,  # 12 minutes ago
        })
        resolved = inc_svc.resolve(inc.id, root_cause="pool exhausted",
                                   remediation_steps=["rollback"], resolver="oncall")
        self.assertEqual(resolved.status, RESOLVED)
        self.assertIsNotNone(resolved.resolved_at)
        self.assertGreaterEqual(resolved.mttr_minutes, 11.0)
        self.assertLessEqual(resolved.mttr_minutes, 13.0)


class TestLearningLoop(unittest.TestCase):
    def test_resolve_retains_memory(self):
        inc_svc, _, _, mem = _svc()
        n0 = mem.count()
        inc = inc_svc.create_incident({"title": "x", "service": "checkout-api",
                                       "severity": "SEV1", "symptom": "5xx after deploy"})
        inc_svc.resolve(inc.id, root_cause="db pool exhausted",
                        remediation_steps=["rollback", "raise pool"], resolver="oncall")
        self.assertEqual(mem.count(), n0 + 1)

    def test_retained_memory_is_recallable(self):
        inc_svc, _, _, mem = _svc()
        inc = inc_svc.create_incident({
            "title": "Checkout 5xx after deploy", "service": "checkout-api",
            "severity": "SEV1", "symptom": "5xx spike after deploy",
            "error_signature": "http_5xx|deploy|conn_pool", "tags": ["deploy", "5xx"]})
        inc_svc.resolve(inc.id, root_cause="DB pool exhausted under load",
                        remediation_steps=["rolled back release", "raised pool to 200"],
                        resolver="payments-oncall")
        rc = mem.recall("checkout 5xx after deploy conn pool exhausted", top_k=3)
        self.assertTrue(rc.memories)
        self.assertEqual(rc.memories[0].source, inc.external_id)
        self.assertIn("Root cause", rc.memories[0].content)

    def test_feedback_reinforces(self):
        inc_svc, _, _, mem = _svc()
        inc = inc_svc.create_incident({"title": "x", "service": "checkout-api",
                                       "severity": "SEV1", "symptom": "5xx"})
        inc_svc.resolve(inc.id, root_cause="rc", remediation_steps=["fix"], resolver="o")
        n1 = mem.count()
        inc_svc.record_feedback(inc.id, helpful=True, root_cause_correct=True, note="great")
        self.assertEqual(mem.count(), n1 + 1)


class TestMetrics(unittest.TestCase):
    def test_mttr(self):
        inc_svc, metrics, _, _ = _svc()
        inc_svc.seed(_sample())
        m = metrics.mttr()
        self.assertGreater(m["n"], 0)
        self.assertGreater(m["overall_min"], 0.0)
        self.assertIn("checkout-api", m["by_service"])

    def test_learning_curve_series(self):
        inc_svc, metrics, _, _ = _svc()
        inc_svc.seed(_sample())
        series = metrics.learning_curve()
        self.assertTrue(series)
        ts = [p["ts"] for p in series]
        self.assertEqual(ts, sorted(ts))                  # chronological
        self.assertGreater(series[-1]["n_memories"], 0)   # memory accumulates
        # a later recurring-family incident should find a prior match
        self.assertTrue(any(p["hit"] for p in series))

    def test_coverage(self):
        inc_svc, metrics, _, _ = _svc()
        inc_svc.seed(_sample())
        cov = metrics.coverage("checkout-api: 5xx spike after deploy "
                               "(http_5xx|deploy|conn_pool)")
        self.assertGreaterEqual(cov["n_similar"], 1)
        self.assertGreater(cov["best_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
