"""The task board: everything the team is doing in six columns (Queued, Running, Waiting for you, Paused, Done, Failed),
filtered by agent, repo and kind, with the actions each item allows. No network, no model calls.
"""

import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import automation
import board
import db
import jobs
import paths
import server
import workflows
from workflows import BaseWorkflow, StepResult

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def iso(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


class _Wf(BaseWorkflow):
    kind = "post"

    def normalize_inputs(self, inputs):
        return dict(inputs or {})

    @property
    def steps(self):
        return [("write_drafts", lambda r, s: StepResult.next()), ("review", lambda r, s: StepResult.next()),
                ("finalize", lambda r, s: StepResult.done())]


REPOS = {
    "alpha": {"id": "alpha", "name": "Alpha app", "product_id": "alpha", "confidential": False, "scope": "personal"},
    "hush": {"id": "hush", "name": "Moonshot Vault", "product_id": "hush", "confidential": True, "scope": "global"},
}


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
        os.makedirs(os.path.join(self._tmp.name, "config"), exist_ok=True)
        shutil.copy(os.path.join(REPO_ROOT, "config", "product_rules.json"), os.path.join(self._tmp.name, "config"))
        for target, new in (
            ("repos.get_repo", lambda rid: dict(REPOS[rid]) if rid in REPOS else None),
            ("repos.list_repos", lambda: [dict(r) for r in REPOS.values()]),
            ("jobs.start_worker", lambda *a, **k: None),
        ):
            p = patch(target, side_effect=new)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(workflows.WORKFLOWS, {"post": _Wf})
        p.start()
        self.addCleanup(p.stop)

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

    def run_of(self, status="queued", repo="alpha", age_days=0, **kw):
        run = workflows.start("post", {"brief": "x", "repo_id": repo} if repo else {"brief": "x"}, start_worker=False, **kw)
        db.update_workflow_run(run["id"], status=status, updated_at=iso(days=age_days))
        return run["id"]

    def items(self, **kw):
        return {i["id"]: i for i in board.build(now=NOW, **kw)["items"]}

    def session(self, status="running", agent="writer", title="Chat", age_days=0, **kw):
        sid = f"sess_{os.urandom(3).hex()}"
        db.create_agent_session({"id": sid, "agent_id": agent, "status": status, "messages": [], "title": title,
                                 "allowed_tools": [], "steps": 0, **kw})
        db.update_agent_session(sid, status=status, updated_at=iso(days=age_days))
        return sid


class ColumnTests(_Base):
    def test_the_six_columns_come_in_order(self):
        data = board.build(now=NOW)
        self.assertEqual([c["id"] for c in data["columns"]], ["queued", "running", "waiting", "paused", "done", "failed"])
        self.assertEqual([c["label"] for c in data["columns"]],
                         ["Queued", "Running", "Waiting for you", "Paused", "Done", "Failed"])

    def test_workflow_runs_land_in_the_column_of_their_status(self):
        expect = {"queued": "queued", "running": "running", "waiting_user": "waiting", "paused": "paused",
                  "done": "done", "failed": "failed", "cancelled": "done"}
        ids = {status: self.run_of(status) for status in expect}
        items = self.items()
        for status, column in expect.items():
            self.assertEqual(items[f"run:{ids[status]}"]["column"], column, status)

    def test_a_run_shows_its_steps_repo_and_scope(self):
        rid = self.run_of("running")
        db.update_workflow_run(rid, current_step=1)
        item = self.items()[f"run:{rid}"]
        self.assertEqual([s["name"] for s in item["detail"]["steps"]], ["write_drafts", "review", "finalize"])
        self.assertEqual([s["status"] for s in item["detail"]["steps"]], ["done", "current", "pending"])
        self.assertEqual((item["repo"], item["repo_name"], item["scope"], item["kind"]), ("alpha", "Alpha app", "personal", "workflow"))
        other = self.run_of("queued", repo="hush")
        self.assertEqual(self.items()[f"run:{other}"]["scope"], "team")

    def test_a_waiting_run_with_a_question_is_an_approval_card_that_opens_the_approval_screen(self):
        rid = self.run_of("waiting_user")
        gate = db.create_gate(rid, step=1, question="Keep this outline?", options=["Approve", "Revise"])
        items = self.items()
        self.assertNotIn(f"run:{rid}", items, "one card, not two, for one question")
        card = items[f"gate:{gate['id']}"]
        self.assertEqual((card["kind"], card["column"], card["title"]), ("approval", "waiting", "Keep this outline?"))
        self.assertEqual(card["route"], "#/create")
        self.assertEqual(card["detail"]["options"], ["Approve", "Revise"])
        self.assertEqual([a["id"] for a in card["actions"]], ["approve", "cancel"])

    def test_a_weekly_plan_gate_opens_the_plan_screen(self):
        run = workflows.start("post", {"brief": "x"}, start_worker=False)
        db.update_workflow_run(run["id"], status="waiting_user")
        with patch.dict(workflows.WORKFLOWS, {"weekly_plan": _Wf}):
            db.create_workflow_run({"id": "wf_weekly_plan_1_aaaaaa", "kind": "weekly_plan", "status": "waiting_user",
                                    "inputs": {}, "state": {}})
        gate = db.create_gate("wf_weekly_plan_1_aaaaaa", step=0, question="Keep these slots?", options=["Keep"])
        self.assertEqual(self.items()[f"gate:{gate['id']}"]["route"], "#/plan")

    def test_a_waiting_run_that_waits_on_an_agent_answer_stays_one_run_card(self):
        rid = self.run_of("waiting_user")
        item = self.items()[f"run:{rid}"]
        self.assertEqual((item["column"], item["route"]), ("waiting", "#/create"))

    def test_the_runs_agent_sessions_are_steps_not_cards(self):
        sid = self.session("running")
        rid = self.run_of("running")
        db.update_workflow_run(rid, state={"session_ids": [sid], "step_sessions": {"write_drafts": sid}})
        items = self.items()
        self.assertNotIn(f"session:{sid}", items)
        self.assertIn(f"run:{rid}", items)

    def test_plain_agent_tasks_show_by_state(self):
        a, b, c = self.session("running", "writer"), self.session("waiting_user", "cmo"), self.session("failed", "analyst")
        items = self.items()
        self.assertEqual([items[f"session:{x}"]["column"] for x in (a, b, c)], ["running", "waiting", "failed"])
        self.assertEqual(items[f"session:{a}"]["agents"], ["writer"])
        self.assertEqual(items[f"session:{b}"]["route"], "#/team")

    def test_jobs_show_but_workflow_step_jobs_are_not_separate_cards(self):
        queued = jobs.enqueue("market_scan", actor="scheduler")
        run = workflows.start("post", {"brief": "x"}, start_worker=False)  # also queues a workflow_step job
        failed = jobs.enqueue("repo_sync", actor="UI")
        db.update_job(failed["id"], status="failed", error="network down", finished_at=iso())
        items = self.items()
        self.assertEqual(items[f"job:{queued['id']}"]["column"], "queued")
        self.assertEqual(items[f"job:{failed['id']}"]["column"], "failed")
        self.assertEqual(items[f"job:{failed['id']}"]["detail"]["error"], "network down")
        self.assertFalse([i for i in items.values() if i["kind"] == "job" and "workflow_step" in i["id"] + i["title"]])
        self.assertIn(f"run:{run['id']}", items)

    def test_reminders_show_by_due_time(self):
        db.create_reminder("Post the update", (NOW - timedelta(hours=1)).isoformat(), reminder_id="rem_due")
        db.create_reminder("Check results", (NOW + timedelta(days=2)).isoformat(), reminder_id="rem_later")
        db.create_reminder("Old", (NOW - timedelta(days=1)).isoformat(), reminder_id="rem_done")
        db.mark_reminder_done("rem_done", done_at=iso(hours=2))
        items = self.items()
        self.assertEqual(items["reminder:rem_due"]["column"], "waiting")
        self.assertEqual(items["reminder:rem_later"]["column"], "queued")
        self.assertEqual(items["reminder:rem_done"]["column"], "done")

    def test_done_items_fade_after_seven_days(self):
        fresh, old = self.run_of("done", age_days=6), self.run_of("done", age_days=8)
        stuck = self.run_of("failed", age_days=10)
        items = self.items()
        self.assertIn(f"run:{fresh}", items)
        self.assertNotIn(f"run:{old}", items)
        self.assertIn(f"run:{stuck}", items, "a failure is kept longer than a success")
        self.assertNotIn(f"run:{self.run_of('failed', age_days=40)}", self.items())
        self.assertEqual(items[f"run:{fresh}"]["age_days"], 6)

    def test_background_jobs_waiting_on_the_global_switch_show_as_paused(self):
        waiting = jobs.enqueue("market_scan", actor="scheduler")
        manual = jobs.enqueue("repo_sync", actor="UI")
        automation.set_paused(True)
        items = self.items()
        self.assertEqual(items[f"job:{waiting['id']}"]["column"], "paused")
        self.assertEqual(items[f"job:{manual['id']}"]["column"], "queued")
        self.assertTrue(board.build(now=NOW)["paused"])


class FilterTests(_Base):
    def test_filters_by_agent_repo_and_kind(self):
        a = self.session("running", "writer")
        b = self.session("running", "cmo")
        r = self.run_of("running", repo="alpha")
        r2 = self.run_of("running", repo="hush")
        job = jobs.enqueue("market_scan")
        self.assertEqual(set(self.items(agent="writer")), {f"session:{a}"})
        self.assertEqual(set(self.items(repo="alpha")), {f"run:{r}"})
        self.assertEqual(set(self.items(repo="hush")), {f"run:{r2}"})
        self.assertEqual(set(self.items(kind="job")), {f"job:{job['id']}"})
        self.assertEqual(set(self.items(kind="agent_task")), {f"session:{a}", f"session:{b}"})

    def test_the_filter_lists_only_what_exists(self):
        self.session("running", "writer")
        self.run_of("running", repo="alpha")
        filters = board.build(now=NOW)["filters"]
        self.assertEqual(filters["agents"], ["writer"])
        self.assertEqual(filters["repos"], [{"id": "alpha", "name": "Alpha app"}])
        self.assertEqual(set(filters["kinds"]), {"agent_task", "workflow"})

    def test_a_hostile_filter_value_matches_nothing_and_breaks_nothing(self):
        self.run_of("running")
        for bad in ("' OR 1=1 --", "<script>", "../../etc/passwd", "x" * 5000, "\x00"):
            self.assertEqual(board.build(now=NOW, agent=bad)["items"], [])
            self.assertEqual(board.build(now=NOW, repo=bad)["items"], [])
            self.assertEqual(board.build(now=NOW, kind=bad)["items"], [])


class ActionTests(_Base):
    def acts(self, item_id):
        return [a["id"] for a in self.items()[item_id]["actions"]]

    def test_each_state_offers_its_own_actions(self):
        run_q, run_r, run_p, run_f = (self.run_of(s) for s in ("queued", "running", "paused", "failed"))
        self.assertEqual(self.acts(f"run:{run_q}"), ["pause", "cancel"])
        self.assertEqual(self.acts(f"run:{run_r}"), ["pause", "cancel"])
        self.assertEqual(self.acts(f"run:{run_p}"), ["resume", "cancel"])
        self.assertEqual(self.acts(f"run:{run_f}"), ["retry"])
        self.assertEqual(self.acts(f"run:{self.run_of('done')}"), [])
        waiting = self.run_of("waiting_user")
        self.assertEqual(self.acts(f"run:{waiting}"), ["approve", "cancel"])

    def test_jobs_offer_cancel_while_waiting_and_retry_when_failed(self):
        queued = jobs.enqueue("market_scan")
        failed = jobs.enqueue("repo_sync")
        db.update_job(failed["id"], status="failed", finished_at=iso())
        self.assertEqual(self.acts(f"job:{queued['id']}"), ["cancel"])
        self.assertEqual(self.acts(f"job:{failed['id']}"), ["retry"])

    def test_pause_resume_cancel_and_retry_go_through_the_real_functions(self):
        rid = self.run_of("queued")
        self.assertEqual(board.act(f"run:{rid}", "pause")["run"]["status"], "paused")
        self.assertEqual(board.act(f"run:{rid}", "resume")["run"]["status"], "running")
        self.assertEqual(board.act(f"run:{rid}", "cancel")["run"]["status"], "cancelled")
        failed = self.run_of("failed")
        self.assertEqual(board.act(f"run:{failed}", "retry")["run"]["status"], "running")

    def test_a_failed_job_is_retried_as_a_new_job_and_only_a_failed_one(self):
        job = jobs.enqueue("repo_sync", actor="UI")
        with self.assertRaises(ValueError):
            board.act(f"job:{job['id']}", "retry")
        db.update_job(job["id"], status="failed", finished_at=iso())
        res = board.act(f"job:{job['id']}", "retry")
        self.assertNotEqual(res["job"]["id"], job["id"])
        self.assertEqual((res["job"]["name"], res["job"]["status"]), ("repo_sync", "queued"))

    def test_an_action_the_item_does_not_offer_is_refused(self):
        rid = self.run_of("done")
        for action in ("pause", "resume", "cancel", "retry", "approve"):
            with self.assertRaises(ValueError):
                board.act(f"run:{rid}", action)
        self.assertEqual(db.get_workflow_run(rid)["status"], "done")

    def test_hostile_ids_and_actions_are_refused(self):
        rid = self.run_of("queued")
        for bad_id in ("", "run:", "run:../x", "nope:1", "run:" + "x" * 500, "job:\x00", None, 5, ["run:1"], "run:wf_post_0_000000"):
            with self.assertRaises((ValueError, LookupError), msg=str(bad_id)):
                board.act(bad_id, "pause")
        for bad_action in ("", "delete", "pause; rm", None, 7, "PAUSE ", "exec"):
            with self.assertRaises(ValueError, msg=str(bad_action)):
                board.act(f"run:{rid}", bad_action)
        self.assertEqual(db.get_workflow_run(rid)["status"], "queued")

    def test_a_running_agent_task_can_be_cancelled(self):
        sid = self.session("running")
        with patch("agent_runtime.cancel_session", return_value={"id": sid, "status": "cancelled"}) as cancel:
            board.act(f"session:{sid}", "cancel")
        cancel.assert_called_once_with(sid)


class ConfidentialTests(_Base):
    def test_the_laptop_shows_a_confidential_item_in_full_and_marks_it(self):
        rid = self.run_of("running", repo="hush")
        item = self.items()[f"run:{rid}"]
        self.assertTrue(item["confidential"])
        self.assertEqual(item["repo_name"], "Moonshot Vault")

    def test_the_phone_copy_is_a_stub_with_no_names(self):
        rid = self.run_of("running", repo="hush")
        sid = self.session("running", title="Notes on Moonshot Vault", confidential=True)
        text = json.dumps(board.build(now=NOW, for_phone=True))
        self.assertNotIn("Moonshot", text)
        self.assertNotIn("hush", text)
        stubs = {i["id"]: i for i in board.build(now=NOW, for_phone=True)["items"]}
        self.assertEqual(stubs[f"run:{rid}"]["title"], "Confidential item")
        self.assertEqual(stubs[f"run:{rid}"]["actions"], [])
        self.assertTrue(stubs[f"session:{sid}"]["confidential"])

    def test_text_that_names_a_confidential_repo_is_hidden_even_without_a_repo_link(self):
        sid = self.session("running", title="Draft about Moonshot Vault launch")
        stubs = {i["id"]: i for i in board.build(now=NOW, for_phone=True)["items"]}
        self.assertEqual(stubs[f"session:{sid}"]["title"], "Confidential item")

    def test_if_the_guard_cannot_be_built_everything_is_a_stub_for_the_phone(self):
        self.run_of("running", repo="alpha")
        with patch("confidential_guard.scope") as scope:
            scope.return_value.broken = True
            scope.return_value.text.return_value = True
            scope.return_value.session.return_value = True
            scope.return_value.linked.return_value = True
            data = board.build(now=NOW, for_phone=True)
        self.assertTrue(all(i["confidential"] for i in data["items"]))

    def test_a_confidential_item_cannot_be_acted_on_from_the_phone_path(self):
        rid = self.run_of("running", repo="hush")
        with self.assertRaises(PermissionError):
            board.act(f"run:{rid}", "pause", from_phone=True)


class _Stub(server.CommandCenterHandler):
    def __init__(self, path, method="GET", body=None):
        self.path, self.command = path, method
        raw = json.dumps(body).encode() if body is not None else b""
        self.rfile, self.wfile = io.BytesIO(raw), io.BytesIO()
        self.headers = {"Content-Length": str(len(raw))} if body is not None else {}
        self.status_code = None

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode())


class RouteTests(_Base):
    def setUp(self):
        super().setUp()
        self._token = server.ENGINE_TOKEN
        server.ENGINE_TOKEN = ""
        self.addCleanup(lambda: setattr(server, "ENGINE_TOKEN", self._token))
        p = patch("cloud_sync.request_sync")
        p.start()
        self.addCleanup(p.stop)

    def call(self, path, body=None):
        h = _Stub(path, "POST" if body is not None else "GET", body)
        (h.do_POST if body is not None else h.do_GET)()
        return h.status_code, h.json()

    def test_the_board_is_one_read_with_filters(self):
        self.run_of("running", repo="alpha")
        code, data = self.call("/api/board")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["items"]), 1)
        code, data = self.call("/api/board?repo=hush")
        self.assertEqual(data["items"], [])
        code, data = self.call("/api/board?agent=" + "x" * 3000)
        self.assertEqual((code, data["items"]), (200, []))

    def test_act_pauses_a_run_and_reports_errors_plainly(self):
        rid = self.run_of("queued")
        code, data = self.call("/api/board/act", {"id": f"run:{rid}", "action": "pause"})
        self.assertEqual((code, data["run"]["status"]), (200, "paused"))
        code, data = self.call("/api/board/act", {"id": f"run:{rid}", "action": "retry"})
        self.assertEqual(code, 409)
        code, data = self.call("/api/board/act", {"id": "run:wf_post_0_000000", "action": "pause"})
        self.assertEqual(code, 404)
        code, data = self.call("/api/board/act", {"id": 5, "action": "pause"})
        self.assertEqual(code, 400)

    def test_the_run_pause_and_resume_routes(self):
        rid = self.run_of("queued")
        code, data = self.call(f"/api/workflows/{rid}/pause", {})
        self.assertEqual((code, data["status"]), (200, "paused"))
        code, data = self.call(f"/api/workflows/{rid}/resume", {})
        self.assertEqual((code, data["status"]), (200, "running"))
        code, _ = self.call("/api/workflows/wf_post_0_000000/pause", {})
        self.assertEqual(code, 404)
        waiting = self.run_of("waiting_user")
        code, _ = self.call(f"/api/workflows/{waiting}/pause", {})
        self.assertEqual(code, 409)


if __name__ == "__main__":
    unittest.main()
