"""LocalMemoryStore round-trip tests (FR-5) — offline, no Hindsight/network."""
import dataclasses
import os
import tempfile
import unittest

from backend.config import settings
from backend.memory.local_store import LocalMemoryStore


def _store():
    d = tempfile.mkdtemp()
    st = dataclasses.replace(settings, db_path=os.path.join(d, "t.db"),
                             hindsight_bank="test-bank")
    return LocalMemoryStore(st), st


class TestLocalMemoryStore(unittest.TestCase):
    def test_retain_increments_count(self):
        store, _ = _store()
        self.assertEqual(store.count(), 0)
        store.retain("checkout 5xx after deploy; conn_pool exhausted", source="INC-0007")
        self.assertEqual(store.count(), 1)

    def test_recall_returns_retained(self):
        store, _ = _store()
        store.retain("[INC-0007 checkout-api] 5xx spike after deploy; http_5xx conn_pool.",
                     source="INC-0007", metadata={"family": "A"})
        rc = store.recall("checkout 5xx after deploy conn_pool", top_k=5)
        self.assertTrue(rc.memories)
        self.assertEqual(rc.memories[0].source, "INC-0007")
        self.assertEqual(rc.backend, "local")
        self.assertGreater(rc.memories[0].score, 0.0)

    def test_recall_respects_top_k_and_ordering(self):
        store, _ = _store()
        store.retain("redis evictions latency cart p99", source="A")
        store.retain("payment gateway timeout circuit breaker", source="B")
        store.retain("tls certificate expired handshake", source="C")
        rc = store.recall("redis evictions latency", top_k=2)
        self.assertLessEqual(len(rc.memories), 2)
        scores = [m.score for m in rc.memories]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(rc.memories[0].source, "A")

    def test_persistence_roundtrip(self):
        store, st = _store()
        store.retain("kafka consumer lag rebalance storm", source="INC-9")
        # a fresh store over the same path must see the retained memory
        reopened = LocalMemoryStore(st)
        self.assertEqual(reopened.count(), 1)
        rc = reopened.recall("kafka consumer lag", top_k=1)
        self.assertEqual(rc.memories[0].source, "INC-9")

    def test_reflect_synthesizes_from_memory(self):
        store, _ = _store()
        store.retain("[INC-0007 checkout-api SEV1]\nRoot cause: DB pool exhausted.\n"
                     "Fix: rolled back release; raised pool to 200.", source="INC-0007",
                     metadata={"family": "A"})
        text = store.reflect("checkout 5xx after deploy")
        self.assertIn("INC-0007", text)
        self.assertIn("local", text.lower())

    def test_health(self):
        store, _ = _store()
        h = store.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["backend"], "local")


if __name__ == "__main__":
    unittest.main()
