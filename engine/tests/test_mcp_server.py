#!/usr/bin/env python3
"""Tests for engine_endpoint.json discovery, mcp_server.py stdio JSON-RPC, confidentiality, and MCP settings."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(_ENGINE_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import coding_agents  # noqa: E402
import db  # noqa: E402
import mcp_tools  # noqa: E402
import paths  # noqa: E402
import product_rules  # noqa: E402
import reminders  # noqa: E402
import server  # noqa: E402
import weekly_summary  # noqa: E402


def _seed_data_dir(tmpdir):
    os.makedirs(os.path.join(tmpdir, "state"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "config"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "research"), exist_ok=True)
    shutil.copy2(
        os.path.join(_REPO_ROOT, "config", "product_rules.json"),
        os.path.join(tmpdir, "config", "product_rules.json"),
    )
    shutil.copy2(
        os.path.join(_REPO_ROOT, "research", "product-facts.md"),
        os.path.join(tmpdir, "research", "product-facts.md"),
    )


def _rpc(proc, req_id, method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if req_id is None:
        return None
    line = proc.stdout.readline()
    if not line:
        stderr_out = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(f"MCP server closed stdout unexpectedly. stderr={stderr_out!r}")
    return json.loads(line)


class McpServerSubprocessTests(unittest.TestCase):
    """End-to-end stdio tests driving engine/mcp_server.py against a real temp engine."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="magnm-mcp-test-")
        _seed_data_dir(self.tmpdir)
        self.token = "mcp-test-secret-token"
        self.engine_proc = None
        self.mcp_proc = None

    def tearDown(self):
        for proc in (self.mcp_proc, self.engine_proc):
            if proc is None:
                continue
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            for stream in (proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    try:
                        stream.close()
                    except Exception:
                        pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start_temp_engine(self):
        env = os.environ.copy()
        env["MAGNM_HOME"] = self.tmpdir
        env["MAGNM_ENGINE_TOKEN"] = self.token
        env["MAGNM_DISABLE_SCHEDULER"] = "1"
        env["MAGNM_DISABLE_CLOUD_SYNC"] = "1"
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join(_ENGINE_DIR, "main.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--data-dir",
                self.tmpdir,
            ],
            cwd=_REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.engine_proc = proc
        ready_line = ""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("MAGNM_ENGINE_READY "):
                ready_line = line
                break
        if not ready_line:
            stderr_txt = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"Temp engine did not emit READY. stderr={stderr_txt!r}")
        self.assertNotIn(self.token, ready_line)
        self.assertNotIn("engine_endpoint.json", ready_line)
        info = json.loads(ready_line.split(" ", 1)[1])
        return info["port"]

    def _start_mcp(self):
        env = os.environ.copy()
        env.pop("MAGNM_HOME", None)
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join(_ENGINE_DIR, "mcp_server.py"),
                "--data-dir",
                self.tmpdir,
            ],
            cwd=_REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.mcp_proc = proc
        return proc

    def _http(self, port, method, path, payload=None):
        headers = {"X-Magnm-Token": self.token, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_endpoint_file_mode_0600_and_removed_on_shutdown(self):
        port = self._start_temp_engine()
        ep_path = os.path.join(self.tmpdir, "state", "engine_endpoint.json")
        self.assertTrue(os.path.isfile(ep_path))
        self.assertEqual(stat.S_IMODE(os.stat(ep_path).st_mode), 0o600)

        with open(ep_path, "r", encoding="utf-8") as f:
            ep_data = json.load(f)
        self.assertEqual(ep_data["url"], f"http://127.0.0.1:{port}")
        self.assertEqual(ep_data["token"], self.token)
        self.assertEqual(ep_data["pid"], self.engine_proc.pid)
        self.assertTrue(ep_data["started_at"])

        # Clean shutdown via /api/shutdown removes engine_endpoint.json
        st, body = self._http(port, "POST", "/api/shutdown", {})
        self.assertEqual(st, 200)
        self.assertTrue(body.get("ok"))
        self.engine_proc.wait(timeout=5)
        self.assertFalse(os.path.exists(ep_path))

        # Verify .gitignore ignores state/engine_endpoint.json
        with open(os.path.join(_REPO_ROOT, ".gitignore"), "r", encoding="utf-8") as f:
            self.assertIn("state/engine_endpoint.json", f.read())

    def test_server_py_entrypoint_writes_and_removes_endpoint_file(self):
        """engine/server.py main() also writes mode-0600 engine_endpoint.json and removes it on shutdown."""
        orig_host = server.HOST
        orig_port = server.PORT
        orig_token = server.ENGINE_TOKEN
        old_db_path = db._db_path_override
        old_data_root = paths.DATA_ROOT
        try:
            paths.DATA_ROOT = self.tmpdir
            db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
            server.HOST = "127.0.0.1"
            server.PORT = 0
            server.ENGINE_TOKEN = "srv-entry-tok"

            t = threading.Thread(target=server.main, daemon=True)
            t.start()

            ep_path = os.path.join(self.tmpdir, "state", "engine_endpoint.json")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not os.path.isfile(ep_path):
                time.sleep(0.05)
            self.assertTrue(os.path.isfile(ep_path))
            self.assertEqual(stat.S_IMODE(os.stat(ep_path).st_mode), 0o600)
            with open(ep_path, "r", encoding="utf-8") as f:
                ep = json.load(f)
            self.assertEqual(ep["token"], "srv-entry-tok")
            port = int(ep["url"].rsplit(":", 1)[1])

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/shutdown",
                data=b"{}",
                headers={"X-Magnm-Token": "srv-entry-tok", "Content-Type": "application/json"},
                method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
            t.join(timeout=5)
            self.assertFalse(os.path.exists(ep_path))
        finally:
            server.HOST = orig_host
            server.PORT = orig_port
            server.ENGINE_TOKEN = orig_token
            paths.DATA_ROOT = old_data_root
            db.set_db_path(old_db_path)

    def test_handshake_version_negotiation_ping_and_unknown_method(self):
        proc = self._start_mcp()

        # Older version falls back to client's version
        res1 = _rpc(proc, 1, "initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(res1["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(res1["result"]["serverInfo"]["name"], "magnm")

        # Newer or exact version negotiates 2025-06-18
        res2 = _rpc(proc, 2, "initialize", {"protocolVersion": "2026-01-01"})
        self.assertEqual(res2["result"]["protocolVersion"], "2025-06-18")

        # notifications/initialized emits nothing; ping responds immediately after
        _rpc(proc, None, "notifications/initialized", {})
        ping_res = _rpc(proc, 3, "ping")
        self.assertEqual(ping_res["result"], {})

        # Unknown method -> -32601
        unk_res = _rpc(proc, 4, "resources/list")
        self.assertEqual(unk_res["error"]["code"], -32601)

    def test_engine_not_running_error(self):
        proc = self._start_mcp()
        _rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18"})

        res = _rpc(proc, 2, "tools/call", {"name": "list_drafts", "arguments": {}})
        self.assertTrue(res["result"]["isError"])
        self.assertEqual(
            res["result"]["content"][0]["text"],
            "Magnm Studio is not running. Start it, then try again.",
        )

    def test_full_mcp_lifecycle_reads_writes_validation_truncation_and_confidentiality(self):
        # Pre-seed temp DB before starting engine (so DB has read fixtures + confidential fixtures)
        old_db_path = db._db_path_override
        old_data_root = paths.DATA_ROOT
        try:
            paths.DATA_ROOT = self.tmpdir
            db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
            db.init_db()

            # Non-confidential draft & huge draft for truncation test
            db.create_content_item(
                "cnt_public_1",
                "post",
                "linkedin",
                "Cero Release Notes",
                "Cero shipped faster builds today.",
                status="draft",
                product_id="cero",
            )
            db.create_content_item(
                "cnt_huge_1",
                "blog",
                "blog",
                "Huge Public Post",
                "A" * 25000,
                status="draft",
                product_id="cero",
            )
            # Confidential draft by session, by product_id, and by body mention
            conf_sess = db.create_agent_session({
                "id": "sess_conf_1",
                "agent_id": "writer",
                "status": "done",
                "messages": [{"role": "user", "content": "Secret prompt"}],
                "confidential": True,
            })
            db.create_content_item(
                "cnt_conf_sess",
                "post",
                "x",
                "Internal session draft",
                "Public words only",
                session_id=conf_sess["id"],
            )
            db.create_content_item(
                "cnt_conf_prod",
                "post",
                "x",
                "Something new",
                "Teasing LazyLayout features here",
                product_id="lazylayout",
            )
            db.create_content_item(
                "cnt_conf_body",
                "post",
                "x",
                "Plain title",
                "Hidden mention of LazyLayout in body",
            )

            # Research notes (public + confidential)
            db.save_research_note(
                "note_pub_1",
                "Developer CLI adoption",
                "CLI workflows are growing fast.",
                url="https://example.org/cli",
                source="hn",
            )
            db.save_research_note(
                "note_conf_1",
                "LazyLayout competitor note",
                "Secret comparison",
                url="https://example.org/secret",
            )

            # Reminders (public + confidential)
            reminders.create_reminder(
                "Publish Cero recap",
                "2099-01-01T10:00:00+05:30",
                note="Check hashtags",
            )
            reminders.create_reminder(
                "Review LazyLayout teaser",
                "2099-01-02T10:00:00+05:30",
                content_id="cnt_conf_prod",
            )

            # Weekly summary (public)
            db.upsert_weekly_summary(
                "2026-11-09",
                data={"week_start": "2026-11-09", "week_end": "2026-11-15", "posts": [], "drafts_created": []},
                highlights={"counts": {"posts_posted": 0}},
                narrative="Quiet week for Cero.",
                status="ready",
            )

            # Repos (public + confidential)
            with open(os.path.join(self.tmpdir, "config", "repos.json"), "w", encoding="utf-8") as rf:
                json.dump(
                    {
                        "repositories": [
                            {
                                "id": "cero",
                                "name": "Cero",
                                "product_id": "cero",
                                "kind": "github",
                                "github": "magnm/cero",
                                "confidential": False,
                            },
                            {
                                "id": "secret-repo",
                                "name": "SecretRepo",
                                "product_id": "lazylayout",
                                "kind": "local",
                                "local_path": "/tmp/secret-repo",
                                "confidential": True,
                            },
                        ]
                    },
                    rf,
                )
            # Draft mentioning confidential repo name
            db.create_content_item(
                "cnt_conf_repo",
                "post",
                "x",
                "Repo update",
                "Built inside SecretRepo yesterday.",
            )
        finally:
            paths.DATA_ROOT = old_data_root
            db.set_db_path(old_db_path)

        port = self._start_temp_engine()
        proc = self._start_mcp()
        _rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18"})

        # 1. tools/list with writes disabled (default): 7 read tools only, no write or never-exposed tools
        tl1 = _rpc(proc, 2, "tools/list")
        names1 = [t["name"] for t in tl1["result"]["tools"]]
        self.assertEqual(
            set(names1),
            {
                "list_drafts",
                "read_draft",
                "list_research_notes",
                "latest_weekly_summary",
                "list_reminders",
                "product_rules",
                "list_repos",
            },
        )
        for forbidden in (
            "create_draft",
            "add_research_note",
            "set_reminder",
            "approve_coding_run",
            "list_coding_runs",
            "publish_post",
            "delete_content",
            "get_keys",
            "admin_users",
        ):
            self.assertNotIn(forbidden, names1)

        # 2. Calling a write tool directly while disabled is refused with JSON-RPC error
        refused = _rpc(
            proc,
            3,
            "tools/call",
            {
                "name": "create_draft",
                "arguments": {
                    "title": "Blocked",
                    "body": "Body",
                    "platform": "x",
                    "type": "post",
                },
            },
        )
        self.assertIn("error", refused)
        self.assertEqual(refused["error"]["code"], -32602)

        # 3. Invalid arguments -> JSON-RPC error (-32602)
        bad_args = _rpc(proc, 4, "tools/call", {"name": "list_drafts", "arguments": {"limit": "ten"}})
        self.assertEqual(bad_args["error"]["code"], -32602)
        bad_extra = _rpc(proc, 5, "tools/call", {"name": "list_repos", "arguments": {"foo": 1}})
        self.assertEqual(bad_extra["error"]["code"], -32602)
        unknown_tool = _rpc(proc, 6, "tools/call", {"name": "delete_everything", "arguments": {}})
        self.assertEqual(unknown_tool["error"]["code"], -32602)

        # 4. Read tools filter out all confidential records
        drafts_res = json.loads(_rpc(proc, 7, "tools/call", {"name": "list_drafts", "arguments": {}})["result"]["content"][0]["text"])
        draft_ids = {d["id"] for d in drafts_res["drafts"]}
        self.assertEqual(draft_ids, {"cnt_public_1", "cnt_huge_1"})

        # Reading confidential draft is refused
        conf_read = _rpc(proc, 8, "tools/call", {"name": "read_draft", "arguments": {"content_id": "cnt_conf_prod"}})
        self.assertTrue(conf_read["result"]["isError"])

        # Reading huge draft truncates cleanly to <= 20,000 chars
        huge_read = _rpc(proc, 9, "tools/call", {"name": "read_draft", "arguments": {"content_id": "cnt_huge_1"}})
        huge_text = huge_read["result"]["content"][0]["text"]
        self.assertLessEqual(len(huge_text), 20000)
        self.assertTrue(huge_text.endswith("... [truncated]"))

        notes_res = json.loads(_rpc(proc, 10, "tools/call", {"name": "list_research_notes", "arguments": {}})["result"]["content"][0]["text"])
        self.assertEqual([n["id"] for n in notes_res["notes"]], ["note_pub_1"])

        rem_res = json.loads(_rpc(proc, 11, "tools/call", {"name": "list_reminders", "arguments": {"status": "open"}})["result"]["content"][0]["text"])
        self.assertEqual([r["title"] for r in rem_res["reminders"]], ["Publish Cero recap"])

        sum_res = json.loads(_rpc(proc, 12, "tools/call", {"name": "latest_weekly_summary", "arguments": {}})["result"]["content"][0]["text"])
        self.assertEqual(sum_res["summary"]["week_start"], "2026-11-09")

        rules_res = json.loads(_rpc(proc, 13, "tools/call", {"name": "product_rules", "arguments": {}})["result"]["content"][0]["text"])
        rule_ids = [p["id"] for p in rules_res["products"]]
        self.assertEqual(rule_ids, ["cero"])
        self.assertNotIn("lazylayout", rule_ids)

        repos_res = json.loads(_rpc(proc, 14, "tools/call", {"name": "list_repos", "arguments": {}})["result"]["content"][0]["text"])
        self.assertEqual([r["id"] for r in repos_res["repos"]], ["cero"])

        # 5. Enable writes via engine settings API -> tools/list now shows all 10 tools
        st, set_res = self._http(port, "POST", "/api/coding/mcp", {"writes_enabled": True})
        self.assertEqual(st, 200)
        self.assertTrue(set_res["writes_enabled"])

        tl2 = _rpc(proc, 15, "tools/list")
        names2 = [t["name"] for t in tl2["result"]["tools"]]
        self.assertIn("create_draft", names2)
        self.assertIn("add_research_note", names2)
        self.assertIn("set_reminder", names2)
        self.assertEqual(len(names2), 10)

        # 6. create_draft goes through normal content creation + Reviewer + product rules
        created = json.loads(
            _rpc(
                proc,
                16,
                "tools/call",
                {
                    "name": "create_draft",
                    "arguments": {
                        "title": "Cero MCP Draft",
                        "body": "Introducing revolutionary ROS support in Cero!",
                        "platform": "x",
                        "type": "post",
                    },
                },
            )["result"]["content"][0]["text"]
        )
        new_cid = created["content_id"]
        self.assertTrue(new_cid.startswith("cnt_"))
        self.assertIn("review it in Magnm", created["note"])
        # Product rules warning for blocked term ROS and Reviewer warning for hype word revolutionary
        self.assertTrue(any("ROS" in w.get("message", "") for w in created["warnings"]))
        self.assertTrue(
            any(f.get("code") == "hype_word" for f in created["review"]["findings"])
        )

        # Appears in list_drafts
        drafts_after = json.loads(_rpc(proc, 17, "tools/call", {"name": "list_drafts", "arguments": {}})["result"]["content"][0]["text"])
        self.assertIn(new_cid, {d["id"] for d in drafts_after["drafts"]})

        # 7. add_research_note and set_reminder persist and appear in reads
        note_created = json.loads(
            _rpc(
                proc,
                18,
                "tools/call",
                {
                    "name": "add_research_note",
                    "arguments": {
                        "title": "MCP Protocol Note",
                        "body": "Stdio JSON-RPC works cleanly.",
                        "url": "https://example.org/mcp",
                    },
                },
            )["result"]["content"][0]["text"]
        )
        self.assertEqual(note_created["note"]["title"], "MCP Protocol Note")

        rem_created = json.loads(
            _rpc(
                proc,
                19,
                "tools/call",
                {
                    "name": "set_reminder",
                    "arguments": {
                        "title": "Follow up on MCP draft",
                        "due_at": "2099-02-01T10:00:00+05:30",
                        "note": "Review in Studio",
                    },
                },
            )["result"]["content"][0]["text"]
        )
        self.assertEqual(rem_created["reminder"]["title"], "Follow up on MCP draft")

        # 8. Activity feed logged and calls counted today
        st_mcp, mcp_info = self._http(port, "GET", "/api/coding/mcp")
        self.assertEqual(st_mcp, 200)
        self.assertGreaterEqual(mcp_info["calls_today"], 10)
        # Ensure engine_endpoint.json / token is never exposed in settings or snippets
        serialized_info = json.dumps(mcp_info)
        self.assertNotIn("engine_endpoint.json", serialized_info)
        self.assertNotIn(self.token, serialized_info)

        # Check SQLite activity feed directly via /api/status
        _, status_data = self._http(port, "GET", "/api/status")
        act_lines = "\n".join(status_data.get("activity_log") or [])
        self.assertIn("MCP: set_reminder by coding agent", act_lines)


class McpFakeHttpAndSecurityTests(unittest.TestCase):
    """Fake HTTP server + unit security tests for MCP tools and CLI snippets."""

    def test_fake_http_server_protocol(self):
        calls = []

        class _FakeHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/api/coding/mcp":
                    body = json.dumps({"writes_enabled": False}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                req_json = json.loads(raw)
                calls.append((self.headers.get("X-Magnm-Token"), req_json))
                body = json.dumps({"ok": True, "result": {"repos": [{"id": "demo"}]}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()

        tmpdir = tempfile.mkdtemp(prefix="magnm-mcp-fake-")
        os.makedirs(os.path.join(tmpdir, "state"), exist_ok=True)
        with open(os.path.join(tmpdir, "state", "engine_endpoint.json"), "w", encoding="utf-8") as f:
            json.dump({"url": f"http://127.0.0.1:{port}", "token": "fake-tok", "pid": os.getpid()}, f)

        proc = subprocess.Popen(
            [sys.executable, os.path.join(_ENGINE_DIR, "mcp_server.py"), "--data-dir", tmpdir],
            cwd=_REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18"})
            res = _rpc(proc, 2, "tools/call", {"name": "list_repos", "arguments": {}})
            parsed = json.loads(res["result"]["content"][0]["text"])
            self.assertEqual(parsed, {"repos": [{"id": "demo"}]})
            self.assertEqual(calls, [("fake-tok", {"name": "list_repos", "arguments": {}})])
        finally:
            proc.stdin.close()
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stderr.close()
            httpd.shutdown()
            httpd.server_close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_unreadable_product_rules_fails_closed(self):
        tmpdir = tempfile.mkdtemp(prefix="magnm-mcp-norules-")
        old_db_path = db._db_path_override
        old_data_root = paths.DATA_ROOT
        try:
            paths.DATA_ROOT = tmpdir
            os.makedirs(os.path.join(tmpdir, "state"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "config"), exist_ok=True)
            db.set_db_path(os.path.join(tmpdir, "state", "magnm.db"))
            db.init_db()
            # Corrupt product_rules.json so confidential_checker() raises
            with open(os.path.join(tmpdir, "config", "product_rules.json"), "w", encoding="utf-8") as f:
                f.write("{invalid json")

            with self.assertRaises(mcp_tools.McpToolError) as ctx:
                mcp_tools.execute_tool("list_drafts", {})
            self.assertEqual(ctx.exception.error_code, "product_rules_unreadable")
        finally:
            paths.DATA_ROOT = old_data_root
            db.set_db_path(old_db_path)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_confidential_weekly_summary_refused(self):
        tmpdir = tempfile.mkdtemp(prefix="magnm-mcp-confsum-")
        _seed_data_dir(tmpdir)
        old_db_path = db._db_path_override
        old_data_root = paths.DATA_ROOT
        try:
            paths.DATA_ROOT = tmpdir
            db.set_db_path(os.path.join(tmpdir, "state", "magnm.db"))
            db.init_db()
            db.upsert_weekly_summary(
                "2026-11-16",
                data={"week_start": "2026-11-16", "posts": [], "drafts_created": [{"content_id": "c1", "title": "LazyLayout launch"}]},
                highlights={"counts": {"posts_posted": 0}},
                narrative="LazyLayout teaser summary.",
                status="ready",
            )
            with self.assertRaises(mcp_tools.McpToolError) as ctx:
                mcp_tools.execute_tool("latest_weekly_summary", {})
            self.assertEqual(ctx.exception.error_code, "confidential")
        finally:
            paths.DATA_ROOT = old_data_root
            db.set_db_path(old_db_path)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_cli_snippets_use_real_paths_for_installed_clis_without_endpoint_leak(self):
        fake_clis = [
            {"id": "claude", "name": "Claude Code", "installed": True},
            {"id": "gemini", "name": "Gemini CLI", "installed": True},
            {"id": "codex", "name": "Codex", "installed": True},
            {"id": "agy", "name": "Antigravity", "installed": False},
        ]
        snippets = mcp_tools.build_cli_snippets(clis=fake_clis)
        ids = [s["id"] for s in snippets]
        self.assertEqual(ids, ["claude", "gemini", "codex"])
        expected_mcp = os.path.abspath(os.path.join(_ENGINE_DIR, "mcp_server.py"))
        expected_data = os.path.abspath(paths.DATA_ROOT)
        for s in snippets:
            self.assertIn(expected_mcp, s["snippet"])
            self.assertIn(expected_data, s["snippet"])
            self.assertNotIn("engine_endpoint.json", s["snippet"])
        claude_snip = next(s for s in snippets if s["id"] == "claude")["snippet"]
        self.assertTrue(claude_snip.startswith("claude mcp add magnm -- python3 "))
        codex_snip = next(s for s in snippets if s["id"] == "codex")["snippet"]
        self.assertIn("[mcp_servers.magnm]", codex_snip)
        gemini_snip = next(s for s in snippets if s["id"] == "gemini")["snippet"]
        self.assertIn('"mcpServers"', gemini_snip)


class McpFailClosedTests(unittest.TestCase):
    def test_unreadable_repo_list_pauses_every_tool(self):
        with patch.object(mcp_tools.cloud_sync, "confidential_scope", return_value=(set(), set(), lambda text: False)), \
                patch.object(mcp_tools.product_rules, "confidential_product_ids", return_value=set()), \
                patch.object(mcp_tools.repos, "list_repos", side_effect=OSError("unreadable")):
            with self.assertRaises(mcp_tools.McpToolError) as raised:
                mcp_tools._build_confidential_guard()
        self.assertEqual(raised.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
