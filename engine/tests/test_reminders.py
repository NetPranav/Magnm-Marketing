"""Tests for reminders table, auto results reminders, firing, IST parsing, agent tools, HTTP routes, and cloud sync."""

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

import agents
import cloud_sync
import db
import notifications
import paths
import reminders
import scheduler_loop
import server
import tools
from tools import ToolContext

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


class RemindersTests(unittest.TestCase):
    """Verify reminders lifecycle, IST parsing, scheduler firing, tools, routes, and mirroring."""

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
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

    def tearDown(self):
        db.close_connection()
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        db.set_db_path(self._prev_db_path)
        server.ENGINE_TOKEN = self._prev_token
        self._tmp_obj.cleanup()

    def test_01_posted_creates_two_reminders_idempotent_and_removed_on_unpost(self):
        db.create_content_item(
            content_id="cnt_post_1",
            content_type="post",
            platform="linkedin",
            title="Cero Release Notes",
            body="Now live.",
            status="ready",
        )
        posted_iso = "2026-11-20T10:00:00Z"
        db.mark_posted("cnt_post_1", url="https://linkedin.com/posts/1", platform="linkedin", posted_at=posted_iso)

        open_rems = db.list_reminders(status="open")
        results_rems = [r for r in open_rems if r["content_id"] == "cnt_post_1"]
        self.assertEqual(len(results_rems), 2)
        self.assertEqual(results_rems[0]["kind"], "results_due")
        self.assertEqual(results_rems[0]["title"], "Add results for Cero Release Notes (day 1)")
        self.assertEqual(results_rems[1]["title"], "Add results for Cero Release Notes (day 7)")

        due1 = datetime.fromisoformat(results_rems[0]["due_at"])
        due7 = datetime.fromisoformat(results_rems[1]["due_at"])
        posted_dt = datetime.fromisoformat("2026-11-20T10:00:00+00:00")
        self.assertEqual(due1, posted_dt + timedelta(hours=24))
        self.assertEqual(due7, posted_dt + timedelta(days=7))

        # Idempotent if marked posted again
        db.mark_posted("cnt_post_1", url="https://linkedin.com/posts/1-edit", platform="linkedin", posted_at=posted_iso)
        self.assertEqual(len([r for r in db.list_reminders(status="open") if r["content_id"] == "cnt_post_1"]), 2)

        # Un-posting via status route removes pending results_due reminders
        h_unpost = _StubHandler(
            "/api/content/cnt_post_1/status",
            method="POST",
            body_dict={"status": "ready"},
        )
        h_unpost.do_POST()
        self.assertEqual(h_unpost.status_code, 200)
        self.assertEqual(h_unpost.response_json()["status"], "ready")
        self.assertEqual(len([r for r in db.list_reminders(status="all") if r["content_id"] == "cnt_post_1"]), 0)

    def test_02_firing_marks_fired_once_respects_done_snooze_and_flood_guard(self):
        t0 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)
        r_due = db.create_reminder("Post update", (t0 - timedelta(minutes=5)).isoformat())
        r_done = db.create_reminder("Already done", (t0 - timedelta(minutes=5)).isoformat())
        db.mark_reminder_done(r_done["id"])
        r_snoozed = db.create_reminder("Snoozed item", (t0 - timedelta(minutes=5)).isoformat())
        db.snooze_reminder(r_snoozed["id"], (t0 + timedelta(minutes=30)).isoformat())
        r_future = db.create_reminder("Future item", (t0 + timedelta(hours=2)).isoformat())

        fired = scheduler_loop.tick(now=t0)
        self.assertEqual(fired, [])  # no scheduled jobs in empty schedules or returns enqueued jobs

        # Check notification created only for r_due
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "reminder"]
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["title"], "Post update")
        self.assertEqual(notifs[0]["route"], "#/today")

        self.assertIsNotNone(db.get_reminder(r_due["id"])["fired_at"])
        self.assertIsNone(db.get_reminder(r_done["id"])["fired_at"])
        self.assertIsNone(db.get_reminder(r_snoozed["id"])["fired_at"])
        self.assertIsNone(db.get_reminder(r_future["id"])["fired_at"])

        # Second check at t0+1min does not fire r_due again
        reminders.check_due_reminders(now=t0 + timedelta(minutes=1))
        notifs2 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "reminder"]
        self.assertEqual(len(notifs2), 1)

        # Snooze expires at t0+31min -> fires once
        reminders.check_due_reminders(now=t0 + timedelta(minutes=31))
        notifs3 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "reminder"]
        self.assertEqual(len(notifs3), 2)
        self.assertEqual(notifs3[0]["title"], "Snoozed item")

        # Flood guard: >5 overdue at once emits 1 summary notification
        db.mark_reminder_done(r_due["id"])
        db.mark_reminder_done(r_snoozed["id"])
        db.mark_reminder_done(r_future["id"])
        for i in range(7):
            db.create_reminder(f"Overdue {i}", (t0 - timedelta(hours=1)).isoformat())
        reminders.check_due_reminders(now=t0 + timedelta(minutes=32))
        notifs4 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "reminder"]
        self.assertEqual(len(notifs4), 3)
        self.assertEqual(notifs4[0]["title"], "You have 7 overdue reminders")

        # Re-checking does not flood again because all 7 have fired_at set
        reminders.check_due_reminders(now=t0 + timedelta(minutes=33))
        notifs5 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "reminder"]
        self.assertEqual(len(notifs5), 3)

    def test_03_ist_phrase_parsing_past_refused_and_agent_tools(self):
        # Fixed reference: 2026-11-22 10:00 IST (Sunday) = 2026-11-22 04:30 UTC
        ref_utc = datetime(2026, 11, 22, 10, 0, 0, tzinfo=IST).astimezone(timezone.utc)

        # "in 2 hours"
        dt1 = reminders.parse_due_at("in 2 hours", now=ref_utc)
        self.assertEqual(dt1, ref_utc + timedelta(hours=2))

        # "tomorrow 10am" IST -> 2026-11-23 10:00 IST = 04:30 UTC
        dt2 = reminders.parse_due_at("tomorrow 10am", now=ref_utc)
        self.assertEqual(dt2.astimezone(IST).hour, 10)
        self.assertEqual(dt2.astimezone(IST).day, 23)
        self.assertEqual(dt2, datetime(2026, 11, 23, 4, 30, tzinfo=timezone.utc))

        # "Friday 10am" from Sunday 2026-11-22 -> 2026-11-27 10:00 IST = 04:30 UTC
        dt3 = reminders.parse_due_at("Friday 10am", now=ref_utc)
        self.assertEqual(dt3.astimezone(IST).weekday(), 4)
        self.assertEqual(dt3, datetime(2026, 11, 27, 4, 30, tzinfo=timezone.utc))

        # Naive ISO treated as IST -> converted to UTC
        dt4 = reminders.parse_due_at("2026-11-25T15:30", now=ref_utc)
        self.assertEqual(dt4, datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc))

        # Past times refused
        for bad in ("2020-01-01T10:00:00Z", "yesterday 10am", "today 08:00", "in 0 minutes", "not a time"):
            with self.assertRaises(reminders.ReminderError):
                reminders.parse_due_at(bad, now=ref_utc)

        # Available to all 7 agents in config/agents.json
        all_agents = agents.load_agents()
        self.assertEqual(len(all_agents), 7)
        for ag in all_agents:
            self.assertIn("set_reminder", ag["tools"])
            self.assertIn("list_reminders", ag["tools"])

        # Tool execution via tools.run_tool
        ctx = ToolContext(session_id="sess_cmo", agent_id="cmo")
        cmo_tools = agents.get_agent("cmo")["tools"]
        res_ok = tools.run_tool(
            "set_reminder",
            ctx,
            {"title": "Post launch thread", "due_at": "in 3 hours", "note": "Check links"},
            cmo_tools,
        )
        self.assertIn("reminder", res_ok)
        self.assertEqual(res_ok["reminder"]["created_by"], "cmo")

        res_bad = tools.run_tool(
            "set_reminder",
            ctx,
            {"title": "Past reminder", "due_at": "2020-01-01T00:00:00Z"},
            cmo_tools,
        )
        self.assertIn("error", res_bad)

        res_list = tools.run_tool("list_reminders", ctx, {"status": "open"}, cmo_tools)
        self.assertEqual(len(res_list["reminders"]), 1)
        self.assertEqual(res_list["reminders"][0]["title"], "Post launch thread")

    def test_04_http_routes_and_cloud_sync_mirroring(self):
        # Create via POST /api/reminders
        h_create = _StubHandler(
            "/api/reminders",
            method="POST",
            body_dict={"title": "Check analytics", "due_at": "in 2 hours", "note": "LinkedIn post"},
        )
        h_create.do_POST()
        self.assertEqual(h_create.status_code, 200)
        rem = h_create.response_json()["reminder"]
        rid = rem["id"]

        # GET /api/reminders?status=open
        h_get = _StubHandler("/api/reminders?status=open", method="GET")
        h_get.do_GET()
        self.assertEqual(h_get.status_code, 200)
        self.assertEqual(len(h_get.response_json()["reminders"]), 1)

        # Snooze via POST /api/reminders/<id>/snooze
        h_snooze = _StubHandler(f"/api/reminders/{rid}/snooze", method="POST", body_dict={"minutes": 90})
        h_snooze.do_POST()
        self.assertEqual(h_snooze.status_code, 200)
        self.assertIsNotNone(h_snooze.response_json()["reminder"]["snoozed_until"])

        # Cloud sync mirrors reminders as kind='task' with data.kind='reminder'
        # Also test confidential reminder stubbing
        db.create_reminder("Launch LazyLayout teaser", (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat())
        records = cloud_sync.build_mirror_records()
        rem_recs = [r for r in records if r["kind"] == "task" and (r.get("data") or {}).get("kind") == "reminder"]
        self.assertEqual(len(rem_recs), 2)
        by_id = {r["local_id"]: r for r in rem_recs}
        self.assertEqual(by_id[rid]["title"], "Check analytics")
        conf_rec = [r for r in rem_recs if r["local_id"] != rid][0]
        self.assertEqual(conf_rec["title"], "Confidential item")
        self.assertTrue(conf_rec["data"]["confidential"])

        # Mark done via POST /api/reminders/<id>/done
        h_done = _StubHandler(f"/api/reminders/{rid}/done", method="POST", body_dict={})
        h_done.do_POST()
        self.assertEqual(h_done.status_code, 200)
        self.assertIsNotNone(h_done.response_json()["reminder"]["done_at"])

        h_done_list = _StubHandler("/api/reminders?status=done", method="GET")
        h_done_list.do_GET()
        self.assertEqual(len(h_done_list.response_json()["reminders"]), 1)

        # Delete via DELETE /api/reminders/<id>
        h_del = _StubHandler(f"/api/reminders/{rid}", method="DELETE")
        h_del.do_DELETE()
        self.assertEqual(h_del.status_code, 200)
        self.assertIsNone(db.get_reminder(rid))

        # 404 for missing reminder
        h_miss = _StubHandler("/api/reminders/rem_missing/done", method="POST", body_dict={})
        h_miss.do_POST()
        self.assertEqual(h_miss.status_code, 404)


if __name__ == "__main__":
    unittest.main()
