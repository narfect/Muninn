"""PRIORITY 1c — honest LLM provenance + graceful runtime fallback (security pass).

Offline + deterministic: NO real network. The Groq path is fault-injected by patching
``backend.llm.client.urllib.request.urlopen`` so ``GroqClient`` behaves exactly as it does
when the provider is unreachable — ``chat()`` degrades to safe empties and ``health()``
reports an honest ``ok:false`` verdict — with zero egress. Asserts a degraded brief is
stamped ``llm_backend=="local"`` with ``degraded==True`` (never "groq"), stays grounded in
recalled memory, and that ``GET /api/health`` no longer claims Groq is reachable when it
never answered.
"""
import dataclasses
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from backend import server
from backend.config import settings
from backend.llm.agent import TriageAgent
from backend.llm.client import GroqClient, LocalReasoner
from backend.memory.local_store import LocalMemoryStore
from backend.models import Incident, Memory, RecallResult, SEV1
from backend.router import Request
from backend.services.triage import TriageService

# Single seam for offline fault-injection: everything Groq touches the network through here.
_URLOPEN = "backend.llm.client.urllib.request.urlopen"
_ORIGIN = {"Origin": "http://127.0.0.1", "Host": "127.0.0.1"}

_MEM_A = (
    "[INC-0007 checkout-api SEV1]\n"
    "Symptom: 5xx spike after deploy. Signature: http_5xx|deploy|conn_pool.\n"
    "Root cause: new release lowered DB pool below peak; connections exhausted.\n"
    "Fix: rolled back release; raised pgbouncer pool to 200; added canary gate.\n"
    "Runbook: RB-001. Resolver: payments-oncall. MTTR: 22m."
)


def _groq_settings(**over):
    """A config resolving to the Groq backend with a (never-actually-used) key, so the
    factory + agent build a real GroqClient we can then fault-inject offline."""
    d = tempfile.mkdtemp()
    base = dict(db_path=os.path.join(d, "t.db"), hindsight_bank="degrade-test",
                memory_backend="local", llm_backend="groq",
                groq_api_key="test-key-offline-only")
    base.update(over)
    return dataclasses.replace(settings, **base)


def _incident():
    return Incident(
        external_id="INC-NEW", title="Checkout 5xx climbing after deploy",
        service="checkout-api", severity=SEV1, symptom="5xx spike after deploy",
        error_signature="http_5xx|deploy|conn_pool",
        tags=["deploy", "5xx", "connection-pool"],
    )


def _recalled():
    return RecallResult(query="q", backend="local", memories=[
        Memory(content=_MEM_A, score=0.88, source="INC-0007"),
    ])


def _seeded_memory(st):
    mem = LocalMemoryStore(st)
    mem.retain(_MEM_A, source="INC-0007", metadata={"family": "A"})
    return mem


class TestDegradedProvenance(unittest.TestCase):
    """Groq configured but producing NOTHING -> a real local fallback answers, stamped
    honestly (llm_backend=="local", degraded==True) and still grounded in recall."""

    def test_agent_falls_back_to_local_and_stamps_honestly(self):
        st = _groq_settings()
        agent = TriageAgent(GroqClient(st), _seeded_memory(st), repo=None)
        with mock.patch(_URLOPEN, side_effect=urllib.error.URLError("offline")):
            brief = agent.triage(_incident(), use_memory=True, recalled=_recalled())
        self.assertEqual(brief.llm_backend, "local")   # NOT "groq"
        self.assertTrue(brief.degraded)
        self.assertTrue(brief.summary.startswith("(degraded)"))
        self.assertGreaterEqual(len(brief.citations), 1)
        self.assertEqual(brief.citations[0]["id"], "INC-0007")

    def test_local_path_is_not_marked_degraded(self):
        # Regression: the normal offline path stays degraded==False / llm_backend "local".
        st = _groq_settings(llm_backend="local")
        agent = TriageAgent(LocalReasoner(st), _seeded_memory(st), repo=None)
        brief = agent.triage(_incident(), use_memory=True)
        self.assertEqual(brief.llm_backend, "local")
        self.assertFalse(brief.degraded)


class TestTriageServiceProvenance(unittest.TestCase):
    """TriageService must NOT re-stamp llm_backend. The old clobber relabeled a degraded
    (locally-produced) brief as 'groq' — the exact dishonesty the security pass flagged."""

    def _service(self, st):
        mem = _seeded_memory(st)
        agent = TriageAgent(GroqClient(st), mem, repo=None)
        return TriageService(memory=mem, agent=agent, repo=None, top_k=st.recall_top_k)

    def test_triage_keeps_local_backend_when_groq_dead(self):
        st = _groq_settings()
        svc = self._service(st)
        with mock.patch(_URLOPEN, side_effect=urllib.error.URLError("offline")):
            out = svc.triage(_incident(), use_memory=True, persist=False)
        brief = out["brief"]
        self.assertEqual(brief.llm_backend, "local")   # survives service-level provenance
        self.assertTrue(brief.degraded)
        self.assertEqual(brief.memory_backend, "local")
        self.assertTrue(brief.memory_used)

    def test_compare_warm_and_cold_are_both_honest(self):
        st = _groq_settings()
        svc = self._service(st)
        with mock.patch(_URLOPEN, side_effect=urllib.error.URLError("offline")):
            out = svc.compare(_incident())
        warm, cold = out["warm"], out["cold"]
        self.assertEqual(warm.llm_backend, "local")
        self.assertTrue(warm.degraded)
        self.assertGreaterEqual(len(warm.citations), 1)   # warm stays grounded
        self.assertEqual(cold.llm_backend, "local")
        self.assertTrue(cold.degraded)
        self.assertEqual(cold.citations, [])              # cold never cites


class TestGroqHealthBuckets(unittest.TestCase):
    """A REAL cached probe, never key-presence-only, and it never raises / never egresses
    in the offline suite (every network touch is fault-injected)."""

    def test_no_key(self):
        h = GroqClient(_groq_settings(groq_api_key="")).health()
        self.assertFalse(h["ok"])
        self.assertEqual(h["detail"], "no GROQ_API_KEY")

    def test_unreachable(self):
        c = GroqClient(_groq_settings())
        with mock.patch(_URLOPEN, side_effect=urllib.error.URLError("offline")):
            h = c.health()
        self.assertFalse(h["ok"])
        self.assertEqual(h["detail"], "unreachable")

    def test_unauthorized(self):
        c = GroqClient(_groq_settings())
        err = urllib.error.HTTPError("http://x/models", 401, "unauth", {}, None)
        with mock.patch(_URLOPEN, side_effect=err):
            h = c.health()
        self.assertFalse(h["ok"])
        self.assertEqual(h["detail"], "unauthorized")

    def test_reachable(self):
        c = GroqClient(_groq_settings())
        with mock.patch(_URLOPEN, return_value=mock.MagicMock()):
            h = c.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["detail"], "reachable")

    def test_never_raises_on_unexpected_error(self):
        c = GroqClient(_groq_settings())
        with mock.patch(_URLOPEN, side_effect=ValueError("boom")):
            h = c.health()   # must not propagate
        self.assertFalse(h["ok"])

    def test_verdict_is_cached_within_ttl(self):
        c = GroqClient(_groq_settings())
        with mock.patch(_URLOPEN, return_value=mock.MagicMock()) as m:
            c.health()
            c.health()
        self.assertEqual(m.call_count, 1)   # 2nd call served from the ~30s cache


class TestHealthRoute(unittest.TestCase):
    def test_health_does_not_claim_groq_reachable_when_it_is_not(self):
        ctx = server.build_context(_groq_settings())
        router = server.build_router(ctx)
        # Backend/provenance detail on /api/health is authenticated-only now, so sign up the
        # first account (bootstraps to admin) and probe health with its session. Signup never
        # touches the network, so it's fine to do before the offline fault injection.
        su = router.dispatch(Request.build(
            "POST", "/api/auth/signup", dict(_ORIGIN),
            json.dumps({"email": "a@x.io", "password": "muninn-admin-1"}).encode("utf-8")))
        token = csrf = ""
        for c in su.cookies:
            name, _, rest = c.partition("=")
            val = rest.split(";", 1)[0]
            if name == "muninn_session":
                token = val
            elif name == "muninn_csrf":
                csrf = val
        auth_headers = {"Cookie": f"muninn_session={token}", "X-CSRF-Token": csrf, **_ORIGIN}
        with mock.patch(_URLOPEN, side_effect=urllib.error.URLError("offline")):
            resp = router.dispatch(Request.build("GET", "/api/health", auth_headers, b""))
        self.assertEqual(resp.status, 200)      # health must not blow up when Groq is down
        body = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(body["llm"]["backend"], "groq")
        self.assertFalse(body["llm"]["ok"])     # honest: it never answered


if __name__ == "__main__":
    unittest.main()
