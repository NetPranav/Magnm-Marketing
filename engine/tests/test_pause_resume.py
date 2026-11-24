"""Pause and resume of workflow runs, and the global "Pause automatic work" switch.

A paused run stops at the next step boundary (a running step finishes, nothing new starts) and resumes from that
step, also after an engine restart. The global switch stops schedules from firing and makes queued background jobs
wait; manual actions keep working; missed runs are listed and never run by themselves. No network, no model calls:
the workflow steps are small fakes.
"""

import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import automation
import db
import jobs
import paths
import scheduler_loop
import workflows
from workflows import BaseWorkflow, StepResult


class _FakeWorkflow(BaseWorkflow):
    """Three steps. Step "slow" blocks on GATE so a test can pause while a step is running."""

    kind = "post"
    calls = []
    gate = None  # a threading.Event, or None for no waiting
    entered = None
    outcome = "next"

    def normalize_inputs(self, inputs):
        return dict(inputs or {})

    @property
    def steps(self):
        return [("first", self.first), ("slow", self.slow), ("last", self.last)]

    def first(self, run, state):
        type(self).calls.append("first")
        return StepResult.next()

    def slow(self, run, state):
        type(self).calls.append("slow")
        if type(self).entered is not None:
            type(self).entered.set()
        if type(self).gate is not None:
            type(self).gate.wait(5)
        if type(self).outcome == "wait":
            return StepResult.wait_user("Keep this?", options=["Keep", "Drop"])
        return StepResult.next()

    def last(self, run, state):
        type(self).calls.append("last")
        return StepResult.done()


class _Base(unittest.TestCase):
    def setUp(self):
        jobs.stop_worker(timeout=2.0)
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAGNM_HOME"] = self._tmp.name
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        _FakeWorkflow.calls = []
        _FakeWorkflow.gate = None
        _FakeWorkflow.entered = None
        _FakeWorkflow.outcome = "next"
        patcher = patch.dict(workflows.WORKFLOWS, {"post": _FakeWorkflow})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        jobs.stop_worker(timeout=2.0)
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        self._tmp.cleanup()

    def start(self, **kw):
        return workflows.start("post", {"brief": "x"}, start_worker=False, **kw)

    def run_job(self, run_id):
        """Run the run's queued job the way the worker does: mark it running, execute it, mark it done."""
        job = next((j for j in self.queued_jobs(run_id)), None)
        if job is None:
            return workflows.execute_workflow_job({"run_id": run_id})
        db.update_job(job["id"], status="running")
        try:
            return workflows.execute_workflow_job({"run_id": run_id}, job_id=job["id"])
        finally:
            db.update_job(job["id"], status="done")

    def status(self, run_id):
        return db.get_workflow_run(run_id)["status"]

    def queued_jobs(self, run_id=None):
        out = [j for j in db.list_jobs(limit=100) if j["name"] == "workflow_step" and j["status"] == "queued"]
        if run_id:
            out = [j for j in out if (j.get("params") or {}).get("run_id") == run_id]
        return out


class RunPauseTests(_Base):
    def test_a_queued_run_can_be_paused_and_resumed(self):
        run = self.start()
        paused = workflows.pause(run["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(self.queued_jobs(run["id"]), [])
        # A stale job that still reaches the run does nothing.
        self.run_job(run["id"])
        self.assertEqual(_FakeWorkflow.calls, [])
        self.assertEqual(self.status(run["id"]), "paused")
        resumed = workflows.resume(run["id"])
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(len(self.queued_jobs(run["id"])), 1)
        self.run_job(run["id"])
        self.assertEqual(self.status(run["id"]), "done")
        self.assertEqual(_FakeWorkflow.calls, ["first", "slow", "last"])

    def test_pause_waits_for_the_running_step_and_resumes_from_the_next_one(self):
        run = self.start()
        _FakeWorkflow.gate, _FakeWorkflow.entered = threading.Event(), threading.Event()
        worker = threading.Thread(target=self.run_job, args=(run["id"],))
        worker.start()
        self.assertTrue(_FakeWorkflow.entered.wait(5))
        workflows.pause(run["id"])  # the "model call" in step two is still running
        _FakeWorkflow.gate.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        after = db.get_workflow_run(run["id"])
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["current_step"], 2, "the finished step is kept; nothing new started")
        self.assertEqual(_FakeWorkflow.calls, ["first", "slow"])
        workflows.resume(run["id"])
        self.assertEqual(len(self.queued_jobs(run["id"])), 1)
        self.run_job(run["id"])
        self.assertEqual(_FakeWorkflow.calls, ["first", "slow", "last"], "no step ran twice")
        self.assertEqual(self.status(run["id"]), "done")

    def test_resume_before_the_running_step_ends_keeps_one_job_going(self):
        run = self.start()
        _FakeWorkflow.gate, _FakeWorkflow.entered = threading.Event(), threading.Event()
        worker = threading.Thread(target=self.run_job, args=(run["id"],))
        worker.start()
        self.assertTrue(_FakeWorkflow.entered.wait(5))
        workflows.pause(run["id"])
        workflows.resume(run["id"])
        _FakeWorkflow.gate.set()
        worker.join(5)
        self.assertEqual(self.status(run["id"]), "done")
        self.assertEqual(_FakeWorkflow.calls, ["first", "slow", "last"])
        self.assertEqual(self.queued_jobs(run["id"]), [], "no second job was queued for a run that kept going")

    def test_a_paused_run_survives_an_engine_restart(self):
        run = self.start()
        workflows.pause(run["id"])
        db.reset_connections()  # a new engine process opens the same database
        jobs.recover_stale_jobs()
        workflows.recover_stale_runs(start_worker=False)
        self.assertEqual(self.status(run["id"]), "paused")
        self.assertEqual(self.queued_jobs(run["id"]), [])
        workflows.resume(run["id"])
        self.run_job(run["id"])
        self.assertEqual(self.status(run["id"]), "done")

    def test_a_run_paused_while_its_job_was_running_when_the_engine_stopped_still_resumes(self):
        run = self.start()
        _FakeWorkflow.gate, _FakeWorkflow.entered = threading.Event(), threading.Event()
        job = db.get_next_queued_job()
        db.update_job(job["id"], status="running")  # the engine dies with this job running
        workflows.pause(run["id"])
        db.reset_connections()
        jobs.recover_stale_jobs()
        workflows.recover_stale_runs(start_worker=False)
        _FakeWorkflow.gate = None
        workflows.resume(run["id"])
        self.assertEqual(len(self.queued_jobs(run["id"])), 1)

    def test_cancel_still_works_on_a_paused_run(self):
        run = self.start()
        workflows.pause(run["id"])
        cancelled = workflows.cancel(run["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ValueError):
            workflows.resume(run["id"])

    def test_pause_is_refused_for_runs_that_are_waiting_for_you_or_finished(self):
        _FakeWorkflow.outcome = "wait"
        run = self.start()
        self.run_job(run["id"])
        self.assertEqual(self.status(run["id"]), "waiting_user")
        with self.assertRaises(ValueError) as ctx:
            workflows.pause(run["id"])
        self.assertIn("waiting for your answer", str(ctx.exception))
        done = self.start()
        _FakeWorkflow.outcome = "next"
        self.run_job(done["id"])
        with self.assertRaises(ValueError):
            workflows.pause(done["id"])
        self.assertIsNone(workflows.pause("wf_post_0_000000"))

    def test_pausing_twice_and_resuming_a_run_that_is_not_paused_change_nothing(self):
        run = self.start()
        workflows.pause(run["id"])
        again = workflows.pause(run["id"])
        self.assertEqual(again["status"], "paused")
        workflows.resume(run["id"])
        workflows.resume(run["id"])
        self.assertEqual(len(self.queued_jobs(run["id"])), 1, "a second resume queued nothing")

    def test_a_step_that_asks_you_a_question_while_paused_still_waits_for_you(self):
        run = self.start()
        _FakeWorkflow.gate, _FakeWorkflow.entered = threading.Event(), threading.Event()
        _FakeWorkflow.outcome = "wait"
        worker = threading.Thread(target=self.run_job, args=(run["id"],))
        worker.start()
        self.assertTrue(_FakeWorkflow.entered.wait(5))
        workflows.pause(run["id"])
        _FakeWorkflow.gate.set()
        worker.join(5)
        self.assertEqual(self.status(run["id"]), "waiting_user")
        self.assertEqual(len(db.list_gates_for_run(run["id"])), 1)

    def test_the_run_shows_its_steps_with_the_paused_one(self):
        run = self.start()
        workflows.pause(run["id"])
        shown = workflows.get(run["id"])
        self.assertEqual([s["status"] for s in shown["steps"]], ["paused", "pending", "pending"])


class GlobalSwitchTests(_Base):
    def setUp(self):
        super().setUp()
        db.save_schedules([
            {"id": "market_scan", "name": "Market scan", "command": "market_scan", "interval_hours": 6, "enabled": True,
             "last_run": None, "last_status": "idle"},
            {"id": "cmo_daily", "name": "Daily", "command": "daily_cycle", "cron": "46 8 * * *", "enabled": True,
             "last_run": None, "last_status": "idle"},
        ])
        patcher = patch.object(jobs, "start_worker")
        patcher.start()
        self.addCleanup(patcher.stop)

    NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)

    def test_the_switch_is_off_by_default_and_survives_a_restart(self):
        self.assertFalse(automation.is_paused())
        automation.set_paused(True, by="laptop")
        db.reset_connections()
        self.assertTrue(automation.is_paused())
        self.assertEqual(automation.pause_state()["by"], "laptop")
        automation.set_paused(False)
        self.assertFalse(automation.is_paused())

    def test_a_set_paused_that_is_not_a_boolean_is_refused(self):
        for bad in ("true", 1, None, [], "yes"):
            with self.assertRaises(ValueError):
                automation.set_paused(bad)
        self.assertFalse(automation.is_paused())

    def test_paused_schedules_do_not_fire_and_are_listed_as_missed(self):
        automation.set_paused(True)
        self.assertEqual(scheduler_loop.tick(now=self.NOW), [])
        self.assertEqual(db.list_jobs(limit=10), [])
        missed = automation.missed_runs()
        self.assertEqual(sorted(m["id"] for m in missed), ["cmo_daily", "market_scan"])
        self.assertTrue(all(m["name"] for m in missed))

    def test_resuming_fires_nothing_by_itself(self):
        automation.set_paused(True)
        scheduler_loop.tick(now=self.NOW)
        automation.set_paused(False, now=self.NOW)
        self.assertEqual(scheduler_loop.tick(now=self.NOW + timedelta(seconds=60)), [])
        self.assertEqual([j for j in db.list_jobs(limit=10) if j["actor"] == "scheduler"], [])
        self.assertEqual(sorted(m["id"] for m in automation.missed_runs()), ["cmo_daily", "market_scan"])
        # The schedules count as handled for this slot: the next normal slot fires as usual.
        later = self.NOW + timedelta(hours=7)
        self.assertEqual([j["name"] for j in scheduler_loop.tick(now=later)], ["market_scan"])

    def test_something_due_between_the_last_tick_and_the_resume_is_listed_not_run(self):
        automation.set_paused(True)  # no tick ran while paused
        automation.set_paused(False, now=self.NOW)
        self.assertEqual(scheduler_loop.tick(now=self.NOW), [])
        self.assertEqual(sorted(m["id"] for m in automation.missed_runs()), ["cmo_daily", "market_scan"])

    def test_the_owner_can_run_or_dismiss_a_missed_run(self):
        automation.set_paused(True)
        scheduler_loop.tick(now=self.NOW)
        automation.set_paused(False, now=self.NOW)
        job = automation.run_missed("market_scan")
        self.assertEqual((job["name"], job["actor"]), ("market_scan", "UI"))
        self.assertEqual([m["id"] for m in automation.missed_runs()], ["cmo_daily"])
        with self.assertRaises(LookupError):
            automation.run_missed("market_scan")
        automation.dismiss_missed("cmo_daily")
        self.assertEqual(automation.missed_runs(), [])

    def test_queued_background_jobs_wait_but_manual_ones_run(self):
        background = jobs.enqueue("market_scan", actor="scheduler")
        manual = jobs.enqueue("repo_sync", actor="UI")
        automation.set_paused(True)
        first = jobs.next_runnable_job()
        self.assertEqual(first["id"], manual["id"], "the manual job is not held up by the waiting background job")
        db.update_job(manual["id"], status="done")
        self.assertIsNone(jobs.next_runnable_job())
        self.assertEqual(db.get_job(background["id"])["status"], "queued")
        automation.set_paused(False)
        self.assertEqual(jobs.next_runnable_job()["id"], background["id"])

    def test_a_manual_workflow_keeps_running_while_paused(self):
        automation.set_paused(True)
        run = self.start()
        self.run_job(run["id"])
        self.assertEqual(self.status(run["id"]), "done")

    def test_an_automatic_run_stops_at_the_next_step_and_waits_for_the_global_resume(self):
        run = self.start(automatic=True)
        automation.set_paused(True)
        self.run_job(run["id"])
        after = db.get_workflow_run(run["id"])
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["state"].get("paused_by"), "global")
        self.assertEqual(_FakeWorkflow.calls, [], "nothing new started")
        automation.set_paused(False)
        self.assertEqual(self.status(run["id"]), "running")
        self.assertEqual(len(self.queued_jobs(run["id"])), 1)
        self.run_job(run["id"])
        self.assertEqual(self.status(run["id"]), "done")

    def test_the_global_resume_does_not_resume_a_run_the_owner_paused(self):
        mine = self.start(automatic=True)
        workflows.pause(mine["id"])
        automation.set_paused(True)
        automation.set_paused(False)
        self.assertEqual(self.status(mine["id"]), "paused")

    def test_the_queued_job_of_an_automatic_run_waits_too(self):
        run = self.start(automatic=True)
        automation.set_paused(True)
        self.assertIsNone(jobs.next_runnable_job())
        automation.set_paused(False)
        self.assertEqual((jobs.next_runnable_job().get("params") or {}).get("run_id"), run["id"])

    def test_the_weekly_catch_up_at_startup_respects_the_switch(self):
        db.save_schedules([{"id": "weekly_summary", "name": "Weekly", "command": "weekly_summary", "cron": "0 9 * * 1",
                            "enabled": True, "last_run": None, "last_status": "idle"}])
        automation.set_paused(True)
        self.assertEqual(scheduler_loop.check_missed_weekly_summary(now=self.NOW), [])
        self.assertEqual(db.list_jobs(limit=5), [])

    def test_reminders_still_fire_while_paused(self):
        automation.set_paused(True)
        with patch("reminders.check_due_reminders") as check:
            scheduler_loop.tick(now=self.NOW)
        check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
