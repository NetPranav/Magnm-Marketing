"""Unit tests for the 7-agent roster, prompts, content drafts, tools, chat, and team room."""

import base64
import importlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_chat
import agent_runtime
import agents
import ai_gateway
import db
import key_pool
import paths
import platforms
import server
import team_room
import tools
from tools import ToolContext

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0F"
    "]+"
)


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


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing GET endpoints."""

    def __init__(self, path, method="GET", headers=None):
        self.path = path
        self.command = method
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


class AgentsSystemTests(unittest.TestCase):
    """Tests for Task T2.1b: 7 agents, prompts, content tools, design tools, chat, and team room."""

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
        key_pool.reset()

    def tearDown(self):
        key_pool.reset()
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def test_01_agents_config_legacy_resolution_and_prompt_assembly(self):
        """agents.json loads 7 agents, legacy IDs resolve, files exist, and build_system_prompt is complete and emoji-free."""
        loaded = agents.load_agents()
        self.assertEqual(len(loaded), 7)
        expected_ids = {
            "cmo",
            "researcher",
            "writer",
            "designer",
            "video-producer",
            "reviewer",
            "analyst",
        }
        self.assertEqual({a["id"] for a in loaded}, expected_ids)

        legacy_map = {
            "strategist": "cmo",
            "advisor": "cmo",
            "growth": "cmo",
            "listener": "researcher",
            "blog-writer": "writer",
            "community": "writer",
        }
        for legacy_id, canon_id in legacy_map.items():
            resolved = agents.get_agent(legacy_id)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["id"], canon_id)

        for ag in loaded:
            prompt_path = paths.data(ag["prompt_file"])
            self.assertTrue(os.path.isfile(prompt_path), f"Missing prompt file: {prompt_path}")
            for skill in ag.get("skills") or []:
                skill_path = paths.data("agents", "skills", f"{skill}.md")
                self.assertTrue(os.path.isfile(skill_path), f"Missing skill file: {skill_path}")

            sp = agents.build_system_prompt(ag["id"])
            self.assertIn("## Voice", sp)
            self.assertIn("Product rules", sp)
            self.assertIn("## Tool rules", sp)
            self.assertIn("Today's date (Asia/Kolkata):", sp)
            self.assertIsNone(_EMOJI_RE.search(sp), f"Emoji found in prompt for {ag['id']}")

            if ag["id"] in ("writer", "designer", "reviewer", "video-producer"):
                self.assertIn("Platform norms:", sp)
            else:
                self.assertNotIn("Platform norms:", sp)

        self.assertEqual(platforms.limits_for("x")["max_chars"], 280)
        self.assertEqual(platforms.limits_for("linkedin")["hook_chars"], 210)
        self.assertEqual(platforms.limits_for("instagram")["max_hashtags"], 5)
        self.assertEqual(platforms.limits_for("youtube")["title_max_chars"], 100)
        self.assertEqual(platforms.limits_for("blog")["min_words"], 1200)

    def test_02_write_draft_versions_mirror_and_warnings(self):
        """write_draft creates v1 then v2, mirrors markdown files, and reports platform/product-rule warnings."""
        ctx = ToolContext(session_id="sess_draft_1", agent_id="writer")
        allowed = ["write_draft", "read_content", "write_review"]

        res1 = tools.run_tool(
            "write_draft",
            ctx,
            {
                "type": "post",
                "platform": "x",
                "title": "Overlong X draft",
                "body": "Cero runs commands in a real shell. " + ("x" * 285),
                "product_id": "cero",
            },
            allowed,
        )
        self.assertEqual(res1["version"], 1)
        cid = res1["content_id"]
        v1_path = paths.data("outputs", "content", cid, "v1.md")
        self.assertTrue(os.path.isfile(v1_path))
        self.assertTrue(any(w["product"] == "platform" for w in res1["warnings"]))

        res2 = tools.run_tool(
            "write_draft",
            ctx,
            {
                "type": "post",
                "platform": "linkedin",
                "title": "LazyLayout draft",
                "body": "LazyLayout features include a timeline editor and keyframe animations.",
                "content_id": cid,
                "notes": "v2 test",
            },
            allowed,
        )
        self.assertEqual(res2["content_id"], cid)
        self.assertEqual(res2["version"], 2)
        v2_path = paths.data("outputs", "content", cid, "v2.md")
        self.assertTrue(os.path.isfile(v2_path))
        self.assertTrue(any(w["product"] == "lazylayout" for w in res2["warnings"]))

        read_back = tools.run_tool("read_content", ctx, {"content_id": cid, "version": 1}, allowed)
        self.assertEqual(read_back["version"], 1)
        with open(v1_path, encoding="utf-8") as f:
            self.assertIn("Overlong X draft", f.read())

        rev_ctx = ToolContext(session_id="sess_rev_1", agent_id="reviewer")
        rev_res = tools.run_tool(
            "write_review",
            rev_ctx,
            {"content_id": cid, "verdict": "warn", "notes": "1. Remove LazyLayout feature claims."},
            ["write_review"],
        )
        self.assertEqual(rev_res["verdict"], "warn")
        updated_item = db.get_content_item(cid)
        self.assertTrue(any(f.get("kind") == "review" and f.get("verdict") == "warn" for f in updated_item["files"]))

    def test_03_generate_image_uses_key_pool_no_seed_and_attaches_file(self):
        """generate_image posts to FLUX without 'seed', acquires/reports key pool, decodes JPEG, and attaches it."""
        seen = {}
        fake_jpeg = b"\xff\xd8\xff\xe0FAKEJPEGDATA"
        fake_b64 = base64.b64encode(fake_jpeg).decode("ascii")

        def fake_urlopen(req, timeout=120):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["auth"] = req.get_header("Authorization")
            return _FakeHTTPResponse({"artifacts": [{"base64": fake_b64}]})

        ctx = ToolContext(session_id="sess_img_1", agent_id="designer")
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-flux-test-key"}, clear=False), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            res = tools.run_tool(
                "generate_image",
                ctx,
                {"prompt": "Minimalist line-art terminal prompt asking for approval", "width": 1024, "height": 1024},
                ["generate_image"],
            )
            pool_status = key_pool.status()

        self.assertNotIn("error", res)
        self.assertIn("black-forest-labs/flux.2-klein-4b", seen["url"])
        self.assertEqual(seen["timeout"], 120)
        self.assertNotIn("seed", seen["body"])
        self.assertEqual(seen["body"]["steps"], 4)
        self.assertEqual(seen["auth"], "Bearer nvapi-flux-test-key")

        out_file = paths.data(res["path"])
        self.assertTrue(os.path.isfile(out_file))
        with open(out_file, "rb") as f:
            self.assertEqual(f.read(), fake_jpeg)

        item = db.get_content_item(res["content_id"])
        self.assertTrue(any(e.get("kind") == "image" and e.get("path") == res["path"] for e in item["files"]))

        self.assertTrue(any(s["ok"] == 1 for s in pool_status))

    def test_04_render_card_creates_png_files_and_attaches_to_content(self):
        """render_card renders 4x5 and 1x1 PNG cards via templates/card.py and attaches them."""
        ctx = ToolContext(session_id="sess_card_1", agent_id="designer")
        res = tools.run_tool(
            "render_card",
            ctx,
            {"headline": "See it before it runs", "subline": "Cero terminal", "formats": ["4x5", "1x1"]},
            ["render_card"],
        )
        self.assertNotIn("error", res)
        self.assertEqual(len(res["files"]), 2)
        for rel in res["files"]:
            abs_p = paths.data(rel)
            self.assertTrue(os.path.isfile(abs_p))
            self.assertGreater(os.path.getsize(abs_p), 500)

        item = db.get_content_item(res["content_id"])
        self.assertEqual(len([f for f in item["files"] if f.get("kind") == "card"]), 2)

    def test_05_check_image_rejects_paths_outside_data_root(self):
        """check_image rejects paths outside DATA_ROOT and invokes vision gateway for valid paths."""
        ctx = ToolContext(session_id="sess_chk_1", agent_id="reviewer")
        outside_path = os.path.abspath(os.path.join(self.tmpdir, "..", "outside.png"))
        res_bad = tools.run_tool(
            "check_image",
            ctx,
            {"path": outside_path, "question": "Is text readable?"},
            ["check_image"],
        )
        self.assertIn("error", res_bad)
        self.assertIn("DATA_ROOT", res_bad["error"])

        traversal = tools.run_tool(
            "check_image",
            ctx,
            {"path": "../../etc/passwd", "question": "Read this"},
            ["check_image"],
        )
        self.assertIn("error", traversal)
        self.assertIn("DATA_ROOT", traversal["error"])

        img_rel = os.path.join("outputs", "sample.png")
        img_abs = paths.data(img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        with open(img_abs, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        with patch(
            "ai_gateway.generate_completion_with_meta",
            return_value={"text": "Crisp and readable.", "model_used": "vision-m1"},
        ) as mock_gen:
            res_ok = tools.run_tool(
                "check_image",
                ctx,
                {"path": img_rel, "question": "Is the text crisp?"},
                ["check_image"],
            )
        self.assertEqual(res_ok["answer"], "Crisp and readable.")
        mock_gen.assert_called_once()

    def test_06_delegate_cmo_to_writer_and_depth_enforcement(self):
        """CMO delegates to writer synchronously, returns content_id, posts to team room, and enforces depth <= 2."""
        calls = {"n": 0}

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            calls["n"] += 1
            if agent_role == "cmo" and calls["n"] == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_del_1",
                            "name": "delegate",
                            "arguments": {"agent_id": "writer", "instruction": "Draft an X post about Cero."},
                        }
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "cmo-m",
                    "model_used": "cmo-m",
                    "fallback_used": False,
                }
            if agent_role == "writer" and calls["n"] == 2:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_wd_1",
                            "name": "write_draft",
                            "arguments": {
                                "type": "post",
                                "platform": "x",
                                "title": "Cero X post",
                                "body": "Cero shows every file-changing command before it runs.",
                                "product_id": "cero",
                            },
                        }
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "wrt-m",
                    "model_used": "wrt-m",
                    "fallback_used": False,
                }
            if agent_role == "writer":
                return {
                    "text": "Draft ready.",
                    "tool_calls": [],
                    "provider": "nvidia_nim",
                    "model_requested": "wrt-m",
                    "model_used": "wrt-m",
                    "fallback_used": False,
                }
            return {
                "text": "CMO summary: writer drafted the X post.",
                "tool_calls": [],
                "provider": "nvidia_nim",
                "model_requested": "cmo-m",
                "model_used": "cmo-m",
                "fallback_used": False,
            }

        cmo_cfg = agents.get_agent("cmo")
        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session(
                "cmo",
                "Have the writer draft an X post about Cero.",
                system_prompt=agents.build_system_prompt("cmo"),
                allowed_tools=cmo_cfg["tools"],
            )

        self.assertEqual(sess["status"], "done")
        self.assertIn("CMO summary", sess["result_text"])

        # Sub-agent reply was posted to team room as writer
        msgs = team_room.load_messages(limit=10)
        self.assertTrue(any(m["sender"] == "writer" and m["text"] == "Draft ready." for m in msgs))

        # Depth and role enforcement
        writer_ctx = ToolContext(session_id="s_w", agent_id="writer", depth=1)
        writer_cfg = agents.get_agent("writer")
        denied_role = tools.run_tool(
            "delegate",
            writer_ctx,
            {"agent_id": "reviewer", "instruction": "Check it"},
            writer_cfg["tools"],
        )
        self.assertEqual(denied_role["error"], "tool not allowed for this agent")

        # Direct tool call at depth=2 (next_depth=3 > 2) refused even for cmo
        deep_cmo_ctx = ToolContext(session_id="s_deep", agent_id="cmo", depth=2)
        refused_depth = tools.run_tool(
            "delegate",
            deep_cmo_ctx,
            {"agent_id": "writer", "instruction": "Too deep"},
            cmo_cfg["tools"],
        )
        self.assertIn("depth", refused_depth["error"].lower())

    def test_07_chat_with_agent_legacy_id_and_waiting_user_resume(self):
        """chat_with_agent resolves legacy IDs ('blog-writer' -> 'writer'), pauses on ask_user, and resumes."""
        step = {"n": 0}

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            step["n"] += 1
            self.assertEqual(agent_role, "writer")
            if step["n"] == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "ask_1",
                            "name": "ask_user",
                            "arguments": {"question": "Which platform is this for?", "options": ["X", "LinkedIn"]},
                        }
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "wrt-m",
                    "model_used": "wrt-m",
                    "fallback_used": False,
                }
            return {
                "text": "Got it, drafting for LinkedIn.",
                "tool_calls": [],
                "provider": "nvidia_nim",
                "model_requested": "wrt-m",
                "model_used": "wrt-m",
                "fallback_used": False,
            }

        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            res1 = agent_chat.chat_with_agent("blog-writer", "Write a launch post")
            self.assertTrue(res1["success"])
            self.assertTrue(res1["waiting_user"])
            self.assertEqual(res1["agent"]["id"], "writer")
            self.assertEqual(res1["response"], "Which platform is this for?")
            sess_id = res1["session_id"]

            res2 = agent_chat.chat_with_agent("writer", "LinkedIn")
            self.assertTrue(res2["success"])
            self.assertFalse(res2["waiting_user"])
            self.assertEqual(res2["session_id"], sess_id)
            self.assertEqual(res2["response"], "Got it, drafting for LinkedIn.")

        roster = agent_chat.get_roster_status()
        self.assertEqual(len(roster), 7)
        self.assertNotIn("blog-writer", {r["id"] for r in roster})

    def test_08_team_room_sequential_mentions_and_default_cmo(self):
        """team_room runs @writer then @reviewer in order, and routes unmentioned CEO messages to @cmo."""
        called_roles = []

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            called_roles.append(agent_role)
            return {
                "text": f"Reply from {agent_role}",
                "tool_calls": [],
                "provider": "nvidia_nim",
                "model_requested": "m",
                "model_used": "m",
                "fallback_used": False,
            }

        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            replies = team_room.post_message("CEO", "@writer @reviewer draft and check")
        self.assertEqual(called_roles, ["writer", "reviewer"])
        self.assertEqual([r["sender"] for r in replies], ["CEO", "writer", "reviewer"])

        called_roles.clear()
        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            replies2 = team_room.post_message("CEO", "What should we post this week?")
        self.assertEqual(called_roles, ["cmo"])
        self.assertEqual([r["sender"] for r in replies2], ["CEO", "cmo"])

    def test_09_record_metrics_and_read_metrics(self):
        """record_metrics persists metric objects and read_metrics retrieves them."""
        ctx = ToolContext(session_id="sess_ana_1", agent_id="analyst")
        allowed = ["record_metrics", "read_metrics"]
        rec = tools.run_tool(
            "record_metrics",
            ctx,
            {
                "content_id": "cnt_123",
                "platform": "linkedin",
                "metrics": {"views": 420, "likes": 19, "comments": 3, "reposts": 2},
                "source": "manual",
                "captured_at": "2026-11-19T02:00:00Z",
            },
            allowed,
        )
        self.assertEqual(rec["content_id"], "cnt_123")
        self.assertEqual(rec["metrics"]["views"], 420)

        read = tools.run_tool("read_metrics", ctx, {"content_id": "cnt_123"}, allowed)
        self.assertEqual(len(read["metrics"]), 1)
        self.assertEqual(read["metrics"][0]["source"], "manual")
        self.assertEqual(read["metrics"][0]["metrics"]["likes"], 19)

    def test_10_http_agents_and_content_endpoints(self):
        """HTTP GET /api/agents, /api/content, /api/content/<id>, and /api/content/<id>/file enforce files_json."""
        ctx = ToolContext(session_id="sess_http_1", agent_id="writer")
        draft = tools.run_tool(
            "write_draft",
            ctx,
            {"type": "post", "platform": "linkedin", "title": "HTTP Test", "body": "Body text"},
            ["write_draft"],
        )
        cid = draft["content_id"]

        h_agents = _StubHandler("/api/agents")
        h_agents.do_GET()
        self.assertEqual(h_agents.status_code, 200)
        ag_list = h_agents.response_json()
        self.assertEqual(len(ag_list), 7)
        self.assertTrue(all(a["status"] in ("idle", "working", "waiting_user") for a in ag_list))

        h_list = _StubHandler("/api/content?status=draft&type=post")
        h_list.do_GET()
        self.assertEqual(h_list.status_code, 200)
        items = h_list.response_json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], cid)

        h_one = _StubHandler(f"/api/content/{cid}")
        h_one.do_GET()
        self.assertEqual(h_one.status_code, 200)
        one = h_one.response_json()
        self.assertEqual(len(one["versions"]), 1)

        rel_file = draft["path"]
        h_file_ok = _StubHandler(f"/api/content/{cid}/file?path={rel_file}")
        h_file_ok.do_GET()
        self.assertEqual(h_file_ok.status_code, 200)
        self.assertIn(b"HTTP Test", h_file_ok.wfile.getvalue())

        h_file_bad = _StubHandler(f"/api/content/{cid}/file?path=config.md")
        h_file_bad.do_GET()
        self.assertEqual(h_file_bad.status_code, 403)


if __name__ == "__main__":
    unittest.main()
