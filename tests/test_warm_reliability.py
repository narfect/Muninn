import email.message
import threading
import unittest
import urllib.error
from unittest import mock

from backend.config import Settings
from backend.llm.agent import TriageAgent
from backend.llm.client import GroqClient
from backend.memory.hindsight_store import HindsightStore
from backend.models import Incident, Memory, RecallResult


class _Reasoner:
    """Live-style model that returns a fixed confidence regardless of grounding."""
    backend_name = "groq"

    def __init__(self, conf=0.8):
        self._conf = conf

    def chat(self, messages, tools=None):
        return {"content": '{"summary":"s","root_cause_hypothesis":"r","confidence":%s,'
                           '"remediation_steps":["step"],"runbook_ref":"RB-1","citations":[]}'
                           % self._conf,
                "tool_calls": []}


def _incident():
    return Incident(external_id="INC-1", title="t", service="svc", severity="SEV2",
                    symptom="500s", error_signature="HTTP 500")


def _recall(top):
    return RecallResult(query="HTTP 500", memories=[
        Memory(content="Root cause: bad deploy\nFix: rollback",
               score=top, source="INC-OLD", type="experience"),
    ])


class GradedConfidenceTest(unittest.TestCase):
    def test_cold_low_warm_higher_and_graded(self):
        agent = TriageAgent(_Reasoner(), memory=None, repo=None)
        cold = agent.triage(_incident(), use_memory=False)
        strong = agent.triage(_incident(), use_memory=True, recalled=_recall(0.95))
        weak = agent.triage(_incident(), use_memory=True, recalled=_recall(0.45))
        self.assertLessEqual(cold.confidence, 0.2)
        self.assertGreaterEqual(weak.confidence, 0.4)
        self.assertGreater(strong.confidence, cold.confidence)
        # Graded: a closer match must yield higher confidence than a looser one.
        self.assertGreater(strong.confidence, weak.confidence)
        self.assertLessEqual(strong.confidence, 0.95)


class _FakeHsClient:
    def __init__(self, raw):
        self._raw = raw

    def recall(self, bank, query, top_k=5):
        return {"ok": True, "items": [{"id": "1", "metadata": {}, "text": "c"}]}

    def extract_memories(self, resp):
        return resp["items"]

    def item_score(self, item):
        return self._raw

    def item_content(self, item):
        return item.get("text", "")

    def item_source(self, item):
        return "INC-OLD"


class HindsightNormalizationTest(unittest.TestCase):
    def _store(self, raw):
        store = HindsightStore.__new__(HindsightStore)  # skip __init__ (no network)
        store.settings = Settings()
        store.bank = "x"
        store.client = _FakeHsClient(raw)
        store._lock = threading.Lock()
        store._count = 0
        store._bank_ready = True
        return store

    def test_raw_hindsight_score_is_normalized_to_unit_interval(self):
        hit = self._store(1.1).recall("q").memories[0]       # strong match, raw ~1.1
        self.assertLessEqual(hit.score, 1.0)
        self.assertGreater(hit.score, 0.85)
        self.assertEqual(hit.metadata.get("raw_score"), 1.1)  # raw kept for provenance

    def test_normalization_preserves_ordering(self):
        strong = self._store(1.08).recall("q").memories[0].score
        weak = self._store(0.55).recall("q").memories[0].score
        self.assertGreater(strong, weak)
        self.assertLessEqual(strong, 1.0)


class _Resp:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    hdrs = email.message.Message()
    return urllib.error.HTTPError("http://x", code, "err", hdrs, None)


class GroqRetryTest(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        client = GroqClient(Settings())
        ok = ('{"choices":[{"message":{"content":'
              '"{\\"summary\\":\\"ok\\"}","tool_calls":null}}]}')
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_error(429)
            return _Resp(ok)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("time.sleep", return_value=None):
            resp = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls["n"], 2)                 # retried once
        self.assertIn("ok", resp.get("content", ""))

    def test_gives_up_gracefully_after_persistent_failure(self):
        client = GroqClient(Settings())

        def always_429(req, timeout=None):
            raise _http_error(429)

        with mock.patch("urllib.request.urlopen", side_effect=always_429), \
                mock.patch("time.sleep", return_value=None):
            resp = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(resp, {"content": "", "tool_calls": []})   # never raises


if __name__ == "__main__":
    unittest.main()
