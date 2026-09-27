"""Agent tool-loop + robustness tests (FR-11/12).

FR-12 is a guide-mandated requirement: malformed tool calls must never crash the agent.
The happy-path tests use the deterministic offline LocalReasoner; the robustness tests use
tiny fake reasoners that deliberately emit bad/looping/garbage output.
"""
import dataclasses
import json
import os
import tempfile
import unittest

from backend.config import settings
from backend.llm.agent import _MAX_ITERS, TriageAgent
from backend.llm.client import LocalReasoner
from backend.memory.local_store import LocalMemoryStore
from backend.models import Incident, Memory, RecallResult, SEV1

_MEM_A = (
    "[INC-0007 checkout-api SEV1]\n"
    "Symptom: 5xx spike after deploy. Signature: http_5xx|deploy|conn_pool.\n"
    "Root cause: new release lowered DB pool below peak; connections exhausted.\n"
    "Fix: rolled back release; raised pgbouncer pool to 200; added canary gate.\n"
    "Runbook: RB-001. Resolver: payments-oncall. MTTR: 22m."
)


def _incident():
    return Incident(
        external_id="INC-NEW", title="Checkout 5xx climbing after deploy",
        service="checkout-api", severity=SEV1, symptom="5xx spike after deploy",
        error_signature="http_5xx|deploy|conn_pool",
        tags=["deploy", "5xx", "connection-pool"],
    )


def _seeded_agent():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="agent-test")
    mem = LocalMemoryStore(st)
    mem.retain(_MEM_A, source="INC-0007", metadata={"family": "A"})
    return TriageAgent(LocalReasoner(st), mem, repo=None), mem


class _Fake:
    """Duck-typed reasoner driven by a list of canned responses / a callable."""
    backend_name = "fake"

    def __init__(self, script):
        self._script = script
        self.calls = 0

    def chat(self, messages, *, tools=None, temperature=0.2):
        self.calls += 1
        if callable(self._script):
            return self._script(self.calls, messages)
        idx = min(self.calls - 1, len(self._script) - 1)
        return self._script[idx]

    def health(self):
        return {"backend": self.backend_name, "ok": True, "detail": "fake"}


class TestAgentLoop(unittest.TestCase):
    def test_triage_returns_brief(self):
        agent, _ = _seeded_agent()
        brief = agent.triage(_incident(), use_memory=True)
        self.assertTrue(brief.summary)
        self.assertTrue(brief.remediation_steps)
        self.assertEqual(brief.llm_backend, "local")

    def test_cold_vs_warm_citations(self):
        agent, _ = _seeded_agent()
        warm = agent.triage(_incident(), use_memory=True)
        cold = agent.triage(_incident(), use_memory=False)
        self.assertGreaterEqual(len(warm.citations), 1)
        self.assertEqual(warm.citations[0]["id"], "INC-0007")
        self.assertEqual(cold.citations, [])
        self.assertFalse(cold.memory_used)
        self.assertTrue(warm.memory_used)


class TestAgentRobustness(unittest.TestCase):
    def test_malformed_tool_args_no_crash(self):
        agent, _ = _seeded_agent()

        def script(n, messages):
            if n == 1:  # missing required "query" -> dispatch returns {"error":...}
                return {"content": None,
                        "tool_calls": [{"id": "1", "name": "recall_memory", "arguments": {}}]}
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            self.assertTrue(any("error" in (m.get("content") or "") for m in tool_msgs))
            return {"content": json.dumps({"summary": "recovered", "remediation_steps": ["x"]}),
                    "tool_calls": []}

        agent.reasoner = _Fake(script)
        brief = agent.triage(_incident(), use_memory=True)
        self.assertEqual(brief.summary, "recovered")

    def test_unknown_tool_name(self):
        agent, _ = _seeded_agent()

        def script(n, messages):
            if n == 1:
                return {"content": None,
                        "tool_calls": [{"id": "1", "name": "no_such_tool", "arguments": {}}]}
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            self.assertTrue(any("unknown tool" in (m.get("content") or "") for m in tool_msgs))
            return {"content": json.dumps({"summary": "ok", "remediation_steps": ["x"]}),
                    "tool_calls": []}

        agent.reasoner = _Fake(script)
        brief = agent.triage(_incident(), use_memory=True)
        self.assertEqual(brief.summary, "ok")

    def test_iteration_cap(self):
        agent, _ = _seeded_agent()
        # a reasoner that NEVER stops asking for tools must not loop forever
        always_call = _Fake(lambda n, m: {
            "content": None,
            "tool_calls": [{"id": str(n), "name": "recall_memory",
                            "arguments": {"query": "checkout 5xx deploy conn_pool"}}],
        })
        agent.reasoner = always_call
        brief = agent.triage(_incident(), use_memory=True)
        self.assertEqual(always_call.calls, _MAX_ITERS)   # capped, no infinite loop
        self.assertTrue(brief.summary)                    # still degraded to a real Brief
        self.assertGreaterEqual(len(brief.citations), 1)  # grounded in what it did recall

    def test_graceful_degradation(self):
        agent, _ = _seeded_agent()
        agent.reasoner = _Fake([{"content": "this is not valid json at all", "tool_calls": []}])
        recalled = RecallResult(query="q", backend="local", memories=[
            Memory(content=_MEM_A, score=0.88, source="INC-0007"),
        ])
        brief = agent.triage(_incident(), use_memory=True, recalled=recalled)
        self.assertIn("exhausted", brief.root_cause_hypothesis.lower())
        self.assertEqual(brief.citations[0]["id"], "INC-0007")


if __name__ == "__main__":
    unittest.main()
