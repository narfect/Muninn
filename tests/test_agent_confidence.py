import unittest

from backend.llm.agent import TriageAgent
from backend.models import Incident, Memory, RecallResult


class _FixedReasoner:
    """Simulates a live model that ignores grounding and always claims the same confidence."""
    backend_name = "groq"

    def chat(self, messages, tools=None):
        return {"content": '{"summary":"s","root_cause_hypothesis":"r","confidence":0.8,'
                           '"remediation_steps":["step"],"runbook_ref":"RB-1","citations":[]}',
                "tool_calls": []}


def _incident():
    return Incident(external_id="INC-1", title="t", service="svc", severity="SEV2",
                    symptom="500s", error_signature="HTTP 500")


def _recall():
    return RecallResult(query="HTTP 500", memories=[
        Memory(content="Root cause: bad deploy\nFix: rollback",
               score=0.72, source="INC-OLD", type="experience"),
    ])


class ConfidenceGroundingTest(unittest.TestCase):
    def test_cold_is_low_and_warm_beats_it(self):
        agent = TriageAgent(_FixedReasoner(), memory=None, repo=None)
        cold = agent.triage(_incident(), use_memory=False)
        warm = agent.triage(_incident(), use_memory=True, recalled=_recall())
        self.assertLessEqual(cold.confidence, 0.2)
        self.assertGreaterEqual(warm.confidence, 0.4)
        self.assertGreater(warm.confidence, cold.confidence)


if __name__ == "__main__":
    unittest.main()
