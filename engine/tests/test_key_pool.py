"""Unit tests for engine.key_pool, gateway key rotation, and /api/key_pool."""

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

import ai_gateway
import db
import key_pool
import model_registry
import server


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


class _StubGetHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising GET routes without sockets."""

    def __init__(self, path, headers=None):
        self.path = path
        self.command = "GET"
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.headers = dict(headers or {})
        self.status_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def response_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class KeyPoolTests(unittest.TestCase):
    """Tests for NVIDIA NIM key pool, rate limiting, rotation, and gateway integration."""

    def setUp(self):
        self._db_tmpdir = tempfile.TemporaryDirectory()
        db.set_db_path(os.path.join(self._db_tmpdir.name, "state", "magnm.db"))
        db.init_db()
        key_pool.reset()
        self.env_patcher = patch.object(ai_gateway, "load_env", return_value={})
        self.env_patcher.start()
        self.os_env_patcher = patch.dict(os.environ, {}, clear=True)
        self.os_env_patcher.start()

    def tearDown(self):
        key_pool.reset()
        self.os_env_patcher.stop()
        self.env_patcher.stop()
        db.set_db_path(None)
        self._db_tmpdir.cleanup()

    def test_01_reads_numbered_keys_deduplicates_and_masks_safely(self):
        """Reads KEY1..3 from mocked env, deduplicates duplicate values, and masks safely."""
        mock_env = {
            "NVIDIA_NIM_API_KEY1": "nvapi-alpha-key-111",
            "NVIDIA_NIM_API_KEY2": "nvapi-beta-key-2222",
            "NVIDIA_NIM_API_KEY3": "nvapi-gamma-key-333",
            "NVIDIA_NIM_API_KEY4": "nvapi-alpha-key-111",  # duplicate of KEY1
            "NVIDIA_API_KEY": "nvapi-beta-key-2222",       # duplicate of KEY2
        }
        pool = key_pool.KeyPool(env_loader=lambda: {}, environ=mock_env)
        st = pool.status()

        self.assertEqual([item["label"] for item in st], [
            "NVIDIA_NIM_API_KEY1",
            "NVIDIA_NIM_API_KEY2",
            "NVIDIA_NIM_API_KEY3",
        ])
        self.assertEqual(st[0]["masked"], "nvapi-...111")
        self.assertEqual(st[1]["masked"], "nvapi-...222")
        self.assertEqual(st[2]["masked"], "nvapi-...333")

        serialized = json.dumps(st)
        for full_val in mock_env.values():
            self.assertNotIn(full_val, serialized)

        # Short keys must also never appear in full
        short_pool = key_pool.KeyPool(
            env_loader=lambda: {},
            environ={"NVIDIA_NIM_API_KEY1": "shortkey"},
        )
        short_st = short_pool.status()
        self.assertNotIn("shortkey", json.dumps(short_st))
        self.assertEqual(short_st[0]["masked"], "********")

    def test_02_lru_rotation_three_acquires_return_three_different_keys(self):
        """3 consecutive acquires rotate across all 3 keys via LRU."""
        now = [100.0]
        mock_env = {
            "NVIDIA_NIM_API_KEY1": "nvapi-key-one-aaaa",
            "NVIDIA_NIM_API_KEY2": "nvapi-key-two-bbbb",
            "NVIDIA_NIM_API_KEY3": "nvapi-key-thr-cccc",
        }
        pool = key_pool.KeyPool(
            clock=lambda: now[0],
            sleep=lambda s: None,
            env_loader=lambda: {},
            environ=mock_env,
        )

        labels = []
        for _ in range(3):
            _, lbl = pool.acquire()
            labels.append(lbl)
            now[0] += 0.1

        self.assertEqual(len(set(labels)), 3)
        self.assertEqual(labels, [
            "NVIDIA_NIM_API_KEY1",
            "NVIDIA_NIM_API_KEY2",
            "NVIDIA_NIM_API_KEY3",
        ])

        # 4th acquire wraps back to the least recently used (KEY1)
        _, fourth = pool.acquire()
        self.assertEqual(fourth, "NVIDIA_NIM_API_KEY1")

    def test_03_429_cools_down_key_a_until_retry_after_elapses(self):
        """429 on key A routes next call to B; advancing clock beyond Retry-After restores A."""
        now = [500.0]
        mock_env = {
            "NVIDIA_NIM_API_KEY1": "nvapi-key-aaaa-111",
            "NVIDIA_NIM_API_KEY2": "nvapi-key-bbbb-222",
        }
        pool = key_pool.KeyPool(
            clock=lambda: now[0],
            sleep=lambda s: None,
            env_loader=lambda: {},
            environ=mock_env,
        )

        _, lbl_a = pool.acquire()
        self.assertEqual(lbl_a, "NVIDIA_NIM_API_KEY1")
        pool.report(lbl_a, "rate_limited", retry_after=15.0)

        st_cooling = {item["label"]: item for item in pool.status()}
        self.assertFalse(st_cooling["NVIDIA_NIM_API_KEY1"]["healthy"])
        self.assertEqual(st_cooling["NVIDIA_NIM_API_KEY1"]["cooling_down_s"], 15.0)
        self.assertEqual(st_cooling["NVIDIA_NIM_API_KEY1"]["errors"], 1)

        # Next acquire must skip cooling KEY1 and pick KEY2
        _, lbl_b = pool.acquire()
        self.assertEqual(lbl_b, "NVIDIA_NIM_API_KEY2")

        # Advance clock past Retry-After
        now[0] += 15.1
        st_recovered = {item["label"]: item for item in pool.status()}
        self.assertTrue(st_recovered["NVIDIA_NIM_API_KEY1"]["healthy"])
        self.assertEqual(st_recovered["NVIDIA_NIM_API_KEY1"]["cooling_down_s"], 0.0)

        # KEY1 was used least recently (at 500.0 vs KEY2 at 500.0 + 0), wait: same timestamp 500.0, lower seq!
        _, lbl_next = pool.acquire()
        self.assertEqual(lbl_next, "NVIDIA_NIM_API_KEY1")

    def test_04_401_disables_rejected_key_and_all_401_raises_ai_auth_error(self):
        """401 on key A disables A and succeeds on B; 401 on all keys raises AIAuthError."""
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        seen_auth = []

        def fake_urlopen(req, timeout=45):
            auth = req.headers.get("Authorization")
            seen_auth.append(auth)
            if auth == "Bearer nvapi-bad-key-1111":
                raise _make_http_error(url, 401, "invalid key")
            return _FakeHTTPResponse({"choices": [{"message": {"content": "ok from B"}}]})

        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY1": "nvapi-bad-key-1111",
                "NVIDIA_NIM_API_KEY2": "nvapi-good-key-222",
            },
        ), patch.object(model_registry, "get_catalog", return_value=None), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            res = ai_gateway.generate_completion_with_meta(
                system_prompt="sys",
                user_prompt="hi",
                agent_role="listener",
            )
            st = {item["label"]: item for item in key_pool.status()}

        self.assertEqual(res["text"], "ok from B")
        self.assertEqual(res["key_label"], "NVIDIA_NIM_API_KEY2")
        self.assertEqual(seen_auth, ["Bearer nvapi-bad-key-1111", "Bearer nvapi-good-key-222"])

        self.assertFalse(st["NVIDIA_NIM_API_KEY1"]["healthy"])
        self.assertEqual(st["NVIDIA_NIM_API_KEY1"]["disabled_reason"], "key rejected (HTTP 401/403)")
        self.assertTrue(st["NVIDIA_NIM_API_KEY2"]["healthy"])
        self.assertEqual(st["NVIDIA_NIM_API_KEY2"]["ok"], 1)

        # Now reject KEY2 as well -> every key rejected -> AIAuthError
        def all_401_urlopen(req, timeout=45):
            raise _make_http_error(url, 401, "invalid key")

        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY1": "nvapi-bad-key-1111",
                "NVIDIA_NIM_API_KEY2": "nvapi-good-key-222",
            },
        ), patch.object(model_registry, "get_catalog", return_value=None), patch(
            "urllib.request.urlopen", side_effect=all_401_urlopen
        ):
            with self.assertRaises(ai_gateway.AIAuthError):
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="hi",
                    agent_role="listener",
                )

    def test_05_rate_limit_window_waits_or_raises_after_20s(self):
        """With 1 key and 38 requests in last 60s, acquire waits if <=20s else raises AINotConnected."""
        now = [0.0]
        sleeps = []

        def fake_sleep(dt):
            sleeps.append(dt)
            now[0] += dt

        pool = key_pool.KeyPool(
            clock=lambda: now[0],
            sleep=fake_sleep,
            env_loader=lambda: {},
            environ={"NVIDIA_NIM_API_KEY1": "nvapi-single-key-111"},
        )

        # Fill all 38 slots at t=0.0
        for _ in range(38):
            pool.acquire()

        # At t=10.0, window expires at t=60.0 (50s away > 20s timeout) -> waits 20s then raises AINotConnected
        now[0] = 10.0
        with self.assertRaises(ai_gateway.AINotConnected) as ctx:
            pool.acquire()
        self.assertEqual(
            str(ctx.exception),
            "All NVIDIA keys are busy or rate-limited right now. Try again in a minute.",
        )
        self.assertAlmostEqual(sum(sleeps), 20.0, places=3)

        # At t=50.0, window expires at t=60.0 (10s away <= 20s timeout) -> waits 10s then succeeds
        sleeps.clear()
        now[0] = 50.0
        _, lbl = pool.acquire()
        self.assertEqual(lbl, "NVIDIA_NIM_API_KEY1")
        self.assertAlmostEqual(sum(sleeps), 10.0, places=3)
        self.assertAlmostEqual(now[0], 60.0, places=3)

    def test_06_role_specific_key_used_before_pool(self):
        """A role-specific key is used first for that role, and pool is used after it."""
        now = [100.0]
        mock_env = {
            "NVIDIA_NIM_API_KEY1": "nvapi-pool-key-1111",
            "NVIDIA_NIM_API_KEY2": "nvapi-pool-key-2222",
            "NVIDIA_NIM_API_KEY_CMO": "nvapi-cmo-key-9999",
        }
        pool = key_pool.KeyPool(
            clock=lambda: now[0],
            sleep=lambda s: None,
            env_loader=lambda: {},
            environ=mock_env,
        )

        # CMO gets its role key first on repeated healthy calls
        k1, l1 = pool.acquire(role="cmo")
        self.assertEqual((k1, l1), ("nvapi-cmo-key-9999", "NVIDIA_NIM_API_KEY_CMO"))
        k2, l2 = pool.acquire(role="cmo")
        self.assertEqual((k2, l2), ("nvapi-cmo-key-9999", "NVIDIA_NIM_API_KEY_CMO"))

        # Other roles do NOT use CMO's key
        _, l_other = pool.acquire(role="listener")
        self.assertEqual(l_other, "NVIDIA_NIM_API_KEY1")

        # If CMO's role key cools down, CMO falls back to the pool
        pool.report("NVIDIA_NIM_API_KEY_CMO", "rate_limited", retry_after=30.0)
        _, l3 = pool.acquire(role="cmo")
        self.assertIn(l3, ("NVIDIA_NIM_API_KEY1", "NVIDIA_NIM_API_KEY2"))

    def test_07_gateway_integration_429_on_key1_succeeds_on_key2_primary_model(self):
        """Gateway call with KEY1 returning 429 and KEY2 returning 200 succeeds on primary without model fallback."""
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        calls = []

        def fake_urlopen(req, timeout=45):
            body = json.loads(req.data.decode("utf-8"))
            auth = req.headers.get("Authorization")
            calls.append((auth, body["model"]))
            if auth == "Bearer nvapi-key-one-1111":
                raise _make_http_error(url, 429, "rate limited", headers={"Retry-After": "45"})
            return _FakeHTTPResponse({"choices": [{"message": {"content": "answer from key2"}}]})

        sleeps = []
        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY1": "nvapi-key-one-1111",
                "NVIDIA_NIM_API_KEY2": "nvapi-key-two-2222",
                "NVIDIA_NIM_API_KEY3": "nvapi-key-thr-3333",
            },
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway, "_sleep", side_effect=sleeps.append
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            cfg = ai_gateway.get_config()
            self.assertEqual(cfg["NVIDIA_NIM_API_KEY"], "nvapi-key-one-1111")

            matrix = ai_gateway.get_role_model_matrix()
            listener_row = next(r for r in matrix if r["role_id"] == "listener")
            self.assertTrue(listener_row["has_key"])
            self.assertEqual(listener_row["key_source"], "pool (3 keys)")

            res = ai_gateway.generate_completion_with_meta(
                system_prompt="sys",
                user_prompt="hello",
                agent_role="listener",
            )

        self.assertEqual(res["text"], "answer from key2")
        self.assertEqual(res["model_used"], res["model_requested"])
        self.assertFalse(res["fallback_used"])
        self.assertEqual(res["key_label"], "NVIDIA_NIM_API_KEY2")
        # Switched directly to KEY2 without sleeping
        self.assertEqual(sleeps, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], calls[1][1])

    def test_08_api_key_pool_route_token_gated_and_catalog_uses_numbered_key(self):
        """GET /api/key_pool is token-gated and returns status(); get_catalog works with numbered keys."""
        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY1": "nvapi-num-key-1111",
                "NVIDIA_NIM_API_KEY2": "nvapi-num-key-2222",
            },
        ):
            with patch.object(server, "ENGINE_TOKEN", "secret-tok"):
                h_unauth = _StubGetHandler("/api/key_pool")
                h_unauth.do_GET()
                self.assertEqual(h_unauth.status_code, 401)

                h_auth = _StubGetHandler("/api/key_pool", headers={"X-Magnm-Token": "secret-tok"})
                h_auth.do_GET()
                self.assertEqual(h_auth.status_code, 200)
                payload = h_auth.response_json()
                self.assertEqual(len(payload), 2)
                self.assertEqual(payload[0]["label"], "NVIDIA_NIM_API_KEY1")
                self.assertNotIn("nvapi-num-key-1111", json.dumps(payload))

            with tempfile.TemporaryDirectory() as tmpdir:
                cache_file = os.path.join(tmpdir, "catalog.json")
                seen_headers = []

                def fake_urlopen(req, timeout=10):
                    seen_headers.append(req.headers.get("Authorization"))
                    return _FakeHTTPResponse({"data": [{"id": "meta/llama-3.3-70b-instruct"}]})

                with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    cat = model_registry.get_catalog(
                        None,
                        "https://integrate.api.nvidia.com/v1",
                        force=True,
                        cache_path=cache_file,
                    )
                self.assertEqual(cat, {"meta/llama-3.3-70b-instruct"})
                self.assertEqual(seen_headers, ["Bearer nvapi-num-key-1111"])


if __name__ == "__main__":
    unittest.main()
