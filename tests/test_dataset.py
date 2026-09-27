"""Dataset-integrity tests over data/seed_sample.json (concrete — green).

Guards the demo data contract from DATASET.md: valid schema, resolved 'memory'
incidents carry a resolution, and at least one 'queue' incident shares a family with a
retained 'memory' incident (so recall has a demonstrable true hit).
"""
import json
import unittest
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "seed_sample.json"
SEVERITIES = {"SEV1", "SEV2", "SEV3"}


class TestDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        cls.services = {s["name"] for s in cls.data["services"]}
        cls.incidents = cls.data["incidents"]

    def test_labeled_synthetic(self):
        self.assertTrue(self.data["meta"].get("synthetic"))
        self.assertIn("note", self.data["meta"])

    def test_catalog_nonempty(self):
        self.assertGreaterEqual(len(self.data["services"]), 8)
        self.assertGreaterEqual(len(self.data["runbooks"]), 10)

    def test_incident_schema(self):
        for i in self.incidents:
            self.assertIn(i["service"], self.services, i["external_id"])
            self.assertIn(i["severity"], SEVERITIES, i["external_id"])
            self.assertTrue(i["error_signature"])

    def test_memory_incidents_have_resolution(self):
        mem = [i for i in self.incidents if i["seed_role"] == "memory"]
        self.assertTrue(mem)
        for i in mem:
            self.assertTrue(i["root_cause"], i["external_id"])
            self.assertTrue(i["remediation_steps"], i["external_id"])
            self.assertIsNotNone(i["mttr_minutes"], i["external_id"])

    def test_queue_incident_matches_a_retained_family(self):
        mem_families = {i["family"] for i in self.incidents if i["seed_role"] == "memory"}
        matches = [i["external_id"] for i in self.incidents
                   if i["seed_role"] == "queue" and i["family"] in mem_families]
        self.assertTrue(matches, "no queue incident recalls a retained family")

    def test_recurring_family_has_downward_mttr(self):
        # Family A should show the 'we've seen this' effect: later MTTR < earlier.
        fam_a = sorted([i for i in self.incidents
                        if i["family"] == "A" and i["seed_role"] == "memory"],
                       key=lambda x: -x["created_days_ago"])  # oldest first
        if len(fam_a) >= 2:
            self.assertLess(fam_a[-1]["mttr_minutes"], fam_a[0]["mttr_minutes"])


if __name__ == "__main__":
    unittest.main()
