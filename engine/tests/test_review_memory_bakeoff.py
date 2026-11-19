"""Tests for deterministic auto_review, agent memory/feedback, and model bake-off (T2.5)."""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
for p in (ENGINE_DIR, SCRIPTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import agents
import ai_gateway
import bakeoff
import db
import key_pool
import model_registry
import paths
import review
import server
import tools
from tools import ToolContext


class _FakeHTTPResponse:
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
    def __init__(self, path, method="GET", body_dict=None, headers=None):
        self.path = path
        self.command = method
        raw = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        if body_dict is not None and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(raw))
        self.headers = hdrs
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


class ReviewMemoryBakeoffTests(unittest.TestCase):
    """Test suite for Task T2.5 (auto_review, memory/feedback, and bake-off)."""

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

    def test_01_auto_review_all_checks(self):
        """auto_review catches emoji, hype words, limits, hooks, hashtags, unapproved links, alt text, and blog rules."""
        # Clean approved fact source_url
        db.create_product_fact(
            product_id="cero",
            text="Cero runs in a real shell.",
            source_url="https://docs.example.com/cero-shell",
            source_kind="manual",
            status="approved",
        )
        # Approved link in research_items and research_notes
        db.insert_research_items([{"url": "https://news.ycombinator.com/item?id=123", "title": "HN", "source": "hn"}])
        db.save_research_note("n1", "Note", "See https://blog.example.org/post", url="https://blog.example.org/note")

        # 1. Emoji + hype word + X over 280 + too many hashtags + unapproved link
        bad_x = {
            "type": "post",
            "platform": "x",
            "title": "Launch",
            "body": (
                "Cero is a revolutionary tool \U0001F680 "
                + ("a" * 260)
                + " https://unapproved.example.net/spam #one #two #three"
            ),
            "files": [{"kind": "image", "path": "outputs/img.png"}],
            "notes": "",
        }
        res_x = review.auto_review(bad_x)
        self.assertEqual(res_x["verdict"], "warn")
        codes_x = {f["code"] for f in res_x["findings"]}
        self.assertIn("emoji_present", codes_x)
        self.assertIn("hype_word", codes_x)
        self.assertIn("platform_max_chars", codes_x)
        self.assertIn("too_many_hashtags", codes_x)
        self.assertIn("unapproved_link", codes_x)
        self.assertIn("missing_alt_text", codes_x)
        self.assertTrue(any("link not from an approved source" in f["message"] for f in res_x["findings"]))

        # 2. Approved links pass without unapproved_link warning
        ok_x = {
            "type": "post",
            "platform": "x",
            "title": "Approved links",
            "body": (
                "Cero runs in a real shell: https://docs.example.com/cero-shell "
                "and https://github.com/NetPranav/Cero-Terminal #CLI"
            ),
        }
        res_ok_x = review.auto_review(ok_x)
        self.assertEqual(res_ok_x["verdict"], "ok")
        self.assertEqual(res_ok_x["findings"], [])

        # 3. LinkedIn hook > 210 chars and Instagram hook > 125 chars
        li_long_hook = {
            "type": "post",
            "platform": "linkedin",
            "title": "Long hook",
            "body": ("w" * 220) + "\n\nSecond paragraph. #DevTools #CLI #Rust",
        }
        res_li = review.auto_review(li_long_hook)
        self.assertEqual(res_li["verdict"], "warn")
        self.assertIn("hook_too_long", {f["code"] for f in res_li["findings"]})

        ig_long_hook = {
            "type": "post",
            "platform": "instagram",
            "title": "IG long hook",
            "body": ("h" * 130) + "\n\nDetails #CLI",
        }
        res_ig = review.auto_review(ig_long_hook)
        self.assertEqual(res_ig["verdict"], "warn")
        self.assertIn("hook_too_long", {f["code"] for f in res_ig["findings"]})

        # 4. X thread parts <= 280 pass even when total > 280
        good_thread = {
            "type": "thread",
            "platform": "x",
            "title": "Thread",
            "body": ("1/ " + "a" * 180) + "\n\n" + ("2/ " + "b" * 180),
        }
        self.assertEqual(review.auto_review(good_thread)["verdict"], "ok")

        # 5. Blog missing TL;DR and < 3 '##' headings
        bad_blog = {
            "type": "blog",
            "platform": "blog",
            "title": "Short blog",
            "body": "## Intro\nHello.\n\n## Details\nWorld.",
        }
        res_blog = review.auto_review(bad_blog)
        self.assertEqual(res_blog["verdict"], "warn")
        blog_codes = {f["code"] for f in res_blog["findings"]}
        self.assertIn("blog_missing_tldr", blog_codes)
        self.assertIn("blog_few_headings", blog_codes)

        # 6. Product rules check (LazyLayout feature description)
        lazy_item = {
            "type": "post",
            "platform": "x",
            "title": "Teaser",
            "body": "LazyLayout features include a keyframe timeline editor.",
        }
        res_lazy = review.auto_review(lazy_item)
        self.assertEqual(res_lazy["verdict"], "warn")
        self.assertIn("product_rule", {f["code"] for f in res_lazy["findings"]})

        # 7. GET /api/content/<id>/auto_review endpoint
        db.create_content_item(
            content_id="cnt_autorev_1",
            content_type="post",
            platform="x",
            title="Emoji post",
            body="Cero is great \U0001F525",
            status="draft",
        )
        with patch.object(server, "ENGINE_TOKEN", ""):
            h = _StubHandler("/api/content/cnt_autorev_1/auto_review", method="GET")
            h.do_GET()
            self.assertEqual(h.status_code, 200)
            payload = h.response_json()
            self.assertEqual(payload["verdict"], "warn")
            self.assertTrue(any(f["code"] == "emoji_present" for f in payload["findings"]))

    def test_02_write_review_merges_auto_review_warn(self):
        """write_review runs auto_review first and elevates agent 'ok' to 'warn' when auto_review warns."""
        db.create_content_item(
            content_id="cnt_merge_1",
            content_type="post",
            platform="x",
            title="Hype post",
            body="Cero is a seamless, game-changing terminal.",
            status="draft",
            created_by="writer",
        )
        rev_ctx = ToolContext(session_id="sess_rev_merge", agent_id="reviewer")
        res = tools.run_tool(
            "write_review",
            rev_ctx,
            {"content_id": "cnt_merge_1", "verdict": "ok", "notes": "Looks fine to me."},
            ["write_review"],
        )
        self.assertEqual(res["verdict"], "warn")
        self.assertEqual(res["agent_verdict"], "ok")
        self.assertEqual(res["auto_review"]["verdict"], "warn")
        self.assertTrue(any(f["code"] == "hype_word" for f in res["findings"]))
        self.assertIn("Auto-review findings:", res["notes"])

        item = db.get_content_item("cnt_merge_1")
        rev_entries = [f for f in item["files"] if f.get("kind") == "review"]
        self.assertEqual(len(rev_entries), 1)
        self.assertEqual(rev_entries[0]["verdict"], "warn")

    def test_03_memory_feedback_sharing_cmo_summary_pinning_cap_and_http(self):
        """Feedback on writer goes to writer + video-producer, CMO summary, pinned first, 12-item cap, HTTP CRUD."""
        db.create_content_item(
            content_id="cnt_fb_1",
            content_type="post",
            platform="linkedin",
            title="Cero Post",
            body="Cero runs commands in a real shell.",
            status="draft",
            created_by="writer",
        )

        with patch.object(server, "ENGINE_TOKEN", ""):
            fb_h = _StubHandler(
                "/api/feedback",
                method="POST",
                body_dict={"content_id": "cnt_fb_1", "text": "Lead with the approval prompt screenshot."},
            )
            fb_h.do_POST()
            self.assertEqual(fb_h.status_code, 200)
            fb_data = fb_h.response_json()
            self.assertTrue(fb_data["success"])
            self.assertEqual(fb_data["memory"]["agent_id"], "writer")
            self.assertEqual(fb_data["memory"]["source"], "feedback")

        # Activity line logged
        act_lines = db.get_activity_lines(limit=10)
        self.assertTrue(any("feedback:cnt_fb_1" in ln and "writer" in ln for ln in act_lines))

        # Shared content memory: writer and video-producer both see writer feedback
        writer_mem = agents.memory_block("writer")
        vid_mem = agents.memory_block("video-producer")
        self.assertIn("Things the user told you before:", writer_mem)
        self.assertIn("Lead with the approval prompt screenshot.", writer_mem)
        self.assertIn("Lead with the approval prompt screenshot.", vid_mem)

        # Prompt rule present in Tool rules
        sp = agents.build_system_prompt("writer")
        self.assertIn("Memory notes are the user's preferences. Follow them unless they conflict with product rules or facts.", sp)
        self.assertIn("Lead with the approval prompt screenshot.", sp)

        # CMO summary sees newest 5 writer + designer feedback
        for i in range(6):
            db.create_agent_memory(
                agent_id="designer" if i % 2 == 0 else "writer",
                text=f"Team feedback note {i}",
                source="feedback",
                created_at=f"2026-11-19T0{i}:00:00+00:00",
            )
        cmo_mem = agents.memory_block("cmo")
        self.assertIn("Things the user told you before:", cmo_mem)
        cmo_bullets = [ln for ln in cmo_mem.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(cmo_bullets), 5)

        # Pinned items come first + 12-item cap + <= 200 chars per bullet
        for i in range(15):
            db.create_agent_memory(
                agent_id="analyst",
                text=f"Analyst note {i:02d} " + ("x" * 240),
                source="manual",
                pinned=(i == 0),
                created_at=f"2026-11-19T10:{i:02d}:00+00:00",
            )
        ana_mem = agents.memory_block("analyst")
        ana_bullets = [ln for ln in ana_mem.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(ana_bullets), 12)
        self.assertTrue(ana_bullets[0].startswith("- Analyst note 00"))
        self.assertTrue(all(len(ln) <= 200 for ln in ana_bullets))

        # HTTP GET/POST/DELETE /api/memory
        with patch.object(server, "ENGINE_TOKEN", ""):
            post_h = _StubHandler(
                "/api/memory",
                method="POST",
                body_dict={"agent_id": "reviewer", "text": "Always verify CLI flags."},
            )
            post_h.do_POST()
            self.assertEqual(post_h.status_code, 200)
            mem_id = post_h.response_json()["id"]

            get_h = _StubHandler("/api/memory?agent_id=reviewer", method="GET")
            get_h.do_GET()
            self.assertEqual(get_h.status_code, 200)
            self.assertTrue(any(m["id"] == mem_id for m in get_h.response_json()))

            upd_h = _StubHandler(
                f"/api/memory/{mem_id}",
                method="POST",
                body_dict={"pinned": True, "text": "Always verify exact CLI flags."},
            )
            upd_h.do_POST()
            self.assertEqual(upd_h.status_code, 200)
            self.assertTrue(upd_h.response_json()["pinned"])
            self.assertEqual(upd_h.response_json()["text"], "Always verify exact CLI flags.")

            del_h = _StubHandler(f"/api/memory/{mem_id}", method="DELETE")
            del_h.do_DELETE()
            self.assertEqual(del_h.status_code, 200)
            self.assertIsNone(db.get_agent_memory(mem_id))

    def test_04_remember_dedupe_max_three_and_recall_permissions(self):
        """remember enforces near-duplicate refusal and max 3 per session; recall_memory works for all agents."""
        loaded = {a["id"]: set(a.get("tools") or []) for a in agents.load_agents()}
        for aid in ("cmo", "writer", "designer", "analyst"):
            self.assertIn("remember", loaded[aid])
        for aid in ("researcher", "video-producer", "reviewer"):
            self.assertNotIn("remember", loaded[aid])
        for aid in loaded:
            self.assertIn("recall_memory", loaded[aid])

        ctx = ToolContext(session_id="sess_rem_1", agent_id="writer")
        allowed = ["remember", "recall_memory"]

        r1 = tools.run_tool("remember", ctx, {"text": "Keep X posts under 220 characters."}, allowed)
        self.assertTrue(r1["saved"])

        # Near-duplicate (different case + extra whitespace) is refused and does not consume quota
        dup = tools.run_tool("remember", ctx, {"text": "  keep x   posts under 220 CHARACTERS. "}, allowed)
        self.assertIn("error", dup)

        r2 = tools.run_tool("remember", ctx, {"text": "Mention .flow files are single-file."}, allowed)
        self.assertTrue(r2["saved"])
        r3 = tools.run_tool("remember", ctx, {"text": "Avoid exclamation marks in headlines."}, allowed)
        self.assertTrue(r3["saved"])

        # 4th call in same session is refused
        r4 = tools.run_tool("remember", ctx, {"text": "Fourth note should fail."}, allowed)
        self.assertIn("error", r4)

        recalled = tools.run_tool("recall_memory", ctx, {}, allowed)
        self.assertEqual(recalled["agent_id"], "writer")
        self.assertEqual(len(recalled["items"]), 3)
        self.assertIn("Keep X posts under 220 characters.", recalled["summary"])

    def test_05_bakeoff_mocked_gateway_blind_sheet_scoring_and_http(self):
        """Bake-off writes results.json + blind sheet.md, serves blind latest API, and scores ratings."""
        models = ["model/alpha", "model/beta", "model/gamma"]
        seen_calls = []

        def fake_generate_meta(system_prompt, user_prompt, agent_role="", force_live_only=False, model=None, **_kwargs):
            seen_calls.append({"agent_role": agent_role, "model": model, "force_live_only": force_live_only})
            if model == "model/alpha":
                txt = "Cero runs commands in a real shell and asks before changing files. #CLI #DevTools #Rust"
            elif model == "model/beta":
                txt = "Cero is a revolutionary game-changing shell! \U0001F680 #CLI #DevTools #Rust"
            else:
                txt = "Cero previews commands that modify your system before executing them. #CLI #DevTools #Shell"
            return {
                "text": txt,
                "provider": "nvidia_nim",
                "model_requested": model,
                "model_used": model,
                "fallback_used": False,
            }

        with patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake_generate_meta):
            res = bakeoff.run_bakeoff("writer", models, briefs_count=3, seed=42)

        self.assertEqual(len(seen_calls), 3 * 3)
        self.assertTrue(all(c["force_live_only"] for c in seen_calls))
        self.assertTrue(os.path.isfile(res["results_path"]))
        self.assertTrue(os.path.isfile(res["sheet_path"]))

        with open(res["results_path"], "r", encoding="utf-8") as f:
            results_data = json.load(f)
        self.assertEqual(len(results_data["briefs"]), 3)
        for brief in results_data["briefs"]:
            bid = brief["id"]
            entries = results_data["outputs"][bid]
            self.assertEqual(len(entries), 3)
            self.assertEqual({e["model"] for e in entries}, set(models))
            self.assertEqual(set(results_data["key"][bid].values()), set(models))
            for e in entries:
                self.assertIn("latency_ms", e)
                self.assertIn("chars", e)
                self.assertIn("auto_review", e)
                self.assertIn("text", e)

        # Blind sheet must contain zero model names
        with open(res["sheet_path"], "r", encoding="utf-8") as f:
            sheet_text = f.read()
        for m in models:
            self.assertNotIn(m, sheet_text)

        # GET /api/bakeoff/latest hides key unless ?reveal=1
        with patch.object(server, "ENGINE_TOKEN", ""):
            blind_h = _StubHandler("/api/bakeoff/latest", method="GET")
            blind_h.do_GET()
            self.assertEqual(blind_h.status_code, 200)
            blind_json = blind_h.response_json()
            self.assertNotIn("key", blind_json)
            for entries in blind_json["outputs"].values():
                for e in entries:
                    self.assertNotIn("model", e)

            rev_h = _StubHandler("/api/bakeoff/latest?reveal=1", method="GET")
            rev_h.do_GET()
            self.assertEqual(rev_h.status_code, 200)
            self.assertIn("key", rev_h.response_json())

        # Score ratings: give model/gamma 5, model/alpha 4, model/beta 2 on every brief
        ratings = {}
        score_for_model = {"model/gamma": 5, "model/alpha": 4, "model/beta": 2}
        for bid, bkey in results_data["key"].items():
            ratings[bid] = {label: score_for_model[m] for label, m in bkey.items()}
        ratings_path = os.path.join(os.path.dirname(res["results_path"]), "ratings.json")
        with open(ratings_path, "w", encoding="utf-8") as f:
            json.dump(ratings, f)

        with open(paths.resource("config", "models.json"), "r", encoding="utf-8") as f:
            models_cfg_before = f.read()
        report = bakeoff.score_bakeoff(ratings_path)
        with open(paths.resource("config", "models.json"), "r", encoding="utf-8") as f:
            models_cfg_after = f.read()
        self.assertEqual(models_cfg_before, models_cfg_after)

        self.assertEqual(report["suggested_primary"], "model/gamma")
        self.assertEqual(report["suggested_backup"], "model/alpha")
        by_m = {r["model"]: r for r in report["ranking"]}
        self.assertEqual(by_m["model/gamma"]["avg_score"], 5.0)
        self.assertEqual(by_m["model/alpha"]["avg_score"], 4.0)
        self.assertEqual(by_m["model/beta"]["avg_score"], 2.0)
        self.assertGreater(by_m["model/beta"]["auto_review_warns"], 0)

        # CLI --score prints summary and suggestion
        import contextlib
        import importlib.util
        spec = importlib.util.spec_from_file_location("scripts_bakeoff", os.path.join(SCRIPTS_DIR, "bakeoff.py"))
        bakeoff_cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bakeoff_cli)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bakeoff_cli.main(["--score", ratings_path])
        self.assertEqual(rc, 0)
        cli_out = buf.getvalue()
        self.assertIn("Suggested primary (writer): model/gamma", cli_out)
        self.assertIn("Suggested backup (writer): model/alpha", cli_out)

    def test_06_gateway_model_override_bypasses_tier_uses_pool_and_logs_run(self):
        """generate_completion_with_meta(model=...) bypasses tier/catalog resolution but uses key pool and run logging."""
        seen = {}

        def fake_urlopen(req, timeout=60):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["auth"] = req.get_header("Authorization")
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeHTTPResponse({
                "choices": [{"message": {"content": "Grounded Cero draft."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-override-pool-key"}, clear=False), patch.object(
            model_registry, "get_catalog", side_effect=AssertionError("tier/catalog resolution should be bypassed")
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            key_pool.reset()
            meta = ai_gateway.generate_completion_with_meta(
                system_prompt="You are writer.",
                user_prompt="Write a short Cero line.",
                agent_role="writer",
                model="custom/explicit-model",
            )
            pool_stat = key_pool.status()

        self.assertEqual(meta["text"], "Grounded Cero draft.")
        self.assertEqual(meta["model_requested"], "custom/explicit-model")
        self.assertEqual(meta["model_used"], "custom/explicit-model")
        self.assertEqual(seen["body"]["model"], "custom/explicit-model")
        self.assertEqual(seen["auth"], "Bearer nvapi-override-pool-key")
        self.assertEqual(seen["timeout"], 240)  # writer tier timeout preserved

        # Key pool recorded usage and runs table logged the invocation
        self.assertTrue(any(s["ok"] >= 1 for s in pool_stat))
        runs = db.get_recent_runs(limit=5)
        self.assertTrue(any(r["model_requested"] == "custom/explicit-model" and r["ok"] for r in runs))


if __name__ == "__main__":
    unittest.main()
