"""Local AI runs on the CPU on many laptops: a long system prompt takes minutes, so the wait must be long enough."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ai_gateway  # noqa: E402
import model_registry  # noqa: E402


class OllamaTimeoutTests(unittest.TestCase):
    def _timeouts(self, models_cfg):
        seen = []

        def fake_post(url, payload, headers, timeout=None):
            seen.append(timeout)
            return {"message": {"content": "ready"}}

        with patch.object(model_registry, "load_models_config", return_value=models_cfg), \
                patch.object(ai_gateway, "_http_post_json", side_effect=fake_post), \
                patch.object(ai_gateway, "_ollama_reachable", return_value=True):
            ai_gateway._complete_confidential_ollama("http://localhost:11434", "llama3.2",
                                                     [{"role": "user", "content": "hi"}])
            ai_gateway.complete_ollama("http://localhost:11434", "sys", "hi")
        return seen

    def test_the_default_wait_is_long_enough_for_a_cpu_laptop(self):
        self.assertEqual(self._timeouts({"local": {"ollama_model": "llama3.2"}}), [300, 300])

    def test_the_wait_can_be_set_in_models_json(self):
        self.assertEqual(self._timeouts({"local": {"ollama_model": "llama3.2", "timeout_s": 600}}), [600, 600])

    def test_a_bad_setting_falls_back_to_the_default(self):
        for bad in ("soon", -5, 0, True, 100000):
            self.assertEqual(self._timeouts({"local": {"timeout_s": bad}}), [300, 300], bad)


if __name__ == "__main__":
    unittest.main()
