"""Domain-model tests (concrete layer — green)."""
import unittest

from backend import models
from backend.models import Incident, RecallResult, Memory, Brief


class TestModels(unittest.TestCase):
    def _incident(self) -> Incident:
        return Incident(
            external_id="INC-0007",
            title="Checkout 5xx after release",
            service="checkout-api",
            severity=models.SEV1,
            symptom="5xx spike after deploy",
            error_signature="http_5xx|deploy|conn_pool",
            tags=["deploy", "5xx"],
        )

    def test_now_ms_is_positive_int(self):
        self.assertIsInstance(models.now_ms(), int)
        self.assertGreater(models.now_ms(), 0)

    def test_signature_text_includes_key_fields(self):
        sig = self._incident().signature_text()
        self.assertIn("checkout-api", sig)
        self.assertIn("5xx spike after deploy", sig)
        self.assertIn("http_5xx|deploy|conn_pool", sig)
        self.assertIn("deploy", sig)  # tags surfaced

    def test_incident_as_dict_roundtrips_fields(self):
        d = self._incident().as_dict()
        for key in ("external_id", "service", "severity", "error_signature", "status"):
            self.assertIn(key, d)
        self.assertEqual(d["status"], models.OPEN)

    def test_recall_result_as_dict_shape(self):
        rr = RecallResult(query="q", memories=[Memory(content="c", score=0.9)], backend="local")
        d = rr.as_dict()
        self.assertEqual(d["backend"], "local")
        self.assertEqual(len(d["memories"]), 1)
        self.assertEqual(d["memories"][0]["score"], 0.9)

    def test_brief_defaults(self):
        b = Brief(summary="s")
        self.assertEqual(b.summary, "s")
        self.assertTrue(b.memory_used)
        self.assertIsInstance(b.as_dict(), dict)


if __name__ == "__main__":
    unittest.main()
