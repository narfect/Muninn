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
    """Records calls and returns canned OK responses — no network. Inherits the static
    response parsers (extract_memories/item_content/item_score) from the real client."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created_banks = []
        self.retained = []

    def create_bank(self, bank_id, name, mission=""):
        self.created_banks.append(bank_id)
        return {"ok": True, "_status": 201}

    def retain(self, bank_id, content, *, mem_type="experience", metadata=None,
               is_async=False):
        self.retained.append({"content": content, "type": mem_type,
                              "metadata": metadata or {}})
        return {"ok": True, "id": f"h-{len(self.retained)}"}

    def recall(self, bank_id, query, top_k=5):
        return {"ok": True, "memories": [
            {"content": r["content"], "type": r["type"], "score": 0.9, "id": "x",
             "metadata": r["metadata"]} for r in self.retained[:top_k]]}

    def reflect(self, bank_id, query):
        return {"ok": True, "answer": "synthesized"}

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


if __name__ == "__main__":
    unittest.main()
