"""Tests for shared AI keys: team proxy routing, error mapping, admin AI routes, and /api/account/ai."""

import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import admin_dashboard
import ai_gateway
import db
import key_pool
import paths
import server
import setup_check
import team_auth

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
MEMBER_ID = "22222222-2222-2222-2222-222222222222"

VALID_KEY_1 = "nvapi-" + ("A1b2C3d4E5" * 3)
VALID_KEY_2 = "nvapi-" + ("Z9y8X7w6V5" * 3)
BAD_SHORT_KEY = "nvapi-short"
NON_NVAPI_KEY = "sk-not-nvidia-key-12345678901234567890"


class _FakeHttpResponse:
    def __init__(self, body_obj=None, status=200):
        self.status = status
        if body_obj is None:
            self._raw = b""
        elif isinstance(body_obj, (bytes, bytearray)):
            self._raw = bytes(body_obj)
        else:
            self._raw = json.dumps(body_obj).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url, status, payload, headers=None):
    raw = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else str(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, f"HTTP {status}", hdrs=headers or {}, fp=io.BytesIO(raw))


class TeamAITests(unittest.TestCase):
    """Verify ai_gateway team proxy routing, /api/account/ai, setup checks, and /api/admin/ai/*."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        os.makedirs(os.path.join(self.tmpdir, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "state"), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_token = server.ENGINE_TOKEN
        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir

        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://team.supabase.co", "publishable_key": "sb_pub_team"}, f)
        with open(os.path.join(REPO_ROOT, "config", "models.json"), "r", encoding="utf-8") as src:
            with open(os.path.join(self.tmpdir, "config", "models.json"), "w", encoding="utf-8") as dst:
                dst.write(src.read())

        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        key_pool.reset()
        team_auth.cancel_login()
        team_auth._set_last_error(None)

        self.env_patcher = patch.object(ai_gateway, "load_env", return_value={})
        self.env_patcher.start()
        self.os_env_patcher = patch.dict(os.environ, {}, clear=True)
        self.os_env_patcher.start()

    def tearDown(self):
        self.os_env_patcher.stop()
        self.env_patcher.stop()
        key_pool.reset()
        db.set_db_path(None)
        server.ENGINE_TOKEN = self._prev_token
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        team_auth.cancel_login()
        team_auth._set_last_error(None)
        self._tmp_obj.cleanup()

    def _sign_in(self, user_id=MEMBER_ID, email="member@example.com"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })

    def _start_server(self):
        server.ENGINE_TOKEN = "test-engine-token"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv, srv.server_address[1]

    def _request_local(self, port, method, path, body=None, token="test-engine-token"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {}
        if token is not None:
            headers["X-Magnm-Token"] = token
        raw_body = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw_body = json.dumps(body).encode("utf-8")
        conn.request(method, path, body=raw_body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, json.loads(raw)

    def _make_urlopen(self, role="admin", active=True, user_id=ADMIN_ID, handler=None):
        calls = []

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            method = req.get_method()
            body = json.loads(req.data.decode("utf-8")) if req.data else None
            headers = {k.lower(): v for k, v in req.header_items()}
            entry = {
                "method": method,
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            }
            calls.append(entry)

            if f"/rest/v1/profiles?id=eq.{user_id}&select=id,email,display_name,avatar_url,role,active" in url:
                return _FakeHttpResponse([{
                    "id": user_id,
                    "email": "user@example.com",
                    "display_name": "User",
                    "avatar_url": None,
                    "role": role,
                    "active": active,
                }])
            if handler:
                return handler(entry)
            return _FakeHttpResponse([])

        return calls, fake_urlopen

    def test_01_routing_own_key_vs_team_proxy_vs_signed_out(self):
        # 1. Own key -> calls NIM URL, never proxy
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": VALID_KEY_1}, clear=True):
            calls, fake = self._make_urlopen(
                role="member",
                active=True,
                user_id=MEMBER_ID,
                handler=lambda e: _FakeHttpResponse(
                    {"data": [{"id": "nvidia/nemotron-3-ultra-550b-a55b"}]}
                    if "/models" in e["url"]
                    else {
                        "choices": [{"message": {"content": "from nim"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                    }
                ),
            )
            self._sign_in(MEMBER_ID)
            with patch("urllib.request.urlopen", side_effect=fake):
                res = ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="hi",
                    agent_role="writer",
                )
            self.assertEqual(res["provider"], "nvidia_nim")
            self.assertEqual(res["text"], "from nim")
            chat_calls = [c for c in calls if "/chat/completions" in c["url"]]
            self.assertEqual(len(chat_calls), 1)
            self.assertTrue(chat_calls[0]["url"].startswith("https://integrate.api.nvidia.com/"))
            self.assertFalse(any("/functions/v1/ai-proxy" in c["url"] for c in calls))

        # 2. No key + signed in -> calls team ai-proxy with bearer, apikey, timeout=120, tools, tool_choice, usage
        key_pool.reset()
        self._sign_in(MEMBER_ID)
        tools = [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}]

        def proxy_handler(entry):
            if entry["url"] == "https://team.supabase.co/functions/v1/ai-proxy":
                return _FakeHttpResponse({
                    "choices": [{
                        "message": {
                            "content": "<think>plan</think>Hello team",
                            "tool_calls": [{
                                "id": "tc_1",
                                "type": "function",
                                "function": {"name": "ping", "arguments": "{\"x\": 1}"},
                            }],
                        }
                    }],
                    "usage": {"prompt_tokens": 25, "completion_tokens": 12},
                })
            return _FakeHttpResponse([])

        calls, fake = self._make_urlopen(role="member", active=True, user_id=MEMBER_ID, handler=proxy_handler)
        with patch("urllib.request.urlopen", side_effect=fake):
            res = ai_gateway.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
                tool_choice="auto",
                agent_role="writer",
                max_tokens=512,
                model="minimaxai/minimax-m3.1-flash",
            )

        self.assertEqual(res["provider"], "team")
        self.assertEqual(res["text"], "Hello team")
        self.assertEqual(res["tool_calls"], [{"id": "tc_1", "name": "ping", "arguments": {"x": 1}}])
        self.assertEqual(res["usage"], {"prompt_tokens": 25, "completion_tokens": 12})

        proxy_calls = [c for c in calls if c["url"] == "https://team.supabase.co/functions/v1/ai-proxy"]
        self.assertEqual(len(proxy_calls), 1)
        pc = proxy_calls[0]
        self.assertEqual(pc["headers"].get("authorization"), f"Bearer tok-{MEMBER_ID}")
        self.assertEqual(pc["headers"].get("apikey"), "sb_pub_team")
        self.assertEqual(pc["timeout"], 120)
        self.assertEqual(pc["body"]["model"], "minimaxai/minimax-m3.1-flash")
        self.assertEqual(pc["body"]["max_tokens"], 512)
        self.assertEqual(pc["body"]["temperature"], 0.4)
        self.assertEqual(pc["body"]["tools"], tools)
        self.assertEqual(pc["body"]["tool_choice"], "auto")

        # Recorded in runs table with provider='team'
        conn = db.connect()
        run_row = conn.execute(
            "SELECT provider, model_used, prompt_tokens, completion_tokens, ok FROM runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(run_row["provider"], "team")
        self.assertEqual(run_row["model_used"], "minimaxai/minimax-m3.1-flash")
        self.assertEqual(run_row["prompt_tokens"], 25)
        self.assertEqual(run_row["completion_tokens"], 12)
        self.assertEqual(run_row["ok"], 1)

        # 3. No key + signed out -> existing AINotConnected error
        team_auth._delete_session_file()
        with patch.object(ai_gateway, "_ollama_reachable", return_value=False):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta("sys", "hi", agent_role="writer")
        self.assertIn("AI is not connected", str(ctx.exception))

    def test_02_proxy_error_mapping_and_no_retry_on_429_quota(self):
        self._sign_in(MEMBER_ID)
        proxy_url = "https://team.supabase.co/functions/v1/ai-proxy"

        # 429 daily quota -> passes message through, does NOT retry or call backup model
        quota_msg = "Daily AI limit reached. It resets at midnight UTC. Ask your admin for a higher limit."
        calls, fake = self._make_urlopen(
            role="member",
            active=True,
            user_id=MEMBER_ID,
            handler=lambda e: (_ for _ in ()).throw(
                _http_error(proxy_url, 429, {"error": quota_msg, "reason": "daily_requests_limit"})
            ),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx:
                ai_gateway.generate_completion_with_meta("sys", "hi", agent_role="writer")
        self.assertEqual(str(ctx.exception), quota_msg)
        self.assertEqual(sum(1 for c in calls if c["url"] == proxy_url), 1)
        run_row = db.connect().execute("SELECT provider, ok FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(run_row["provider"], "team")
        self.assertEqual(run_row["ok"], 0)

        # 503 no team keys -> specific admin message
        _, fake_503 = self._make_urlopen(
            role="member",
            active=True,
            user_id=MEMBER_ID,
            handler=lambda e: (_ for _ in ()).throw(
                _http_error(proxy_url, 503, {"error": "The team has no shared AI keys yet. Ask your admin."})
            ),
        )
        with patch("urllib.request.urlopen", side_effect=fake_503):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx503:
                ai_gateway.generate_completion_with_meta("sys", "hi", agent_role="writer")
        self.assertEqual(str(ctx503.exception), "The team has no shared AI keys yet. Ask your admin.")

        # 401 and 403 -> AIAuthError sign-in message
        for code in (401, 403):
            _, fake_auth = self._make_urlopen(
                role="member",
                active=True,
                user_id=MEMBER_ID,
                handler=lambda e, c=code: (_ for _ in ()).throw(
                    _http_error(proxy_url, c, {"error": "Unauthorized"})
                ),
            )
            with patch("urllib.request.urlopen", side_effect=fake_auth):
                with self.assertRaises(ai_gateway.AIAuthError) as ctx_auth:
                    ai_gateway.generate_completion_with_meta("sys", "hi", agent_role="writer")
            self.assertEqual(str(ctx_auth.exception), "Sign in under Settings, Account to use the team's AI keys.")

        # Network error -> existing not connected path
        _, fake_net = self._make_urlopen(
            role="member",
            active=True,
            user_id=MEMBER_ID,
            handler=lambda e: (_ for _ in ()).throw(urllib.error.URLError("dns failure")),
        )
        with patch.object(ai_gateway, "_ollama_reachable", return_value=False), patch(
            "urllib.request.urlopen", side_effect=fake_net
        ):
            with self.assertRaises(ai_gateway.AINotConnected) as ctx_net:
                ai_gateway.generate_completion_with_meta("sys", "hi", agent_role="writer")
        self.assertIn("AI is not connected", str(ctx_net.exception))

    def test_03_setup_check_and_role_matrix_say_using_team_ai_keys(self):
        self._sign_in(MEMBER_ID)
        _, fake = self._make_urlopen(role="member", active=True, user_id=MEMBER_ID)
        with patch("urllib.request.urlopen", side_effect=fake):
            chk = setup_check._check_nvidia_key()
            matrix = ai_gateway.get_role_model_matrix()

        self.assertEqual(chk["status"], "ok")
        self.assertEqual(chk["detail"], "Using the team's AI keys")
        self.assertTrue(all(item["has_key"] and item["key_source"] == "team" for item in matrix))

    def test_04_account_ai_modes_own_keys_team_and_none(self):
        srv, port = self._start_server()
        try:
            # 1. Signed out + no keys -> 200 mode='none'
            status, body = self._request_local(port, "GET", "/api/account/ai")
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "none")
            self.assertEqual(body["today"], {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
            self.assertEqual(body["limit"], {"daily_requests": 200, "daily_tokens": 400000})

            # 2. Own keys (signed out) -> 200 mode='own_keys'
            with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": VALID_KEY_1}, clear=True):
                status, body = self._request_local(port, "GET", "/api/account/ai")
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "own_keys")

            # 3. Signed in + no local keys -> 200 mode='team' with today usage and member override limit
            key_pool.reset()
            self._sign_in(MEMBER_ID)
            today_iso = datetime.now(timezone.utc).date().isoformat()

            def acct_handler(entry):
                if f"ai_usage_daily?owner_id=eq.{MEMBER_ID}&day=eq.{today_iso}" in entry["url"]:
                    return _FakeHttpResponse([{"requests": 37, "prompt_tokens": 1200, "completion_tokens": 450}])
                if f"ai_limits?or=(owner_id.eq.{MEMBER_ID},owner_id.is.null)" in entry["url"]:
                    return _FakeHttpResponse([
                        {"owner_id": None, "daily_requests": 200, "daily_tokens": 400000},
                        {"owner_id": MEMBER_ID, "daily_requests": 350, "daily_tokens": 700000},
                    ])
                return _FakeHttpResponse([])

            _, fake = self._make_urlopen(role="member", active=True, user_id=MEMBER_ID, handler=acct_handler)
            with patch("urllib.request.urlopen", side_effect=fake):
                status, body = self._request_local(port, "GET", "/api/account/ai")
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "team")
            self.assertEqual(body["today"], {"requests": 37, "prompt_tokens": 1200, "completion_tokens": 450})
            self.assertEqual(body["limit"], {"daily_requests": 350, "daily_tokens": 700000})
        finally:
            srv.shutdown()
            srv.server_close()

    def test_05_every_admin_ai_route_refuses_signed_out_member_and_deactivated(self):
        routes = [
            ("GET", "/api/admin/ai", None),
            ("POST", "/api/admin/ai/share-local-keys", {}),
            ("POST", "/api/admin/ai/clear-keys", {}),
            ("POST", "/api/admin/ai/limits", {"member_id": None, "daily_requests": 100, "daily_tokens": 1000}),
            ("POST", "/api/admin/ai/models", {"model": "m1", "enabled": True, "max_tokens_cap": 4096}),
            ("POST", "/api/admin/ai/test", {}),
        ]
        srv, port = self._start_server()
        try:
            for method, path, body in routes:
                st, data = self._request_local(port, method, path, body=body)
                self.assertEqual(st, 401, f"{method} {path} should return 401 signed out")
                self.assertIn("error", data)

            self._sign_in(MEMBER_ID, "member@example.com")
            _, fake_member = self._make_urlopen(role="member", active=True, user_id=MEMBER_ID)
            with patch("urllib.request.urlopen", side_effect=fake_member):
                for method, path, body in routes:
                    st, data = self._request_local(port, method, path, body=body)
                    self.assertEqual(st, 403, f"{method} {path} should return 403 for member")
                    self.assertEqual(data["error"], "Admins only.")

            self._sign_in(ADMIN_ID, "admin@example.com")
            _, fake_deact = self._make_urlopen(role="admin", active=False, user_id=ADMIN_ID)
            with patch("urllib.request.urlopen", side_effect=fake_deact):
                for method, path, body in routes:
                    st, data = self._request_local(port, method, path, body=body)
                    self.assertEqual(st, 403, f"{method} {path} should return 403 for deactivated admin")
                    self.assertEqual(data["error"], "Admins only.")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_06_share_local_keys_clear_keys_and_no_secret_leak(self):
        srv, port = self._start_server()
        try:
            self._sign_in(ADMIN_ID, "admin@example.com")

            # No valid nvapi- keys -> refusal
            with patch.dict(
                os.environ,
                {"NVIDIA_NIM_API_KEY": BAD_SHORT_KEY, "OPENAI_API_KEY": NON_NVAPI_KEY},
                clear=True,
            ):
                key_pool.reset()
                calls, fake = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID)
                with patch("urllib.request.urlopen", side_effect=fake):
                    st, body = self._request_local(port, "POST", "/api/admin/ai/share-local-keys", {})
                self.assertEqual(st, 400)
                self.assertEqual(body["error"], "This laptop has no NVIDIA keys to share.")
                self.assertFalse(any("admin_set_ai_keys" in c["url"] for c in calls))

            # Valid + invalid + duplicate keys -> uploads only unique valid nvapi- keys, returns only count + labels
            env_vars = {
                "NVIDIA_NIM_API_KEY": VALID_KEY_1,
                "NVIDIA_NIM_API_KEY1": VALID_KEY_1,
                "NVIDIA_NIM_API_KEY2": BAD_SHORT_KEY,
                "NVIDIA_NIM_API_KEY_WRITER": VALID_KEY_2,
                "NVIDIA_NIM_API_KEY_CMO": NON_NVAPI_KEY,
            }
            fake_labels = [
                {"label": f"key-1 (...{VALID_KEY_1[-4:]})", "last_status": None, "last_ok_at": None, "cooldown_until": None, "last_error": None},
                {"label": f"key-2 (...{VALID_KEY_2[-4:]})", "last_status": None, "last_ok_at": None, "cooldown_until": None, "last_error": None},
            ]

            def share_handler(entry):
                if entry["url"].endswith("/rest/v1/rpc/admin_set_ai_keys"):
                    return _FakeHttpResponse(len(entry["body"]["p_keys"]))
                if entry["url"].endswith("/rest/v1/rpc/admin_ai_key_labels"):
                    return _FakeHttpResponse(fake_labels)
                return _FakeHttpResponse([])

            log_buf = io.StringIO()
            with patch.dict(os.environ, env_vars, clear=True), patch("sys.stderr", new=log_buf):
                key_pool.reset()
                calls, fake = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=share_handler)
                with patch("urllib.request.urlopen", side_effect=fake):
                    st, body = self._request_local(port, "POST", "/api/admin/ai/share-local-keys", {})

            self.assertEqual(st, 200)
            self.assertEqual(set(body.keys()), {"count", "labels"})
            self.assertEqual(body["count"], 2)
            self.assertEqual(len(body["labels"]), 2)

            set_calls = [c for c in calls if c["url"].endswith("/rest/v1/rpc/admin_set_ai_keys")]
            self.assertEqual(len(set_calls), 1)
            self.assertEqual(set_calls[0]["body"], {"p_keys": [VALID_KEY_1, VALID_KEY_2]})

            serialized = json.dumps(body)
            self.assertNotIn(VALID_KEY_1, serialized)
            self.assertNotIn(VALID_KEY_2, serialized)
            self.assertNotIn(VALID_KEY_1, log_buf.getvalue())
            self.assertNotIn(VALID_KEY_2, log_buf.getvalue())

            # clear-keys -> admin_set_ai_keys([])
            calls, fake = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=share_handler)
            with patch("urllib.request.urlopen", side_effect=fake):
                st, body = self._request_local(port, "POST", "/api/admin/ai/clear-keys", {})
            self.assertEqual(st, 200)
            self.assertEqual(body, {"count": 0})
            clear_calls = [c for c in calls if c["url"].endswith("/rest/v1/rpc/admin_set_ai_keys")]
            self.assertEqual(clear_calls[0]["body"], {"p_keys": []})
        finally:
            srv.shutdown()
            srv.server_close()

    def test_07_limits_models_overview_and_admin_test_uses_proxy_even_with_local_keys(self):
        srv, port = self._start_server()
        try:
            self._sign_in(ADMIN_ID, "admin@example.com")

            # Limits validation errors
            bad_payloads = [
                {"member_id": None, "daily_requests": 0, "daily_tokens": 1000},
                {"member_id": None, "daily_requests": 100001, "daily_tokens": 1000},
                {"member_id": None, "daily_requests": True, "daily_tokens": 1000},
                {"member_id": None, "daily_requests": 10, "daily_tokens": 0},
                {"member_id": None, "daily_requests": 10, "daily_tokens": 100000001},
                {"member_id": "not-a-uuid", "daily_requests": 10, "daily_tokens": 1000},
                {"member_id": "not-a-uuid", "reset": True},
            ]
            _, fake_basic = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID)
            with patch("urllib.request.urlopen", side_effect=fake_basic):
                for bad in bad_payloads:
                    st, _ = self._request_local(port, "POST", "/api/admin/ai/limits", bad)
                    self.assertEqual(st, 400, f"Expected 400 for {bad}")

            # Upsert default, upsert member override, reset member override
            def limit_handler(entry):
                if "ai_limits" in entry["url"]:
                    return _FakeHttpResponse([entry["body"] or {"ok": True}])
                if "ai_allowed_models" in entry["url"] and entry["method"] == "PATCH":
                    return _FakeHttpResponse([{"model": "m1", **entry["body"]}])
                return _FakeHttpResponse([])

            calls, fake = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=limit_handler)
            with patch("urllib.request.urlopen", side_effect=fake):
                st1, _ = self._request_local(
                    port, "POST", "/api/admin/ai/limits", {"member_id": None, "daily_requests": 300, "daily_tokens": 500000}
                )
                st2, _ = self._request_local(
                    port, "POST", "/api/admin/ai/limits", {"member_id": MEMBER_ID, "daily_requests": 500, "daily_tokens": 900000}
                )
                st3, res3 = self._request_local(
                    port, "POST", "/api/admin/ai/limits", {"member_id": MEMBER_ID, "reset": True}
                )
                st4, res4 = self._request_local(
                    port, "POST", "/api/admin/ai/models", {"model": "m1", "enabled": False, "max_tokens_cap": 2048}
                )
                st_bad_mod, _ = self._request_local(
                    port, "POST", "/api/admin/ai/models", {"model": "m1", "enabled": "yes", "max_tokens_cap": 2048}
                )
                st_bad_cap, _ = self._request_local(
                    port, "POST", "/api/admin/ai/models", {"model": "m1", "enabled": True, "max_tokens_cap": 99999}
                )

            self.assertEqual(st1, 200)
            self.assertEqual(st2, 200)
            self.assertEqual(st3, 200)
            self.assertTrue(res3["reset"])
            self.assertEqual(st4, 200)
            self.assertFalse(res4["model"]["enabled"])
            self.assertEqual(st_bad_mod, 400)
            self.assertEqual(st_bad_cap, 400)

            patch_default = [c for c in calls if "ai_limits?owner_id=is.null" in c["url"] and c["method"] == "PATCH"]
            self.assertEqual(len(patch_default), 1)
            self.assertEqual(patch_default[0]["body"], {"daily_requests": 300, "daily_tokens": 500000})

            post_override = [c for c in calls if "ai_limits?on_conflict=owner_id" in c["url"] and c["method"] == "POST"]
            self.assertEqual(len(post_override), 1)
            self.assertEqual(post_override[0]["body"], {"owner_id": MEMBER_ID, "daily_requests": 500, "daily_tokens": 900000})

            del_override = [c for c in calls if f"ai_limits?owner_id=eq.{MEMBER_ID}" in c["url"] and c["method"] == "DELETE"]
            self.assertEqual(len(del_override), 1)

            patch_model = [c for c in calls if "ai_allowed_models?model=eq.m1" in c["url"] and c["method"] == "PATCH"]
            self.assertEqual(len(patch_model), 1)
            self.assertEqual(patch_model[0]["body"], {"enabled": False, "max_tokens_cap": 2048})

            # Admin /api/admin/ai/test ALWAYS uses proxy even when local NIM keys exist
            long_reply = "OK " + ("x" * 60)

            def test_handler(entry):
                if "ai_allowed_models?enabled=eq.true" in entry["url"]:
                    return _FakeHttpResponse([{"model": "google/gemma-4-31b-it", "max_tokens_cap": 4096}])
                if entry["url"] == "https://team.supabase.co/functions/v1/ai-proxy":
                    return _FakeHttpResponse({"choices": [{"message": {"content": long_reply}}]})
                return _FakeHttpResponse([])

            with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": VALID_KEY_1}, clear=True):
                key_pool.reset()
                calls, fake = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=test_handler)
                with patch("urllib.request.urlopen", side_effect=fake):
                    st_test, res_test = self._request_local(port, "POST", "/api/admin/ai/test", {})

            self.assertEqual(st_test, 200)
            self.assertTrue(res_test["ok"])
            self.assertEqual(res_test["status"], 200)
            self.assertEqual(res_test["model"], "google/gemma-4-31b-it")
            self.assertEqual(len(res_test["reply"]), 40)
            proxy_calls = [c for c in calls if c["url"] == "https://team.supabase.co/functions/v1/ai-proxy"]
            self.assertEqual(len(proxy_calls), 1)
            self.assertEqual(
                proxy_calls[0]["body"],
                {
                    "model": "google/gemma-4-31b-it",
                    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                    "max_tokens": 5,
                },
            )

            # GET /api/admin/ai aggregates usage & limits
            today_iso = datetime.now(timezone.utc).date().isoformat()

            def overview_handler(entry):
                if entry["url"].endswith("/rest/v1/rpc/admin_ai_key_labels"):
                    return _FakeHttpResponse([{"label": "key-1 (...3d4E)", "last_status": 200, "last_ok_at": "2026-11-22T00:00:00Z", "cooldown_until": None, "last_error": None}])
                if "ai_allowed_models?select=" in entry["url"]:
                    return _FakeHttpResponse([{"model": "google/gemma-4-31b-it", "enabled": True, "max_tokens_cap": 4096}])
                if "ai_limits?select=" in entry["url"]:
                    return _FakeHttpResponse([{"id": "l0", "owner_id": None, "daily_requests": 200, "daily_tokens": 400000}])
                if "ai_usage_daily?" in entry["url"]:
                    return _FakeHttpResponse([
                        {"owner_id": MEMBER_ID, "day": today_iso, "requests": 12, "prompt_tokens": 1000, "completion_tokens": 300, "rejected": 0},
                        {"owner_id": MEMBER_ID, "day": "2026-11-20", "requests": 8, "prompt_tokens": 500, "completion_tokens": 100, "rejected": 0},
                    ])
                if "profiles?select=id,email,display_name,role,active" in entry["url"]:
                    return _FakeHttpResponse([
                        {"id": ADMIN_ID, "email": "admin@example.com", "display_name": "Pranav", "role": "admin", "active": True},
                        {"id": MEMBER_ID, "email": "maya@example.com", "display_name": "Maya", "role": "member", "active": True},
                    ])
                return _FakeHttpResponse([])

            with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": VALID_KEY_1}, clear=True):
                key_pool.reset()
                _, fake_ov = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=overview_handler)
                with patch("urllib.request.urlopen", side_effect=fake_ov):
                    st_ov, ov = self._request_local(port, "GET", "/api/admin/ai")

            self.assertEqual(st_ov, 200)
            self.assertEqual(ov["local_key_count"], 1)
            self.assertEqual(len(ov["keys"]), 1)
            maya_usage = next(u for u in ov["usage"] if u["owner_id"] == MEMBER_ID)
            self.assertEqual(maya_usage["display_name"], "Maya")
            self.assertEqual(maya_usage["today"]["requests"], 12)
            self.assertEqual(maya_usage["today"]["tokens"], 1300)
            self.assertEqual(maya_usage["seven_days"]["requests"], 20)
            self.assertEqual(maya_usage["seven_days"]["tokens"], 1900)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
