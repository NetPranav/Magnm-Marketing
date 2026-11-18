"""Unit tests for tools registry, agent_runtime sessions, chat_with_tools, and session HTTP API."""

import importlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_runtime
import ai_gateway
import db
import events
import key_pool
import model_registry
import paths
import server
import tools


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


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing GET and POST session endpoints."""

    def __init__(self, path, method="GET", body_dict=None, headers=None):
        self.path = path
        self.command = method
        raw = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = dict(headers or {})
        if body_dict is not None:
            self.headers.setdefault("Content-Length", str(len(raw)))
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


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class AgentRuntimeTests(unittest.TestCase):
    """Tests covering tool calling, pausable sessions, gateway chat_with_tools, and HTTP routes."""

    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        ai_gateway.ENV_PATH = paths.data(".env")

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def test_01_tool_loop_read_product_facts_and_done(self):
        """Model calls read_product_facts, then answers -> done, 2 steps, tool_calls logged."""
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        call_num = {"n": 0}
        seen_messages = []

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            call_num["n"] += 1
            seen_messages.append(json.loads(json.dumps(messages)))
            if call_num["n"] == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {"id": "call_1", "name": "read_product_facts", "arguments": {"product_id": "cero"}}
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "m1",
                    "model_used": "m1",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "warnings": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            return {
                "text": "Cero is a desktop terminal.",
                "tool_calls": [],
                "provider": "nvidia_nim",
                "model_requested": "m1",
                "model_used": "m1",
                "fallback_used": False,
                "fallback_reason": None,
                "warnings": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            }

        with patch.object(ai_gateway, "chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session(
                "writer",
                "Tell me about Cero.",
                system_prompt="Use read_product_facts.",
                allowed_tools=["read_product_facts"],
            )

        self.assertEqual(sess["status"], "done")
        self.assertEqual(sess["steps"], 2)
        self.assertEqual(sess["result_text"], "Cero is a desktop terminal.")
        self.assertEqual(call_num["n"], 2)

        # Verify tool result was appended in second model call
        second_call_msgs = seen_messages[1]
        tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1")
        tool_payload = json.loads(tool_msgs[0]["content"])
        self.assertIn("Cero", tool_payload["product_facts"])
        self.assertIn("Cero", tool_payload["product_rules"])

        logged = db.list_tool_calls(session_id=sess["id"])
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["tool"], "read_product_facts")
        self.assertTrue(logged[0]["ok"])

        ev_list = []
        while not q.empty():
            ev_list.append(q.get_nowait())
        step_events = [e for e in ev_list if e["type"] == "agent_step"]
        self.assertEqual(len(step_events), 2)
        self.assertEqual(step_events[0]["data"]["tool_names"], ["read_product_facts"])

    def test_02_disallowed_tool_returns_error_and_session_completes(self):
        """Disallowed tool returns error to model and session still finishes."""
        calls = {"n": 0}
        seen_msgs = []

        def fake_chat(messages, tools=None, **_kwargs):
            calls["n"] += 1
            seen_msgs.append(json.loads(json.dumps(messages)))
            if calls["n"] == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {"id": "call_bad", "name": "save_research_note", "arguments": {"title": "t", "body": "b"}}
                    ],
                }
            return {"text": "Completed without saving.", "tool_calls": []}

        with patch.object(ai_gateway, "chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session(
                "writer",
                "Try saving a note.",
                system_prompt="sys",
                allowed_tools=["read_product_facts"],
            )

        self.assertEqual(sess["status"], "done")
        self.assertEqual(sess["result_text"], "Completed without saving.")
        tool_msg = [m for m in seen_msgs[1] if m.get("role") == "tool"][0]
        self.assertEqual(json.loads(tool_msg["content"]), {"error": "tool not allowed for this agent"})

        logged = db.list_tool_calls(session_id=sess["id"])
        self.assertEqual(len(logged), 1)
        self.assertFalse(logged[0]["ok"])
        self.assertEqual(logged[0]["error"], "tool not allowed for this agent")

    def test_03_bad_args_missing_required_and_wrong_type(self):
        """Missing required or wrong-typed args return error dict without raising."""
        ctx = tools.ToolContext(session_id="s_bad", agent_id="researcher")

        res_missing = tools.run_tool("save_research_note", ctx, {"title": "Only title"}, ["save_research_note"])
        self.assertIn("error", res_missing)
        self.assertIn("body", res_missing["error"])

        res_type = tools.run_tool(
            "search_research",
            ctx,
            {"query": "cero", "limit": "not_an_int"},
            ["search_research"],
        )
        self.assertIn("error", res_type)
        self.assertIn("limit", res_type["error"])

    def test_04_ask_user_pause_restart_and_resume_with_answer(self):
        """ask_user pauses session; a fresh AgentRuntime reloads from DB and resumes with user's answer."""
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        rt1 = agent_runtime.AgentRuntime()

        def first_turn(messages, tools=None, **_kwargs):
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_ask",
                        "name": "ask_user",
                        "arguments": {"question": "Proceed with launch?", "options": ["yes", "no"]},
                    }
                ],
            }

        with patch.object(ai_gateway, "chat_with_tools", side_effect=first_turn):
            paused = rt1.start_session(
                "cmo",
                "Check launch readiness.",
                system_prompt="Ask CEO before proceeding.",
                allowed_tools=["ask_user"],
            )

        self.assertEqual(paused["status"], "waiting_user")
        self.assertEqual(
            paused["pending_question"],
            {"question": "Proceed with launch?", "options": ["yes", "no"], "tool_call_id": "call_ask"},
        )

        # Simulate restart with a brand new AgentRuntime instance
        rt2 = agent_runtime.AgentRuntime()
        reloaded = rt2.get_session(paused["id"])
        self.assertEqual(reloaded["status"], "waiting_user")

        observed_msgs = []

        def second_turn(messages, tools=None, **_kwargs):
            observed_msgs.append(json.loads(json.dumps(messages)))
            return {"text": "Launch confirmed by user.", "tool_calls": []}

        with patch.object(ai_gateway, "chat_with_tools", side_effect=second_turn):
            resumed = rt2.resume_session(paused["id"], "yes")

        self.assertEqual(resumed["status"], "done")
        self.assertEqual(resumed["result_text"], "Launch confirmed by user.")
        self.assertIsNone(resumed["pending_question"])

        tool_msgs = [m for m in observed_msgs[0] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_ask")
        self.assertEqual(json.loads(tool_msgs[0]["content"]), {"answer": "yes"})

        ev_types = []
        while not q.empty():
            ev_types.append(q.get_nowait()["type"])
        self.assertIn("agent_waiting", ev_types)
        self.assertIn("agent_done", ev_types)

    def test_05_max_steps_fails_with_too_many_steps(self):
        """Exceeding 10 model steps marks session failed with 'too many steps'."""
        calls = {"n": 0}

        def always_tool(messages, tools=None, **_kwargs):
            calls["n"] += 1
            return {
                "text": "",
                "tool_calls": [{"id": f"call_{calls['n']}", "name": "list_repos", "arguments": {}}],
            }

        with patch.object(ai_gateway, "chat_with_tools", side_effect=always_tool):
            sess = agent_runtime.start_session(
                "researcher",
                "Loop forever.",
                system_prompt="sys",
                allowed_tools=["list_repos"],
            )

        self.assertEqual(calls["n"], 10)
        self.assertEqual(sess["status"], "failed")
        self.assertEqual(sess["error"], "too many steps")

    def test_06_cancel_while_waiting_and_while_running(self):
        """Cancelling a waiting session marks cancelled; cancel flag while running stops at next step."""
        rt = agent_runtime.AgentRuntime()

        with patch.object(
            ai_gateway,
            "chat_with_tools",
            return_value={
                "text": "",
                "tool_calls": [{"id": "c1", "name": "ask_user", "arguments": {"question": "Confirm?"}}],
            },
        ):
            waiting = rt.start_session(
                "cmo",
                "Wait on user",
                system_prompt="sys",
                allowed_tools=["ask_user"],
            )
        self.assertEqual(waiting["status"], "waiting_user")
        cancelled_w = rt.cancel_session(waiting["id"])
        self.assertEqual(cancelled_w["status"], "cancelled")
        self.assertEqual(rt.get_session(waiting["id"])["status"], "cancelled")

        # Cancel while running: cancel triggered during step 1 tool execution -> stops before step 2
        created = rt.create_session(
            "writer",
            "Run and cancel",
            system_prompt="sys",
            allowed_tools=["list_repos"],
        )
        step_calls = {"n": 0}

        def chat_then_cancel(messages, tools=None, **_kwargs):
            step_calls["n"] += 1
            if step_calls["n"] == 1:
                rt.cancel_session(created["id"])
                return {
                    "text": "",
                    "tool_calls": [{"id": "c2", "name": "list_repos", "arguments": {}}],
                }
            return {"text": "Should not reach step 2", "tool_calls": []}

        with patch.object(ai_gateway, "chat_with_tools", side_effect=chat_then_cancel):
            final = rt.run_session(created["id"])

        self.assertEqual(step_calls["n"], 1)
        self.assertEqual(final["status"], "cancelled")

    def test_07_gateway_ai_not_connected_marks_session_failed_without_raising(self):
        """AINotConnected from gateway sets status=failed with plain error message and publishes event."""
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        with patch.object(
            ai_gateway,
            "chat_with_tools",
            side_effect=ai_gateway.AINotConnected("AI is not connected: add an NVIDIA NIM key."),
        ):
            sess = agent_runtime.start_session(
                "writer",
                "Hello",
                system_prompt="sys",
                allowed_tools=[],
            )

        self.assertEqual(sess["status"], "failed")
        self.assertIn("AI is not connected", sess["error"])

        ev_list = []
        while not q.empty():
            ev_list.append(q.get_nowait())
        failed_events = [e for e in ev_list if e["type"] == "agent_failed"]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0]["data"]["session_id"], sess["id"])

    def test_08_large_tool_result_truncated_and_large_args_truncated_in_log(self):
        """Tool results over 12,000 chars are truncated with a note; logged arg values over 2,000 chars truncated."""
        tool_name = "_test_huge_tool_output"

        @tools.tool(
            name=tool_name,
            description="Test tool returning oversized output.",
            parameters_schema={
                "type": "object",
                "properties": {"big_input": {"type": "string"}},
                "required": ["big_input"],
            },
        )
        def _huge_tool(ctx, big_input):
            return {"content": "A" * 20000}

        ctx = tools.ToolContext(session_id="s_trunc", agent_id="writer")
        res = tools.run_tool(tool_name, ctx, {"big_input": "B" * 5000}, [tool_name])
        serialized = json.dumps(res, ensure_ascii=False)
        self.assertLessEqual(len(serialized), 12000)
        self.assertTrue(res.get("truncated"))
        self.assertIn("12,000", res.get("note", ""))

        logged = db.list_tool_calls(session_id="s_trunc")
        self.assertEqual(len(logged), 1)
        logged_args = json.loads(logged[0]["args_json"])
        self.assertLess(len(logged_args["big_input"]), 2100)
        self.assertIn("[truncated]", logged_args["big_input"])

    def test_09_chat_with_tools_parsing_images_pool_backup_confidential_and_secondary(self):
        """Verify NIM tool_calls parsing, text-only, image dropping, pool/backup fallback, Ollama & secondary."""
        # A. NIM tool_calls parsing (empty text + tool_calls is valid, malformed JSON args -> {"_raw": str})
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-k1"}, clear=True), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway,
            "_http_post_json",
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": "<think>hidden</think>",
                            "tool_calls": [
                                {
                                    "id": "tc_1",
                                    "type": "function",
                                    "function": {"name": "list_repos", "arguments": "{}"},
                                },
                                {
                                    "id": "tc_2",
                                    "type": "function",
                                    "function": {"name": "check_text", "arguments": "{bad json"},
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 15, "completion_tokens": 9},
            },
        ):
            res = ai_gateway.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=tools.schemas_for(["list_repos", "check_text"]),
                agent_role="writer",
            )
        self.assertEqual(res["text"], "")
        self.assertEqual(len(res["tool_calls"]), 2)
        self.assertEqual(res["tool_calls"][0], {"id": "tc_1", "name": "list_repos", "arguments": {}})
        self.assertEqual(res["tool_calls"][1]["arguments"], {"_raw": "{bad json"})
        self.assertEqual(res["usage"], {"prompt_tokens": 15, "completion_tokens": 9})

        # B. Text-only response strips <think>
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-k1"}, clear=True), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway,
            "_http_post_json",
            return_value={"choices": [{"message": {"content": "<think>plan</think>Final answer"}}]},
        ):
            res_txt = ai_gateway.chat_with_tools(messages=[{"role": "user", "content": "hi"}], agent_role="writer")
        self.assertEqual(res_txt["text"], "Final answer")
        self.assertEqual(res_txt["tool_calls"], [])

        # C. Images dropped for non-vision model with a warning
        seen_payloads = []

        def capture_payload(url, payload, headers, timeout=45):
            seen_payloads.append(payload)
            return {"choices": [{"message": {"content": "described text only"}}]}

        mm_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-k1"}, clear=True), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway, "_http_post_json", side_effect=capture_payload
        ):
            res_non_vision = ai_gateway.chat_with_tools(messages=mm_messages, agent_role="writer")
        self.assertEqual(seen_payloads[-1]["messages"][0]["content"], "Look at this")
        self.assertTrue(any("Dropped image" in w for w in res_non_vision["warnings"]))

        # Vision model keeps image_url parts without warning
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-k1"}, clear=True), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(model_registry, "get_catalog", return_value=None), patch.object(
            ai_gateway, "_http_post_json", side_effect=capture_payload
        ):
            res_vision = ai_gateway.chat_with_tools(messages=mm_messages, agent_role="designer")
        self.assertIsInstance(seen_payloads[-1]["messages"][0]["content"], list)
        self.assertFalse(any("Dropped image" in w for w in res_vision["warnings"]))

        # D. Key pool rotation + backup fallback work for chat_with_tools
        pool = key_pool.KeyPool(
            env_loader=lambda: {},
            environ={"NVIDIA_NIM_API_KEY1": "nvapi-pool-1", "NVIDIA_NIM_API_KEY2": "nvapi-pool-2"},
        )
        seen_keys_models = []

        def fake_urlopen_pool(req, timeout=60):
            auth = req.get_header("Authorization")
            body = json.loads(req.data.decode("utf-8"))
            seen_keys_models.append((auth, body["model"]))
            if len(seen_keys_models) == 1:
                raise _make_http_error(req.full_url, 429, "rate limited")
            if len(seen_keys_models) == 2:
                raise _make_http_error(req.full_url, 503, "primary down")
            return _FakeHTTPResponse({"choices": [{"message": {"content": "backup answered"}}]})

        with patch.object(key_pool, "get_pool", return_value=pool), patch.dict(
            os.environ, {}, clear=True
        ), patch.object(ai_gateway, "load_env", return_value={}), patch.object(
            model_registry, "get_catalog", return_value=None
        ), patch.object(ai_gateway, "_sleep"), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen_pool
        ):
            res_fb = ai_gateway.chat_with_tools(
                messages=[{"role": "user", "content": "hello"}],
                agent_role="writer",
            )
        self.assertEqual(res_fb["text"], "backup answered")
        self.assertTrue(res_fb["fallback_used"])
        self.assertEqual(len(seen_keys_models), 3)
        self.assertNotEqual(seen_keys_models[0][0], seen_keys_models[1][0])

        # E. Confidential Ollama fallback without tools when model rejects tools
        ollama_calls = []

        def fake_ollama_post(url, payload, headers, timeout=20):
            ollama_calls.append(payload)
            if "tools" in payload:
                raise _make_http_error(url, 400, '{"error":"does not support tools"}')
            return {"message": {"content": "ollama plain reply"}}

        with patch.object(ai_gateway, "_ollama_reachable", return_value=True), patch.object(
            ai_gateway, "_http_post_json", side_effect=fake_ollama_post
        ):
            res_ol = ai_gateway.chat_with_tools(
                messages=[{"role": "user", "content": "secret"}],
                tools=tools.schemas_for(["list_repos"]),
                confidential=True,
            )
        self.assertEqual(res_ol["text"], "ollama plain reply")
        self.assertEqual(res_ol["tool_calls"], [])
        self.assertTrue(any("does not support tools" in w for w in res_ol["warnings"]))
        self.assertEqual(len(ollama_calls), 2)
        self.assertIn("tools", ollama_calls[0])
        self.assertNotIn("tools", ollama_calls[1])

        # F. Secondary providers: skips Anthropic/Gemini when tools given, uses Groq (OpenAI-compat)
        sec_urls = []

        def fake_sec_post(url, payload, headers, timeout=45):
            sec_urls.append(url)
            return {"choices": [{"message": {"content": "groq tools ok"}}]}

        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "ant-k", "GEMINI_API_KEY": "gem-k", "GROQ_API_KEY": "groq-k"},
            clear=True,
        ), patch.object(ai_gateway, "load_env", return_value={}), patch.object(
            ai_gateway, "_http_post_json", side_effect=fake_sec_post
        ):
            res_sec = ai_gateway.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=tools.schemas_for(["list_repos"]),
                agent_role="writer",
            )
        self.assertEqual(res_sec["provider"], "groq")
        self.assertEqual(sec_urls, ["https://api.groq.com/openai/v1/chat/completions"])

    def test_10_http_sessions_start_poll_answer_cancel_and_token_gate(self):
        """HTTP /api/sessions/start runs in background; GET polls until done and checks result_text."""
        # Token gate check
        with patch.object(server, "ENGINE_TOKEN", "secret-tok"):
            unauth = _StubHandler(
                "/api/sessions/start",
                method="POST",
                body_dict={"agent_id": "writer", "message": "hi", "system_prompt": "s", "allowed_tools": []},
            )
            unauth.do_POST()
            self.assertEqual(unauth.status_code, 401)

        def fake_chat(messages, tools=None, **_kwargs):
            return {"text": "HTTP session done.", "tool_calls": []}

        with patch.object(server, "ENGINE_TOKEN", ""), patch.object(
            ai_gateway, "chat_with_tools", side_effect=fake_chat
        ):
            start_h = _StubHandler(
                "/api/sessions/start",
                method="POST",
                body_dict={
                    "agent_id": "writer",
                    "message": "Write something.",
                    "system_prompt": "Be concise.",
                    "allowed_tools": ["read_product_facts"],
                },
            )
            start_h.do_POST()
            self.assertEqual(start_h.status_code, 200)
            sid = start_h.response_json()["session_id"]

            def is_done():
                gh = _StubHandler(f"/api/sessions/{sid}", method="GET")
                gh.do_GET()
                return gh.status_code == 200 and gh.response_json().get("status") == "done"

            self.assertTrue(_wait_until(is_done, timeout=3.0))

            final_h = _StubHandler(f"/api/sessions/{sid}", method="GET")
            final_h.do_GET()
            self.assertEqual(final_h.status_code, 200)
            data = final_h.response_json()
            self.assertEqual(data["result_text"], "HTTP session done.")
            self.assertGreaterEqual(len(data["messages"]), 3)

            list_h = _StubHandler("/api/sessions?limit=5", method="GET")
            list_h.do_GET()
            self.assertEqual(list_h.status_code, 200)
            self.assertTrue(any(s["id"] == sid for s in list_h.response_json()))

    def test_11_core_tools_and_stale_session_recovery(self):
        """Core tools work against repo files/DB and recover_stale_sessions marks running as failed."""
        ctx = tools.ToolContext(session_id="s_core", agent_id="researcher")

        repos_res = tools.run_tool("list_repos", ctx, {}, ["list_repos"])
        repo_ids = [r["id"] for r in repos_res["repositories"]]
        self.assertIn("cero", repo_ids)

        changes_res = tools.run_tool("read_repo_changes", ctx, {"repo_id": "cero", "limit": 2}, ["read_repo_changes"])
        self.assertEqual(len(changes_res["repositories"]), 1)
        self.assertLessEqual(len(changes_res["repositories"][0]["recent_commits"]), 2)
        self.assertTrue(changes_res["repositories"][0]["version"])

        save_res = tools.run_tool(
            "save_research_note",
            ctx,
            {"title": "Cero local SLM finding", "body": "Runs 3B model locally.", "source": "internal"},
            ["save_research_note"],
        )
        self.assertTrue(save_res["saved"])

        search_res = tools.run_tool("search_research", ctx, {"query": "Cero local", "limit": 5}, ["search_research"])
        self.assertTrue(any("Cero local SLM finding" in hit["title"] for hit in search_res["results"]))

        check_res = tools.run_tool("check_text", ctx, {"text": "LazyLayout features 🚀"}, ["check_text"])
        self.assertFalse(check_res["ok"])
        msgs = " ".join(w["message"] for w in check_res["warnings"])
        self.assertIn("teasers_only", msgs)
        self.assertIn("emoji", msgs.lower())

        # Stale running session recovery
        db.create_agent_session(
            {
                "id": "stale_sess_1",
                "agent_id": "writer",
                "status": "running",
                "messages": [],
                "allowed_tools": [],
            }
        )
        recovered = agent_runtime.recover_stale_sessions()
        self.assertEqual([s["id"] for s in recovered], ["stale_sess_1"])
        reloaded = agent_runtime.get_session("stale_sess_1")
        self.assertEqual(reloaded["status"], "failed")
        self.assertEqual(reloaded["error"], "engine restarted")


if __name__ == "__main__":
    unittest.main()
