"""Guarded LIVE smoke test for the real Hindsight service (Tier 0, Step 6).

Runs end-to-end against a real Hindsight bank ONLY when a key is configured AND the host
is actually reachable; otherwise it skips cleanly so ``make test`` stays green offline
(no key in CI) and in a sandbox where the key is present but egress to the Hindsight host
is blocked. It creates a throwaway bank, retains a few memories, recalls (asserting
non-empty, scored hits), and reflects (asserting non-empty prose) — proving the live loop
without touching the demo bank. No secret is printed.
"""
import dataclasses
import os
import time
import unittest

from backend.config import settings
from backend.memory.hindsight_client import HindsightClient
from backend.memory.hindsight_store import HindsightStore

_HAS_KEY = bool(os.getenv("HINDSIGHT_API_KEY") and os.getenv("HINDSIGHT_BASE_URL"))

_MEMORIES = [
    ("[INCIDENT SMOKE-1 · checkout-api · SEV1] 5xx spike after deploy; DB connection pool "
     "exhausted. Fix: rolled back release and raised pgbouncer pool.", "SMOKE-1"),
    ("[INCIDENT SMOKE-2 · payments-api · SEV2] latency from Redis evictions under load. "
     "Fix: raised maxmemory and tuned the eviction policy.", "SMOKE-2"),
    ("[INCIDENT SMOKE-3 · checkout-api · SEV1] 5xx errors after a bad deploy; connection "
     "pool starvation again. Fix: canary gate + pool autoscale.", "SMOKE-3"),
]


@unittest.skipUnless(_HAS_KEY, "set HINDSIGHT_BASE_URL + HINDSIGHT_API_KEY to run the live "
                               "Hindsight smoke test")
class TestHindsightLiveSmoke(unittest.TestCase):
    def setUp(self):
        probe = HindsightClient(settings.hindsight_base_url,
                                api_key=settings.hindsight_api_key,
                                namespace=settings.hindsight_namespace, timeout=8.0)
        if not probe.health().get("ok"):
            self.skipTest("Hindsight host not reachable from this environment")
        bank = f"muninn-smoke-{int(time.time())}"
        st = dataclasses.replace(settings, hindsight_bank=bank, memory_backend="hindsight")
        self.store = HindsightStore(st)

    def test_retain_recall_reflect_roundtrip(self):
        for content, src in _MEMORIES:
            self.store.retain(content, mem_type="experience", source=src,
                              metadata={"service": "checkout-api", "severity": "SEV1"})
        # retain may process asynchronously — poll recall until the bank has extracted facts.
        deadline = time.time() + 90
        rc = None
        while time.time() < deadline:
            rc = self.store.recall("checkout 5xx after deploy connection pool", top_k=5)
            if rc.memories:
                break
            time.sleep(5)
        self.assertTrue(rc and rc.memories, "live recall returned no memories")
        self.assertTrue(all(m.score > 0 for m in rc.memories))
        self.assertTrue(any(m.content.strip() for m in rc.memories))

        reflection = self.store.reflect(
            "What recurring failure pattern does a checkout 5xx-after-deploy alert match, "
            "and what fix has worked before?")
        self.assertTrue(reflection.strip())
        self.assertNotIn("unavailable", reflection.lower())


if __name__ == "__main__":
    unittest.main()
