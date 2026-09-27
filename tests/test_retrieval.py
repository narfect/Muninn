"""Retrieval scoring tests (FR-2/3) — implemented alongside backend/memory/retrieval.py."""
import unittest

from backend.memory import retrieval
from backend.memory.retrieval import (
    Bm25,
    WEIGHTS,
    entity_overlap,
    extract_entities,
    fuse,
    semantic_score,
    temporal_decay,
    tokenize,
)
from backend.models import now_ms


class TestRetrieval(unittest.TestCase):
    def test_semantic_cosine_bounds(self):
        t = "checkout-api 5xx spike after deploy http_5xx conn_pool"
        self.assertAlmostEqual(semantic_score(t, t), 1.0, places=3)
        self.assertLess(semantic_score("redis evictions latency", "tls cert expired handshake"), 0.3)

    def test_keyword_score(self):
        corpus = [
            tokenize("redis evictions caused cart latency p99"),
            tokenize("tls certificate expired handshake failure"),
            tokenize("payment gateway timeout circuit breaker open"),
        ]
        bm25 = Bm25(corpus)
        q = tokenize("redis evictions latency")
        # the redis document must outscore the unrelated ones on keyword overlap
        self.assertGreater(bm25.score(q, 0), bm25.score(q, 1))
        self.assertGreater(bm25.score(q, 0), bm25.score(q, 2))

    def test_temporal_decay_monotonic(self):
        day = 86_400_000
        self.assertEqual(temporal_decay(0), 1.0)
        self.assertTrue(0.0 < temporal_decay(30 * day) <= 1.0)
        # newer (smaller age) ranks strictly above older
        self.assertGreater(temporal_decay(1 * day), temporal_decay(120 * day))

    def test_entity_overlap(self):
        q = extract_entities("checkout-api http_5xx conn_pool deploy")
        d = extract_entities("release lowered db pool; http_5xx conn_pool exhausted deploy")
        self.assertGreater(entity_overlap(q, d), 0.5)
        self.assertEqual(entity_overlap(q, extract_entities("kafka consumer lag rebalance")), 0.0)

    def test_fusion_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)
        self.assertEqual(WEIGHTS["semantic"], 0.45)
        self.assertEqual(WEIGHTS["keyword"], 0.30)
        self.assertEqual(WEIGHTS["entity"], 0.15)
        self.assertEqual(WEIGHTS["temporal"], 0.10)
        fused, breakdown = fuse({"semantic": 1.0, "keyword": 1.0, "entity": 1.0, "temporal": 1.0})
        self.assertAlmostEqual(fused, 1.0, places=6)
        self.assertAlmostEqual(sum(breakdown.values()), 1.0, places=6)

    def test_true_family_match_is_top_1(self):
        now = now_ms()
        day = 86_400_000
        docs = [
            {"content": "[INC-0007 checkout-api SEV1] 5xx spike after deploy. "
                        "Signature: http_5xx|deploy|conn_pool. Root cause: pool exhausted.",
             "created_at": now - 60 * day, "source": "INC-0007"},
            {"content": "[INC-0026 payments-svc SEV1] gateway timeouts. "
                        "Signature: payment|gateway_timeout|circuit_open.",
             "created_at": now - 40 * day, "source": "INC-0026"},
            {"content": "[INC-0018 cart-svc SEV2] redis evictions latency. "
                        "Signature: redis|evictions|latency_p99.",
             "created_at": now - 30 * day, "source": "INC-0018"},
        ]
        query = ("checkout-api: 5xx spike after deploy (http_5xx|deploy|conn_pool). "
                 "Checkout 5xx climbing after deploy. tags: deploy, 5xx, connection-pool")
        ranked = retrieval.rank(query, docs, now_ms=now, top_k=3)
        self.assertEqual(docs[ranked[0]["index"]]["source"], "INC-0007")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])


if __name__ == "__main__":
    unittest.main()
