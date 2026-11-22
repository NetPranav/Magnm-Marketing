"""Repo text stays out of cloud prompts when confidential; schedules are editable and all run."""

import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import agent_chat
import agent_runtime
import agents
import ai_gateway
import bakeoff
import confidential_guard
import db
import jobs
import learnings
import mcp_tools
import paths
import product_rules
import review_studio
import scheduler
import scheduler_loop
import results_extract
import server
import team_room
import tools
import weekly_summary
import workflows
from tools import core

REPOS = [
    {"id": "open", "name": "OpenRepo", "product_id": "open", "confidential": False},
    {"id": "hidden", "name": "ProjectNightfall", "product_id": "hidden", "confidential": True},
]


class _Handler(server.CommandCenterHandler):
    """In-memory request handler for route tests."""

    def __init__(self, path, method="GET", body=None):
        self.path = path
        self.command = method
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
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


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous = db.get_db_path()
        db.set_db_path(os.path.join(tmp.name, "magnm.db"))
        self.addCleanup(db.set_db_path, previous)
        self.addCleanup(db.reset_connections)
        db.init_db()
        self.tmp = tmp.name


class RepoTextTests(_Case):
    def setUp(self):
        super().setUp()
        sync = os.path.join(self.tmp, "repo-sync.json")
        with open(sync, "w", encoding="utf-8") as f:
            json.dump({"repositories": [{"id": "hidden", "recent_commits": [{"message": "CANARY-COMMIT"}]}]}, f)
        for p in (
            patch.object(core.repos, "list_repos", return_value=REPOS),
            patch.object(core, "_resolve_readable_path", return_value=sync),
            patch.object(core.product_rules, "confidential_product_ids", return_value=set()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_reading_every_repo_omits_confidential_ones_without_tainting(self):
        marked = []
        ctx = SimpleNamespace(confidential=False, mark_confidential=lambda: marked.append(1))
        text = json.dumps(core.read_repo_changes(ctx))
        self.assertIn("OpenRepo", text)
        self.assertNotIn("CANARY-COMMIT", text)
        self.assertNotIn("ProjectNightfall", text)
        self.assertEqual(marked, [])

    def test_shipped_this_week_omits_confidential_repos(self):
        items = [{"repo_id": "hidden", "title": "CANARY-COMMIT"}, {"repo_id": "open", "title": "Open work"}]
        ctx = SimpleNamespace(confidential=False, mark_confidential=lambda: self.fail("tainted"))
        with patch.object(core.repos, "shipped", return_value=items):
            found = json.dumps(core.shipped_this_week(ctx))
        self.assertIn("Open work", found)
        self.assertNotIn("CANARY-COMMIT", found)

    def test_asking_for_a_confidential_repo_still_taints(self):
        marked = []
        ctx = SimpleNamespace(confidential=False, mark_confidential=lambda: marked.append(1))
        self.assertIn("CANARY-COMMIT", json.dumps(core.read_repo_changes(ctx, repo_id="hidden")))
        self.assertEqual(marked, [1])

    def test_blog_grounding_never_holds_confidential_repo_text(self):
        with patch.object(core, "read_product_facts", return_value={}), \
                patch.object(core, "search_research", return_value={"results": []}):
            for product in (None, "hidden"):
                found = json.dumps(workflows.BlogWorkflow._fetch_grounding_material("Faster builds", product))
                self.assertNotIn("CANARY-COMMIT", found, product)
                self.assertNotIn("ProjectNightfall", found, product)


class ScheduleTests(_Case):
    def setUp(self):
        super().setUp()
        db.save_schedules([dict(s) for s in scheduler_loop.DEFAULT_SCHEDULES])

    def _get(self, sched_id):
        return next(s for s in db.load_schedules() if s["id"] == sched_id)

    def test_time_and_day_can_be_changed(self):
        scheduler.update_schedule("cmo_daily", time_of_day="07:05", day="*")
        self.assertEqual(self._get("cmo_daily")["cron"], "5 7 * * *")
        scheduler.update_schedule("weekly_summary", time_of_day="18:30", day="5")
        weekly = self._get("weekly_summary")
        self.assertEqual(weekly["cron"], "30 18 * * 5")
        thursday = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
        friday_evening = datetime(2026, 11, 20, 13, 1, tzinfo=timezone.utc)
        self.assertFalse(scheduler_loop.is_due(weekly, thursday))
        self.assertTrue(scheduler_loop.is_due(weekly, friday_evening))

    def test_bad_values_are_refused(self):
        for kwargs in ({"time_of_day": "25:00"}, {"time_of_day": "7"}, {"day": "9"}):
            with self.assertRaises(ValueError, msg=kwargs):
                scheduler.update_schedule("cmo_daily", **kwargs)
        for hours in (0, -1, "x", 10000):
            with self.assertRaises(ValueError, msg=hours):
                scheduler.update_schedule("repo_sync", interval_hours=hours)
        with self.assertRaises(ValueError):
            scheduler.update_schedule("weekly_summary", day="*")
        with self.assertRaises(LookupError):
            scheduler.update_schedule("nope", enabled=False)

    def test_editing_never_writes_timer_files_into_the_repo(self):
        with patch.object(scheduler, "generate_systemd_units") as generate:
            scheduler.update_schedule("repo_sync", interval_hours=4, enabled=False)
        generate.assert_not_called()
        self.assertEqual((self._get("repo_sync")["interval_hours"], self._get("repo_sync")["enabled"]), (4, False))

    def test_enabled_must_be_a_real_boolean(self):
        for bad in ("false", "no", 0, 1, "", [], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                scheduler.update_schedule("repo_sync", enabled=bad)
        self.assertTrue(self._get("repo_sync")["enabled"])
        scheduler.update_schedule("repo_sync", enabled=False)
        self.assertIs(self._get("repo_sync")["enabled"], False)

    def test_a_true_or_false_is_not_an_interval(self):
        for bad in (True, False, 1.5, "2.5"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                scheduler.update_schedule("repo_sync", interval_hours=bad)
        scheduler.update_schedule("repo_sync", interval_hours="3")
        self.assertEqual(self._get("repo_sync")["interval_hours"], 3)

    def test_a_refused_edit_changes_nothing(self):
        before = self._get("cmo_daily")
        with self.assertRaises(ValueError):
            scheduler.update_schedule("cmo_daily", time_of_day="07:00", enabled="maybe")
        self.assertEqual(self._get("cmo_daily"), before)

    def test_time_edits_on_interval_schedules_are_refused(self):
        with self.assertRaises(ValueError):
            scheduler.update_schedule("repo_sync", time_of_day="07:00")

    def test_the_default_weekly_summary_still_runs_monday_morning(self):
        weekly = self._get("weekly_summary")
        self.assertEqual(weekly["cron"], "0 9 * * 1")
        ran = dict(weekly, last_run="2026-11-16T04:00:00+00:00")  # Monday 09:30 IST
        self.assertFalse(scheduler_loop.is_due(ran, datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)))  # Sunday
        self.assertTrue(scheduler_loop.is_due(ran, datetime(2026, 11, 23, 3, 31, tzinfo=timezone.utc)))  # next Monday 09:01 IST
        self.assertFalse(scheduler_loop.is_due(weekly, datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)))  # Mon 08:30 IST
        self.assertTrue(scheduler_loop.is_due(weekly, datetime(2026, 11, 23, 3, 31, tzinfo=timezone.utc)))  # Mon 09:01 IST

    def test_a_tick_does_not_lose_an_edit_made_while_it_enqueues(self):
        def enqueue_and_edit(name, actor="UI", **_kw):
            if name == "market_scan":
                scheduler.update_schedule("repo_sync", interval_hours=5, enabled=False)
            return {"id": f"job_{name}"}

        with patch.object(jobs, "enqueue", side_effect=enqueue_and_edit):
            scheduler_loop.tick(now=datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc))
        repo_sync = self._get("repo_sync")
        self.assertEqual((repo_sync["interval_hours"], repo_sync["enabled"]), (5, False))
        for sched_id in ("market_scan", "watch_topics", "cmo_daily", "weekly_summary"):
            self.assertEqual(self._get(sched_id)["last_status"], "queued", sched_id)
            self.assertTrue(self._get(sched_id)["last_run"], sched_id)

    def test_an_edit_does_not_lose_a_run_recorded_by_the_scheduler(self):
        real_save = db.save_schedules
        saved_by_tick = threading.Event()
        release = threading.Event()

        def slow_save(schedules):
            real_save(schedules)
            saved_by_tick.set()
            release.wait(2)

        with patch.object(jobs, "enqueue", return_value={"id": "job_1"}), patch.object(db, "save_schedules", side_effect=slow_save):
            ticker = threading.Thread(target=scheduler_loop.tick, kwargs={"now": datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)})
            ticker.start()
            self.assertTrue(saved_by_tick.wait(2))
            editor = threading.Thread(target=scheduler.update_schedule, args=("repo_sync",), kwargs={"interval_hours": 7})
            editor.start()
            editor.join(0.3)
            self.assertTrue(editor.is_alive(), "the edit must wait for the scheduler's save to finish")
            release.set()
            ticker.join(3)
            editor.join(3)
        repo_sync = self._get("repo_sync")
        self.assertEqual(repo_sync["interval_hours"], 7)
        self.assertEqual(repo_sync["last_status"], "queued")
        self.assertTrue(repo_sync["last_run"])

    def test_the_missed_weekly_summary_check_keeps_edits_too(self):
        def enqueue_and_edit(name, actor="UI", **_kw):
            scheduler.update_schedule("weekly_summary", time_of_day="10:00")
            return {"id": "job_w"}

        with patch.object(jobs, "enqueue", side_effect=enqueue_and_edit), \
                patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "0"}):
            scheduler_loop.check_missed_weekly_summary(now=datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc))
        weekly = self._get("weekly_summary")
        self.assertEqual(weekly["cron"], "0 10 * * 1")
        self.assertEqual(weekly["last_status"], "queued")

    def test_toggle_flips_one_schedule_and_refuses_unknown_ones(self):
        scheduler.toggle_schedule("repo_sync")
        self.assertIs(self._get("repo_sync")["enabled"], False)
        scheduler.toggle_schedule("repo_sync")
        self.assertIs(self._get("repo_sync")["enabled"], True)
        with self.assertRaises(LookupError):
            scheduler.toggle_schedule("nope")


class ScheduleRouteTests(_Case):
    def setUp(self):
        super().setUp()
        db.save_schedules([dict(s) for s in scheduler_loop.DEFAULT_SCHEDULES])
        token = patch.object(server, "ENGINE_TOKEN", "")
        token.start()
        self.addCleanup(token.stop)

    def _post(self, path, body):
        handler = _Handler(path, "POST", body)
        with patch.object(scheduler, "generate_systemd_units") as generate:
            handler.do_POST()
        generate.assert_not_called()
        return handler

    def _get(self, sched_id):
        return next(s for s in db.load_schedules() if s["id"] == sched_id)

    def test_update_changes_time_day_and_interval(self):
        self.assertEqual(self._post("/api/schedule/update", {"id": "cmo_daily", "time": "06:30", "day": "3"}).status_code, 200)
        self.assertEqual(self._get("cmo_daily")["cron"], "30 6 * * 3")
        self.assertEqual(self._post("/api/schedule/update", {"id": "repo_sync", "interval_hours": 8}).status_code, 200)
        self.assertEqual(self._get("repo_sync")["interval_hours"], 8)

    def test_update_refuses_a_string_for_enabled(self):
        handler = self._post("/api/schedule/update", {"id": "repo_sync", "enabled": "false"})
        self.assertEqual(handler.status_code, 400)
        self.assertTrue(self._get("repo_sync")["enabled"])

    def test_update_refuses_bad_times_and_unknown_schedules(self):
        self.assertEqual(self._post("/api/schedule/update", {"id": "cmo_daily", "time": "99:99"}).status_code, 400)
        self.assertEqual(self._post("/api/schedule/update", {"id": "nope", "enabled": True}).status_code, 404)
        self.assertEqual(self._post("/api/schedule/update", {"enabled": True}).status_code, 404)

    def test_toggle_route_flips_and_404s(self):
        self.assertEqual(self._post("/api/schedule/toggle", {"id": "repo_sync"}).status_code, 200)
        self.assertIs(self._get("repo_sync")["enabled"], False)
        self.assertEqual(self._post("/api/schedule/toggle", {"id": "nope"}).status_code, 404)


class McpSetupTests(unittest.TestCase):
    def test_agy_setup_uses_its_own_command(self):
        [snippet] = mcp_tools.build_cli_snippets([{"id": "agy", "name": "Antigravity", "installed": True}])
        self.assertTrue(snippet["snippet"].startswith("agy mcp add magnm python3 "))
        self.assertNotIn(".gemini/antigravity/", snippet["config_hint"])


RULES = {
    "version": 1,
    "show_levels": {
        "everything": "Show it.",
        "teasers_only": "Teasers only.",
    },
    "products": [
        {"id": "cero", "name": "Cero", "status": "released", "show": "everything", "confidential": False},
        {
            "id": "nightfall",
            "name": "ProjectNightfall",
            "status": "in_development",
            "show": "teasers_only",
            "confidential": True,
        },
    ],
}

FACTS_MD = (
    "# Verified product facts\n\n"
    "## Cero\n- CERO-FACT runs on your computer.\n\n"
    "## ProjectNightfall\n- CANARY-FACT has a secret editor.\n\n"
    "## ProjectNightfall rule\n- CANARY-RULE teasers only.\n"
)
SYNC_MD = (
    "# Sync\n\n## Cero Terminal (cero)\n- `a1` (2026-10-01): CERO-COMMIT fix\n\n"
    "## ProjectNightfall (nightfall)\n- `b2` (2026-10-02): CANARY-COMMIT secret work\n\n"
    "## Nightfall Web (nightfall)\n- `c3` (2026-10-03): CANARY-WEB-COMMIT secret site\n"
)
FIXTURE_REPOS = [
    {"id": "cero", "name": "Cero Terminal", "product_id": "cero", "confidential": False, "version": "2.2.3"},
    {
        "id": "nightfall", "name": "ProjectNightfall", "product_id": "nightfall",
        "confidential": True, "version": "CANARY-VERSION", "status": "CANARY-STATUS",
    },
    # Not flagged itself, but its product is confidential in the product rules.
    {"id": "nf-web", "name": "Nightfall Web", "product_id": "nightfall", "confidential": False},
    # Flagged confidential with no product rule at all.
    {"id": "solo", "name": "SoloVault", "product_id": "solo", "confidential": True},
]
SYNC_JSON = {
    "synced_at": "2026-10-03T00:00:00+00:00",
    "repositories": [
        {"id": "cero", "recent_commits": [{"message": "CERO-COMMIT fix"}]},
        {"id": "nightfall", "recent_commits": [{"message": "CANARY-COMMIT secret work"}]},
        {"id": "nf-web", "recent_commits": [{"message": "CANARY-WEB-COMMIT secret site"}]},
        {"id": "solo", "recent_commits": [{"message": "CANARY-SOLO-COMMIT"}]},
    ],
}
CANARIES = ("CANARY-COMMIT", "CANARY-WEB-COMMIT", "CANARY-SOLO-COMMIT", "CANARY-FACT", "CANARY-RULE", "CANARY-VERSION")


class _DataCase(_Case):
    """A private data folder holding invented rules, facts, repo sync files and four repos."""

    def setUp(self):
        super().setUp()
        root = os.path.join(self.tmp, "data")
        for sub in ("config", "research", "state", "logs", "outputs"):
            os.makedirs(os.path.join(root, sub))
        files = {
            ("config", "product_rules.json"): json.dumps(RULES),
            ("research", "product-facts.md"): FACTS_MD,
            ("research", "repo-sync.md"): SYNC_MD,
            ("research", "repo-sync.json"): json.dumps(SYNC_JSON),
        }
        for parts, text in files.items():
            with open(os.path.join(root, *parts), "w", encoding="utf-8") as f:
                f.write(text)
        for p in (
            patch.object(paths, "DATA_ROOT", root),
            patch.object(core.repos, "list_repos", return_value=FIXTURE_REPOS),
            patch.object(core.repos, "shipped", return_value=[
                {"repo_id": "nightfall", "title": "CANARY-SHIPPED"},
                {"repo_id": "nf-web", "title": "CANARY-SHIPPED-WEB"},
                {"repo_id": "cero", "title": "Cero shipped"},
            ]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.cloud = SimpleNamespace(confidential=False, mark_confidential=lambda: self.marked.append(1))
        self.local = SimpleNamespace(confidential=True, mark_confidential=lambda: self.marked.append(1))
        self.marked = []

    def assertClean(self, value, msg=None):
        text = json.dumps(value, default=str)
        for canary in CANARIES + ("CANARY-SHIPPED", "CANARY-NOTE", "CANARY-DBFACT"):
            self.assertNotIn(canary, text, msg)


class ConfidentialToolTests(_DataCase):
    def test_facts_leave_out_confidential_sections_and_facts_in_cloud_sessions(self):
        db.create_product_fact("nightfall", "CANARY-DBFACT secret", status="approved")
        db.create_product_fact("cero", "Cero is local", status="approved")
        res = core.read_product_facts(self.cloud)
        self.assertClean(res)
        self.assertIn("CERO-FACT", res["product_facts"])
        self.assertIn("Cero is local", json.dumps(res))
        self.assertEqual(self.marked, [])

    def test_facts_stay_whole_in_local_sessions_and_when_asked_by_id(self):
        db.create_product_fact("nightfall", "CANARY-DBFACT secret", status="approved")
        text = json.dumps(core.read_product_facts(self.local))
        self.assertIn("CANARY-FACT", text)
        self.assertIn("CANARY-DBFACT", text)
        by_id = json.dumps(core.read_product_facts(self.cloud, product_id="nightfall"))
        self.assertIn("CANARY-FACT", by_id)
        self.assertEqual(self.marked, [1])

    def test_facts_without_a_session_never_include_confidential_text(self):
        self.assertClean(core.read_product_facts(None))
        self.assertClean(core.read_product_facts(None, product_id="nightfall"))
        self.assertIn("CANARY-FACT", json.dumps(core.read_product_facts(None, _allow_confidential=True)))

    def test_research_hits_naming_confidential_things_are_dropped(self):
        db.save_research_note("n1", "Zebra plan for ProjectNightfall", "CANARY-NOTE body", None, "me")
        db.save_research_note("n2", "Zebra notes for Nightfall Web", "CANARY-NOTE web", None, "me")
        db.save_research_note("n3", "Zebra ideas", "Ordinary body", None, "me")
        titles = [h["title"] for h in core.search_research(self.cloud, "zebra")["results"]]
        self.assertEqual(titles, ["Zebra ideas"])
        self.assertEqual(len(core.search_research(self.local, "zebra")["results"]), 3)
        self.assertEqual([h["title"] for h in core.search_research(None, "zebra")["results"]], ["Zebra ideas"])

    def test_listing_repos_hides_details_of_confidential_ones(self):
        listed = {r["id"]: r for r in core.list_repos(self.cloud)["repositories"]}
        self.assertEqual(set(listed), {"cero", "nightfall", "nf-web", "solo"})
        for rid in ("nightfall", "nf-web", "solo"):
            self.assertTrue(listed[rid]["confidential"], rid)
            self.assertIsNone(listed[rid]["version"], rid)
            self.assertIsNone(listed[rid]["status"], rid)
        self.assertEqual(listed["cero"]["version"], "2.2.3")
        local = {r["id"]: r for r in core.list_repos(self.local)["repositories"]}
        self.assertEqual(local["nightfall"]["version"], "CANARY-VERSION")

    def test_repo_lists_omit_repos_made_confidential_by_the_product_rules(self):
        changes = core.read_repo_changes(self.cloud)
        self.assertEqual([r["id"] for r in changes["repositories"]], ["cero"])
        self.assertClean(core.read_repo_changes(self.cloud))
        self.assertClean(core.shipped_this_week(self.cloud))
        self.assertEqual(self.marked, [])

    def test_reading_without_a_session_never_returns_confidential_repos_even_by_id(self):
        for repo_id in ("nightfall", "nf-web", "solo"):
            self.assertClean(core.read_repo_changes(None, repo_id=repo_id), repo_id)
        self.assertIn("CANARY-COMMIT", json.dumps(core.read_repo_changes(None, repo_id="nightfall", _allow_confidential=True)))

    def test_asking_for_a_repo_by_id_taints_whatever_makes_it_confidential(self):
        for repo_id in ("nightfall", "nf-web", "solo"):
            self.marked.clear()
            core.read_repo_changes(self.cloud, repo_id=repo_id)
            self.assertTrue(self.marked, repo_id)
        self.marked.clear()
        core.read_repo_changes(self.cloud, repo_id="cero")
        self.assertEqual(self.marked, [])

    def test_unreadable_product_rules_fail_closed(self):
        with patch.object(product_rules, "load_rules", side_effect=ValueError("broken")):
            self.assertTrue(core._is_confidential_repo({"id": "cero", "product_id": "cero"}))
            self.assertEqual(core.read_repo_changes(self.cloud)["repositories"], [])
            core.read_repo_changes(self.cloud, repo_id="cero")
            self.assertEqual(self.marked, [1])
            self.assertEqual(core.search_research(self.cloud, "zebra")["results"], [])

    def test_a_weekly_plan_session_runs_in_the_cloud_and_sees_no_confidential_text(self):
        db.save_research_note("n1", "Zebra plan for ProjectNightfall", "CANARY-NOTE body", None, "me")
        db.save_research_note("n3", "Zebra ideas", "Ordinary body", None, "me")
        calls = []

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            calls.append((bool(confidential), json.dumps(messages, default=str)))
            if len(calls) == 1:
                names = ("list_repos", "read_repo_changes", "shipped_this_week", "read_product_facts")
                tool_calls = [{"id": f"c{i}", "name": n, "arguments": {}} for i, n in enumerate(names)]
                tool_calls.append({"id": "c9", "name": "search_research", "arguments": {"query": "zebra"}})
                return {"text": "", "tool_calls": tool_calls}
            return {"text": "Plan ready.", "tool_calls": []}

        allowed = ["list_repos", "read_repo_changes", "shipped_this_week", "read_product_facts", "search_research"]
        with patch.object(ai_gateway, "chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session("cmo", "Plan the week.", system_prompt="sys", allowed_tools=allowed)
        self.assertEqual(sess["status"], "done")
        self.assertEqual([c[0] for c in calls], [False, False])
        self.assertClean(calls[1][1])
        self.assertIn("CERO-COMMIT", calls[1][1])


class ConfidentialPromptTests(_DataCase):
    def _pr_prompt(self, source_context):
        seen = {}

        def fake(system_prompt, user_prompt, **kwargs):
            seen.update(kwargs, system_prompt=system_prompt, user_prompt=user_prompt)
            return {"text": "Draft.", "model_used": "m"}

        handler = _Handler("/api/pr/draft_reply", "POST", {"scenario": "x", "severity": "L1", "source_context": source_context})
        with patch.object(server, "ENGINE_TOKEN", ""), \
                patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake):
            handler.do_POST()
        self.assertEqual(handler.status_code, 200)
        return seen

    def test_pr_reply_prompt_leaves_out_confidential_facts_and_commits(self):
        seen = self._pr_prompt("Someone asks about your terminal")
        self.assertFalse(seen.get("confidential"))
        self.assertClean(seen["system_prompt"] + seen["user_prompt"])
        self.assertIn("CERO-FACT", seen["system_prompt"])
        self.assertIn("CERO-COMMIT", seen["system_prompt"])

    def test_pr_reply_naming_a_confidential_product_goes_to_local_ai(self):
        seen = self._pr_prompt("Is ProjectNightfall real?")
        self.assertTrue(seen["confidential"])

    def test_bakeoff_grounding_leaves_out_confidential_facts(self):
        self.assertClean(bakeoff._grounding_facts_block("cero"))
        self.assertIn("CERO-FACT", bakeoff._grounding_facts_block("cero"))

    def test_review_studio_context_leaves_out_confidential_facts_unless_local(self):
        item = {"product_id": "cero", "platform": "linkedin"}
        self.assertClean(review_studio._build_product_context_block(item))
        local = review_studio._build_product_context_block(item, confidential=True)
        self.assertIn("CANARY-FACT", local)

    def test_weekly_summary_and_learnings_notice_confidential_repo_names(self):
        data = {"week_label": "w", "posts": [{"title": "Nightfall Web launch", "product_id": ""}]}
        self.assertTrue(weekly_summary._data_mentions_confidential(data))
        db.create_learning("Nightfall Web posts did well", status="approved", applies_to=["all"])
        db.create_learning("Short posts did well", status="approved", applies_to=["all"])
        block = learnings.prompt_block("writer")
        self.assertIn("Short posts", block)
        self.assertNotIn("Nightfall", block)

    def test_weekly_summary_data_that_names_nothing_confidential_stays_in_the_cloud(self):
        data = {"week_label": "w", "posts": [{"title": "Cero launch", "product_id": "cero"}]}
        self.assertFalse(weekly_summary._data_mentions_confidential(data))


class BlogGroundingTests(_DataCase):
    def _outline_prompt(self, state, product_id=None, topic="Faster builds"):
        prompts = []

        def fake(system_prompt, user_prompt, **kwargs):
            prompts.append((user_prompt, kwargs.get("confidential")))
            return {"text": "# Title\n## TL;DR\nShort.", "model_used": "m"}

        run = {"id": "run_x", "inputs": {"topic": topic, "product_id": product_id}}
        with patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake):
            workflows.BlogWorkflow().step_write_outline(run, state)
        return prompts[0]

    def test_fresh_grounding_for_every_product_choice_is_clean(self):
        with patch.object(core, "search_research", return_value={"results": []}):
            for product in (None, "cero", "nightfall", "nf-web", "solo"):
                found = workflows.BlogWorkflow._fetch_grounding_material("Faster builds", product)
                self.assertClean(found, product)

    def test_grounding_saved_before_the_fix_is_filtered_too(self):
        old = {
            "product_facts": FACTS_MD,
            "product_rules": "rules",
            "repo_changes": {"repositories": [
                {"id": "cero", "name": "Cero Terminal", "recent_commits": [{"message": "CERO-COMMIT fix"}]},
                {"id": "nightfall", "name": "ProjectNightfall", "product_id": "nightfall", "confidential": False,
                 "recent_commits": [{"message": "CANARY-COMMIT secret work"}]},
                {"id": "nf-web", "name": "Nightfall Web", "product_id": "nightfall", "confidential": False,
                 "recent_commits": [{"message": "CANARY-WEB-COMMIT secret site"}]},
                {"id": "x", "name": "Mystery", "confidential": True,
                 "recent_commits": [{"message": "CANARY-SOLO-COMMIT"}]},
            ]},
            "research_notes": [{"title": "Plan for ProjectNightfall", "summary": "CANARY-NOTE"}],
        }
        prompt, conf = self._outline_prompt({"grounding": old})
        self.assertClean(prompt)
        self.assertIn("CERO-COMMIT", prompt)
        self.assertFalse(conf)

    def test_a_confidential_blog_keeps_its_own_repo_text_and_uses_local_ai(self):
        with patch.object(core, "search_research", return_value={"results": []}):
            state = {}
            prompt, conf = self._outline_prompt(state, product_id="nightfall")
        self.assertTrue(conf)
        self.assertIn("CANARY-COMMIT", prompt)
        self.assertIn("CANARY-FACT", prompt)

    def test_repos_flagged_confidential_without_a_product_rule_count_as_confidential_blogs(self):
        for product_id in ("solo", "Nightfall", "nf-web"):
            run = {"inputs": {"topic": "Faster builds", "product_id": product_id}}
            self.assertTrue(workflows.BlogWorkflow()._blog_is_confidential(run), product_id)
        run = {"inputs": {"topic": "Faster builds", "product_id": "cero"}}
        self.assertFalse(workflows.BlogWorkflow()._blog_is_confidential(run))


class _Planted(_DataCase):
    """Confidential and open data of every kind, planted with CANARY strings in the confidential ones."""

    def setUp(self):
        super().setUp()
        root = paths.DATA_ROOT
        repo_dir = os.path.join(self.tmp, "nightfall-repo")
        os.makedirs(repo_dir)
        with open(os.path.join(repo_dir, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("CANARY_REPO_FILE")
        planted = [dict(r) for r in FIXTURE_REPOS]
        planted[1].update(kind="local", local_path=repo_dir)
        for p in (
            patch.object(core.repos, "list_repos", return_value=planted),
            patch.object(core.repos, "sync_repo", return_value={"version": "CANARY_VER", "readme": "CANARY_README"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        db.create_agent_session({"id": "s-conf", "agent_id": "writer", "confidential": True, "status": "done"})
        db.create_agent_session({"id": "s-child", "agent_id": "writer", "status": "done", "parent_session_id": "s-conf"})
        db.create_content_item("c-name", "post", "x", "ProjectNightfall CANARY_TITLE_NAMED", "CANARY_BODY_NAMED")
        db.create_content_item("c-prod", "post", "x", "CANARY_TITLE_PROD", "CANARY_BODY_PROD", product_id="nightfall")
        db.create_content_item("c-repo", "post", "x", "Nightfall Web CANARY_TITLE_REPO", "CANARY_BODY_REPO")
        db.create_content_item("c-sess", "post", "x", "CANARY_TITLE_SESS", "CANARY_BODY_SESS", session_id="s-conf")
        db.create_content_item("c-kid", "post", "x", "CANARY_TITLE_KID", "CANARY_BODY_KID", session_id="s-child")
        db.create_content_item("c-open", "post", "x", "Cero tips", "OPEN-BODY", product_id="cero")
        db.create_reminder("ProjectNightfall CANARY_REMINDER", "2030-01-01T00:00:00+00:00")
        db.create_reminder("CANARY_REMINDER_INHERITED", "2030-01-01T00:00:00+00:00", content_id="c-prod")
        db.create_reminder("OPEN-REMINDER", "2030-01-01T00:00:00+00:00")
        db.record_metric("m1", "c-prod", "x", {"CANARY_METRIC": 7}, "manual")
        db.record_metric("m2", "c-open", "x", {"OPEN_METRIC": 3}, "manual")
        db.create_agent_memory(agent_id="writer", text="ProjectNightfall CANARY_MEMORY")
        db.create_agent_memory(agent_id="writer", text="Linked CANARY_MEMORY_LINKED", content_id="c-sess")
        db.create_agent_memory(agent_id="writer", text="OPEN-MEMORY keep posts short")
        db.save_research_note("n1", "Zebra plan for ProjectNightfall", "CANARY-NOTE body", None, "me")
        os.makedirs(os.path.join(root, "outputs", "content", "c-sess"))
        with open(os.path.join(root, "outputs", "content", "c-sess", "pic.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def cloud_ctx(self, agent_id="writer"):
        return tools.ToolContext(session_id="s-cloud", agent_id=agent_id)

    def cannot_switch(self, agent_id="writer"):
        return SimpleNamespace(session_id=None, agent_id=agent_id, confidential=False, depth=0, attachments=[])

    def chat_script(self, steps):
        """Replace the model: `steps` is a list of replies. Returns the list of (confidential, messages) calls."""
        calls = []

        def fake(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            calls.append((bool(confidential), json.dumps(messages, default=str)))
            step = steps[len(calls) - 1] if len(calls) <= len(steps) else "done"
            if isinstance(step, str):
                return {"text": step, "tool_calls": []}
            return {"text": "", "tool_calls": [dict(id=f"c{len(calls)}_{i}", name=n, arguments=a) for i, (n, a) in enumerate(step)]}

        patcher = patch.object(ai_gateway, "chat_with_tools", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


# Every registered tool is classified here. A tool that returns local data goes in READERS, with calls that go
# after planted confidential data. A new tool fails test_every_registered_tool_is_classified until it is added.
READERS = {
    "read_product_facts": [{}, {"product_id": "nightfall"}],
    "list_repos": [{}],
    "read_repo_changes": [{}, {"repo_id": "nightfall"}, {"repo_id": "nf-web"}, {"repo_id": "solo"}],
    "read_repo_file": [{"repo_id": "nightfall", "path": "secret.txt"}],
    "list_repo_media": [{"repo_id": "nightfall"}],
    "shipped_this_week": [{}],
    "draft_repo_facts": [{"repo_id": "nightfall"}],
    "search_research": [{"query": "zebra"}],
    "read_content": [{"content_id": c} for c in ("c-name", "c-prod", "c-repo", "c-sess", "c-kid")],
    "list_content": [{}, {"status": "draft"}],
    "write_review": [{"content_id": c, "verdict": "ok", "notes": "fine"} for c in ("c-name", "c-sess")],
    "read_metrics": [{}, {"content_id": "c-prod"}],
    "list_reminders": [{"status": "all"}],
    "recall_memory": [{}],
    "check_image": [{"path": "outputs/content/c-sess/pic.png", "question": "What is shown?"}],
}
# Tools that return nothing from local data: they write, wait for a person, or only talk to the outside.
NO_LOCAL_DATA = {
    "write_draft": {"type": "post", "platform": "x", "title": "T", "body": "B", "content_id": "c-prod"},
    "record_metrics": {"content_id": "c-prod", "platform": "x", "metrics": {"views": 1}, "source": "manual"},
    "set_reminder": {"title": "Later", "due_at": "in 2 hours", "content_id": "c-prod"},
    "remember": {"text": "Keep it short"},
    "save_research_note": {"title": "A note", "body": "Body"},
    "propose_fact": {"product_id": "cero", "text": "Cero runs locally", "source_kind": "manual"},
    "check_text": {"text": "ProjectNightfall is coming"},
    "ask_user": {"question": "Which one?"},
    "list_pitch_catalog": {},
    "draft_proposal": None,  # builds a proposal from the model's own text
    "render_card": None,  # renders the model's own headline
    "generate_image": None,  # sends the model's own prompt out, and is refused in a confidential session
    "web_search": None,  # outbound only
    "read_page": None,  # outbound only
    "screenshot_page": None,  # outbound only
    "social_research": None,  # creates a pending run a person must start
    "run_coding_agent": None,  # creates a pending run a person must approve
}
# Needs a parent and child session, so it has its own tests below.
DEDICATED = {"delegate"}


class EveryToolTests(_Planted):
    def test_every_registered_tool_is_classified(self):
        known = set(READERS) | set(NO_LOCAL_DATA) | DEDICATED
        # Other tests register throwaway tools of their own; only the shipped tool modules count.
        registered = {n for n in tools.list_registered_tools() if tools.get_tool(n).fn.__module__.startswith("tools.")}
        self.assertEqual(registered - known, set(), "New tools must be added to READERS or NO_LOCAL_DATA in this test.")
        self.assertEqual(known - registered, set(), "A classified tool is no longer registered.")

    def _run(self, name, args, ctx):
        return json.dumps(tools.run_tool(name, ctx, args, [name]), default=str)

    def test_readers_return_no_confidential_data_unless_the_session_switched_to_local_first(self):
        for name, calls in READERS.items():
            for args in calls:
                label = f"{name} {args}"
                with patch.object(ai_gateway, "generate_completion_with_meta", return_value={"text": "CANARY_IMAGE_TEXT"}):
                    ctx = self.cloud_ctx()
                    switched = self._run(name, args, ctx)
                    # A session that can switch may get confidential data, but only once it runs on local AI.
                    if "CANARY" in switched:
                        self.assertTrue(ctx.confidential, label)
                    for other in (self.cannot_switch(), None):
                        self.assertNotIn("CANARY", self._run(name, args, other), label)

    def test_a_session_already_on_local_ai_gets_the_data(self):
        ctx = self.cloud_ctx()
        ctx.confidential = True
        self.assertIn("CANARY_BODY_SESS", self._run("read_content", {"content_id": "c-sess"}, ctx))
        self.assertIn("CANARY_TITLE_NAMED", self._run("list_content", {}, ctx))
        self.assertIn("CANARY_REMINDER", self._run("list_reminders", {"status": "all"}, ctx))
        self.assertIn("CANARY_METRIC", self._run("read_metrics", {}, ctx))
        self.assertIn("CANARY_MEMORY", self._run("recall_memory", {}, ctx))

    def test_reading_one_item_by_id_switches_the_session_for_every_way_of_being_confidential(self):
        for cid in ("c-name", "c-prod", "c-repo", "c-sess", "c-kid"):
            ctx = self.cloud_ctx()
            self.assertIn("CANARY_BODY", self._run("read_content", {"content_id": cid}, ctx), cid)
            self.assertTrue(ctx.confidential, cid)
        for name, args in (("read_metrics", {"content_id": "c-prod"}), ("write_review", {"content_id": "c-sess", "verdict": "ok", "notes": "n"})):
            ctx = self.cloud_ctx()
            self._run(name, args, ctx)
            self.assertTrue(ctx.confidential, name)

    def test_open_things_still_work_in_the_cloud_when_confidential_ones_exist(self):
        ctx = self.cloud_ctx()
        listed = self._run("list_content", {}, ctx)
        self.assertIn("Cero tips", listed)
        self.assertNotIn("CANARY", listed)
        self.assertIn("OPEN-BODY", self._run("read_content", {"content_id": "c-open"}, ctx))
        self.assertIn("OPEN-REMINDER", self._run("list_reminders", {"status": "all"}, ctx))
        self.assertIn("OPEN_METRIC", self._run("read_metrics", {}, ctx))
        self.assertIn("OPEN-MEMORY", self._run("recall_memory", {}, ctx))
        self.assertFalse(ctx.confidential)

    def test_tools_that_return_no_local_data_return_no_canary_even_aimed_at_confidential_items(self):
        for name, args in NO_LOCAL_DATA.items():
            if args is None:
                continue
            ctx = self.cloud_ctx()
            self.assertNotIn("CANARY", self._run(name, args, ctx), name)

    def test_looking_at_an_image_of_a_confidential_item_runs_on_local_ai(self):
        flags = []

        def fake(**kwargs):
            flags.append(bool(kwargs.get("confidential")))
            return {"text": "ok", "model_used": "m"}

        args = {"path": "outputs/content/c-sess/pic.png", "question": "What is shown?"}
        with patch.object(ai_gateway, "generate_completion_with_meta", side_effect=fake):
            self._run("check_image", args, self.cloud_ctx())
            self._run("check_image", args, None)
            self._run("check_image", args, self.cannot_switch())
        self.assertEqual(flags, [True])

    def test_a_confidential_child_session_switches_the_parent_before_it_sees_the_result(self):
        child = {"id": "s-child2", "status": "done", "result_text": "CANARY_CHILD_RESULT", "confidential": True}
        db.create_agent_session({"id": "s-child2", "agent_id": "writer", "confidential": True, "status": "done"})
        call = {"agent_id": "writer", "instruction": "Draft a short post about our week"}
        with patch.object(agent_runtime, "start_session", return_value=child):
            ctx = self.cloud_ctx("cmo")
            self.assertIn("CANARY_CHILD_RESULT", self._run("delegate", call, ctx))
            self.assertTrue(ctx.confidential)
            stuck = self.cannot_switch("cmo")
            self.assertNotIn("CANARY", self._run("delegate", call, stuck))
            db.create_agent_session({"id": "s-child3", "agent_id": "writer", "status": "done"})
            plain = dict(child, id="s-child3", result_text="OPEN_CHILD_RESULT", confidential=False)
        with patch.object(agent_runtime, "start_session", return_value=plain):
            ctx = self.cloud_ctx("cmo")
            self.assertIn("OPEN_CHILD_RESULT", self._run("delegate", call, ctx))
            self.assertFalse(ctx.confidential)

    def test_the_parent_session_runs_its_next_call_on_local_ai_after_a_confidential_child(self):
        calls = self.chat_script([
            [("delegate", {"agent_id": "writer", "instruction": "Draft a short post about our week"})],
            [("read_content", {"content_id": "c-sess"})],
            "CANARY_CHILD_RESULT",
            "All done.",
        ])
        sess = agent_runtime.start_session("cmo", "Plan the week.", system_prompt="sys", allowed_tools=["delegate"])
        self.assertEqual(sess["status"], "done")
        self.assertEqual([c[0] for c in calls], [False, False, True, True])
        self.assertTrue(sess["confidential"])


class SessionDecisionTests(_Planted):
    def test_a_message_naming_a_repo_that_is_confidential_on_its_own_stays_local(self):
        for text in ("Nightfall Web plan", "SoloVault status", "ProjectNightfall plan", "projectnightfall plan"):
            calls = self.chat_script(["fine"])
            agent_runtime.start_session("writer", text, system_prompt="sys", allowed_tools=[])
            self.assertTrue(calls[0][0], text)

    def test_names_in_the_system_prompt_do_not_taint_a_session(self):
        calls = self.chat_script(["fine"])
        prompt = agents.build_system_prompt("writer")
        self.assertIn("ProjectNightfall", prompt)  # the product rules name it
        agent_runtime.start_session("writer", "Plan three posts about Cero.", system_prompt=prompt, allowed_tools=[])
        self.assertEqual([c[0] for c in calls], [False])

    def test_memory_about_confidential_things_stays_out_of_the_system_prompt(self):
        cloud = agents.build_system_prompt("writer")
        self.assertNotIn("CANARY", cloud)
        self.assertIn("OPEN-MEMORY", cloud)
        local = agents.build_system_prompt("writer", confidential=True)
        self.assertIn("CANARY_MEMORY", local)

    def test_an_answer_naming_a_confidential_repo_turns_a_waiting_session_local_and_it_stays_so(self):
        calls = self.chat_script([[("ask_user", {"question": "Which project?"})], "Noted.", "Again."])
        sess = agent_runtime.start_session("writer", "Plan posts.", system_prompt="sys", allowed_tools=["ask_user"])
        self.assertEqual(sess["status"], "waiting_user")
        resumed = agent_runtime.resume_session(sess["id"], "The one called Nightfall Web")
        self.assertEqual([c[0] for c in calls], [False, True])
        self.assertTrue(resumed["confidential"])

    def test_a_session_that_turned_confidential_before_waiting_stays_local_on_resume(self):
        calls = self.chat_script([
            [("read_content", {"content_id": "c-sess"}), ("ask_user", {"question": "Shall I go on?"})],
            "Yes.",
        ])
        sess = agent_runtime.start_session("writer", "Plan posts.", system_prompt="sys", allowed_tools=["read_content", "ask_user"])
        self.assertTrue(sess["confidential"])
        agent_runtime.resume_session(sess["id"], "Go on")
        self.assertEqual([c[0] for c in calls], [False, True])

    def test_a_child_of_a_confidential_session_starts_local(self):
        calls = self.chat_script(["fine"])
        agent_runtime.start_session("writer", "Draft.", system_prompt="s", allowed_tools=[], parent_session_id="s-conf")
        self.assertTrue(calls[0][0])

    def test_chat_stays_in_the_cloud_when_confidential_items_merely_exist(self):
        calls = self.chat_script(["Posting plan."])
        res = agent_chat.chat_with_agent("writer", "What should I post this week?")
        self.assertTrue(res["success"])
        self.assertEqual([c[0] for c in calls], [False])

    def test_chat_replaying_a_turn_from_a_confidential_session_stays_local_even_naming_nothing(self):
        history = [{"sender": "writer", "text": "Here are some thoughts.", "session_id": "s-conf"}]
        with patch.object(agent_chat, "get_agent_chat_history", return_value=history), \
                patch.object(agent_chat, "save_agent_chat_history"):
            calls = self.chat_script(["ok"])
            agent_chat.chat_with_agent("writer", "and what else?")
            self.assertEqual([c[0] for c in calls], [True])
            plain = [{"sender": "writer", "text": "Here are some thoughts.", "session_id": "s-open"}]
            db.create_agent_session({"id": "s-open", "agent_id": "writer", "status": "done"})
        with patch.object(agent_chat, "get_agent_chat_history", return_value=plain), \
                patch.object(agent_chat, "save_agent_chat_history"):
            calls = self.chat_script(["ok"])
            agent_chat.chat_with_agent("writer", "and what else?")
            self.assertEqual([c[0] for c in calls], [False])

    def test_chat_replaying_a_turn_that_names_a_repo_stays_local(self):
        history = [{"sender": "writer", "text": "Nightfall Web is going well"}]
        with patch.object(agent_chat, "get_agent_chat_history", return_value=history), \
                patch.object(agent_chat, "save_agent_chat_history"):
            calls = self.chat_script(["ok"])
            agent_chat.chat_with_agent("writer", "and what else?")
        self.assertEqual([c[0] for c in calls], [True])

    def _post(self, text):
        with patch.object(team_room, "save_message"):
            return team_room.post_message("CEO", text)

    def test_team_room_keeps_later_agents_local_after_a_confidential_reply(self):
        calls = self.chat_script([[("read_content", {"content_id": "c-sess"})], "First reply.", "Second reply."])
        self._post("@writer then @designer please plan the week")
        self.assertEqual([c[0] for c in calls][:2], [False, True])
        self.assertTrue(calls[-1][0], "the second agent must start local")

    def test_team_room_stays_in_the_cloud_when_nothing_is_confidential(self):
        calls = self.chat_script(["First reply.", "Second reply."])
        self._post("@writer then @designer please plan the week")
        self.assertEqual([c[0] for c in calls], [False, False])

    def test_team_room_message_naming_a_repo_is_local(self):
        calls = self.chat_script(["ok"])
        self._post("@writer how is Nightfall Web doing")
        self.assertTrue(calls[0][0])

    def test_the_weekly_plan_session_stays_in_the_cloud_with_confidential_items_around(self):
        calls = self.chat_script([
            [("list_content", {}), ("list_reminders", {"status": "all"}), ("recall_memory", {}),
             ("read_metrics", {}), ("read_product_facts", {}), ("list_repos", {}), ("read_repo_changes", {}),
             ("shipped_this_week", {}), ("search_research", {"query": "zebra"})],
            "Plan ready.",
        ])
        allowed = ["list_content", "list_reminders", "recall_memory", "read_metrics", "read_product_facts",
                   "list_repos", "read_repo_changes", "shipped_this_week", "search_research"]
        sess = agent_runtime.start_session(
            "cmo", "Plan the week.", system_prompt=agents.build_system_prompt("cmo"), allowed_tools=allowed
        )
        self.assertEqual(sess["status"], "done")
        self.assertEqual([c[0] for c in calls], [False, False])
        self.assertNotIn("CANARY", calls[1][1])

    def test_results_weekly_and_learnings_use_the_shared_definition(self):
        self.assertTrue(results_extract._is_content_confidential({"title": "Notes", "session_id": "s-conf"}))
        self.assertTrue(results_extract._is_content_confidential({"title": "Notes", "session_id": "s-child"}))
        self.assertTrue(results_extract._is_content_confidential({"title": "Nightfall Web notes"}))
        self.assertFalse(results_extract._is_content_confidential({"title": "Cero tips", "product_id": "cero"}))
        data = {"week_label": "w", "posts": [{"title": "Notes", "product_id": "", "content_id": "c-sess"}]}
        self.assertTrue(weekly_summary._data_mentions_confidential(data))
        data = {"week_label": "w", "posts": [{"title": "Cero tips", "product_id": "cero", "content_id": "c-open"}]}
        self.assertFalse(weekly_summary._data_mentions_confidential(data))
        db.create_learning("Short posts did well", evidence=[{"content_id": "c-sess", "metric_id": "m1"}],
                           status="approved", applies_to=["all"])
        db.create_learning("Cero tips did well", evidence=[{"content_id": "c-open", "metric_id": "m2"}],
                           status="approved", applies_to=["all"])
        block = learnings.prompt_block("writer")
        self.assertIn("Cero tips did well", block)
        self.assertNotIn("Short posts", block)

    def test_everything_counts_as_confidential_when_the_rules_cannot_be_read(self):
        with patch.object(product_rules, "load_rules", side_effect=ValueError("broken")):
            scope = confidential_guard.scope()
            self.assertTrue(scope.broken)
            self.assertTrue(scope.item({"id": "c-open", "title": "Cero tips"}))
            self.assertTrue(scope.session("s-open"))
            ctx = self.cloud_ctx()
            self.assertEqual(json.loads(self._run_list(ctx))["items"], [])

    def _run_list(self, ctx):
        return json.dumps(tools.run_tool("list_content", ctx, {}, ["list_content"]), default=str)


if __name__ == "__main__":
    unittest.main()
