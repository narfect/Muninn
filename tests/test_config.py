"""Config resolution tests (concrete layer — green)."""
import dataclasses
import unittest

from backend.config import Settings, settings


class TestConfig(unittest.TestCase):
    def test_memory_backend_forced(self):
        s = dataclasses.replace(settings, memory_backend="local")
        self.assertEqual(s.resolved_memory_backend(), "local")
        s = dataclasses.replace(settings, memory_backend="hindsight")
        self.assertEqual(s.resolved_memory_backend(), "hindsight")

    def test_memory_backend_auto_needs_url_and_key(self):
        auto_no_creds = dataclasses.replace(
            settings, memory_backend="auto", hindsight_base_url="", hindsight_api_key=""
        )
        self.assertEqual(auto_no_creds.resolved_memory_backend(), "local")
        auto_creds = dataclasses.replace(
            settings, memory_backend="auto",
            hindsight_base_url="https://api.hindsight.vectorize.io", hindsight_api_key="k",
        )
        self.assertEqual(auto_creds.resolved_memory_backend(), "hindsight")

    def test_llm_backend_auto_needs_key(self):
        no_key = dataclasses.replace(settings, llm_backend="auto", groq_api_key="")
        self.assertEqual(no_key.resolved_llm_backend(), "local")
        with_key = dataclasses.replace(settings, llm_backend="auto", groq_api_key="k")
        self.assertEqual(with_key.resolved_llm_backend(), "groq")

    def test_defaults_present(self):
        self.assertEqual(settings.hindsight_namespace, "default")
        self.assertEqual(settings.hindsight_bank, "muninn-incidents")
        self.assertGreaterEqual(settings.recall_top_k, 1)
        self.assertTrue(settings.groq_base_url.startswith("http"))


if __name__ == "__main__":
    unittest.main()
