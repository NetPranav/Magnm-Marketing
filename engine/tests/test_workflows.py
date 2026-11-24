"""Unit tests for multi-step workflows, gates, posting kits, content status, and recovery."""

import importlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_runtime
import agents
import ai_gateway
import db
import events
import jobs
import paths
import posting_kit
import server
import tools
from tools import ToolContext
from tools import core as core_tools
import workflows


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing workflow and content status routes."""

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


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _create_fake_session(agent_id, title="", status="done", result_text="", pending_question=None):
    """Persist a deterministic session row in SQLite and return it."""
    sid = f"sess_fake_{int(time.time() * 1000)}_{os.urandom(3).hex()}"
    return db.create_agent_session(
        {
            "id": sid,
            "agent_id": agent_id,
            "status": status,
            "messages": [{"role": "user", "content": title}],
            "pending_question": pending_question,
            "title": title,
            "result_text": result_text,
            "allowed_tools": [],
            "steps": 1,
        }
    )


class WorkflowTests(unittest.TestCase):
    """Tests for workflows, gates, posting kits, crash recovery, and HTTP routes."""

    def setUp(self):
        jobs.stop_worker(timeout=2.0)
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
        jobs.stop_worker(timeout=2.0)
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def test_01_post_workflow_two_platforms_image_warn_revise_ready_posting_kit(self):
        """post: 2 platforms + image -> 2 drafts, card, 2 reviews (1 warn -> 1 revision), ready, POSTING-KIT.md."""
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        session_calls = []

        def fake_start_session(agent_id, user_message, **kwargs):
            title = kwargs.get("title") or ""
            sess = _create_fake_session(agent_id, title=title)
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            session_calls.append((agent_id, title))

            if agent_id == "writer" and "Revise ONLY" not in user_message:
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "post",
                        "platform": "linkedin",
                        "title": "Cero LinkedIn Post",
                        "body": "Cero shows every command before running it on your machine. #DevTools #Security #CLI",
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "post",
                        "platform": "x",
                        "title": "Cero X Post",
                        "body": "Cero asks before running system-changing commands. #CLI",
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                return sess

            if agent_id == "designer":
                draft_ids = [
                    cid for cid in re.findall(r"cnt_[a-zA-Z0-9_]+", user_message)
                ]
                target_cid = draft_ids[0] if draft_ids else None
                tools.run_tool(
                    "render_card",
                    ctx,
                    {
                        "headline": "ASKS FIRST",
                        "subline": "before changing your files",
                        "formats": ["4x5", "1x1"],
                        "content_id": target_cid,
                    },
                    ["render_card"],
                )
                db.update_agent_session(
                    sess["id"],
                    result_text="ALT_TEXT: Minimal sky-blue typographic card reading ASKS FIRST.",
                )
                return db.get_agent_session(sess["id"])

            if agent_id == "reviewer":
                m = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                cid = m.group(1)
                item = db.get_content_item(cid)
                if item["platform"] == "x":
                    tools.run_tool(
                        "write_review",
                        ctx,
                        {
                            "content_id": cid,
                            "verdict": "warn",
                            "notes": "1. Clarify local approval flow in X post.",
                        },
                        ["write_review"],
                    )
                else:
                    tools.run_tool(
                        "write_review",
                        ctx,
                        {"content_id": cid, "verdict": "ok", "notes": "Accurate and within limits."},
                        ["write_review"],
                    )
                return sess

            if agent_id == "writer" and "Revise ONLY" in user_message:
                cids = re.findall(r"cnt_[a-zA-Z0-9_]+", user_message)
                for cid in cids:
                    tools.run_tool(
                        "write_draft",
                        ctx,
                        {
                            "type": "post",
                            "platform": "x",
                            "title": "Cero X Post",
                            "body": "Cero shows every command that changes your system and asks first. #CLI",
                            "content_id": cid,
                            "notes": "alt_text: Minimal sky-blue typographic card reading ASKS FIRST.",
                            "product_id": "cero",
                        },
                        ["write_draft"],
                    )
                return sess

            return sess

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start(
                "post",
                {
                    "brief": "Cero shows every command that changes your system and asks first",
                    "platforms": ["linkedin", "x"],
                    "product_id": "cero",
                    "with_image": True,
                },
            )
            finished = _wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done")
            self.assertTrue(finished, "post workflow did not finish")

        final_run = workflows.get(run["id"])
        self.assertEqual(final_run["status"], "done")
        draft_ids = final_run["state"]["draft_ids"]
        self.assertEqual(len(draft_ids), 2)

        items = [db.get_content_item(cid, include_versions=True) for cid in draft_ids]
        by_plat = {it["platform"]: it for it in items}
        self.assertEqual(by_plat["linkedin"]["status"], "ready")
        self.assertEqual(by_plat["x"]["status"], "ready")
        self.assertEqual(by_plat["linkedin"]["current_version"], 1)
        self.assertEqual(by_plat["x"]["current_version"], 2)  # revised once due to warn

        # Card rendered and attached
        card_files = [f for f in by_plat["linkedin"]["files"] if f.get("kind") == "card"]
        self.assertGreaterEqual(len(card_files), 2)
        for cf in card_files:
            self.assertTrue(os.path.isfile(paths.data(cf["path"])))

        # Posting kit exists with char counts, see-more cutoff, review verdicts, and alt text
        kit_rel = final_run["state"]["posting_kit_path"]
        kit_abs = paths.data(kit_rel)
        self.assertTrue(os.path.isfile(kit_abs))
        with open(kit_abs, "r", encoding="utf-8") as f:
            kit_text = f.read()
        self.assertIn("Char count vs limit:", kit_text)
        self.assertIn("See more cutoff: ~210 chars", kit_text)
        self.assertIn("alt text:", kit_text)
        self.assertIn("Verdict: warn", kit_text)
        self.assertIn("Verdict: ok", kit_text)

        # files_json updated on drafts
        self.assertTrue(
            any(f.get("kind") == "posting_kit" and f.get("path") == kit_rel for f in by_plat["x"]["files"])
        )

        # SSE events published
        ev_types = []
        while not q.empty():
            ev_types.append(q.get_nowait()["type"])
        self.assertIn("workflow_step", ev_types)
        self.assertIn("workflow_done", ev_types)

    def test_02_blog_workflow_outline_gate_revise_approve_sections_seo_repurpose(self):
        """blog: stops at outline gate -> Revise -> v2 -> gate -> Approve -> direct section calls -> SEO -> repurpose -> done."""
        outline_calls = {"n": 0}
        gateway_calls = []
        seo_calls = []

        def fake_start_session(agent_id, user_message, **kwargs):
            title = kwargs.get("title") or ""
            sess = _create_fake_session(agent_id, title=title)
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)

            if agent_id == "writer" and "blog outline" in user_message.lower():
                outline_calls["n"] += 1
                m_cid = re.search(r"content_id='(cnt_[a-zA-Z0-9_]+)'", user_message)
                cid = m_cid.group(1) if m_cid else None
                outline_body = (
                    "## TL;DR\nCero previews every mutating command before execution.\n\n"
                    "## Why Command Approval Matters\nExplain risks of unreviewed shell commands.\n\n"
                    "## How Classification Works\nDetail read-only vs write classification."
                )
                if cid:
                    outline_body += "\n\n## Recovery and Audit\nAdded per feedback."
                draft_args = {
                    "type": "blog",
                    "platform": "blog",
                    "title": "How Cero Decides Approval",
                    "body": outline_body,
                    "notes": "outline",
                    "product_id": "cero",
                }
                if cid:
                    draft_args["content_id"] = cid
                tools.run_tool("write_draft", ctx, draft_args, ["write_draft"])
                return sess

            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                cid = m_cid.group(1)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": cid, "verdict": "ok", "notes": "Grounded and clean."},
                    ["write_review"],
                )
                return sess

            if agent_id == "writer" and "SEO pass" in user_message:
                m_cid = re.search(r"content_id='(cnt_[a-zA-Z0-9_]+)'", user_message)
                cid = m_cid.group(1)
                item = db.get_content_item(cid)
                seo_payload = {
                    "seo_title": "How Cero Decides Command Approval",
                    "meta_description": "Learn how Cero classifies shell commands and asks before making system changes.",
                    "slug": "how-cero-decides-command-approval",
                }
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "blog",
                        "platform": "blog",
                        "title": item["title"],
                        "body": item["body"],
                        "content_id": cid,
                        "notes": json.dumps(seo_payload),
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                return sess

            if agent_id == "writer" and "Repurpose blog" in user_message:
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "thread",
                        "platform": "x",
                        "title": "Cero Approval Thread",
                        "body": "1/ How Cero decides which commands need approval.\n\n2/ Read-only stays fast; writes ask first.",
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "post",
                        "platform": "linkedin",
                        "title": "Cero Approval LinkedIn",
                        "body": "Most terminal tools execute first and let you clean up later. Cero classifies commands first.",
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                return sess

            raise AssertionError(f"Unexpected agent session: {agent_id} ({title})")

        def fake_generate_meta(**kwargs):
            if "Return ONLY a JSON object with exactly these keys" in kwargs["user_prompt"]:
                seo_calls.append(kwargs)
                return {
                    "text": json.dumps({
                        "seo_title": "How Cero Decides Command Approval",
                        "meta_description": "How Cero asks before running commands that change your system.",
                        "slug": "how-cero-decides-command-approval",
                    }),
                    "provider": "nvidia_nim",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                }
            if "Write a blog OUTLINE" in kwargs["user_prompt"]:
                outline_calls["n"] += 1
                outline_body = (
                    "# How Cero Decides Approval\n\n"
                    "## TL;DR\nCero previews every mutating command before execution.\n\n"
                    "## Why Command Approval Matters\nIntent: Explain risks of unreviewed shell commands.\n\n"
                    "## How Classification Works\nIntent: Detail read-only vs write classification."
                )
                if "### User feedback to apply" in kwargs["user_prompt"]:
                    outline_body += "\n\n## Recovery and Audit\nIntent: Added per feedback."
                return {"text": outline_body, "provider": "nvidia_nim", "model_used": "minimaxai/minimax-m3.1-flash"}
            gateway_calls.append(kwargs)
            prompt = kwargs["user_prompt"]
            m_sec = re.search(r"Draft ONLY section \d+/\d+ \('([^']+)'\)", prompt)
            sec_name = m_sec.group(1) if m_sec else "Section"
            body = (
                f"## {sec_name}\n"
                f"Detailed engineering explanation for {sec_name} grounded in Cero facts. "
                f"Ending paragraph for {sec_name} flow check."
            )
            return {"text": body, "provider": "nvidia_nim", "model_used": "minimaxai/minimax-m3.1-flash"}

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session), patch.object(
            ai_gateway, "generate_completion_with_meta", side_effect=fake_generate_meta
        ):
            run = workflows.start("blog", {"topic": "How Cero decides which commands need your approval", "product_id": "cero"})
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "waiting_user"))

            paused1 = workflows.get(run["id"])
            self.assertEqual(paused1["current_step"], 1)
            pending_g1 = [g for g in paused1["gates"] if g["status"] == "pending"][0]
            self.assertEqual(pending_g1["options"], ["Approve", "Revise"])

            # Answer Revise without feedback should fail validation
            with self.assertRaises(ValueError):
                workflows.answer_gate(pending_g1["id"], "Revise", feedback="")

            # Answer Revise with feedback -> produces outline v2 and pauses at gate again
            workflows.answer_gate(pending_g1["id"], "Revise", feedback="Add a section on recovery and audit")
            self.assertTrue(
                _wait_until(
                    lambda: (
                        (workflows.get(run["id"]) or {}).get("status") == "waiting_user"
                        and len((workflows.get(run["id"]) or {}).get("gates", [])) == 2
                    )
                )
            )
            paused2 = workflows.get(run["id"])
            blog_id = paused2["state"]["outline_content_id"]
            self.assertEqual(db.get_content_item(blog_id)["current_version"], 2)

            pending_g2 = [g for g in paused2["gates"] if g["status"] == "pending"][0]
            workflows.answer_gate(pending_g2["id"], "Approve")

            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

        done_run = workflows.get(run["id"])
        self.assertEqual(outline_calls["n"], 2)
        # Exactly one direct gateway call per section (3 sections)
        self.assertEqual(len(gateway_calls), 3)
        expected_sys = agents.build_system_prompt("writer")
        for idx, call in enumerate(gateway_calls):
            self.assertEqual(call["agent_role"], "writer")
            self.assertEqual(call["tier"], "longform")
            self.assertEqual(call["system_prompt"], expected_sys)
            self.assertIn("Word budget: 533 words", call["user_prompt"])
            self.assertIn("Verified product facts:", call["user_prompt"])
            if idx > 0:
                prev_sec = ["Why Command Approval Matters", "How Classification Works"][idx - 1]
                self.assertIn(f"Ending paragraph for {prev_sec} flow check.", call["user_prompt"])

        # Grounding stored once in state and progress reported
        self.assertIn("product_facts", done_run["state"]["grounding"])
        self.assertIn("repo_changes", done_run["state"]["grounding"])
        self.assertRegex(done_run["state"]["progress"], r"^section 3/3, \d+ words so far$")

        blog_item = db.get_content_item(blog_id, include_versions=True)
        self.assertEqual(blog_item["status"], "ready")
        seo_data = json.loads(blog_item["notes"])
        self.assertLessEqual(len(seo_data["seo_title"]), 60)
        self.assertLessEqual(len(seo_data["meta_description"]), 155)
        self.assertEqual(seo_data["slug"], "how-cero-decides-command-approval")

        rep_ids = done_run["state"]["repurposed_ids"]
        self.assertEqual(len(rep_ids), 2)
        rep_items = [db.get_content_item(rid) for rid in rep_ids]
        self.assertEqual({r["platform"] for r in rep_items}, {"x", "linkedin"})
        self.assertEqual({r["type"] for r in rep_items}, {"thread", "post"})

    def test_03_weekly_plan_three_slots_keep_change_drop_starts_two_post_runs(self):
        """weekly_plan: 3 slots; Keep / Change / Drop -> 2 post runs started with right inputs."""
        started_post_calls = []

        def fake_start_session(agent_id, user_message, **kwargs):
            title = kwargs.get("title") or ""
            sess = _create_fake_session(agent_id, title=title)
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "cmo":
                plan_json = {
                    "slots": [
                        {
                            "date": "2026-11-23",
                            "platform": "linkedin",
                            "topic": "Cero command preview",
                            "angle": "Safety first",
                            "product_id": "cero",
                            "reason": "Core differentiator",
                        },
                        {
                            "date": "2026-11-25",
                            "platform": "x",
                            "topic": "Local SLM privacy",
                            "angle": "Runs offline",
                            "product_id": "cero",
                            "reason": "Strong developer interest",
                        },
                        {
                            "date": "2026-11-27",
                            "platform": "instagram",
                            "topic": "Behind the scenes",
                            "angle": "Team story",
                            "product_id": None,
                            "reason": "Culture post",
                        },
                    ]
                }
                db.update_agent_session(sess["id"], result_text=json.dumps(plan_json))
                return db.get_agent_session(sess["id"])
            if agent_id == "writer":
                started_post_calls.append(user_message)
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "post",
                        "platform": "linkedin",
                        "title": "Slot Draft",
                        "body": "Draft body for scheduled slot.",
                    },
                    ["write_draft"],
                )
                return sess
            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": m_cid.group(1), "verdict": "ok", "notes": "ok"},
                    ["write_review"],
                )
                return sess
            return sess

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start(
                "weekly_plan",
                {"posts_per_week": 3, "platforms": ["linkedin", "x", "instagram"], "week_start": "2026-11-23"},
            )
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "waiting_user"))

            # Gate 1: Keep
            g1 = [g for g in workflows.get(run["id"])["gates"] if g["status"] == "pending"][0]
            workflows.answer_gate(g1["id"], "Keep")
            self.assertTrue(_wait_until(lambda: len(workflows.get(run["id"])["gates"]) == 2))

            # Gate 2: Change
            g2 = [g for g in workflows.get(run["id"])["gates"] if g["status"] == "pending"][0]
            workflows.answer_gate(g2["id"], "Change", feedback="Emphasize zero cloud uploads")
            self.assertTrue(_wait_until(lambda: len(workflows.get(run["id"])["gates"]) == 3))

            # Gate 3: Drop
            g3 = [g for g in workflows.get(run["id"])["gates"] if g["status"] == "pending"][0]
            workflows.answer_gate(g3["id"], "Drop")

            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

            done_run = workflows.get(run["id"])
            child_ids = done_run["state"]["child_run_ids"]
            self.assertEqual(sorted(child_ids.keys()), ["0", "1"])

            child0 = workflows.get(child_ids["0"])
            child1 = workflows.get(child_ids["1"])
            self.assertEqual(child0["kind"], "post")
            self.assertEqual(child0["inputs"]["platforms"], ["linkedin"])
            self.assertEqual(child0["inputs"]["product_id"], "cero")
            self.assertIn("Cero command preview", child0["inputs"]["brief"])

            self.assertEqual(child1["kind"], "post")
            self.assertEqual(child1["inputs"]["platforms"], ["x"])
            self.assertIn("Emphasize zero cloud uploads", child1["inputs"]["brief"])

            # Wait for spawned child post runs to finish while start_session is still patched
            self.assertTrue(
                _wait_until(
                    lambda: (
                        (workflows.get(child_ids["0"]) or {}).get("status") == "done"
                        and (workflows.get(child_ids["1"]) or {}).get("status") == "done"
                    )
                )
            )

    def test_04_video_script_captions_srt_valid_and_monotonic(self):
        """video_script: writes script.md, shotlist.json, and valid monotonic captions.srt."""
        def fake_start_session(agent_id, user_message, **kwargs):
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "video-producer":
                script_body = (
                    "Scene 1 (0s-3s)\n"
                    "Visual: Terminal showing a destructive command prompt.\n"
                    "On-screen: ASKS FIRST.\n"
                    "VO: Most terminal agents run commands before you can blink.\n\n"
                    "Scene 2 (3s-10s)\n"
                    "Visual: Cero approval banner highlighting file changes.\n"
                    "On-screen: PREVIEW DIFF.\n"
                    "VO: Cero inspects every command and asks for approval first.\n\n"
                    "Scene 3 (10s-15s)\n"
                    "Visual: End card with repo link.\n"
                    "On-screen: TRY CERO.\n"
                    "VO: Stay in control of every change."
                )
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "script",
                        "platform": "instagram",
                        "title": "Cero 15s Reel",
                        "body": script_body,
                        "product_id": "cero",
                    },
                    ["write_draft"],
                )
                return sess
            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": m_cid.group(1), "verdict": "ok", "notes": "Valid script."},
                    ["write_review"],
                )
                return sess
            return sess

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start("video_script", {"topic": "Cero command safety", "duration_s": 15, "aspect": "9:16"})
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

        done_run = workflows.get(run["id"])
        script_id = done_run["state"]["script_content_id"]
        out_dir = paths.data("outputs", "content", script_id)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "script.md")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "shotlist.json")))
        srt_path = os.path.join(out_dir, "captions.srt")
        self.assertTrue(os.path.isfile(srt_path))

        with open(srt_path, "r", encoding="utf-8") as f:
            srt_content = f.read().strip()

        blocks = re.split(r"\n\s*\n", srt_content)
        self.assertEqual(len(blocks), 3)
        ts_re = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")

        def to_ms(h, m, s, ms):
            return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms)

        prev_end = -1
        for expected_idx, block in enumerate(blocks, start=1):
            lines = block.splitlines()
            self.assertGreaterEqual(len(lines), 3)
            self.assertEqual(int(lines[0]), expected_idx)
            m = ts_re.match(lines[1])
            self.assertIsNotNone(m, f"Invalid SRT timestamp line: {lines[1]}")
            start_ms = to_ms(*m.groups()[:4])
            end_ms = to_ms(*m.groups()[4:])
            self.assertLess(start_ms, end_ms)
            self.assertGreaterEqual(start_ms, prev_end)
            prev_end = end_ms
            self.assertTrue(" ".join(lines[2:]).strip())

    def test_04b_video_script_keeps_a_saved_script_when_the_agent_runs_out_of_steps(self):
        """A script saved before the step limit is used, so the run is not lost to endless rewrites."""
        def fake_start_session(agent_id, user_message, **kwargs):
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "video-producer":
                tools.run_tool("write_draft", ctx, {
                    "type": "script", "platform": "instagram", "title": "Cero 15s Reel",
                    "body": "Scene 1 (0s-5s)\nVisual: Terminal.\nOn-screen: ASKS FIRST.\nVO: Cero asks first.\n\n"
                            "Scene 2 (5s-15s)\nVisual: End card.\nOn-screen: TRY CERO.\nVO: Stay in control.",
                    "product_id": "cero",
                }, ["write_draft"])
                return db.update_agent_session(sess["id"], status="failed", error="too many steps")
            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                tools.run_tool("write_review", ctx, {"content_id": m_cid.group(1), "verdict": "ok", "notes": "ok"},
                               ["write_review"])
            return sess

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start("video_script", {"topic": "Cero command safety", "duration_s": 15, "aspect": "9:16"})
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") in ("done", "failed")))
        done_run = workflows.get(run["id"])
        self.assertEqual(done_run["status"], "done", done_run.get("error"))
        self.assertTrue(done_run["state"].get("script_content_id"))

    def test_04c_video_script_without_a_saved_script_still_fails_on_the_step_limit(self):
        def fake_start_session(agent_id, user_message, **kwargs):
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            return db.update_agent_session(sess["id"], status="failed", error="too many steps")

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start("video_script", {"topic": "Cero command safety", "duration_s": 15, "aspect": "9:16"})
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") in ("done", "failed")))
        self.assertEqual(workflows.get(run["id"])["status"], "failed")

    def test_05_crash_resume_mid_run_does_not_duplicate_step_one(self):
        """Simulate crash mid-run (status=running, current_step=2) -> recover_stale_runs resumes without redoing step 1."""
        # Pre-create step-1 draft and a workflow run frozen at status='running', current_step=2 (review_drafts)
        existing_sess = _create_fake_session("writer", title="Post drafts")
        ctx = ToolContext(session_id=existing_sess["id"], agent_id="writer")
        draft_res = tools.run_tool(
            "write_draft",
            ctx,
            {
                "type": "post",
                "platform": "x",
                "title": "Pre-crash Draft",
                "body": "Existing step 1 draft.",
            },
            ["write_draft"],
        )
        draft_id = draft_res["content_id"]

        run = db.create_workflow_run(
            {
                "id": "wf_crash_test_1",
                "kind": "post",
                "status": "running",
                "current_step": 2,
                "inputs": {"brief": "Crash test", "platforms": ["x"], "with_image": False},
                "state": {
                    "content_ids": [draft_id],
                    "session_ids": [existing_sess["id"]],
                    "step_sessions": {"write_drafts": existing_sess["id"]},
                    "draft_ids": [draft_id],
                },
            }
        )

        called_agents = []

        def fake_start_session(agent_id, user_message, **kwargs):
            called_agents.append(agent_id)
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            r_ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "reviewer":
                tools.run_tool(
                    "write_review",
                    r_ctx,
                    {"content_id": draft_id, "verdict": "ok", "notes": "Recovered cleanly."},
                    ["write_review"],
                )
            return sess

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            recovered = workflows.recover_stale_runs(start_worker=True)
            self.assertEqual([r["id"] for r in recovered], [run["id"]])
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

        final_run = workflows.get(run["id"])
        self.assertEqual(final_run["status"], "done")
        # Step 1 (writer) was NOT re-run; only reviewer ran.
        self.assertEqual(called_agents, ["reviewer"])
        self.assertEqual(final_run["state"]["draft_ids"], [draft_id])
        self.assertEqual(db.get_content_item(draft_id)["status"], "ready")

    def test_06_agent_waiting_user_inside_step_resumes_workflow_on_answer(self):
        """Agent ask_user pauses workflow -> answering session resumes and completes workflow."""
        call_num = {"n": 0}

        def fake_chat(messages, tools=None, agent_role="", **_kwargs):
            call_num["n"] += 1
            if agent_role == "writer":
                if call_num["n"] == 1:
                    return {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "call_ask",
                                "name": "ask_user",
                                "arguments": {"question": "Include CLI flag example?", "options": ["Yes", "No"]},
                            }
                        ],
                    }
                if call_num["n"] == 2:
                    return {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "call_draft",
                                "name": "write_draft",
                                "arguments": {
                                    "type": "post",
                                    "platform": "x",
                                    "title": "X Post",
                                    "body": "Cero asks before running `rm` or `git push`. #CLI",
                                },
                            }
                        ],
                    }
                return {"text": "Draft created.", "tool_calls": []}

            if agent_role == "reviewer":
                # Extract content_id from prompt
                user_msg = messages[-1]["content"] if messages else ""
                m_cid = re.search(r"(cnt_[a-zA-Z0-9_]+)", str(user_msg))
                if call_num["n"] == 4 and m_cid:
                    return {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "call_rev",
                                "name": "write_review",
                                "arguments": {
                                    "content_id": m_cid.group(1),
                                    "verdict": "ok",
                                    "notes": "Looks good.",
                                },
                            }
                        ],
                    }
                return {"text": "Reviewed.", "tool_calls": []}

            return {"text": "Done.", "tool_calls": []}

        with patch.object(ai_gateway, "chat_with_tools", side_effect=fake_chat):
            run = workflows.start("post", {"brief": "Cero safety", "platforms": ["x"], "with_image": False})
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "waiting_user"))

            waiting_run = workflows.get(run["id"])
            waiting_sid = waiting_run["state"].get("waiting_session_id")
            self.assertTrue(waiting_sid)
            self.assertEqual(agent_runtime.get_session(waiting_sid)["status"], "waiting_user")

            # Answer the waiting agent session -> triggers on_session_finished -> resumes workflow!
            agent_runtime.resume_session(waiting_sid, "Yes, include a short flag example.")
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

        done_run = workflows.get(run["id"])
        self.assertEqual(done_run["status"], "done")
        self.assertEqual(len(done_run["state"]["draft_ids"]), 1)

    def _run_one_wait_step(self, step):
        """Run a one-step workflow on this thread, without the job worker."""
        run = workflows.start("post", {"brief": "Cero", "platforms": ["x"]}, start_worker=False)

        class OneStep:
            steps = [("wait", step)]

        with patch.dict(workflows.WORKFLOWS, {"post": OneStep}), patch.object(jobs, "start_worker"):
            workflows.execute_workflow_job({"run_id": run["id"]})
        return db.get_workflow_run(run["id"])

    def test_answer_while_waiting_job_is_still_closing_resumes(self):
        run = workflows.start("post", {"brief": "Cero", "platforms": ["x"]}, start_worker=False)
        job = db.get_next_queued_job()
        db.update_job(job["id"], status="running")
        sess = _create_fake_session("writer")
        db.update_workflow_run(run["id"], status="waiting_user", state={"waiting_session_id": sess["id"]})
        with patch.object(jobs, "start_worker"):
            workflows.on_session_finished(sess)
        self.assertIsNotNone(db.get_next_queued_job(), "resume was dropped")

    def test_session_finished_before_wait_was_saved_resumes(self):
        sess = _create_fake_session("writer")
        run = self._run_one_wait_step(lambda run, state: workflows.StepResult("wait_user", session_id=sess["id"]))
        self.assertEqual(run["status"], "running")

    def test_gate_answered_before_wait_was_saved_resumes(self):
        def step(run, state):
            gate = db.create_gate(run["id"], step=0, question="Post it?", options=["yes", "no"])
            workflows.answer_gate(gate["id"], "yes", start_worker=False)
            return workflows.StepResult("wait_user", gate_id=gate["id"])

        self.assertEqual(self._run_one_wait_step(step)["status"], "running")

    def test_07_cancel_workflow_stops_further_steps(self):
        """Cancelling a workflow run stops further steps from executing."""
        entered_step0 = threading.Event()
        allow_step0_finish = threading.Event()
        step_calls = []

        def fake_start_session(agent_id, user_message, **kwargs):
            step_calls.append(agent_id)
            if agent_id == "writer":
                entered_step0.set()
                allow_step0_finish.wait(timeout=2.0)
                sess = _create_fake_session(agent_id, title="cancelled")
                ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {"type": "post", "platform": "x", "title": "T", "body": "B"},
                    ["write_draft"],
                )
                return sess
            return _create_fake_session(agent_id, title="unexpected")

        with patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            run = workflows.start("post", {"brief": "Cancel test", "platforms": ["x"], "with_image": False})
            self.assertTrue(entered_step0.wait(timeout=2.0))
            cancelled = workflows.cancel(run["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            allow_step0_finish.set()
            time.sleep(0.15)

        final_run = workflows.get(run["id"])
        self.assertEqual(final_run["status"], "cancelled")
        # Reviewer step must never have been invoked
        self.assertEqual(step_calls, ["writer"])

    def test_08_mark_posted_and_http_routes_and_token_gate(self):
        """mark_posted sets url/status/activity/SSE, and workflow/content HTTP routes honor token gate."""
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        item = db.create_content_item(
            content_id="cnt_status_1",
            content_type="post",
            platform="x",
            title="Launch Post",
            body="Cero v2.2.3 is out.",
            status="draft",
        )
        self.assertEqual(item["status"], "draft")

        # HTTP token gate check
        with patch.object(server, "ENGINE_TOKEN", "tok-123"):
            unauth = _StubHandler(
                "/api/content/cnt_status_1/status",
                method="POST",
                body_dict={"status": "ready"},
            )
            unauth.do_POST()
            self.assertEqual(unauth.status_code, 401)

        with patch.object(server, "ENGINE_TOKEN", ""):
            ready_h = _StubHandler(
                "/api/content/cnt_status_1/status",
                method="POST",
                body_dict={"status": "ready"},
            )
            ready_h.do_POST()
            self.assertEqual(ready_h.status_code, 200)
            self.assertEqual(ready_h.response_json()["status"], "ready")

            posted_h = _StubHandler(
                "/api/content/cnt_status_1/status",
                method="POST",
                body_dict={
                    "status": "posted",
                    "url": "https://x.com/magnm/status/12345",
                    "platform": "x",
                },
            )
            posted_h.do_POST()
            self.assertEqual(posted_h.status_code, 200)
            posted_data = posted_h.response_json()
            self.assertEqual(posted_data["status"], "posted")
            self.assertEqual(posted_data["posted_url"], "https://x.com/magnm/status/12345")
            self.assertTrue(posted_data["posted_at"])

        act_lines = db.get_activity_lines(limit=10)
        self.assertTrue(any("mark_posted:cnt_status_1" in ln and "https://x.com/magnm/status/12345" in ln for ln in act_lines))

        ev_list = []
        while not q.empty():
            ev_list.append(q.get_nowait())
        self.assertTrue(any(e["type"] == "content_status" and e["data"]["status"] == "posted" for e in ev_list))

        # HTTP workflow routes smoke check
        def fake_start_session(agent_id, user_message, **kwargs):
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "writer":
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {"type": "post", "platform": "x", "title": "HTTP Post", "body": "Hello from HTTP workflow."},
                    ["write_draft"],
                )
            elif agent_id == "reviewer":
                m = re.search(r"cnt_[a-zA-Z0-9_]+", user_message)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": m.group(0), "verdict": "ok", "notes": "ok"},
                    ["write_review"],
                )
            return sess

        with patch.object(server, "ENGINE_TOKEN", ""), patch.object(
            agent_runtime, "start_session", side_effect=fake_start_session
        ):
            start_h = _StubHandler(
                "/api/workflows/start",
                method="POST",
                body_dict={"kind": "post", "inputs": {"brief": "HTTP test", "platforms": ["x"], "with_image": False}},
            )
            start_h.do_POST()
            self.assertEqual(start_h.status_code, 200)
            wf_id = start_h.response_json()["id"]

            self.assertTrue(_wait_until(lambda: (workflows.get(wf_id) or {}).get("status") == "done"))

            get_h = _StubHandler(f"/api/workflows/{wf_id}", method="GET")
            get_h.do_GET()
            self.assertEqual(get_h.status_code, 200)
            self.assertEqual(get_h.response_json()["status"], "done")

            list_h = _StubHandler("/api/workflows?limit=5", method="GET")
            list_h.do_GET()
            self.assertEqual(list_h.status_code, 200)
            self.assertTrue(any(r["id"] == wf_id for r in list_h.response_json()))

    def test_09_blog_section_budget_enforced_triggers_single_shortening_call(self):
        """Over-long section (>1.3x budget) triggers exactly one shortening call with tier='longform'."""
        outline_body = (
            "## TL;DR\nCero previews mutating commands.\n\n"
            "## Motivation (weight: 0.25)\nWhy approval matters.\n\n"
            "## Architecture (weight: 0.75)\nHow the classifier works."
        )
        blog_item = db.create_content_item(
            content_id="cnt_blog_budget_1",
            content_type="blog",
            platform="blog",
            title="Cero Approval Architecture",
            body=outline_body,
            notes="outline",
            product_id="cero",
            status="draft",
        )
        run = db.create_workflow_run(
            {
                "id": "wf_blog_budget_1",
                "kind": "blog",
                "status": "running",
                "current_step": 2,
                "inputs": {
                    "topic": "How Cero decides approval",
                    "product_id": "cero",
                    "length_words": "400-800",  # midpoint = 600 -> budgets 150 and 450
                },
                "state": {
                    "content_ids": [blog_item["id"]],
                    "session_ids": [],
                    "step_sessions": {},
                    "outline_content_id": blog_item["id"],
                    "outline_approved": True,
                },
            }
        )

        gateway_calls = []
        seo_calls = []

        def fake_generate_meta(**kwargs):
            if "Return ONLY a JSON object with exactly these keys" in kwargs["user_prompt"]:
                seo_calls.append(kwargs)
                return {
                    "text": json.dumps({
                        "seo_title": "How Cero Decides Command Approval",
                        "meta_description": "How Cero asks before running commands that change your system.",
                        "slug": "how-cero-decides-command-approval",
                    }),
                    "provider": "nvidia_nim",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                }
            gateway_calls.append(kwargs)
            prompt = kwargs["user_prompt"]
            if "Tighten to 150 words, keep facts unchanged" in prompt:
                return {
                    "text": "## Motivation\n" + ("concise " * 120) + "end.",
                    "provider": "nvidia_nim",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                }
            if "Draft ONLY section 1/2" in prompt:
                # 260 words > 1.3 * 150 (195) -> must trigger one shortening call
                return {
                    "text": "## Motivation\n" + ("verbose " * 260),
                    "provider": "nvidia_nim",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                }
            # Section 2/2: within budget (450)
            return {
                "text": "## Architecture\n" + ("detail " * 200),
                "provider": "nvidia_nim",
                "model_used": "minimaxai/minimax-m3.1-flash",
            }

        def fake_start_session(agent_id, user_message, **kwargs):
            sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": m_cid.group(1), "verdict": "ok", "notes": "ok"},
                    ["write_review"],
                )
            elif agent_id == "writer" and "SEO pass" in user_message:
                item = db.get_content_item(blog_item["id"])
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "blog",
                        "platform": "blog",
                        "title": item["title"],
                        "body": item["body"],
                        "content_id": blog_item["id"],
                        "notes": json.dumps({"seo_title": "Cero Approval", "meta_description": "Desc", "slug": "cero-approval"}),
                    },
                    ["write_draft"],
                )
            elif agent_id == "writer" and "Repurpose blog" in user_message:
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {"type": "post", "platform": "linkedin", "title": "LI", "body": "Cero post."},
                    ["write_draft"],
                )
            return sess

        with patch.object(core_tools, "read_product_facts", wraps=core_tools.read_product_facts) as spy_facts, patch.object(
            ai_gateway, "generate_completion_with_meta", side_effect=fake_generate_meta
        ), patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            workflows.execute_workflow_job({"run_id": run["id"]})

        done_run = workflows.get(run["id"])
        self.assertEqual(done_run["status"], "done")
        # Grounding fetched exactly ONCE before drafting
        self.assertEqual(spy_facts.call_count, 1)
        # 2 sections: section 1 drafted + 1 shortening call; section 2 drafted (no shortening) -> 3 calls total
        self.assertEqual(len(gateway_calls), 3)
        self.assertTrue(all(c["tier"] == "longform" for c in gateway_calls))
        self.assertIn("Word budget: 150 words", gateway_calls[0]["user_prompt"])
        self.assertIn("Tighten to 150 words, keep facts unchanged", gateway_calls[1]["user_prompt"])
        self.assertIn("Word budget: 450 words", gateway_calls[2]["user_prompt"])

        final_item = db.get_content_item(blog_item["id"])
        self.assertIn("concise", final_item["body"])
        self.assertNotIn("verbose", final_item["body"])
        self.assertRegex(done_run["state"]["progress"], r"^section 2/2, \d+ words so far$")

    def test_10_blog_crash_resume_skips_completed_sections_and_skips_fix_when_ok(self):
        """Crash resume skips completed sections, reuses cached grounding, and skips fix_blog on verdict='ok'."""
        outline_body = (
            "## TL;DR\nCero previews mutating commands.\n\n"
            "## Intro\nMotivation.\n\n"
            "## Classifier\nHow it decides.\n\n"
            "## Wrapup\nClosing notes."
        )
        blog_item = db.create_content_item(
            content_id="cnt_blog_resume_1",
            content_type="blog",
            platform="blog",
            title="Cero Approval Resume",
            body=outline_body,
            notes="outline",
            product_id="cero",
            status="draft",
        )
        # Simulate section 0 already written as v2 before crash
        db.add_content_version(
            content_id=blog_item["id"],
            body="## TL;DR\nCero previews mutating commands.\n\n## Intro\nExisting intro body.\n\nIntro closing paragraph.",
            notes="section 1/3: Intro",
            title="Cero Approval Resume",
            platform="blog",
            product_id="cero",
        )

        run = db.create_workflow_run(
            {
                "id": "wf_blog_resume_1",
                "kind": "blog",
                "status": "running",
                "current_step": 2,
                "inputs": {"topic": "How Cero decides approval", "product_id": "cero", "length_words": "1200-2000"},
                "state": {
                    "content_ids": [blog_item["id"]],
                    "session_ids": [],
                    "step_sessions": {},
                    "outline_content_id": blog_item["id"],
                    "outline_approved": True,
                    "outline_body": outline_body,
                    "sections": ["Intro", "Classifier", "Wrapup"],
                    "completed_sections": [0],
                    "grounding": {
                        "product_facts": "Cero verified facts.",
                        "product_rules": "Cero show everything.",
                        "repo_changes": {"repositories": []},
                        "research_notes": [],
                    },
                },
            }
        )

        gateway_calls = []
        seo_calls = []
        session_titles = []

        def fake_generate_meta(**kwargs):
            if "Return ONLY a JSON object with exactly these keys" in kwargs["user_prompt"]:
                seo_calls.append(kwargs)
                return {
                    "text": json.dumps({
                        "seo_title": "How Cero Decides Command Approval",
                        "meta_description": "How Cero asks before running commands that change your system.",
                        "slug": "how-cero-decides-command-approval",
                    }),
                    "provider": "nvidia_nim",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                }
            gateway_calls.append(kwargs)
            m_sec = re.search(r"Draft ONLY section (\d+)/\d+ \('([^']+)'\)", kwargs["user_prompt"])
            sec_name = m_sec.group(2)
            return {
                "text": f"## {sec_name}\nResumed content for {sec_name}.",
                "provider": "nvidia_nim",
                "model_used": "minimaxai/minimax-m3.1-flash",
            }

        def fake_start_session(agent_id, user_message, **kwargs):
            title = kwargs.get("title") or ""
            session_titles.append((agent_id, title))
            sess = _create_fake_session(agent_id, title=title)
            ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
            if agent_id == "reviewer":
                m_cid = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
                tools.run_tool(
                    "write_review",
                    ctx,
                    {"content_id": m_cid.group(1), "verdict": "ok", "notes": "ok"},
                    ["write_review"],
                )
            elif agent_id == "writer" and "SEO pass" in user_message:
                item = db.get_content_item(blog_item["id"])
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {
                        "type": "blog",
                        "platform": "blog",
                        "title": item["title"],
                        "body": item["body"],
                        "content_id": blog_item["id"],
                        "notes": json.dumps({"seo_title": "Cero Resume", "meta_description": "Desc", "slug": "cero-resume"}),
                    },
                    ["write_draft"],
                )
            elif agent_id == "writer" and "Repurpose blog" in user_message:
                tools.run_tool(
                    "write_draft",
                    ctx,
                    {"type": "post", "platform": "x", "title": "X", "body": "Cero X post."},
                    ["write_draft"],
                )
            return sess

        with patch.object(core_tools, "read_product_facts", side_effect=AssertionError("should reuse cached grounding")), patch.object(
            ai_gateway, "generate_completion_with_meta", side_effect=fake_generate_meta
        ), patch.object(agent_runtime, "start_session", side_effect=fake_start_session):
            recovered = workflows.recover_stale_runs(start_worker=True)
            self.assertEqual([r["id"] for r in recovered], [run["id"]])
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") == "done"))

        done_run = workflows.get(run["id"])
        self.assertEqual(done_run["status"], "done")
        # Section 0 was skipped; only sections 1 (Classifier) and 2 (Wrapup) called the gateway
        self.assertEqual(len(gateway_calls), 2)
        self.assertIn("Draft ONLY section 2/3 ('Classifier')", gateway_calls[0]["user_prompt"])
        self.assertIn("Intro closing paragraph.", gateway_calls[0]["user_prompt"])
        self.assertIn("Draft ONLY section 3/3 ('Wrapup')", gateway_calls[1]["user_prompt"])
        self.assertEqual(done_run["state"]["completed_sections"], [0, 1, 2])
        # fix_blog did NOT run because verdict was 'ok'
        self.assertFalse(any(title == "Fix blog" for _, title in session_titles))

        final_item = db.get_content_item(blog_item["id"])
        self.assertIn("Existing intro body.", final_item["body"])
        self.assertIn("## Classifier", final_item["body"])
        self.assertIn("## Wrapup", final_item["body"])

    def test_11_seo_and_fix_never_lose_article_text(self):
        """Regression (live run 2026-11-19): the SEO step re-saved a truncated body, dropping ~300
        words. SEO must keep the full DB body; fix_blog must reject a reply that lost content."""
        from tools import content as content_tools

        long_body = "\n\n".join(f"## Section {i}\n" + ("word " * 300) for i in range(6))
        created = content_tools.write_draft(
            None, type="blog", platform="blog", title="Long blog", body=long_body, product_id="cero"
        )
        blog_id = created["content_id"]
        wf = workflows.BlogWorkflow()
        run = {"id": "wf_test_regression", "inputs": {"topic": "Cero approvals", "product_id": "cero"}}

        def fake_seo(**kwargs):
            return {"text": json.dumps({"seo_title": "T", "meta_description": "D", "slug": "t"}),
                    "provider": "nvidia_nim", "model_used": "m"}

        state = {"outline_content_id": blog_id}
        with patch.object(workflows, "_persist_state", lambda *a, **k: None), \
                patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake_seo):
            wf.step_seo_pass(run, state)
        after_seo = db.get_content_item(blog_id)
        self.assertEqual(after_seo["body"], long_body)
        self.assertEqual(json.loads(after_seo["notes"])["slug"], "t")

        def fake_truncated_fix(**kwargs):
            return {"text": "## Section 0\nshort", "provider": "nvidia_nim", "model_used": "m"}

        state = {"outline_content_id": blog_id, "blog_review": {"verdict": "warn", "notes": "fix x"}}
        with patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake_truncated_fix):
            wf.step_fix_blog(run, state)
        self.assertEqual(db.get_content_item(blog_id)["body"], long_body)
        self.assertIn("rejected", state["blog_fix_result"])


if __name__ == "__main__":
    unittest.main()
