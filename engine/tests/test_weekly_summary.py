"""Tests for weekly_summary: IST boundaries, highlights math, grounded narrative, scheduler, notifications, routes, and mirroring."""

from datetime import datetime, timedelta, timezone
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

import ai_gateway
import cloud_sync
import cmo_runtime
import db
import jobs
import notifications
import paths
import scheduler_loop
import server
import team_auth
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


class WeeklySummaryTests(unittest.TestCase):
    """Verify IST week boundaries, highlights math, narrative grounding, schedule, routes, and mirroring."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        for sub in ("config", "state", "outputs"):
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
        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)

        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

    def tearDown(self):
        db.close_connection()
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        db.set_db_path(self._prev_db_path)
        server.ENGINE_TOKEN = self._prev_token
        self._tmp_obj.cleanup()

    def test_01_ist_week_boundaries_sunday_2359_vs_monday_0000(self):
        # Week 1: Mon 2026-11-09 00:00 IST -> Sun 2026-11-15 23:59 IST
        # Week 2: Mon 2026-11-16 00:00 IST -> Sun 2026-11-22 23:59 IST
        sun_2359_ist = datetime(2026, 11, 15, 23, 59, 0, tzinfo=IST)
        mon_0000_ist = datetime(2026, 11, 16, 0, 0, 0, tzinfo=IST)
        sun_2359_w2_ist = datetime(2026, 11, 22, 23, 59, 0, tzinfo=IST)
        mon_0000_w3_ist = datetime(2026, 11, 23, 0, 0, 0, tzinfo=IST)

        db.create_content_item(
            "cnt_w1_end", "post", "linkedin", "Sunday Night Post", "body",
            created_at=sun_2359_ist.astimezone(timezone.utc).isoformat(),
        )
        db.mark_posted("cnt_w1_end", url="https://linkedin.com/1", platform="linkedin", posted_at=sun_2359_ist.isoformat())

        db.create_content_item(
            "cnt_w2_start", "post", "x", "Monday Midnight Post", "body",
            created_at=mon_0000_ist.astimezone(timezone.utc).isoformat(),
        )
        db.mark_posted("cnt_w2_start", url="https://x.com/2", platform="x", posted_at=mon_0000_ist.isoformat())

        db.create_content_item(
            "cnt_w2_end", "post", "x", "Sunday W2 End Post", "body",
            created_at=sun_2359_w2_ist.astimezone(timezone.utc).isoformat(),
        )
        db.mark_posted("cnt_w2_end", url="https://x.com/3", platform="x", posted_at=sun_2359_w2_ist.isoformat())

        db.create_content_item(
            "cnt_w3_start", "post", "linkedin", "Monday W3 Start Post", "body",
            created_at=mon_0000_w3_ist.astimezone(timezone.utc).isoformat(),
        )
        db.mark_posted("cnt_w3_start", url="https://linkedin.com/4", platform="linkedin", posted_at=mon_0000_w3_ist.isoformat())

        # Note and reminders in W2
        db.save_research_note(
            "note_w2", "HN note", "Body", source="hn",
            created_at=datetime(2026, 11, 18, 12, 0, tzinfo=IST).astimezone(timezone.utc).isoformat(),
        )
        rem_done = db.create_reminder(
            "Done reminder",
            datetime(2026, 11, 17, 10, 0, tzinfo=IST).isoformat(),
            created_at=datetime(2026, 11, 16, 10, 0, tzinfo=IST).isoformat(),
        )
        db.mark_reminder_done(rem_done["id"], done_at=datetime(2026, 11, 17, 11, 0, tzinfo=IST).isoformat())

        rem_missed = db.create_reminder(
            "Missed reminder",
            datetime(2026, 11, 19, 10, 0, tzinfo=IST).isoformat(),
            created_at=datetime(2026, 11, 16, 10, 0, tzinfo=IST).isoformat(),
        )

        # Evaluate W2 on Monday 2026-11-23 09:00 IST
        ref_now = datetime(2026, 11, 23, 9, 0, tzinfo=IST).astimezone(timezone.utc)
        data = weekly_summary.build_week_data("2026-11-16", now=ref_now)
        self.assertEqual(data["week_start"], "2026-11-16")
        self.assertEqual(data["week_end"], "2026-11-22")
        self.assertEqual([p["content_id"] for p in data["posts"]], ["cnt_w2_start", "cnt_w2_end"])
        self.assertEqual([p["content_id"] for p in data["previous_week"]["posts"]], ["cnt_w1_end"])
        self.assertEqual([d["content_id"] for d in data["drafts_created"]], ["cnt_w2_start", "cnt_w2_end"])
        self.assertEqual([n["id"] for n in data["research_notes"]], ["note_w2"])
        self.assertEqual([r["id"] for r in data["reminders"]["done"]], [rem_done["id"]])
        # Note: mark_posted auto-created day-1/day-7 results_due reminders too; rem_missed is definitely in missed
        missed_ids = {r["id"] for r in data["reminders"]["missed"]}
        self.assertIn(rem_missed["id"], missed_ids)

    def test_02_highlights_math_wow_na_top_bottom_and_missing_results(self):
        # Week 2026-11-09 (previous week): 1 LinkedIn post with 100 views, 10 likes
        t_prev = datetime(2026, 11, 10, 10, 0, tzinfo=IST).isoformat()
        db.create_content_item("p_prev", "post", "linkedin", "Prev LI", "body", created_at=t_prev)
        db.mark_posted("p_prev", url="https://linkedin.com/prev", platform="linkedin", posted_at=t_prev)
        db.record_metric("m_prev", "p_prev", "linkedin", {"views": 100, "likes": 10, "comments": 0, "shares": 0, "saves": 0}, "manual", captured_at=t_prev)

        # Week 2026-11-16 (current week):
        # 2 LinkedIn posts with metrics, 1 LinkedIn post missing results, 1 X post with metrics (no prev X data -> "n/a")
        t1 = datetime(2026, 11, 17, 10, 0, tzinfo=IST).isoformat()
        t2 = datetime(2026, 11, 18, 10, 0, tzinfo=IST).isoformat()
        t3 = datetime(2026, 11, 19, 10, 0, tzinfo=IST).isoformat()
        t4 = datetime(2026, 11, 20, 10, 0, tzinfo=IST).isoformat()

        db.create_content_item("p_li_1", "post", "linkedin", "LI Top", "body", created_at=t1)
        db.mark_posted("p_li_1", url="https://linkedin.com/1", platform="linkedin", posted_at=t1)
        # Older metric then newer metric -> latest metric should win
        db.record_metric("m_li_1_old", "p_li_1", "linkedin", {"views": 10, "likes": 1}, "manual", captured_at="2026-11-17T06:00:00Z")
        db.record_metric("m_li_1_new", "p_li_1", "linkedin", {"views": 200, "likes": 20, "comments": 4, "shares": 2, "saves": 4}, "manual", captured_at="2026-11-18T06:00:00Z")
        # engagement = 30, views = 200 -> score = 0.15

        db.create_content_item("p_li_2", "post", "linkedin", "LI Low", "body", created_at=t2)
        db.mark_posted("p_li_2", url="https://linkedin.com/2", platform="linkedin", posted_at=t2)
        db.record_metric("m_li_2", "p_li_2", "linkedin", {"views": 400, "likes": 8, "comments": 0, "shares": 0, "saves": 0}, "manual", captured_at=t2)
        # engagement = 8, views = 400 -> score = 0.02

        db.create_content_item("p_li_miss", "post", "linkedin", "LI Missing", "body", created_at=t3)
        db.mark_posted("p_li_miss", url="https://linkedin.com/miss", platform="linkedin", posted_at=t3)

        db.create_content_item("p_x_1", "post", "x", "X Mid", "body", created_at=t4)
        db.mark_posted("p_x_1", url="https://x.com/1", platform="x", posted_at=t4)
        db.record_metric("m_x_1", "p_x_1", "x", {"views": 100, "likes": 5, "comments": 1, "shares": 0, "saves": 0}, "manual", captured_at=t4)
        # engagement = 6, views = 100 -> score = 0.06

        data = weekly_summary.build_week_data("2026-11-16")
        hl = weekly_summary.compute_highlights(data)

        li = hl["platforms"]["linkedin"]
        self.assertEqual(li["posts_count"], 3)
        self.assertEqual(li["with_results_count"], 2)
        self.assertEqual(li["totals"]["views"], 600)
        self.assertEqual(li["totals"]["likes"], 28)
        self.assertEqual(li["totals"]["engagement"], 38)
        self.assertEqual(li["averages"]["views"], 300)
        self.assertEqual(li["averages"]["likes"], 14)
        # WoW: views 100 -> 600 = +500.0%, likes 10 -> 28 = +180.0%, posts 1 -> 3 = +200.0%
        self.assertEqual(li["wow_change"]["views"], 500.0)
        self.assertEqual(li["wow_change"]["likes"], 180.0)
        self.assertEqual(li["wow_change"]["posts_count"], 200.0)

        # X has no previous week data -> "n/a"
        x_row = hl["platforms"]["x"]
        self.assertEqual(x_row["wow_change"]["views"], "n/a")
        self.assertEqual(x_row["wow_change"]["posts_count"], "n/a")

        # Top = p_li_1 (0.15), Bottom = p_li_2 (0.02)
        self.assertEqual(hl["top_post"]["content_id"], "p_li_1")
        self.assertAlmostEqual(hl["top_post"]["score"], 0.15, places=4)
        self.assertEqual(hl["bottom_post"]["content_id"], "p_li_2")
        self.assertAlmostEqual(hl["bottom_post"]["score"], 0.02, places=4)

        # Missing results
        self.assertEqual([m["content_id"] for m in hl["missing_results"]], ["p_li_miss"])

    def test_03_narrative_kept_when_grounded_dropped_when_invented_and_tables_only_when_ai_unavailable(self):
        t1 = datetime(2026, 11, 17, 10, 0, tzinfo=IST).isoformat()
        db.create_content_item("p1", "post", "linkedin", "Cero CLI Tips", "body", created_at=t1)
        db.mark_posted("p1", url="https://linkedin.com/p1", platform="linkedin", posted_at=t1)
        db.record_metric("m1", "p1", "linkedin", {"views": 500, "likes": 25}, "manual", captured_at=t1)

        # 1. Grounded narrative kept (uses only numbers in data/dates/counts)
        good_narrative = (
            "During 16-22 Nov, 1 post was published on LinkedIn and reached 500 views with 25 likes. "
            "No posts are missing results this week.\n"
            "1. Keep focusing on LinkedIn where 500 views came from 1 post.\n"
            "2. Capture saves and comments alongside likes."
        )
        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": good_narrative}) as mock_ai:
            res = weekly_summary.generate_summary("2026-11-16", notify=True, propose_learnings=False)
        self.assertEqual(mock_ai.call_count, 1)
        # Prompt includes product rules
        self.assertIn("Product rules", mock_ai.call_args.kwargs["system_prompt"])
        self.assertEqual(res["summary"]["status"], "ready")
        self.assertEqual(res["summary"]["narrative"], good_narrative)

        # Notification sent for weekly_summary
        ws_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "weekly_summary"]
        self.assertEqual(len(ws_notifs), 1)
        self.assertIn("#/today/summary/", ws_notifs[0]["route"])

        # 2. Invented number -> narrative dropped with exact note, replaces same week row
        bad_narrative = "This week LinkedIn reached 9999 views and 420 clicks across 3 campaigns."
        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": bad_narrative}):
            res2 = weekly_summary.generate_summary("2026-11-16", notify=False, propose_learnings=False)
        self.assertEqual(res2["summary"]["status"], "narrative_skipped")
        self.assertEqual(res2["summary"]["narrative"], weekly_summary.NARRATIVE_SKIPPED_NOTE)
        self.assertEqual(len(db.list_weekly_summaries()), 1)

        # 3. AI unavailable -> tables_only saved
        with patch("ai_gateway.generate_completion_with_meta", side_effect=ai_gateway.AINotConnected("no key")):
            res3 = weekly_summary.generate_summary("2026-11-16", notify=False, propose_learnings=False)
        self.assertEqual(res3["summary"]["status"], "tables_only")
        self.assertEqual(res3["summary"]["narrative"], "")
        self.assertEqual(len(db.list_weekly_summaries()), 1)

    def test_04_schedule_monday_0900_ist_catchup_and_disable_flag(self):
        scheduler_loop.ensure_default_schedules()
        scheds = db.load_schedules()
        ws_sched = [s for s in scheds if s["command"] == "weekly_summary"]
        self.assertEqual(len(ws_sched), 1)
        self.assertEqual(ws_sched[0]["cron"], "0 9 * * 1")
        self.assertIn("weekly_summary", cmo_runtime.ALLOWED_JOBS)

        # Keep only weekly_summary in schedules for isolated tick check
        db.save_schedules(ws_sched)

        mon_0859 = datetime(2026, 11, 23, 8, 59, tzinfo=IST).astimezone(timezone.utc)
        mon_0900 = datetime(2026, 11, 23, 9, 0, tzinfo=IST).astimezone(timezone.utc)
        mon_0905 = datetime(2026, 11, 23, 9, 5, tzinfo=IST).astimezone(timezone.utc)
        tue_1100 = datetime(2026, 11, 24, 11, 0, tzinfo=IST).astimezone(timezone.utc)
        wed_1100 = datetime(2026, 11, 25, 11, 0, tzinfo=IST).astimezone(timezone.utc)

        # Monday 08:59 IST -> not due
        self.assertEqual(scheduler_loop.tick(now=mon_0859), [])
        # Monday 09:00 IST -> fires once
        enq1 = scheduler_loop.tick(now=mon_0900)
        self.assertEqual([j["name"] for j in enq1], ["weekly_summary"])
        # Monday 09:05 IST -> does not fire again
        self.assertEqual(scheduler_loop.tick(now=mon_0905), [])

        # Reset last_run to simulate missed Monday, then run catch-up on Tuesday 11:00 IST
        scheds = db.load_schedules()
        scheds[0]["last_run"] = None
        db.save_schedules(scheds)
        # Clear active job first
        db.cancel_job_record(enq1[0]["id"], mon_0905.isoformat())

        with patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "0"}):
            caught = scheduler_loop.check_missed_weekly_summary(now=tue_1100)
            self.assertEqual([j["name"] for j in caught], ["weekly_summary"])
            # Wednesday does not re-fire
            db.cancel_job_record(caught[0]["id"], tue_1100.isoformat())
            self.assertEqual(scheduler_loop.check_missed_weekly_summary(now=wed_1100), [])

        # Respects MAGNM_DISABLE_SCHEDULER=1
        with patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "1"}):
            self.assertIsNone(scheduler_loop.start())

    def test_05_routes_and_cloud_sync_mirroring_including_confidential(self):
        t1 = datetime(2026, 11, 18, 12, 0, tzinfo=IST).isoformat()
        db.create_content_item("p_cero", "post", "x", "Cero Speedup", "body", product_id="cero", created_at=t1)
        db.mark_posted("p_cero", url="https://x.com/cero", platform="x", posted_at=t1)
        db.record_metric("m_cero", "p_cero", "x", {"views": 200, "likes": 20}, "manual", captured_at=t1)

        with patch("ai_gateway.generate_completion_with_meta", side_effect=ai_gateway.AINotConnected("offline")):
            h_gen = _StubHandler(
                "/api/summaries/generate",
                method="POST",
                body_dict={"week_start": "2026-11-16"},
            )
            h_gen.do_POST()
        self.assertEqual(h_gen.status_code, 200)
        summary = h_gen.response_json()["summary"]
        sid = summary["id"]

        h_list = _StubHandler("/api/summaries", method="GET")
        h_list.do_GET()
        self.assertEqual(h_list.status_code, 200)
        self.assertEqual(len(h_list.response_json()["summaries"]), 1)

        h_get = _StubHandler(f"/api/summaries/{sid}", method="GET")
        h_get.do_GET()
        self.assertEqual(h_get.status_code, 200)
        self.assertEqual(h_get.response_json()["summary"]["week_start"], "2026-11-16")

        h_404 = _StubHandler("/api/summaries/wsum_missing", method="GET")
        h_404.do_GET()
        self.assertEqual(h_404.status_code, 404)

        # Mirror record uses brief with data.kind = "weekly_summary"
        records = cloud_sync.build_mirror_records()
        briefs = [r for r in records if r["kind"] == "brief" and (r.get("data") or {}).get("kind") == "weekly_summary"]
        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0]["title"], "Weekly summary 16-22 Nov")
        self.assertEqual(briefs[0]["local_id"], sid)
        self.assertEqual(briefs[0]["data"]["week_start"], "2026-11-16")

        # Confidential week summary (mentions LazyLayout) is stubbed
        t2 = datetime(2026, 11, 11, 12, 0, tzinfo=IST).isoformat()
        db.create_content_item("p_lazy", "post", "x", "LazyLayout Teaser", "body", product_id="lazylayout", created_at=t2)
        db.mark_posted("p_lazy", url="https://x.com/lazy", platform="x", posted_at=t2)
        with patch("ai_gateway.generate_completion_with_meta", side_effect=ai_gateway.AINotConnected("offline")):
            res_conf = weekly_summary.generate_summary("2026-11-09", notify=False, propose_learnings=False)
        records2 = cloud_sync.build_mirror_records()
        conf_brief = [r for r in records2 if r["kind"] == "brief" and r["local_id"] == res_conf["summary"]["id"]][0]
        self.assertEqual(conf_brief["title"], "Confidential item")
        self.assertTrue(conf_brief["data"]["confidential"])
        self.assertEqual(conf_brief["data"]["kind"], "weekly_summary")


if __name__ == "__main__":
    unittest.main()
