"""Unit tests for engine.db SQLite persistence, migration, concurrency, and run logs."""

import importlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_chat
import ai_gateway
import assets_manager
import cmo_runtime
import db
import model_registry
import paths
import scheduler
import server
import team_room


class _FakeHTTPResponse:
    """Minimal context-manager mock for urllib.request.urlopen responses."""

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
    """In-memory HTTP handler stub for testing routes without sockets."""

    def __init__(self, path, headers=None):
        self.path = path
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.headers = dict(headers or {})
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


class DbTests(unittest.TestCase):
    """Tests for SQLite DB setup, file migration, round-trips, concurrency, and run logging."""

    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        self._tmpdir_obj.cleanup()

    def test_01_init_db_idempotent_and_sets_schema_version(self):
        """Calling init_db twice is idempotent and records schema_version."""
        db.init_db()
        db.init_db()

        conn = db.connect()
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version ORDER BY version ASC").fetchall()]
        self.assertEqual(versions, list(range(1, db.SCHEMA_VERSION + 1)))

        meta_row = conn.execute("SELECT value FROM meta WHERE key = 'files_migrated'").fetchone()
        self.assertIsNotNone(meta_row)

    def test_02_migrate_from_files_imports_once_skips_malformed_and_preserves_originals(self):
        """Migration imports all legacy state files once, skips malformed lines, and leaves originals intact."""
        state_dir = paths.data("state")
        chats_dir = paths.data("state", "chats")
        os.makedirs(chats_dir, exist_ok=True)

        msgs_path = os.path.join(state_dir, "global_messages.json")
        writer_path = os.path.join(chats_dir, "writer.json")
        assets_path = os.path.join(state_dir, "assets.json")
        sched_path = os.path.join(state_dir, "schedules.json")
        act_path = os.path.join(state_dir, "activity.log")

        with open(msgs_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "messages": [
                        {
                            "id": "m1",
                            "sender": "CEO",
                            "sender_name": "CEO (Founder)",
                            "sender_code": "CEO",
                            "text": "Hello team",
                            "mentions": ["cmo"],
                            "attached_assets": [],
                            "timestamp": "2026-11-12T07:00:00Z",
                        },
                        {
                            "id": "m2",
                            "sender": "cmo",
                            "sender_name": "CMO (Chief Marketing Officer)",
                            "sender_code": "CMO",
                            "text": "On it @writer",
                            "mentions": ["writer"],
                            "model_used": "test-model",
                            "timestamp": "2026-11-12T07:01:00Z",
                        },
                    ]
                },
                f,
            )

        with open(writer_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {"sender": "user", "text": "Draft a post", "timestamp": "2026-11-12T07:02:00Z"},
                    {"sender": "writer", "name": "Social Copywriter", "text": "Draft v1", "timestamp": "2026-11-12T07:02:05Z"},
                    {"sender": "user", "text": "Make it tighter", "timestamp": "2026-11-12T07:03:00Z"},
                ],
                f,
            )

        with open(assets_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "assets": [
                        {
                            "id": "asset_1",
                            "name": "shot.png",
                            "relative_path": "assets/uploads/asset_1.png",
                            "mime_type": "image/png",
                            "file_group": "image",
                            "uploaded_at": "2026-11-12T07:00:00Z",
                        }
                    ]
                },
                f,
            )

        with open(sched_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schedules": [
                        {"id": "second_first", "name": "Second First", "command": "repo_sync", "enabled": True},
                        {"id": "first_second", "name": "First Second", "command": "daily_cycle", "enabled": False},
                    ]
                },
                f,
            )

        with open(act_path, "w", encoding="utf-8") as f:
            f.write(
                "2026-11-12T07:00:00Z | CEO -> CMO | brief | briefs/a.md\n"
                "this is a malformed line without delimiters\n"
                "2026-11-12T07:01:00Z | CMO -> Writer | draft | outputs/b.md\n"
                "2026-11-12T07:02:00Z | Reviewer -> CMO | approve | reviews/c.md\n"
            )

        watched = [msgs_path, writer_path, assets_path, sched_path, act_path]
        before_bytes = {}
        for p in watched:
            with open(p, "rb") as f:
                before_bytes[p] = f.read()

        summary1 = db.init_db()
        self.assertFalse(summary1["already_migrated"])
        self.assertEqual(summary1["messages"], 2)
        self.assertEqual(summary1["chat_turns"], 3)
        self.assertEqual(summary1["assets"], 1)
        self.assertEqual(summary1["schedules"], 2)
        self.assertEqual(summary1["activity"], 3)
        self.assertEqual(summary1["skipped"], 1)

        summary2 = db.init_db()
        self.assertTrue(summary2["already_migrated"])

        self.assertEqual(len(team_room.load_messages(limit=100)), 2)
        self.assertEqual(len(agent_chat.get_agent_chat_history("writer")), 3)
        self.assertEqual(len(assets_manager.load_registry()), 1)
        scheds = scheduler.load_schedules()
        self.assertEqual([s["id"] for s in scheds], ["second_first", "first_second"])
        act_lines = cmo_runtime.get_activity_log(limit=20)
        self.assertEqual(
            act_lines,
            [
                "2026-11-12T07:00:00Z | CEO -> CMO | brief | briefs/a.md",
                "2026-11-12T07:01:00Z | CMO -> Writer | draft | outputs/b.md",
                "2026-11-12T07:02:00Z | Reviewer -> CMO | approve | reviews/c.md",
            ],
        )

        for p, expected in before_bytes.items():
            with open(p, "rb") as f:
                self.assertEqual(f.read(), expected)

    def test_03_save_and_load_message_round_trip_with_extra_fields(self):
        """save_message then load_messages returns an equal dict including extra fields."""
        db.init_db()
        original = {
            "id": "msg_roundtrip_1",
            "sender": "writer",
            "sender_name": "Social Copywriter",
            "sender_code": "WRT",
            "text": "Draft ready for @cmo",
            "mentions": ["cmo"],
            "attached_assets": ["asset_123"],
            "model_used": "deepseek-ai/deepseek-v4.1-flash",
            "provider": "nvidia_nim",
            "fallback_used": False,
            "capability_redirect": False,
            "custom_meta": {"nested": [1, 2, 3], "flag": True},
            "nullable_extra": None,
            "timestamp": "2026-11-12T08:00:00+00:00",
        }
        team_room.save_message(original)
        loaded = team_room.load_messages(limit=10)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0], original)

    def test_04_save_agent_chat_history_replaces_in_order(self):
        """save_agent_chat_history replaces an agent's turns and preserves order."""
        db.init_db()
        first = [
            {"sender": "user", "text": "Old 1", "timestamp": "2026-11-12T01:00:00Z"},
            {"sender": "writer", "name": "Social Copywriter", "text": "Old 2", "timestamp": "2026-11-12T01:00:01Z"},
        ]
        agent_chat.save_agent_chat_history("writer", first)
        self.assertEqual(agent_chat.get_agent_chat_history("writer"), first)

        replacement = [
            {"sender": "user", "text": "New 1", "attached_assets": ["a1"], "timestamp": "2026-11-12T02:00:00Z"},
            {"sender": "writer", "name": "Social Copywriter", "text": "New 2", "model_used": "m1", "timestamp": "2026-11-12T02:00:01Z"},
            {"sender": "user", "text": "New 3", "timestamp": "2026-11-12T02:00:02Z"},
        ]
        agent_chat.save_agent_chat_history("writer", replacement)
        self.assertEqual(agent_chat.get_agent_chat_history("writer"), replacement)

    def test_05_concurrent_save_message_8_threads_25_each(self):
        """8 threads each saving 25 messages produce 200 rows without locking errors."""
        db.init_db()
        errors = []
        barrier = threading.Barrier(8)

        def worker(tid):
            try:
                barrier.wait(timeout=5)
                for i in range(25):
                    team_room.save_message(
                        {
                            "id": f"msg_t{tid}_{i}",
                            "sender": "CEO",
                            "sender_name": "CEO (Founder)",
                            "sender_code": "CEO",
                            "text": f"Thread {tid} message {i}",
                            "mentions": [],
                            "attached_assets": [],
                            "timestamp": "2026-11-12T09:00:00Z",
                        }
                    )
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close_connection()

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [])
        all_msgs = team_room.load_messages(limit=500)
        self.assertEqual(len(all_msgs), 200)

    def test_06_gateway_logs_runs_on_success_and_ai_not_connected(self):
        """ai_gateway logs a run row on NIM success (with token usage) and on AINotConnected."""
        db.init_db()

        def fake_urlopen(req, timeout=45):
            return _FakeHTTPResponse(
                {
                    "choices": [{"message": {"content": "ready"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }
            )

        with patch.object(ai_gateway, "load_env", return_value={}), patch.dict(
            os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-secret-test"}, clear=True
        ), patch.object(model_registry, "get_catalog", return_value=None), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            res = ai_gateway.generate_completion_with_meta(
                system_prompt="Secret system prompt",
                user_prompt="Secret user prompt",
                agent_role="cmo",
            )
        self.assertEqual(res["text"], "ready")

        with patch.object(ai_gateway, "load_env", return_value={}), patch.dict(
            os.environ, {}, clear=True
        ), patch.object(ai_gateway, "_ollama_reachable", return_value=False), patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("offline")
        ):
            with self.assertRaises(ai_gateway.AINotConnected):
                ai_gateway.generate_completion_with_meta(
                    system_prompt="sys",
                    user_prompt="hi",
                    agent_role="writer",
                )

        runs = db.get_recent_runs(limit=10)
        self.assertEqual(len(runs), 2)

        # Newest first: failure row is index 0, success row is index 1
        fail_run, ok_run = runs[0], runs[1]
        self.assertFalse(fail_run["ok"])
        self.assertEqual(fail_run["role"], "writer")
        self.assertIsNone(fail_run["model_used"])
        self.assertIn("AI is not connected", fail_run["error"])

        self.assertTrue(ok_run["ok"])
        self.assertEqual(ok_run["role"], "cmo")
        self.assertEqual(ok_run["provider"], "nvidia_nim")
        self.assertEqual(ok_run["prompt_tokens"], 11)
        self.assertEqual(ok_run["completion_tokens"], 7)
        self.assertIsNone(ok_run["error"])

        # Verify prompts and keys never leak into the runs table
        serialized = json.dumps(runs)
        self.assertNotIn("nvapi-secret-test", serialized)
        self.assertNotIn("Secret system prompt", serialized)
        self.assertNotIn("Secret user prompt", serialized)

        # Verify GET /api/runs?limit=1 returns newest first and respects token gate
        with patch.object(server, "ENGINE_TOKEN", "tok123"):
            unauth = _StubHandler("/api/runs?limit=1")
            unauth.do_GET()
            self.assertEqual(unauth.status_code, 401)

            authed = _StubHandler("/api/runs?limit=1", headers={"X-Magnm-Token": "tok123"})
            authed.do_GET()
            self.assertEqual(authed.status_code, 200)
            payload = authed.response_json()
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["role"], "writer")

    def test_07_load_schedules_preserves_order_after_save(self):
        """load_schedules returns schedules in the exact order saved by save_schedules."""
        db.init_db()
        ordered = [
            {"id": "z_last_alpha", "name": "First", "command": "repo_sync", "enabled": True},
            {"id": "a_first_alpha", "name": "Second", "command": "daily_cycle", "enabled": False},
            {"id": "m_mid_alpha", "name": "Third", "command": "market_scan", "enabled": True},
        ]
        scheduler.save_schedules(ordered)
        loaded = scheduler.load_schedules()
        self.assertEqual(loaded, ordered)


if __name__ == "__main__":
    unittest.main()
