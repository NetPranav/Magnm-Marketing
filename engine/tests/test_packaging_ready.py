"""Unit tests for Task T1.5: packaging readiness, in-process runners, digest, card render, and legacy import."""

import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cmo_runtime
import db
import digest
import paths
import repo_sync
import research_scanner
import server
import setup_check


class _StubPostHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising POST routes without sockets."""

    def __init__(self, path, method="POST", body_dict=None, headers=None):
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


class PackagingReadyTests(unittest.TestCase):
    """Tests for in-process digest, runners, card render, setup git check, and legacy import."""

    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        importlib.reload(paths)
        paths.ensure_data_root()
        importlib.reload(cmo_runtime)
        db.set_db_path(None)
        db.init_db()

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        importlib.reload(cmo_runtime)
        self._tmpdir_obj.cleanup()

    def test_01_digest_runs_on_temp_home_without_bash(self):
        """digest.build_digest() builds all digest sections without invoking bash."""
        with open(paths.data("state", "board.md"), "w", encoding="utf-8") as f:
            f.write("# Board\nActive slot: cero-launch\n")
        os.makedirs(paths.data("plan"), exist_ok=True)
        with open(paths.data("plan", "plan.md"), "w", encoding="utf-8") as f:
            f.write("| Slot | Platforms | Topic | Status |\n")
        os.makedirs(paths.data("logs"), exist_ok=True)
        last_log = paths.data("logs", "20261119-cmo.md")
        with open(last_log, "w", encoding="utf-8") as f:
            f.write("CMO run summary line 1\n")

        for sub in ("done", "failed", "inbox"):
            os.makedirs(paths.data("queue", "agy", sub), exist_ok=True)
        done_task = paths.data("queue", "agy", "done", "task1.md")
        with open(done_task, "w", encoding="utf-8") as f:
            f.write("output: outputs/posts/demo/draft.md # generated\n")
        # Ensure done_task mtime is newer than last_log
        log_mtime = os.path.getmtime(last_log)
        os.utime(done_task, (log_mtime + 10, log_mtime + 10))
        with open(paths.data("queue", "agy", "inbox", "pending.md"), "w", encoding="utf-8") as f:
            f.write("pending task\n")
        with open(paths.data("queue", "agy", "runner.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\nline4\n")

        post_dir = paths.data("outputs", "posts", "cero-post")
        os.makedirs(post_dir, exist_ok=True)
        with open(os.path.join(post_dir, "READY.md"), "w", encoding="utf-8") as f:
            f.write("Status: AWAITING YES\n")
        with open(paths.data("state", "palette-log.md"), "w", encoding="utf-8") as f:
            f.write("p1\np2\np3\np4\n")
        db.log_activity("CMO", "CEO", "digest_test", "outputs/posts/cero-post/READY.md", ts="2026-11-19T00:00:00Z")

        with patch.object(subprocess, "run", side_effect=AssertionError("subprocess.run must not be called")):
            out = cmo_runtime.run_digest()

        self.assertIn("=== DIGEST ", out)
        self.assertIn("(IST ", out)
        self.assertIn("--- board", out)
        self.assertIn("Active slot: cero-launch", out)
        self.assertIn("--- plan", out)
        self.assertIn("--- last log", out)
        self.assertIn("--- agy done since last log", out)
        self.assertIn("outputs/posts/demo/draft.md", out)
        self.assertIn("--- agy inbox (pending)", out)
        self.assertIn("pending.md", out)
        self.assertIn("--- runner", out)
        self.assertIn("line4", out)
        self.assertNotIn("line1", out)
        self.assertIn("--- awaiting yes", out)
        self.assertIn("cero-post/READY.md", out)
        self.assertIn("--- palette log", out)
        self.assertIn("p4", out)
        self.assertIn("--- activity (last 5)", out)
        self.assertIn("digest_test", out)

    def test_02_cmo_runtime_research_and_repo_sync_run_in_process(self):
        """run_research() and run_repo_sync() call Python functions in-process without sys.executable subprocess."""
        def fake_scan():
            print("scanned ok in-process")
            return "summary.md"

        def fake_sync():
            print("synced ok in-process")
            return {"repositories": []}

        with patch.object(research_scanner, "run_scan", side_effect=fake_scan) as m_scan, \
             patch.object(repo_sync, "run_sync", side_effect=fake_sync) as m_sync, \
             patch.object(subprocess, "run", side_effect=AssertionError("subprocess.run must not be called")):
            res_out = cmo_runtime.run_research()
            sync_out = cmo_runtime.run_repo_sync()

        m_scan.assert_called_once()
        m_sync.assert_called_once()
        self.assertIn("scanned ok in-process", res_out)
        self.assertIn("synced ok in-process", sync_out)

    def test_03_cards_render_in_process_and_pillow_missing_error(self):
        """/api/cards/render renders PNGs in-process and reports clean error if Pillow is missing."""
        with patch.object(subprocess, "run", side_effect=AssertionError("subprocess.run must not be called")):
            h = _StubPostHandler(
                "/api/cards/render",
                method="POST",
                body_dict={
                    "post_id": "test-card-pkg",
                    "field": "sky",
                    "kicker": "CERO",
                    "big": ["ASKS", "FIRST."],
                    "serif": "before touching files.",
                    "foot": "magnm.org",
                    "sizes": ["4x5", "1x1"],
                },
            )
            h.do_POST()

        self.assertEqual(h.status_code, 200)
        payload = h.response_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["rendered_files"], ["test-card-pkg_4x5.png", "test-card-pkg_1x1.png"])
        for fname in payload["rendered_files"]:
            fpath = paths.data("outputs", "posts", "test-card-pkg", fname)
            self.assertTrue(os.path.isfile(fpath), f"Missing rendered card {fpath}")
            self.assertGreater(os.path.getsize(fpath), 0)
        self.assertTrue(os.path.isfile(paths.data("outputs", "posts", "test-card-pkg", "POSTING-KIT.md")))

        # Simulate missing Pillow during card module load
        orig_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("No module named 'PIL'")
            return orig_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            h_err = _StubPostHandler(
                "/api/cards/render",
                method="POST",
                body_dict={"post_id": "test-card-nopil", "big": ["TEST"]},
            )
            h_err.do_POST()

        err_payload = h_err.response_json()
        self.assertFalse(err_payload["success"])
        self.assertEqual(err_payload["error"], "Card rendering needs the Pillow library.")

    def test_04_setup_check_includes_git(self):
        """setup_check reports missing git with expected fix text."""
        with patch.object(setup_check.shutil, "which", return_value=None):
            res = setup_check._check_git()
        self.assertEqual(res["id"], "git")
        self.assertEqual(res["status"], "missing")
        self.assertEqual(res["fix"], "Install Git from git-scm.com")

    def test_05_import_legacy_state_dedupes_when_run_twice(self):
        """db.import_legacy_state() imports and deduplicates across repeated calls even after files_migrated."""
        with tempfile.TemporaryDirectory() as legacy_dir:
            src_state = os.path.join(legacy_dir, "state")
            src_chats = os.path.join(src_state, "chats")
            os.makedirs(src_chats, exist_ok=True)

            with open(os.path.join(src_state, "global_messages.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "messages": [
                            {"id": "m1", "sender": "CEO", "text": "hello", "timestamp": "2026-11-01T00:00:00Z"},
                            {"id": "m2", "sender": "cmo", "text": "ready", "timestamp": "2026-11-01T00:01:00Z"},
                        ]
                    },
                    f,
                )
            with open(os.path.join(src_chats, "writer.json"), "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {"sender": "user", "text": "draft a post", "timestamp": "2026-11-01T00:02:00Z"},
                        {"sender": "assistant", "text": "here is draft", "timestamp": "2026-11-01T00:03:00Z"},
                    ],
                    f,
                )
            with open(os.path.join(src_state, "assets.json"), "w", encoding="utf-8") as f:
                json.dump({"assets": [{"id": "asset-1", "filename": "shot.png"}]}, f)
            with open(os.path.join(src_state, "schedules.json"), "w", encoding="utf-8") as f:
                json.dump({"schedules": [{"id": "sched-1", "name": "Daily", "command": "daily_cycle"}]}, f)
            with open(os.path.join(src_state, "activity.log"), "w", encoding="utf-8") as f:
                f.write(
                    "2026-11-01T00:00:00Z | CEO -> CMO | launch | outputs/posts/1/READY.md\n"
                    "2026-11-01T00:00:00Z | CEO -> CMO | launch | outputs/posts/1/READY.md\n"
                )

            first = db.import_legacy_state(src_state)
            self.assertEqual(first["messages"], 2)
            self.assertEqual(first["chat_turns"], 2)
            self.assertEqual(first["assets"], 1)
            self.assertEqual(first["schedules"], 1)
            self.assertEqual(first["activity"], 1)

            second = db.import_legacy_state(src_state)
            self.assertEqual(second["messages"], 0)
            self.assertEqual(second["chat_turns"], 0)
            self.assertEqual(second["assets"], 0)
            self.assertEqual(second["schedules"], 0)
            self.assertEqual(second["activity"], 0)

            self.assertEqual(len(db.load_messages(limit=50)), 2)
            self.assertEqual(len(db.get_agent_chat_history("writer")), 2)
            self.assertEqual(len(db.load_assets()), 1)
            self.assertEqual(len(db.load_schedules()), 1)
            self.assertEqual(len(db.get_activity_lines(limit=50)), 1)

    def test_06_api_import_legacy_validation_and_copy(self):
        """/api/import_legacy rejects DATA_ROOT and bad paths, and imports valid legacy folders without overwriting."""
        # Reject DATA_ROOT itself
        h_self = _StubPostHandler("/api/import_legacy", method="POST", body_dict={"path": self.tmpdir})
        h_self.do_POST()
        self.assertEqual(h_self.status_code, 400)
        self.assertIn("already using this folder", h_self.response_json()["error"].lower())

        # Reject non-existent path
        missing_dir = os.path.join(self.tmpdir, "does-not-exist")
        h_missing = _StubPostHandler("/api/import_legacy", method="POST", body_dict={"path": missing_dir})
        h_missing.do_POST()
        self.assertEqual(h_missing.status_code, 400)
        self.assertFalse(h_missing.response_json()["success"])

        with tempfile.TemporaryDirectory() as legacy_dir:
            # Reject folder without state/ or outputs/
            h_bad = _StubPostHandler("/api/import_legacy", method="POST", body_dict={"path": legacy_dir})
            h_bad.do_POST()
            self.assertEqual(h_bad.status_code, 400)

            # Populate valid legacy folder
            os.makedirs(os.path.join(legacy_dir, "outputs", "posts", "old-post"), exist_ok=True)
            with open(os.path.join(legacy_dir, "outputs", "posts", "old-post", "READY.md"), "w", encoding="utf-8") as f:
                f.write("old post content\n")
            os.makedirs(os.path.join(legacy_dir, "state"), exist_ok=True)
            with open(os.path.join(legacy_dir, "state", "global_messages.json"), "w", encoding="utf-8") as f:
                json.dump({"messages": [{"id": "legacy-1", "sender": "CEO", "text": "hi"}]}, f)

            # Pre-existing file in DATA_ROOT must not be overwritten
            os.makedirs(paths.data("plan"), exist_ok=True)
            os.makedirs(os.path.join(legacy_dir, "plan"), exist_ok=True)
            with open(paths.data("plan", "plan.md"), "w", encoding="utf-8") as f:
                f.write("keep existing\n")
            with open(os.path.join(legacy_dir, "plan", "plan.md"), "w", encoding="utf-8") as f:
                f.write("should not overwrite\n")

            h_ok = _StubPostHandler("/api/import_legacy", method="POST", body_dict={"path": legacy_dir})
            h_ok.do_POST()
            self.assertEqual(h_ok.status_code, 200)
            res = h_ok.response_json()
            self.assertTrue(res["success"])
            self.assertGreaterEqual(res["files_copied"], 1)
            self.assertEqual(res["db"]["messages"], 1)

            self.assertTrue(os.path.isfile(paths.data("outputs", "posts", "old-post", "READY.md")))
            with open(paths.data("plan", "plan.md"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "keep existing\n")


if __name__ == "__main__":
    unittest.main()
