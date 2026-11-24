"""Pause and resume from the phone: run_control (one workflow run) and automation_control (the global "Pause
automatic work" switch), plus the task board and the automation state that travel to the phone's cloud copy.

The cloud is hostile: payloads are rebuilt from checked fields, only the signed-in user's own rows are used, a
confidential run can only be changed on the laptop, and every action applies exactly once. No network.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import automation
import board
import cloud_sync
import db
import jobs
import phone_control
import phone_queue
import repos
import workflows
from workflows import BaseWorkflow, StepResult
from engine.tests.test_phone_queue import OTHER, OWNER, _Base as _QueueBase

REPOS = {
    "cero-web": {"id": "cero-web", "name": "Cero web", "product_id": "cero", "confidential": False, "scope": "personal"},
    "nightfall": {"id": "nightfall", "name": "Nightfall core", "product_id": "secretproj", "confidential": True, "scope": "global"},
}


class _Wf(BaseWorkflow):
    kind = "post"

    def normalize_inputs(self, inputs):
        return dict(inputs or {})

    @property
    def steps(self):
        return [("one", lambda r, s: StepResult.next()), ("two", lambda r, s: StepResult.done())]


class _Base(_QueueBase):
    def setUp(self):
        super().setUp()
        for target, new in (
            ("repos.get_repo", lambda rid: REPOS.get(str(rid).lower())),
            ("repos.list_repos", lambda: [dict(r) for r in REPOS.values()]),
            ("jobs.start_worker", lambda *a, **k: None),
        ):
            p = patch(target, side_effect=new)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(workflows.WORKFLOWS, {"post": _Wf})
        p.start()
        self.addCleanup(p.stop)

    def new_run(self, repo="cero-web", status="queued", **kw):
        inputs = {"brief": "x"}
        if repo:
            inputs["repo_id"] = repo
        run = workflows.start("post", inputs, start_worker=False, **kw)
        if status != "queued":
            db.update_workflow_run(run["id"], status=status)
        return run["id"]

    def go(self, kind, target, payload):
        action = self.queue(kind=kind, target=target, payload=payload)
        self.run_sync()
        return action

    def outcome(self, action):
        res = self.result_of(action)
        return res["status"], res["result"]

    def status(self, run_id):
        return db.get_workflow_run(run_id)["status"]


class RunControlTests(_Base):
    def test_the_phone_can_pause_and_resume_a_run(self):
        rid = self.new_run()
        a = self.go("run_control", rid, {"action": "pause"})
        self.assertEqual(self.outcome(a)[0], "applied", self.outcome(a))
        self.assertEqual(self.status(rid), "paused")
        self.assertEqual(db.get_workflow_run(rid)["state"].get("paused_by"), "phone")
        b = self.go("run_control", rid, {"action": "resume"})
        self.assertEqual(self.outcome(b)[0], "applied")
        self.assertEqual(self.status(rid), "running")

    def test_the_same_action_twice_changes_nothing_more(self):
        rid = self.new_run()
        a = self.go("run_control", rid, {"action": "pause"})
        workflows.resume(rid)
        self.cloud.get_row(a["id"]).update(status="queued", applied_by_device=None, result=None)
        self.run_sync()
        self.assertEqual(self.status(rid), "running", "a replayed pause is not applied a second time")

    def test_a_stale_second_pause_or_resume_is_applied_as_already_so(self):
        rid = self.new_run()
        self.go("run_control", rid, {"action": "pause"})
        again = self.go("run_control", rid, {"action": "pause"})
        self.assertEqual(self.outcome(again), ("applied", "Already paused."))
        done = self.go("run_control", rid, {"action": "resume"})
        self.assertEqual(self.outcome(done)[0], "applied")
        late = self.go("run_control", rid, {"action": "resume"})
        self.assertEqual(self.outcome(late)[0], "applied")
        self.assertEqual(self.status(rid), "running")

    def test_a_run_that_is_waiting_for_you_or_finished_is_refused_with_a_reason(self):
        for status in ("waiting_user", "done", "failed", "cancelled"):
            rid = self.new_run(status=status)
            a = self.go("run_control", rid, {"action": "pause"})
            self.assertEqual(self.outcome(a)[0], "rejected", status)
            self.assertEqual(self.status(rid), status)

    def test_an_unknown_run_is_rejected_not_guessed(self):
        a = self.go("run_control", "wf_post_1700000000000_abcdef", {"action": "pause"})
        self.assertEqual(self.outcome(a)[0], "rejected")
        self.assertIn("not found", self.outcome(a)[1])

    def test_a_confidential_run_can_only_be_changed_on_the_laptop(self):
        rid = self.new_run(repo="nightfall")
        a = self.go("run_control", rid, {"action": "pause"})
        self.assertEqual(self.outcome(a), ("rejected", phone_control.CONFIDENTIAL_REASON))
        self.assertEqual(self.status(rid), "queued")

    def test_a_run_whose_inputs_name_a_confidential_product_is_refused(self):
        run = workflows.start("post", {"brief": "Announce ProjectNightfall"}, start_worker=False)
        with patch("confidential_guard.mentions", return_value=True):
            a = self.go("run_control", run["id"], {"action": "pause"})
        self.assertEqual(self.outcome(a)[0], "rejected")
        self.assertEqual(self.status(run["id"]), "queued")

    def test_hostile_payloads_are_rejected_and_change_nothing(self):
        rid = self.new_run()
        bad = ({}, {"action": "cancel"}, {"action": "delete"}, {"action": "PAUSE"}, {"action": ["pause"]}, {"action": None},
               {"action": "pause", "extra": 1}, {"action": "pause", "run": "other"}, [], "pause", {"action": "pause\n"},
               {"action": "x" * 5000}, {"action": {"$ne": 1}})
        for payload in bad:
            a = self.go("run_control", rid, payload)
            self.assertEqual(self.outcome(a)[0], "rejected", payload)
        self.assertEqual(self.status(rid), "queued")

    def test_hostile_targets_never_reach_the_database_lookup(self):
        for target in ("../etc/passwd", "wf_post_1_abcdef' OR 1=1", "x" * 150, "wf post", "all", "RUN"):
            with patch.object(db, "get_workflow_run") as lookup:
                a = self.go("run_control", target, {"action": "pause"})
            lookup.assert_not_called()
            self.assertEqual(self.outcome(a)[0], "rejected", target)

    def test_a_row_of_another_user_is_never_used(self):
        rid = self.new_run()
        self.cloud.ignore_owner_filter = True
        foreign = self.queue(owner=OTHER, kind="run_control", target=rid, payload={"action": "pause"})
        self.run_sync()
        self.assertEqual(self.status(rid), "queued")
        self.assertEqual(self.result_of(foreign)["status"], "queued")
        self.assertEqual(self.cloud.calls("claim_phone_action"), [])

    def test_a_crash_after_pausing_never_pauses_twice_or_loses_the_answer(self):
        rid = self.new_run()
        self.queue(kind="run_control", target=rid, payload={"action": "pause"})
        with patch.object(db, "record_phone_action_receipt", side_effect=RuntimeError("crash before the receipt")):
            self.run_sync()
        self.assertEqual(self.status(rid), "paused")
        self.run_sync()
        self.assertEqual(self.status(rid), "paused")

    def test_nothing_is_started_published_or_approved_by_a_pause(self):
        rid = self.new_run()
        with patch.object(db, "mark_posted", side_effect=AssertionError), patch.object(db, "mark_ready", side_effect=AssertionError):
            self.go("run_control", rid, {"action": "pause"})
            self.go("run_control", rid, {"action": "resume"})
        self.assertEqual(db.list_gates_for_run(rid), [])


class AutomationControlTests(_Base):
    def test_the_phone_can_pause_and_resume_automatic_work(self):
        a = self.go("automation_control", "all", {"action": "pause"})
        self.assertEqual(self.outcome(a)[0], "applied", self.outcome(a))
        self.assertTrue(automation.is_paused())
        self.assertEqual(automation.pause_state()["by"], "phone")
        b = self.go("automation_control", "all", {"action": "resume"})
        self.assertEqual(self.outcome(b)[0], "applied")
        self.assertFalse(automation.is_paused())

    def test_replays_and_repeats_are_harmless(self):
        a = self.go("automation_control", "all", {"action": "pause"})
        self.cloud.get_row(a["id"]).update(status="queued", applied_by_device=None, result=None)
        automation.set_paused(False)
        self.run_sync()
        self.assertFalse(automation.is_paused(), "the replayed pause is not applied again")
        again = self.go("automation_control", "all", {"action": "resume"})
        self.assertEqual(self.outcome(again)[0], "applied")

    def test_resuming_from_the_phone_starts_nothing(self):
        db.save_schedules([{"id": "market_scan", "name": "Market", "command": "market_scan", "interval_hours": 6, "enabled": True,
                            "last_run": None, "last_status": "idle"}])
        self.go("automation_control", "all", {"action": "pause"})
        self.go("automation_control", "all", {"action": "resume"})
        self.assertEqual([j for j in db.list_jobs(limit=10) if j["name"] == "market_scan"], [])
        self.assertEqual([m["id"] for m in automation.missed_runs()], ["market_scan"])

    def test_hostile_payloads_and_targets_are_rejected(self):
        for target, payload in (("all", {}), ("all", {"action": "stop"}), ("all", {"action": "pause", "x": 1}), ("everything", {"action": "pause"}),
                                ("all", {"action": True}), ("all", []), ("ALL", {"action": "pause"}),
                                ("all", {"paused": True}), ("all", {"action": "resume", "missed": "run"})):
            a = self.go("automation_control", target, payload)
            self.assertEqual(self.outcome(a)[0], "rejected", (target, payload))
        self.assertFalse(automation.is_paused())

    def test_a_row_of_another_user_cannot_flip_the_switch(self):
        self.cloud.ignore_owner_filter = True
        foreign = self.queue(owner=OTHER, kind="automation_control", target="all", payload={"action": "pause"})
        self.run_sync()
        self.assertFalse(automation.is_paused())
        self.assertEqual(self.result_of(foreign)["status"], "queued")


class KindsTests(_Base):
    def test_the_new_kinds_are_asked_for_and_every_old_kind_is_kept(self):
        for kind in ("draft_comment", "draft_posted", "reminder_done", "agent_task", "pitch_create", "pitch_update",
                     "pitch_finalize", "pitch_link", "library_upload", "schedule_update", "draft_create", "workflow_request",
                     "run_control", "automation_control"):
            self.assertIn(kind, phone_queue.KINDS)

    def test_a_kind_this_engine_does_not_know_stays_queued(self):
        row = self.queue(kind="run_teleport", target="all", payload={})
        self.run_sync()
        self.assertEqual(self.result_of(row)["status"], "queued")
        self.assertEqual(self.cloud.calls("claim_phone_action"), [])


class MirrorTests(_Base):
    def make(self, kind, local_id, parent, title, data, ts):
        return {"kind": kind, "local_id": local_id, "parent_local_id": parent, "title": title, "data": data, "local_updated_at": ts}

    def guard(self):
        import phone_server
        return phone_server._build_confidential_guard()

    def test_board_items_are_mirrored_for_the_phone_with_only_the_actions_a_phone_may_queue(self):
        rid = self.new_run()
        recs = [r for r in phone_control.mirror_records(self.make, "2026-11-24T00:00:00+00:00", self.guard()) if r["kind"] == "board_item"]
        rec = next(r for r in recs if r["local_id"] == f"run:{rid}")
        self.assertEqual(rec["data"]["column"], "queued")
        self.assertEqual(rec["data"]["actions"], ["pause"])
        self.assertEqual(rec["data"]["scope"], "personal")
        self.assertFalse(rec["data"].get("confidential"))

    def test_a_confidential_item_is_a_stub_with_no_text(self):
        rid = self.new_run(repo="nightfall")
        sid = "sess_c"
        db.create_agent_session({"id": sid, "agent_id": "writer", "status": "running", "messages": [], "title": "Nightfall notes",
                                 "allowed_tools": [], "steps": 0, "confidential": True})
        recs = phone_control.mirror_records(self.make, "2026-11-24T00:00:00+00:00", self.guard())
        text = json.dumps(recs)
        self.assertNotIn("Nightfall", text)
        self.assertNotIn("nightfall", text)
        stub = next(r for r in recs if r["local_id"] == f"run:{rid}")
        self.assertEqual((stub["title"], stub["data"]["confidential"], stub["data"]["actions"]), ("Confidential item", True, []))

    def test_the_automation_state_is_one_record_with_pause_missed_runs_and_the_pipeline(self):
        db.save_schedules([
            {"id": "auto_pipeline", "name": "Automatic content", "command": "auto_pipeline", "cron": "30 10 * * *", "enabled": True,
             "last_run": None, "last_status": "idle"},
            {"id": "market_scan", "name": "Market", "command": "market_scan", "interval_hours": 6, "enabled": True,
             "last_run": None, "last_status": "idle"},
        ])
        automation.set_paused(True, by="phone")
        import scheduler_loop
        scheduler_loop.tick(now=datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc))
        recs = [r for r in phone_control.mirror_records(self.make, "t", self.guard()) if r["kind"] == "automation"]
        self.assertEqual(len(recs), 1)
        data = recs[0]["data"]
        self.assertEqual((recs[0]["local_id"], data["paused"], data["paused_by"]), ("state", True, "phone"))
        self.assertEqual(sorted(m["id"] for m in data["missed"]), ["auto_pipeline", "market_scan"])
        self.assertEqual(data["pipeline"]["enabled"], True)
        self.assertEqual(data["pipeline"]["kinds"], ["post"])
        self.assertNotIn("command", json.dumps(data))

    def test_the_cycle_builds_both_kinds_and_a_broken_builder_keeps_what_is_in_the_cloud(self):
        self.new_run()
        self.assertNotIn("automation", {r["kind"] for r in cloud_sync.build_mirror_records()}, "nothing set up yet")
        automation.set_paused(True)
        kinds = {r["kind"] for r in cloud_sync.build_mirror_records()}
        self.assertTrue({"board_item", "automation"} <= kinds, kinds)
        with patch.object(phone_control, "mirror_records", side_effect=RuntimeError("boom")):
            cloud_sync.build_mirror_records()
            self.assertTrue({"board_item", "automation"} <= set(cloud_sync._SKIP_DELETE_KINDS))

    def test_the_mirror_is_capped(self):
        for _ in range(phone_control.MAX_BOARD_ITEMS + 20):
            self.new_run()
        recs = [r for r in phone_control.mirror_records(self.make, "t", self.guard()) if r["kind"] == "board_item"]
        self.assertEqual(len(recs), phone_control.MAX_BOARD_ITEMS)


if __name__ == "__main__":
    unittest.main()
