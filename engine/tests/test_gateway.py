"""Unit tests for ai_gateway, model_registry, and graceful caller error handling."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_chat
import ai_gateway
import model_registry
import team_room


class _FakeHTTPResponse:
    """Minimal context-manager mock for urllib.request.urlopen responses."""

    def __init__(self, body_dict, status=200):
        self._raw = json.dumps(body_dict).encode("utf-8")
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_http_error(url, code, body_str="", headers=None):
    return urllib.error.HTTPError(
        url=url,
        code=code,
        msg=f"HTTP {code}",
        hdrs=headers or {},
        fp=io.BytesIO(body_str.encode("utf-8")),
    )


def _blank_env():
    return {}


class GatewayTests(unittest.TestCase):
    """Tests for model_registry, ai_gateway, and error handling across callers."""

    def setUp(self):
        self.env_patcher = patch.object(ai_gateway, "load_env", side_effect=_blank_env)
        self.env_patcher.start()
        self.os_env_patcher = patch.dict(os.environ, {}, clear=True)
        self.os_env_patcher.start()

    def tearDown(self):
        self.os_env_patcher.stop()
        self.env_patcher.stop()

    def test_01_no_keys_and_ollama_unreachable_raises_ai_not_connected(self):
        """No keys and unreachable Ollama raises AINotConnected with no canned fallback."""
        with patch.object(ai_gateway, "_ollama_reachable", return_value=False), patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")
        ):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="You are CMO.",
                    user_prompt="Status check",
                    agent_role="cmo",
                )
        msg = str(ctx.exception)
        self.assertIn("AI is not connected", msg)
        self.assertNotIn("OFFLINE FALLBACK", msg)

    def test_02_429_twice_then_200_honours_retry_after_and_sleeps_twice(self):
        """HTTP 429 twice then 200 succeeds on primary model and records 2 sleeps."""
        sleeps = []
        calls = []
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            calls.append(json.loads(req.data.decode("utf-8"))["model"])
            if len(calls) == 1:
                raise _make_http_error(url, 429, "rate limited", headers={"Retry-After": "7"})
            if len(calls) == 2:
                raise _make_http_error(url, 429, "rate limited", headers={"Retry-After": "60"})
            return _FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch.object(
            ai_gateway, "_sleep", side_effect=sleeps.append
        ):
            res = ai_gateway.complete_nvidia_nim(
                api_key="test-key",
                base_url="https://integrate.api.nvidia.com/v1",
                system_prompt="sys",
                user_prompt="hi",
                model="deepseek-ai/deepseek-v4.1-flash",
                fallback_model="nvidia/nemotron-3-ultra-550b-a55b",
            )

        self.assertEqual(res["text"], "ok")
        self.assertEqual(res["model_used"], "deepseek-ai/deepseek-v4.1-flash")
        self.assertFalse(res["fallback_used"])
        self.assertEqual(sleeps, [7.0, 20.0])

    def test_03_429_three_times_falls_back_to_backup(self):
        """HTTP 429 three times exhausts retries on primary and succeeds on backup."""
        sleeps = []
        models_called = []
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            model = json.loads(req.data.decode("utf-8"))["model"]
            models_called.append(model)
            if len(models_called) <= 3:
                raise _make_http_error(url, 429, "rate limited")
            return _FakeHTTPResponse({"choices": [{"message": {"content": "backup ok"}}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch.object(
            ai_gateway, "_sleep", side_effect=sleeps.append
        ):
            res = ai_gateway.complete_nvidia_nim(
                api_key="test-key",
                base_url="https://integrate.api.nvidia.com/v1",
                system_prompt="sys",
                user_prompt="hi",
                model="deepseek-ai/deepseek-v4.1-flash",
                fallback_model="nvidia/nemotron-3-ultra-550b-a55b",
            )

        self.assertEqual(sleeps, [2.0, 4.0])
        self.assertTrue(res["fallback_used"])
        self.assertEqual(res["model_requested"], "deepseek-ai/deepseek-v4.1-flash")
        self.assertEqual(res["model_used"], "nvidia/nemotron-3-ultra-550b-a55b")
        self.assertEqual(
            models_called,
            [
                "deepseek-ai/deepseek-v4.1-flash",
                "deepseek-ai/deepseek-v4.1-flash",
                "deepseek-ai/deepseek-v4.1-flash",
                "nvidia/nemotron-3-ultra-550b-a55b",
            ],
        )

    def test_04_404_on_primary_tries_backup_with_catalog_reason(self):
        """HTTP 404 on primary falls back immediately with catalog removal reason."""
        models_called = []
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            model = json.loads(req.data.decode("utf-8"))["model"]
            models_called.append(model)
            if model == "missing/model":
                raise _make_http_error(url, 404, "not found")
            return _FakeHTTPResponse({"choices": [{"message": {"content": "from backup"}}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = ai_gateway.complete_nvidia_nim(
                api_key="test-key",
                base_url="https://integrate.api.nvidia.com/v1",
                system_prompt="sys",
                user_prompt="hi",
                model="missing/model",
                fallback_model="nvidia/nemotron-3-ultra-550b-a55b",
            )

        self.assertTrue(res["fallback_used"])
        self.assertEqual(res["model_used"], "nvidia/nemotron-3-ultra-550b-a55b")
        self.assertIn("catalog", res["fallback_reason"].lower())
        self.assertEqual(models_called, ["missing/model", "nvidia/nemotron-3-ultra-550b-a55b"])

    def test_05_401_raises_ai_auth_error_without_trying_backup(self):
        """HTTP 401 raises AIAuthError immediately and never calls backup model."""
        models_called = []
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            models_called.append(json.loads(req.data.decode("utf-8"))["model"])
            raise _make_http_error(url, 401, "unauthorized")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(ai_gateway.AIAuthError):
                ai_gateway.complete_nvidia_nim(
                    api_key="bad-key",
                    base_url="https://integrate.api.nvidia.com/v1",
                    system_prompt="sys",
                    user_prompt="hi",
                    model="deepseek-ai/deepseek-v4.1-flash",
                    fallback_model="nvidia/nemotron-3-ultra-550b-a55b",
                )

        self.assertEqual(models_called, ["deepseek-ai/deepseek-v4.1-flash"])

    def test_06_confidential_true_with_ollama_reachable_never_calls_nim(self):
        """confidential=True uses Ollama only and never calls NIM or cloud URLs."""
        seen_urls = []

        def fake_urlopen(req, timeout=20):
            seen_urls.append(req.full_url)
            return _FakeHTTPResponse({"message": {"content": "local confidential reply"}})

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "secret-nim-key", "OPENAI_API_KEY": "secret-oai"}), patch.object(
            ai_gateway, "_ollama_reachable", return_value=True
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = ai_gateway.generate_completion_with_meta(
                system_prompt="sys",
                user_prompt="confidential prompt",
                agent_role="cmo",
                confidential=True,
            )

        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["text"], "local confidential reply")
        self.assertFalse(any("nvidia.com" in u for u in seen_urls))
        self.assertTrue(all("11434" in u for u in seen_urls))

    def test_07_confidential_true_with_ollama_unreachable_raises_ai_not_connected(self):
        """confidential=True with unreachable Ollama raises AINotConnected and never calls NIM."""
        seen_urls = []

        def fake_urlopen(req, timeout=20):
            seen_urls.append(req.full_url)
            raise urllib.error.URLError("connection refused")

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "secret-nim-key"}), patch.object(
            ai_gateway, "_ollama_reachable", return_value=False
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="confidential prompt",
                    agent_role="cmo",
                    confidential=True,
                )

        self.assertIn("confidential", str(ctx.exception).lower())
        self.assertFalse(any("nvidia.com" in u for u in seen_urls))

    def test_08_registry_resolve_role_models_override_and_catalog(self):
        """Env override is ignored when absent from catalog, used when in catalog or catalog is None."""
        cfg = model_registry.load_models_config()
        tier_primary = cfg["tiers"]["reasoning"]["primary"]
        tier_backup = cfg["tiers"]["reasoning"]["backup"]

        with patch.dict(os.environ, {"NIM_MODEL_CMO": "deepseek-ai/deepseek-r1"}):
            # Case A: override not in catalog -> ignored with warning
            catalog_without = {tier_primary, tier_backup}
            p, b, warns = model_registry.resolve_role_models("cmo", cfg, catalog_without)
            self.assertEqual(p, tier_primary)
            self.assertEqual(b, tier_backup)
            self.assertEqual(len(warns), 1)
            self.assertIn("NIM_MODEL_CMO=deepseek-ai/deepseek-r1", warns[0])

            # Case B: override in catalog -> used
            catalog_with = {tier_primary, tier_backup, "deepseek-ai/deepseek-r1"}
            p2, b2, warns2 = model_registry.resolve_role_models("cmo", cfg, catalog_with)
            self.assertEqual(p2, "deepseek-ai/deepseek-r1")
            self.assertEqual(b2, tier_backup)
            self.assertEqual(warns2, [])

            # Case C: catalog None -> override used
            p3, b3, warns3 = model_registry.resolve_role_models("cmo", cfg, None)
            self.assertEqual(p3, "deepseek-ai/deepseek-r1")
            self.assertEqual(b3, tier_backup)
            self.assertEqual(warns3, [])

    def test_09_catalog_cache_fresh_stale_and_http_error(self):
        """Fresh cache avoids HTTP; stale cache refetches; HTTP error falls back to existing cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "model_catalog.json")
            fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": fresh_ts, "models": ["cached/model-a"]}, f)

            # Fresh cache -> reused without HTTP
            with patch("urllib.request.urlopen", side_effect=AssertionError("should not hit network")):
                res = model_registry.get_catalog("key", "https://integrate.api.nvidia.com/v1", cache_path=cache_file)
            self.assertEqual(res, {"cached/model-a"})

            # Stale cache -> refetches
            stale_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": stale_ts, "models": ["cached/old"]}, f)

            with patch(
                "urllib.request.urlopen",
                return_value=_FakeHTTPResponse({"data": [{"id": "fresh/model-b"}]}),
            ):
                res2 = model_registry.get_catalog("key", "https://integrate.api.nvidia.com/v1", cache_path=cache_file)
            self.assertEqual(res2, {"fresh/model-b"})

            # Stale cache + HTTP error -> returns existing cached set
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": stale_ts, "models": ["cached/fallback"]}, f)

            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
                res3 = model_registry.get_catalog("key", "https://integrate.api.nvidia.com/v1", cache_path=cache_file)
            self.assertEqual(res3, {"cached/fallback"})

    def test_10_is_vision_model(self):
        """is_vision_model returns True for vision_models and False for text models."""
        self.assertTrue(model_registry.is_vision_model("nvidia/nemotron-3.5-super-vl-120b-a12b"))
        self.assertTrue(model_registry.is_vision_model("meta/llama-3.2-90b-vision-instruct"))
        self.assertFalse(model_registry.is_vision_model("deepseek-ai/deepseek-v4.1-flash"))
        self.assertFalse(model_registry.is_vision_model("z-ai/glm-5.3-flash"))

    def test_11_callers_handle_ai_errors_gracefully(self):
        """agent_chat and team_room handle AINotConnected and AIAuthError without fake output or cascades."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chats_dir = os.path.join(tmpdir, "chats")
            os.makedirs(chats_dir, exist_ok=True)
            msgs_file = os.path.join(tmpdir, "global_messages.json")

            with patch.object(agent_chat, "CHATS_DIR", chats_dir), patch.object(
                ai_gateway, "_ollama_reachable", return_value=False
            ), patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
                res = agent_chat.chat_with_agent("cmo", "Plan today's launch")
            self.assertFalse(res["success"])
            self.assertEqual(res["error_code"], "ai_not_connected")
            self.assertEqual(agent_chat.get_agent_chat_history("cmo"), [])

            with patch.object(team_room, "MESSAGES_FILE", msgs_file), patch(
                "ai_gateway.generate_completion_with_meta",
                side_effect=ai_gateway.AIAuthError("bad key"),
            ):
                replies = team_room.post_message("CEO", "Check @writer and @cmo")
            self.assertEqual(len(replies), 2)
            self.assertEqual(replies[1]["sender"], "system")
            self.assertEqual(replies[1]["text"], "bad key")

    def test_12_nim_503_on_primary_and_backup_reports_nim_error(self):
        """NIM key set with 503 on primary and backup raises accurate AINotConnected."""
        models_called = []
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            models_called.append(json.loads(req.data.decode("utf-8"))["model"])
            raise _make_http_error(url, 503, "service unavailable")

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-secret-key"}), patch.object(
            model_registry, "get_catalog", return_value=None
        ), patch.object(ai_gateway, "_ollama_reachable", return_value=False), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="hi",
                    agent_role="cmo",
                )

        msg = str(ctx.exception)
        self.assertIn("couldn't answer", msg)
        self.assertIn("503", msg)
        self.assertNotIn("add an NVIDIA NIM key", msg)
        self.assertNotIn("nvapi-secret-key", msg)
        self.assertEqual(len(models_called), 2)

    def test_13_confidential_ollama_reachable_404_suggests_pull(self):
        """confidential=True with reachable Ollama returning 404 tells user to ollama pull."""
        seen_urls = []

        def fake_urlopen(req, timeout=20):
            seen_urls.append(req.full_url)
            raise _make_http_error(req.full_url, 404, '{"error":"model not found"}')

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-secret-key"}), patch.object(
            ai_gateway, "_ollama_reachable", return_value=True
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="confidential prompt",
                    agent_role="cmo",
                    confidential=True,
                )

        msg = str(ctx.exception)
        self.assertIn("ollama pull", msg)
        self.assertFalse(any("nvidia.com" in u for u in seen_urls))

    def test_14_confidential_ollama_unreachable_never_calls_urlopen(self):
        """confidential=True with unreachable Ollama never calls urlopen."""
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-secret-key"}), patch.object(
            ai_gateway, "_ollama_reachable", return_value=False
        ), patch("urllib.request.urlopen", side_effect=AssertionError("urlopen should not be called")):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="confidential prompt",
                    agent_role="cmo",
                    confidential=True,
                )

        self.assertIn("not running", str(ctx.exception).lower())

    def test_15_resolve_role_models_missing_primary_returns_backup_none(self):
        """When tier primary is missing from catalog, backup is returned as primary with backup=None."""
        cfg = model_registry.load_models_config()
        tier_backup = cfg["tiers"]["reasoning"]["backup"]
        catalog = {tier_backup}

        primary, backup, warnings = model_registry.resolve_role_models("cmo", cfg, catalog)
        self.assertEqual(primary, tier_backup)
        self.assertIsNone(backup)
        self.assertEqual(len(warnings), 1)

    def test_16_secondary_failures_budget_and_ollama_reachable_helper(self):
        """Secondary provider failure reasons stay under 300 chars without key leaks, and _ollama_reachable parses URLs."""
        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        def fake_urlopen(req, timeout=45):
            if "nvidia.com" in req.full_url:
                raise _make_http_error(url, 429, "rate limited")
            if "openai.com" in req.full_url:
                raise _make_http_error(req.full_url, 502, "bad gateway")
            if "generativelanguage.googleapis.com" in req.full_url:
                raise _make_http_error(req.full_url, 403, "forbidden")
            raise urllib.error.URLError("timed out")

        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY": "nvapi-secret-nim",
                "OPENAI_API_KEY": "sk-secret-openai",
                "GEMINI_API_KEY": "AIza-secret-gemini",
            },
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway, "_sleep"
        ), patch.object(
            ai_gateway, "_ollama_reachable", return_value=False
        ), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="hi",
                    agent_role="cmo",
                )

        msg = str(ctx.exception)
        self.assertLessEqual(len(msg), 300)
        self.assertIn("429", msg)
        self.assertIn("openai: HTTP 502", msg)
        self.assertIn("gemini: HTTP 403", msg)
        self.assertNotIn("nvapi-secret-nim", msg)
        self.assertNotIn("sk-secret-openai", msg)
        self.assertNotIn("AIza-secret-gemini", msg)

        with patch.object(ai_gateway, "_is_port_open", return_value=True) as mock_port:
            self.assertTrue(ai_gateway._ollama_reachable("http://127.0.0.1:11435"))
            mock_port.assert_called_with("127.0.0.1", 11435, timeout=0.5)
            self.assertTrue(ai_gateway._ollama_reachable("http://ollama.local"))
            mock_port.assert_called_with("ollama.local", 11434, timeout=0.5)
            self.assertFalse(ai_gateway._ollama_reachable(""))

    def test_17_tier_timeout_and_max_tokens_reach_http_call(self):
        """Tier timeout_s and max_tokens reach _http_post_json, and explicit max_tokens overrides tier."""
        seen = []

        def fake_post(url, payload, headers, timeout=45):
            seen.append({"payload": payload, "timeout": timeout})
            return {"choices": [{"message": {"content": "ready"}}]}

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-test"}), patch.object(
            model_registry, "get_catalog", return_value=None
        ), patch.object(ai_gateway, "_http_post_json", side_effect=fake_post):
            res = ai_gateway.generate_completion_with_meta(
                system_prompt="sys",
                user_prompt="hi",
                agent_role="researcher",
            )
            self.assertEqual(res["text"], "ready")
            self.assertEqual(seen[-1]["timeout"], 240)
            self.assertEqual(seen[-1]["payload"]["max_tokens"], 8000)

            ai_gateway.generate_completion_with_meta(
                system_prompt="sys",
                user_prompt="hi",
                agent_role="researcher",
                max_tokens=512,
            )
            self.assertEqual(seen[-1]["timeout"], 240)
            self.assertEqual(seen[-1]["payload"]["max_tokens"], 512)

    def test_18_empty_content_with_reasoning_content_falls_back_to_backup(self):
        """Empty content with reasoning_content triggers EmptyModelResponse and backup fallback."""
        calls = []

        def fake_post(url, payload, headers, timeout=45):
            calls.append(payload["model"])
            if len(calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "reasoning_content": "long internal chain of thought...",
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"content": "backup answer"}}]}

        with patch.object(ai_gateway, "_http_post_json", side_effect=fake_post):
            res = ai_gateway.complete_nvidia_nim(
                api_key="test-key",
                base_url="https://integrate.api.nvidia.com/v1",
                system_prompt="sys",
                user_prompt="hi",
                model="primary/reasoning-model",
                fallback_model="backup/model",
            )

        self.assertEqual(calls, ["primary/reasoning-model", "backup/model"])
        self.assertEqual(res["text"], "backup answer")
        self.assertEqual(res["model_used"], "backup/model")
        self.assertTrue(res["fallback_used"])
        self.assertEqual(res["fallback_reason"], "empty answer")

    def test_19_think_only_response_treated_as_empty_falls_back_to_backup(self):
        """Response containing only <think>...</think> raises EmptyModelResponse and uses backup."""
        calls = []

        def fake_post(url, payload, headers, timeout=45):
            calls.append(payload["model"])
            if len(calls) == 1:
                return {"choices": [{"message": {"content": "<think>x</think>"}}]}
            return {"choices": [{"message": {"content": "clean backup answer"}}]}

        with patch.object(ai_gateway, "_http_post_json", side_effect=fake_post):
            with self.assertRaises(ai_gateway.EmptyModelResponse) as ctx:
                ai_gateway._single_nim_request(
                    api_key="test-key",
                    base_url="https://integrate.api.nvidia.com/v1",
                    system_prompt="sys",
                    user_prompt="hi",
                    model="primary/model",
                )
            self.assertIn("all tokens spent on reasoning", str(ctx.exception))

            calls.clear()
            res = ai_gateway.complete_nvidia_nim(
                api_key="test-key",
                base_url="https://integrate.api.nvidia.com/v1",
                system_prompt="sys",
                user_prompt="hi",
                model="primary/model",
                fallback_model="backup/model",
            )

        self.assertEqual(calls, ["primary/model", "backup/model"])
        self.assertEqual(res["text"], "clean backup answer")
        self.assertTrue(res["fallback_used"])
        self.assertEqual(res["fallback_reason"], "empty answer")

    def test_20_tier_settings_defaults_and_matrix_fields(self):
        """tier_settings defaults to 60/1500 when missing, and role matrix includes tier/timeout_s/max_tokens."""
        cfg_missing = {
            "tiers": {
                "reasoning": {"primary": "m1", "backup": "m2"},
                "fast": {"primary": "m3", "backup": "m4"},
            },
            "roles": {"cmo": "reasoning"},
        }
        self.assertEqual(
            model_registry.tier_settings("cmo", cfg_missing),
            {"tier": "reasoning", "timeout_s": 60, "max_tokens": 1500},
        )
        self.assertEqual(
            model_registry.tier_settings("unknown-role", cfg_missing),
            {"tier": "fast", "timeout_s": 60, "max_tokens": 1500},
        )

        matrix = ai_gateway.get_role_model_matrix()
        self.assertTrue(len(matrix) > 0)
        by_role = {row["role_id"]: row for row in matrix}
        self.assertEqual(by_role["cmo"]["tier"], "reasoning")
        self.assertEqual(by_role["cmo"]["timeout_s"], 180)
        self.assertEqual(by_role["cmo"]["max_tokens"], 8000)
        self.assertEqual(by_role["listener"]["tier"], "fast")
        self.assertEqual(by_role["listener"]["timeout_s"], 60)
        self.assertEqual(by_role["listener"]["max_tokens"], 1500)


if __name__ == "__main__":
    unittest.main()
