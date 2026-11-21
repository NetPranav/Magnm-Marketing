"""Tests for learnings: proposal evidence/number checks, approval lifecycle, prompt injection, limits, confidentiality, routes, and mirroring."""

from datetime import datetime, timezone
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agents
import cloud_sync
import db
import learnings
import notifications
import paths
import server
import weekly_summary

IST = ZoneInfo("Asia/Kolkata")


class _StubHandler(server.CommandCenterHandler):
    def __init__(self, path, method="GET", body_dict=None, headers=None):
        self.path = path
        self.command = method
        raw = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        if body_dict is not None:
            hdrs.setdefault("Content-Length", str(len(raw)))
            hdrs.setdefault("Content-Type", "application/json")
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


class LearningsTests(unittest.TestCase):
    """Verify learnings evidence validation, number grounding, approval/reject/retire, prompt injection, routes, and mirroring."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        for sub in ("config", "state", "outputs", "agents"):
            os.makedirs(os.path.join(self.tmpdir, sub), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_token = server.ENGINE_TOKEN

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        server.ENGINE_TOKEN = ""

        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.tmpdir, "config", "product_rules.json"),
        )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "agents.json"),
            os.path.join(self.tmpdir, "config", "agents.json"),
        )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config.md"),
            os.path.join(self.tmpdir, "config.md"),
        )
        shutil.copytree(
            os.path.join(self._prev_resource_root, "agents"),
            os.path.join(self.tmpdir, "agents"),
            dirs_exist_ok=True,
        )
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

    def tearDown(self):
        db.close_connection()
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        db.set_db_path(self._prev_db_path)
        server.ENGINE_TOKEN = self._prev_token
        self._tmp_obj.cleanup()

    def _seed_two_posts(self):
        t1 = datetime(2026, 11, 17, 10, 0, tzinfo=IST).isoformat()
        t2 = datetime(2026, 11, 19, 10, 0, tzinfo=IST).isoformat()
        db.create_content_item("post_a", "post", "linkedin", "Carousel Guide", "body a", created_at=t1)
        db.mark_posted("post_a", url="https://linkedin.com/a", platform="linkedin", posted_at=t1)
        db.record_metric("met_a", "post_a", "linkedin", {"views": 1200, "likes": 45, "saves": 18}, "manual", captured_at=t1)

        db.create_content_item("post_b", "post", "linkedin", "Single Image", "body b", created_at=t2)
        db.mark_posted("post_b", url="https://linkedin.com/b", platform="linkedin", posted_at=t2)
        db.record_metric("met_b", "post_b", "linkedin", {"views": 600, "likes": 15, "saves": 6}, "manual", captured_at=t2)

    def test_01_proposal_requires_two_posts_and_drops_invented_numbers(self):
        # Fewer than 2 posts -> proposes nothing without calling AI
        t1 = datetime(2026, 11, 17, 10, 0, tzinfo=IST).isoformat()
        db.create_content_item("only_one", "post", "x", "Single Post", "body", created_at=t1)
        db.mark_posted("only_one", url="https://x.com/1", platform="x", posted_at=t1)
        db.record_metric("m_one", "only_one", "x", {"views": 100, "likes": 5}, "manual", captured_at=t1)

        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": "Summary for 1 post with 100 views and 5 likes."}) as mock_ai:
            res_one = weekly_summary.generate_summary("2026-11-16", notify=False, propose_learnings=True)
        self.assertEqual(mock_ai.call_count, 1)  # only narrative called; learnings skipped (<2 posts)
        self.assertEqual(res_one["learnings"], [])

        # Now seed 2 posts
        self._seed_two_posts()
        data = weekly_summary.build_week_data("2026-11-16")
        highlights = weekly_summary.compute_highlights(data)
        summary = db.upsert_weekly_summary("2026-11-16", data, highlights, narrative="", status="tables_only")

        ai_proposals = json.dumps([
            {
                "text": "LinkedIn Carousel Guide got 18 saves vs 6 saves on Single Image across 2 posts.",
                "evidence": [
                    {"content_id": "post_a", "metric_id": "met_a"},
                    {"content_id": "post_b", "metric_id": "met_b"},
                ],
                "applies_to": ["writer", "designer"],
            },
            {
                "text": "Invented stat: carousel posts got 9999 clicks.",
                "evidence": [
                    {"content_id": "post_a", "metric_id": "met_a"},
                    {"content_id": "post_b", "metric_id": "met_b"},
                ],
                "applies_to": ["writer"],
            },
            {
                "text": "Single evidence post only: 18 saves.",
                "evidence": [{"content_id": "post_a", "metric_id": "met_a"}],
                "applies_to": ["writer"],
            },
            {
                "text": "Bogus evidence post id.",
                "evidence": [
                    {"content_id": "post_a", "metric_id": "met_a"},
                    {"content_id": "nonexistent_post", "metric_id": "met_fake"},
                ],
                "applies_to": ["writer"],
            },
        ])

        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": ai_proposals}):
            proposed = learnings.propose_for_summary(summary, data=data, highlights=highlights, notify=True)

        # Only the first learning passes both checks
        self.assertEqual(len(proposed), 1)
        lr = proposed[0]
        self.assertEqual(lr["status"], "pending")
        self.assertEqual(lr["applies_to"], ["writer", "designer"])
        self.assertEqual(len(lr["evidence"]), 2)
        self.assertEqual(lr["evidence"][0]["title"], "Carousel Guide")

        # Notification emitted for learning_proposed
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "learning_proposed"]
        self.assertEqual(len(notifs), 1)

        # Nothing injected into agent prompts while pending
        self.assertNotIn("What has worked for us", agents.build_system_prompt("writer"))
        self.assertNotIn("Carousel Guide got 18 saves", agents.build_system_prompt("writer"))

    def test_02_approval_edit_reject_retire_manual_limits_and_confidential_filter(self):
        self._seed_two_posts()
        l1 = db.create_learning(
            "Original carousel note",
            evidence=[{"content_id": "post_a", "metric_id": "met_a"}, {"content_id": "post_b", "metric_id": "met_b"}],
            applies_to=["writer"],
            status="pending",
        )
        l2 = db.create_learning(
            "Rejected video note",
            evidence=[{"content_id": "post_a", "metric_id": "met_a"}, {"content_id": "post_b", "metric_id": "met_b"}],
            applies_to=["all"],
            status="pending",
        )

        # Edit and approve l1 via HTTP route
        h_app = _StubHandler(
            f"/api/learnings/{l1['id']}/approve",
            method="POST",
            body_dict={"text": "Prefer carousels over single images on LinkedIn."},
        )
        h_app.do_POST()
        self.assertEqual(h_app.status_code, 200)
        self.assertEqual(h_app.response_json()["learning"]["status"], "approved")

        # Reject l2 via HTTP route
        h_rej = _StubHandler(f"/api/learnings/{l2['id']}/reject", method="POST", body_dict={})
        h_rej.do_POST()
        self.assertEqual(h_rej.status_code, 200)
        self.assertEqual(h_rej.response_json()["learning"]["status"], "rejected")

        # Writer sees l1 AFTER Product rules; CMO does not see l1; rejected l2 is absent everywhere
        sp_writer = agents.build_system_prompt("writer")
        self.assertIn("## What has worked for us", sp_writer)
        self.assertIn("Prefer carousels over single images on LinkedIn.", sp_writer)
        self.assertNotIn("Rejected video note", sp_writer)
        self.assertLess(sp_writer.index("Product rules"), sp_writer.index("## What has worked for us"))

        sp_cmo = agents.build_system_prompt("cmo")
        self.assertNotIn("Prefer carousels over single images on LinkedIn.", sp_cmo)

        # Retire removes l1 from writer prompt
        h_ret = _StubHandler(f"/api/learnings/{l1['id']}/retire", method="POST", body_dict={})
        h_ret.do_POST()
        self.assertEqual(h_ret.status_code, 200)
        self.assertEqual(h_ret.response_json()["learning"]["status"], "retired")
        self.assertNotIn("Prefer carousels over single images on LinkedIn.", agents.build_system_prompt("writer"))

        # Manual learning approved immediately
        h_man = _StubHandler(
            "/api/learnings",
            method="POST",
            body_dict={"text": "Short hooks under 120 chars work best on X.", "applies_to": ["all"]},
        )
        h_man.do_POST()
        self.assertEqual(h_man.status_code, 200)
        self.assertEqual(h_man.response_json()["learning"]["status"], "approved")
        self.assertIn("Short hooks under 120 chars work best on X.", agents.build_system_prompt("cmo"))

        # Confidential learning naming LazyLayout is NEVER injected
        learnings.add_manual_learning("LazyLayout posts need dark backgrounds.", ["all"])
        self.assertNotIn("LazyLayout posts need dark backgrounds.", agents.build_system_prompt("writer"))

        # Limits: max 8 most recent approved and <= 600 chars total
        for i in range(12):
            db.create_learning(
                f"Approved pattern number {i:02d} with concise actionable advice for posts.",
                applies_to=["writer"],
                status="approved",
                created_at=f"2026-11-20T10:{i:02d}:00+00:00",
                decided_at=f"2026-11-20T10:{i:02d}:00+00:00",
            )
        block = learnings.prompt_block("writer")
        self.assertLessEqual(len(block), 600)
        bullets = [ln for ln in block.splitlines() if ln.startswith("- ")]
        self.assertLessEqual(len(bullets), 8)
        self.assertIn("Approved pattern number 11", block)

        # GET /api/learnings?status=approved
        h_get = _StubHandler("/api/learnings?status=approved", method="GET")
        h_get.do_GET()
        self.assertEqual(h_get.status_code, 200)
        self.assertTrue(all(x["status"] == "approved" for x in h_get.response_json()["learnings"]))

    def test_03_cloud_sync_mirror_mapping_and_confidential_stub(self):
        l_pub = learnings.add_manual_learning("Lead with concrete CLI examples.", ["writer"])
        l_conf = learnings.add_manual_learning("LazyLayout teaser got high saves.", ["all"])

        records = cloud_sync.build_mirror_records()
        lr_recs = [r for r in records if r["kind"] == "research_note" and (r.get("data") or {}).get("kind") == "learning"]
        self.assertEqual(len(lr_recs), 2)
        by_id = {r["local_id"]: r for r in lr_recs}

        self.assertEqual(by_id[l_pub["id"]]["title"], "Lead with concrete CLI examples.")
        self.assertEqual(by_id[l_pub["id"]]["data"]["status"], "approved")
        self.assertEqual(by_id[l_pub["id"]]["data"]["applies_to"], ["writer"])

        self.assertEqual(by_id[l_conf["id"]]["title"], "Confidential item")
        self.assertTrue(by_id[l_conf["id"]]["data"]["confidential"])
        self.assertEqual(by_id[l_conf["id"]]["data"]["kind"], "learning")


if __name__ == "__main__":
    unittest.main()
