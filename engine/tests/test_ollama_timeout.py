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
        self.assertEqual(self._timeouts({"local": {"ollama_model": "llama3.2"}}), [600, 600])

    def test_the_wait_can_be_set_in_models_json(self):
        self.assertEqual(self._timeouts({"local": {"ollama_model": "llama3.2", "timeout_s": 600}}), [600, 600])

    def test_a_bad_setting_falls_back_to_the_default(self):
        for bad in ("soon", -5, 0, True, 100000):
            self.assertEqual(self._timeouts({"local": {"timeout_s": bad}}), [600, 600], bad)


class OllamaToolCallFormatTests(unittest.TestCase):
    """Ollama wants tool-call arguments as an object; the engine keeps them as a JSON string (OpenAI style)."""

    def _sent_messages(self, messages):
        sent = []

        def fake_post(url, payload, headers, timeout=None):
            sent.append(payload)
            return {"message": {"content": "ok"}}

        with patch.object(ai_gateway, "_http_post_json", side_effect=fake_post):
            ai_gateway._single_ollama_chat_meta("http://localhost:11434", messages, "llama3.2", tools=None, timeout=5)
        return sent[0]["messages"]

    def test_string_arguments_are_sent_as_an_object(self):
        messages = [
            {"role": "user", "content": "review it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_content", "arguments": '{"content_id": "cnt_1"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ]
        out = self._sent_messages(messages)
        self.assertEqual(out[1]["tool_calls"][0]["function"]["arguments"], {"content_id": "cnt_1"})
        # The stored session messages are not changed.
        self.assertIsInstance(messages[1]["tool_calls"][0]["function"]["arguments"], str)

    def test_unreadable_or_empty_arguments_become_an_empty_object(self):
        for raw in ("", "not json", "[1, 2]", None):
            msgs = [{"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "t", "arguments": raw}}]}]
            self.assertEqual(self._sent_messages(msgs)[0]["tool_calls"][0]["function"]["arguments"], {}, raw)


if __name__ == "__main__":
    unittest.main()
