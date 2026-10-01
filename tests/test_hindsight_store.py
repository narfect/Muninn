"""Phase 1 regression tests — Hindsight parity S5, S6, S7, S8.

Fully offline: the network client is replaced with a fake so no real HTTP happens, and
the catch-all is exercised by patching urlopen to raise. These verify the HindsightStore
now behaves like the LocalMemoryStore for bank setup/reset and never hard-fails.
"""
import dataclasses
import unittest
from unittest.mock import patch

from backend.config import settings
from backend.memory.hindsight_client import HindsightClient
from backend.memory.hindsight_store import HindsightStore


class _FakeHindsightClient(HindsightClient):
    """Records calls and returns canned OK responses in the REAL Hindsight response shapes
    (recall -> ``results`` with ``text``/``id``/``scores.final``; reflect -> ``text``) — no
    network. Inherits the static parsers (extract_memories/item_content/item_score/
    item_source) from the real client so this exercises them against realistic payloads."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created_banks = []
        self.retained = []

    def create_bank(self, bank_id, name="", mission="", disposition=None):
        self.created_banks.append(bank_id)
        return {"ok": True, "_status": 200}

    def retain(self, bank_id, content, *, mem_type="experience", metadata=None,
               is_async=False, document_id=""):
        self.retained.append({"content": content, "type": mem_type,
                              "metadata": metadata or {}, "document_id": document_id})
        # Mirror the synchronous retain response (no per-item id is returned).
        return {"ok": True, "success": True, "items_count": 1}

    def recall(self, bank_id, query, top_k=5):
        return {"ok": True, "results": [
            {"text": r["content"], "id": r.get("document_id") or "x",
             "scores": {"final": 0.9}, "metadata": r["metadata"]}
            for r in self.retained[:top_k]]}

    def reflect(self, bank_id, query):
        return {"ok": True, "text": "synthesized"}

    def health(self):
        return {"ok": True, "_status": 200}


def _store():
    st = dataclasses.replace(settings, hindsight_base_url="http://fake.invalid",
                             hindsight_api_key="k", hindsight_bank="hs-test",
                             memory_backend="hindsight")
    return HindsightStore(st)


class TestS5EnsureBankOnInit(unittest.TestCase):
    def test_bank_created_during_init(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
        self.assertTrue(store._bank_ready)
        self.assertIn("hs-test", store.client.created_banks)


class TestS6ClientNeverRaises(unittest.TestCase):
    def test_unexpected_error_degrades_to_safe_dict(self):
        client = HindsightClient("http://fake.invalid", api_key="k")
        with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
            resp = client._request("GET", "http://fake.invalid/v1/default/x")
        self.assertFalse(resp.get("ok"))
        self.assertEqual(resp.get("error"), "unexpected")

    def test_store_recall_degrades_when_backend_down(self):
        # A store whose client always reports failure returns an empty result, not a raise.
        class _DownClient(HindsightClient):
            def create_bank(self, *a, **k):
                return {"ok": False, "error": "unreachable"}

            def recall(self, *a, **k):
                return {"ok": False, "error": "unreachable"}

        with patch("backend.memory.hindsight_store.HindsightClient", _DownClient):
            store = _store()
        rc = store.recall("anything")
        self.assertEqual(rc.memories, [])
        self.assertEqual(rc.backend, "hindsight")


class TestS7Reset(unittest.TestCase):
    def test_reset_clears_count_and_reensures_bank(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
            store.retain("mem 1", source="INC-1")
            store.retain("mem 2", source="INC-2")
            self.assertEqual(store.count(), 2)
            n_banks_before = len(store.client.created_banks)
            store.reset()
        self.assertEqual(store.count(), 0)
        self.assertTrue(store._bank_ready)
        self.assertEqual(len(store.client.created_banks), n_banks_before + 1)

    def test_reset_is_callable_by_seeder_guard(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
        self.assertTrue(hasattr(store, "reset"))
        store.reset()  # must not raise


class TestS8CountIsSessionLocal(unittest.TestCase):
    def test_count_tracks_in_process_retains(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
            self.assertEqual(store.count(), 0)
            store.retain("a", source="INC-1")
            self.assertEqual(store.count(), 1)
            # a fresh store (new "process") starts at 0 — documented session-local behavior
            fresh = _store()
        self.assertEqual(fresh.count(), 0)


class TestRecallMappingRealShape(unittest.TestCase):
    def test_results_text_score_and_source_mapped(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
            store.retain("[INCIDENT INC-1] checkout 5xx after deploy", source="INC-1",
                         metadata={"service": "checkout", "mttr_minutes": 22})
            store.retain("[INCIDENT INC-2] redis evictions", source="INC-2")
            rc = store.recall("checkout 5xx", top_k=5)
        self.assertEqual(len(rc.memories), 2)
        m = rc.memories[0]
        self.assertIn("checkout 5xx", m.content)        # read from results[].text
        self.assertEqual(m.source, "INC-1")             # from metadata.source / document_id
        # scores.final (0.9) is normalized onto 0..1 against the configured scale so warm
        # confidence grades and citation % never exceeds 100; the raw value is kept in metadata.
        expected = round(0.9 / store.settings.hindsight_score_scale, 4)
        self.assertEqual(m.score, expected)
        self.assertEqual(m.metadata.get("raw_score"), 0.9)
        self.assertEqual(m.metadata.get("score_source"), "backend")

    def test_top_k_is_applied_client_side(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
            for i in range(5):
                store.retain(f"mem {i}", source=f"INC-{i}")
            rc = store.recall("q", top_k=2)
        self.assertEqual(len(rc.memories), 2)


class TestRecallUnscoredRankHeuristic(unittest.TestCase):
    """When the recall response carries no numeric score (the documented shape), hits are
    still genuinely ranked — the store assigns a transparent descending rank value and
    flags it so nothing masquerades as a backend score."""

    def test_unscored_hits_get_rank_heuristic(self):
        class _UnscoredClient(_FakeHindsightClient):
            def recall(self, bank_id, query, top_k=5):
                return {"ok": True, "results": [
                    {"text": r["content"], "id": r.get("document_id") or "x",
                     "metadata": r["metadata"]} for r in self.retained[:top_k]]}

        with patch("backend.memory.hindsight_store.HindsightClient", _UnscoredClient):
            store = _store()
            store.retain("a", source="INC-1")
            store.retain("b", source="INC-2")
            rc = store.recall("q", top_k=5)
        self.assertEqual(len(rc.memories), 2)
        self.assertTrue(all(m.metadata.get("score_source") == "rank_heuristic"
                            for m in rc.memories))
        # first hit ranks highest and clears the metrics hit threshold (0.35)
        self.assertGreaterEqual(rc.memories[0].score, 0.35)
        self.assertGreaterEqual(rc.memories[0].score, rc.memories[1].score)


class TestReflectReadsTextField(unittest.TestCase):
    def test_reflect_returns_text(self):
        with patch("backend.memory.hindsight_store.HindsightClient", _FakeHindsightClient):
            store = _store()
        self.assertEqual(store.reflect("why?"), "synthesized")


class TestClientWireContract(unittest.TestCase):
    """The real client builds documented request paths/bodies and degrades on 422."""

    def _capture(self, status=200, payload=None):
        calls = []

        def fake_request(method, url, body=None):
            calls.append({"method": method, "url": url, "body": body})
            resp = dict(payload or {"ok": True})
            resp["_status"] = status
            return resp

        return calls, fake_request

    def test_recall_hits_memories_recall_path_with_query_only(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture()
        c.recall("bank1", "my query", top_k=3)
        self.assertEqual(calls[-1]["url"], "http://h/v1/default/banks/bank1/memories/recall")
        self.assertEqual(calls[-1]["body"], {"query": "my query"})  # no top_k on the wire

    def test_health_hits_root_health(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture()
        c.health()
        self.assertEqual(calls[-1]["url"], "http://h/health")

    def test_create_bank_is_single_put_to_bank_id_with_mission_and_disposition(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture()
        c.create_bank("bank1", name="Display name", mission="be an SRE analyst",
                      disposition={"skepticism": 4, "literalism": 4, "empathy": 2})
        self.assertEqual(len(calls), 1)                       # ONE call, no separate /profile
        self.assertEqual(calls[-1]["method"], "PUT")
        self.assertEqual(calls[-1]["url"], "http://h/v1/default/banks/bank1")
        body = calls[-1]["body"]
        self.assertEqual(body["reflect_mission"], "be an SRE analyst")
        self.assertEqual(body["disposition_skepticism"], 4)
        self.assertEqual(body["disposition_literalism"], 4)
        self.assertEqual(body["disposition_empathy"], 2)
        self.assertNotIn("name", body)                        # name is not a documented field
        self.assertNotIn("bank_id", body)                     # bank_id lives in the path

    def test_create_bank_omits_unset_fields_for_default_autofill(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture()
        c.create_bank("bank1")                                # no mission, no disposition
        self.assertEqual(calls[-1]["url"], "http://h/v1/default/banks/bank1")
        self.assertEqual(calls[-1]["body"], {})               # omitted -> server auto-fills defaults

    def test_retain_coerces_metadata_and_sets_document_id_without_type(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture()
        c.retain("bank1", "doc text", mem_type="experience", document_id="INC-7",
                 metadata={"service": "checkout", "mttr_minutes": 22, "created_at": 123})
        item = calls[-1]["body"]["items"][0]
        self.assertEqual(calls[-1]["url"], "http://h/v1/default/banks/bank1/memories")
        self.assertEqual(item["content"], "doc text")
        self.assertEqual(item["document_id"], "INC-7")
        self.assertNotIn("type", item)                       # fact type is inferred, not sent
        self.assertEqual(item["metadata"]["mttr_minutes"], "22")   # coerced to string
        self.assertEqual(item["metadata"]["created_at"], "123")
        self.assertEqual(item["metadata"]["muninn_type"], "experience")

    def test_retain_retries_without_optional_fields_on_422(self):
        c = HindsightClient("http://h", api_key="k")
        calls, c._request = self._capture(status=422)
        c.retain("bank1", "doc text", document_id="INC-7",
                 metadata={"service": "checkout"})
        self.assertEqual(len(calls), 2)                      # initial + retry
        retry_item = calls[-1]["body"]["items"][0]
        self.assertNotIn("metadata", retry_item)             # retry drops optional fields
        self.assertNotIn("context", retry_item)
        self.assertEqual(retry_item, {"content": "doc text", "document_id": "INC-7"})


if __name__ == "__main__":
    unittest.main()
