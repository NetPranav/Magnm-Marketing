#!/usr/bin/env python3
"""Comprehensive tests for confined coding agents, workspaces, copies, approval gate, confidentiality, env, hostile out/, and DB v21."""

from http.server import ThreadingHTTPServer
import importlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.join(os.path.dirname(_TESTS_DIR), "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cloud_sync  # noqa: E402
import coding_agents  # noqa: E402
import db  # noqa: E402
import events  # noqa: E402
import paths  # noqa: E402
import repos  # noqa: E402
import server  # noqa: E402
import tools as tools_pkg  # noqa: E402
from tools import ToolContext  # noqa: E402


_FAKE_CLAUDE_BODY = """\
import json
import os
import signal
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("claude 2.1.0 (Claude Code)")
    sys.exit(0)

if "--help" in args:
    print("Usage: claude [options]")
    print("  -p, --print")
    print("  --output-format <fmt>")
    print("  --verbose")
    print("  --restricted")
    print("  --strict-mcp-config")
    print("  --permission-mode <mode>")
    print("  --tools <tools>")
    print("  --allowedTools <tools...>")
    sys.exit(0)

prompt = sys.stdin.read()
record_path = os.path.join(os.getcwd(), "out", "_invoked.json")
with open(record_path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "argv": args,
            "stdin": prompt,
            "env": dict(os.environ),
            "cwd": os.getcwd(),
        },
        f,
    )

if "SLEEP_FOREVER" in prompt:
    child_pid_file = os.path.join(os.getcwd(), "out", "_child.pid")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) if False else None
    import subprocess as sp
    child = sp.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(child_pid_file, "w", encoding="utf-8") as cf:
        cf.write(str(child.pid))
    sys.stdout.write("\\x1b[32mstarting slow task\\x1b[0m\\n")
    sys.stdout.flush()
    while True:
        time.sleep(0.2)

if "FAIL_NOW" in prompt:
    sys.stdout.write("failing now\\n")
    sys.stdout.flush()
    sys.exit(2)

if "WRITE_OUT_FILES" in prompt:
    with open(os.path.join(os.getcwd(), "out", "report.md"), "w", encoding="utf-8") as rf:
        rf.write("# Report\\nDone\\n")
    with open(os.path.join(os.getcwd(), "out", "evil.html"), "w", encoding="utf-8") as hf:
        hf.write("<script>alert(1)</script>")
    with open(os.path.join(os.getcwd(), "out", "vector.svg"), "w", encoding="utf-8") as sf:
        sf.write("<svg onload=alert(1)></svg>")
    with open(os.path.join(os.getcwd(), "out", "script.js"), "w", encoding="utf-8") as jf:
        jf.write("alert(1);")

sys.stdout.write("\\x1b[36mWorking on task\\x1b[0m\\n")
sys.stdout.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Step 1 done."}]}}) + "\\n")
sys.stdout.write(json.dumps({"type": "result", "result": f"Final answer for: {prompt}"}) + "\\n")
sys.stdout.flush()
sys.exit(0)
"""


_FAKE_CLAUDE_OLD_BODY = """\
import sys
args = sys.argv[1:]
if "--version" in args:
    print("claude 1.0.0")
    sys.exit(0)
if "--help" in args:
    print("Usage: claude -p --tools Read")
    sys.exit(0)
sys.exit(1)
"""


_FAKE_GENERIC_CLI_BODY = """\
import sys
args = sys.argv[1:]
if "--version" in args:
    print("tool 0.9.0")
    sys.exit(0)
if "--help" in args:
    print("Usage: tool --acp")
    sys.exit(0)
sys.exit(99)
"""


_FAKE_SLEEP_VERSION_BODY = """\
import sys, time
if "--version" in sys.argv[1:]:
    time.sleep(2.0)
print("slow 1.0")
"""


class CodingAgentsTests(unittest.TestCase):
    """Test suite for coding_agents security boundaries, approval gate, and lifecycle."""

    def setUp(self):
        self._tmpdir_obj = tempfile.TemporaryDirectory(prefix="magnm-coding-test-")
        self.tmpdir = os.path.realpath(self._tmpdir_obj.name)
        self.home_dir = os.path.join(self.tmpdir, "home")
        self.xdg_cache_dir = os.path.join(self.tmpdir, "xdg_cache")
        self.magnm_home = os.path.join(self.tmpdir, "magnm_home")
        self.ws_dir = os.path.join(self.tmpdir, "coding_ws")
        self.fake_bin = os.path.join(self.tmpdir, "fake_bin")
        os.makedirs(self.home_dir, exist_ok=True)
        os.makedirs(self.xdg_cache_dir, exist_ok=True)
        os.makedirs(self.magnm_home, exist_ok=True)
        os.makedirs(self.ws_dir, exist_ok=True)
        os.makedirs(self.fake_bin, exist_ok=True)

        # Symlink only safe system tools (git, python3, env, sh) into fake_bin so real CLIs can never be found.
        for tool_name in ("git", "python3", "env", "sh"):
            found = shutil.which(tool_name)
            if found:
                dst = os.path.join(self.fake_bin, tool_name)
                if not os.path.exists(dst):
                    os.symlink(found, dst)

        self._saved_env = dict(os.environ)
        os.environ["HOME"] = self.home_dir
        os.environ["XDG_CACHE_HOME"] = self.xdg_cache_dir
        os.environ["MAGNM_HOME"] = self.magnm_home
        os.environ["MAGNM_CODING_WORKSPACES"] = self.ws_dir
        os.environ["PATH"] = self.fake_bin

        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        coding_agents.clear_detect_cache()

        self._prev_token = server.ENGINE_TOKEN
        server.ENGINE_TOKEN = None
        self._httpd = None

    def tearDown(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        server.ENGINE_TOKEN = self._prev_token
        self.assertTrue(coding_agents.wait_for_workers(), "coding worker outlived its test")
        coding_agents.clear_detect_cache()
        db.reset_connections()
        db.set_db_path(None)

        os.environ.clear()
        os.environ.update(self._saved_env)
        importlib.reload(paths)
        self._tmpdir_obj.cleanup()

    def _write_fake_cli(self, name, body):
        p = os.path.join(self.fake_bin, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\n")
            f.write(body)
        os.chmod(p, 0o755)
        return p

    def _init_git_repo(self, dirname, files, *, untracked=None):
        repo_dir = os.path.join(self.tmpdir, dirname)
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(["git", "-C", repo_dir, "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "Tester"], check=True)
        for rel, content in files.items():
            full = os.path.join(repo_dir, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m", "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for rel, content in (untracked or {}).items():
            full = os.path.join(repo_dir, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        return repo_dir

    def _write_repos_json(self, entries):
        cfg_path = paths.data("config", "repos.json")
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"repositories": entries}, f)

    def _start_server(self, token=None):
        server.ENGINE_TOKEN = token
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        return self._httpd.server_address[1]

    def _http_req(self, port, method, path, body=None, token=None):
        url = f"http://127.0.0.1:{port}{path}"
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Magnm-Token"] = token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), raw
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    # --- A. Workspace location ---

    def test_a_workspace_location_and_negative_paths(self):
        """Workspace lives outside DATA_ROOT, RESOURCE_ROOT, and git work trees; paths.py has no workspaces dir."""
        self.assertNotIn(("workspaces",), paths._EMPTY_DATA_DIRS)
        self.assertFalse(os.path.exists(paths.data("workspaces")))

        # Default location when MAGNM_CODING_WORKSPACES is unset uses user cache dir
        os.environ.pop("MAGNM_CODING_WORKSPACES", None)
        ws = coding_agents._resolve_workspace_for_run("crun_test_1")
        expected_prefix = os.path.join(self.xdg_cache_dir, "magnm-studio", "coding-runs")
        self.assertTrue(ws.startswith(expected_prefix), f"unexpected workspace: {ws}")
        self.assertFalse(coding_agents._is_path_within(paths.DATA_ROOT, ws))
        self.assertFalse(coding_agents._is_path_within(paths.RESOURCE_ROOT, ws))

        # Negative: under DATA_ROOT
        os.environ["MAGNM_CODING_WORKSPACES"] = paths.data("workspaces")
        with self.assertRaises(ValueError):
            coding_agents._resolve_workspace_for_run("crun_bad_1")

        # Negative: under RESOURCE_ROOT
        os.environ["MAGNM_CODING_WORKSPACES"] = os.path.join(paths.RESOURCE_ROOT, "tmp_ws")
        with self.assertRaises(ValueError):
            coding_agents._resolve_workspace_for_run("crun_bad_2")

        # Negative: inside a git work tree (.git dir and .git file)
        git_tree = os.path.join(self.tmpdir, "some_git_tree")
        os.makedirs(os.path.join(git_tree, ".git"), exist_ok=True)
        os.environ["MAGNM_CODING_WORKSPACES"] = os.path.join(git_tree, "sub")
        with self.assertRaises(ValueError):
            coding_agents._resolve_workspace_for_run("crun_bad_3")

        git_wt = os.path.join(self.tmpdir, "some_git_worktree")
        os.makedirs(git_wt, exist_ok=True)
        with open(os.path.join(git_wt, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: /tmp/elsewhere\n")
        os.environ["MAGNM_CODING_WORKSPACES"] = git_wt
        with self.assertRaises(ValueError):
            coding_agents._resolve_workspace_for_run("crun_bad_4")

        # Negative: symlink escaping to DATA_ROOT
        os.environ["MAGNM_CODING_WORKSPACES"] = self.ws_dir
        link_ws = os.path.join(self.ws_dir, "escape_run")
        os.symlink(paths.data("state"), link_ws)
        with self.assertRaises(ValueError):
            coding_agents.validate_workspace_path(link_ws, expected_base=self.ws_dir)

    # --- B. Copies ---

    def test_b_git_ls_files_only_no_secrets_no_symlinks_no_hooks_and_data_root_exclusions(self):
        """Git repos copy only tracked non-secret, non-symlink files with hooks/fsmonitor disabled."""
        outside_secret = os.path.join(self.tmpdir, "outside.txt")
        with open(outside_secret, "w", encoding="utf-8") as f:
            f.write("OUTSIDE_SECRET")

        repo_dir = self._init_git_repo(
            "repo_copy_test",
            files={
                "src/app.py": "print('ok')\n",
                "README.md": "# Repo\n",
                ".env": "SECRET=1\n",
                ".env.local": "SECRET=2\n",
                "certs/server.pem": "PEM",
                "certs/priv.key": "KEY",
                "certs/store.p12": "P12",
                "certs/trust.jks": "JKS",
                "keys/id_rsa": "RSA",
                "keys/id_ed25519": "ED",
                "auth/credentials.json": "{}",
                "auth/service-account-prod.json": "{}",
                "auth/team_session.json": "{}",
                "node_modules/pkg/index.js": "module.exports = 1;",
                ".venv/bin/activate": "echo hi",
            },
            untracked={
                "untracked_notes.txt": "should not be copied",
            },
        )
        # Add symlinks (outside and inside repo) and a fsmonitor hook sentinel
        os.symlink(outside_secret, os.path.join(repo_dir, "evil_link.txt"))
        os.symlink(os.path.join(repo_dir, "README.md"), os.path.join(repo_dir, "inside_link.md"))
        sentinel = os.path.join(self.tmpdir, "fsmonitor_ran")
        subprocess.run(
            ["git", "-C", repo_dir, "config", "core.fsmonitor", f"touch {sentinel} && false"],
            check=True,
        )

        self._write_repos_json([
            {
                "id": "demo",
                "name": "Demo",
                "product_id": "cero",
                "kind": "local",
                "confidential": False,
                "local_path": repo_dir,
            },
            {
                "id": "self-data",
                "name": "SelfData",
                "product_id": "cero",
                "kind": "local",
                "confidential": False,
                "local_path": paths.DATA_ROOT,
            },
        ])

        # Create files inside DATA_ROOT excluded folders + one allowed file
        for rel in (
            "state/secret.db",
            "queue/agy/job.json",
            "logs/app.log",
            "assets/uploads/pic.png",
            "assets/results/res.png",
            "outputs/post.md",
            "config.md",
        ):
            fp = paths.data(*rel.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write("data")

        ws = coding_agents.prepare_workspace("crun_copy_1", ["demo", "self-data"])
        try:
            self.assertFalse(os.path.exists(sentinel), "core.fsmonitor executed during copy!")
            demo_dst = os.path.join(ws, "repos", "demo")
            self.assertTrue(os.path.isfile(os.path.join(demo_dst, "src", "app.py")))
            self.assertTrue(os.path.isfile(os.path.join(demo_dst, "README.md")))

            # Read-only enforcement on POSIX
            if os.name != "nt":
                with self.assertRaises(PermissionError):
                    with open(os.path.join(demo_dst, "README.md"), "w", encoding="utf-8") as f:
                        f.write("mutated")

            for forbidden in (
                ".git",
                "untracked_notes.txt",
                "evil_link.txt",
                "inside_link.md",
                ".env",
                ".env.local",
                "certs/server.pem",
                "certs/priv.key",
                "certs/store.p12",
                "certs/trust.jks",
                "keys/id_rsa",
                "keys/id_ed25519",
                "auth/credentials.json",
                "auth/service-account-prod.json",
                "auth/team_session.json",
                "node_modules",
                ".venv",
            ):
                self.assertFalse(
                    os.path.lexists(os.path.join(demo_dst, forbidden)),
                    f"Forbidden path copied: {forbidden}",
                )

            data_dst = os.path.join(ws, "repos", "self-data")
            self.assertTrue(os.path.isfile(os.path.join(data_dst, "config.md")))
            for excluded in (
                "state",
                "queue",
                "logs",
                "assets/uploads",
                "assets/results",
                "outputs",
            ):
                self.assertFalse(
                    os.path.lexists(os.path.join(data_dst, excluded)),
                    f"DATA_ROOT excluded dir copied: {excluded}",
                )
        finally:
            coding_agents._restore_tree_writable_and_remove(ws)

    # --- C. Detection & confined engines ---

    def test_c_detection_confinement_flags_refusals_and_exact_args(self):
        """Only Claude with all 4 confinement flags can run; prompt on stdin; read_only saves out/answer.md."""
        # 1. Missing all CLIs
        det0 = coding_agents.detect(refresh=True)
        self.assertFalse(det0["claude"]["installed"])
        self.assertFalse(det0["claude"]["enabled"])
        with self.assertRaises(ValueError):
            coding_agents.run("claude", "hello")

        # 2. Outdated Claude (missing confinement flags) -> refused
        self._write_fake_cli("claude", _FAKE_CLAUDE_OLD_BODY)
        self._write_fake_cli("codex", _FAKE_GENERIC_CLI_BODY)
        self._write_fake_cli("agy", _FAKE_GENERIC_CLI_BODY)
        self._write_fake_cli("gemini", _FAKE_GENERIC_CLI_BODY)
        self._write_fake_cli("claude-code-acp", _FAKE_GENERIC_CLI_BODY)
        self._write_fake_cli("codex-acp", _FAKE_GENERIC_CLI_BODY)

        det1 = coding_agents.detect(refresh=True)
        self.assertTrue(det1["claude"]["installed"])
        self.assertFalse(det1["claude"]["enabled"])
        self.assertEqual(det1["claude"]["reason"], "Update Claude Code to use it here")

        for unconfined_id in ("codex", "agy", "gemini"):
            self.assertTrue(det1[unconfined_id]["installed"])
            self.assertFalse(det1[unconfined_id]["enabled"])
            self.assertEqual(
                det1[unconfined_id]["reason"],
                "Found, not enabled: this tool can't yet be confined to a workspace",
            )
            with self.assertRaises(ValueError):
                coding_agents.run(unconfined_id, "test prompt")
            with self.assertRaises(ValueError):
                coding_agents.set_agent_engine("writer", unconfined_id)

        adapters = coding_agents.list_detected_adapters()
        self.assertTrue(any(a["id"] == "claude-code-acp" and not a["enabled"] for a in adapters))
        self.assertTrue(
            all(a["reason"] == "Found, not enabled: this tool can't yet be confined to a workspace" for a in adapters)
        )

        with self.assertRaises(ValueError):
            coding_agents.run("claude", "hello")
        with self.assertRaises(ValueError):
            coding_agents.set_agent_engine("writer", "claude")

        # 3. Confined Claude -> enabled & runs with exact argv and stdin prompt
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        det2 = coding_agents.detect(refresh=True)
        self.assertTrue(det2["claude"]["enabled"])
        self.assertTrue(det2["claude"]["confined"])

        # Server refuses codex/agy/gemini on POST /api/coding/runs too
        port = self._start_server()
        for bad_eng in ("codex", "agy", "gemini"):
            st, _, _ = self._http_req(port, "POST", "/api/coding/runs", {"engine": bad_eng, "prompt": "hi"})
            self.assertEqual(st, 400)

        # read_only run
        res_ro = coding_agents.run("claude", "Inspect repo", mode="read_only", wait=True)
        self.assertEqual(res_ro["status"], "done")
        with open(os.path.join(res_ro["workspace"], "out", "_invoked.json"), "r", encoding="utf-8") as f:
            inv_ro = json.load(f)
        self.assertEqual(
            inv_ro["argv"],
            [
                "-p",
                "--output-format", "stream-json",
                "--verbose",
                "--restricted",
                "--strict-mcp-config",
                "--permission-mode", "dontAsk",
                "--tools", "Read,Grep,Glob",
            ],
        )
        self.assertEqual(inv_ro["stdin"], "Inspect repo")
        self.assertNotIn("Inspect repo", inv_ro["argv"])
        for forbidden_flag in ("acceptEdits", "bypassPermissions", "auto", "--add-dir"):
            self.assertNotIn(forbidden_flag, inv_ro["argv"])

        # Engine itself saves final answer to out/answer.md in read_only
        answer_path = os.path.join(res_ro["workspace"], "out", "answer.md")
        self.assertTrue(os.path.isfile(answer_path))
        with open(answer_path, "r", encoding="utf-8") as f:
            self.assertIn("Final answer for: Inspect repo", f.read())
        self.assertTrue(any(item["name"] == "answer.md" for item in res_ro["out_files"]))

        # write_out run
        res_wo = coding_agents.run("claude", "WRITE_OUT_FILES", mode="write_out", wait=True)
        self.assertEqual(res_wo["status"], "done")
        with open(os.path.join(res_wo["workspace"], "out", "_invoked.json"), "r", encoding="utf-8") as f:
            inv_wo = json.load(f)
        self.assertEqual(
            inv_wo["argv"],
            [
                "-p",
                "--output-format", "stream-json",
                "--verbose",
                "--restricted",
                "--strict-mcp-config",
                "--permission-mode", "dontAsk",
                "--tools", "Read,Grep,Glob,Write,Edit",
                "--allowedTools", "Write(./out/**)", "Edit(./out/**)",
            ],
        )
        self.assertEqual(inv_wo["stdin"], "WRITE_OUT_FILES")
        self.assertNotIn("acceptEdits", inv_wo["argv"])

    def test_c_version_probe_timeout_handled(self):
        """A CLI whose --version hangs past timeout does not crash detect()."""
        self._write_fake_cli("codex", _FAKE_SLEEP_VERSION_BODY)
        orig_timeout = coding_agents.DETECT_TIMEOUT_S
        coding_agents.DETECT_TIMEOUT_S = 0.2
        try:
            det = coding_agents.detect(refresh=True)
            self.assertTrue(det["codex"]["installed"])
            self.assertIsNone(det["codex"]["version"])
        finally:
            coding_agents.DETECT_TIMEOUT_S = orig_timeout

    # --- D. User approval gate ---

    def test_d_user_approval_required_single_use_and_hash_bound(self):
        """run_coding_agent only creates awaiting_approval; single-use hash-bound approval required to run."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)
        coding_agents.set_agent_engine("writer", "claude")

        ctx = ToolContext(session_id="sess_test_1", agent_id="writer")
        tool_res = tools_pkg.run_tool(
            "run_coding_agent",
            ctx,
            {"prompt": "Summarize architecture", "repo_ids": [], "mode": "read_only"},
            ["run_coding_agent"],
        )
        self.assertEqual(tool_res["status"], "awaiting_approval")
        run_id = tool_res["run_id"]

        # Nothing executed yet
        stored = coding_agents.get_run(run_id)
        self.assertEqual(stored["status"], "awaiting_approval")
        self.assertFalse(os.path.exists(os.path.join(stored["workspace"], "out", "_invoked.json")))

        # Negative: hash mismatch refused
        port = self._start_server()
        st_bad, _, _ = self._http_req(
            port,
            "POST",
            f"/api/coding/runs/{run_id}/approve",
            {"approval_hash": "0" * 64},
        )
        self.assertEqual(st_bad, 400)

        # Negative: tampering with prompt in DB invalidates approval
        conn = db.connect()
        with conn:
            conn.execute("UPDATE coding_runs SET prompt = 'Tampered prompt' WHERE id = ?", (run_id,))
        st_tamper, _, _ = self._http_req(
            port,
            "POST",
            f"/api/coding/runs/{run_id}/approve",
            {"approval_hash": stored["approval_hash"]},
        )
        self.assertEqual(st_tamper, 400)
        with conn:
            conn.execute("UPDATE coding_runs SET prompt = 'Summarize architecture' WHERE id = ?", (run_id,))

        # Approve once via API -> runs and finishes
        st_ok, _, raw_ok = self._http_req(
            port,
            "POST",
            f"/api/coding/runs/{run_id}/approve",
            {"approval_hash": stored["approval_hash"]},
        )
        self.assertEqual(st_ok, 200)
        final_run = coding_agents.wait_for_run(run_id, timeout=10.0)
        self.assertEqual(final_run["status"], "done")

        # Negative: single-use (second approval refused even if status hacked back)
        st_replay, _, _ = self._http_req(
            port,
            "POST",
            f"/api/coding/runs/{run_id}/approve",
            {"approval_hash": stored["approval_hash"]},
        )
        self.assertEqual(st_replay, 400)
        with conn:
            conn.execute("UPDATE coding_runs SET status = 'awaiting_approval' WHERE id = ?", (run_id,))
        with self.assertRaises(ValueError):
            coding_agents.approve_run(run_id, expected_hash=stored["approval_hash"])

        # Negative: directly inserting queued row without approval_consumed=1 is refused by worker
        unapproved_id = "crun_unapproved_1"
        unapproved_ws = coding_agents._resolve_workspace_for_run(unapproved_id)
        os.makedirs(os.path.join(unapproved_ws, "out"), exist_ok=True)
        db.create_coding_run(
            {
                "id": unapproved_id,
                "engine": "claude",
                "mode": "read_only",
                "prompt": "Bypass approval",
                "repo_ids": [],
                "status": "queued",
                "workspace": unapproved_ws,
                "created_at": coding_agents._now_iso(),
                "log_path": os.path.join(unapproved_ws, "run.log"),
                "approval_hash": coding_agents.compute_approval_hash("claude", "read_only", [], "Bypass approval"),
                "approved_at": None,
                "approval_consumed": False,
            }
        )
        self.assertIsNone(db.get_next_queued_coding_run("claude"))
        coding_agents._execute_run(unapproved_id)
        self.assertEqual(coding_agents.get_run(unapproved_id)["status"], "failed")

        # Cancelling awaiting_approval run prevents later approval
        pending2 = coding_agents.request_run("claude", "Cancel me", repo_ids=[], mode="read_only")
        cancelled = coding_agents.cancel(pending2["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ValueError):
            coding_agents.approve_run(pending2["id"], expected_hash=pending2["approval_hash"])

    def test_restart_cancels_unstarted_runs_and_removes_repo_copies(self):
        """A restart never starts paid runs unattended and leaves no repo copies behind."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)
        for status, expected in (("queued", "cancelled"), ("running", "failed")):
            created = coding_agents.request_run("claude", f"left {status}", repo_ids=[], mode="read_only")
            db.update_coding_run(created["id"], status=status)
            copy = os.path.join(created["workspace"], "repos", "r")
            os.makedirs(copy)
            coding_agents.recover_stale_runs()
            self.assertEqual(coding_agents.get_run(created["id"])["status"], expected)
            self.assertFalse(os.path.exists(copy))

    # --- E. Confidential repos ---

    def test_e_confidential_repo_confirmation_session_flag_mirror_and_event_redaction(self):
        """Confidential repos require explicit confirmation, mark session confidential, and redact events/mirrors."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)
        coding_agents.set_agent_engine("writer", "claude")

        secret_repo = self._init_git_repo("lazy_repo", {"README.md": "Secret LazyLayout details"})
        self._write_repos_json([
            {
                "id": "lazylayout",
                "name": "LazyLayout",
                "product_id": "lazylayout",
                "kind": "local",
                "confidential": True,
                "local_path": secret_repo,
            }
        ])

        # Refused without confirm_confidential
        with self.assertRaises(ValueError):
            coding_agents.run("claude", "Analyze LazyLayout", repo_ids=["lazylayout"])

        # Agent tool request creates awaiting_approval and marks agent session confidential
        sess = db.create_agent_session(
            {
                "id": "sess_conf_test",
                "agent_id": "writer",
                "status": "running",
                "messages": [{"role": "user", "content": "Help with repo"}],
                "allowed_tools": ["run_coding_agent"],
                "confidential": False,
                "created_at": coding_agents._now_iso(),
                "updated_at": coding_agents._now_iso(),
            }
        )
        marked = {"called": False}

        def _mark_conf():
            marked["called"] = True
            db.update_agent_session(sess["id"], confidential=True, updated_at=coding_agents._now_iso())

        ctx = ToolContext(session_id=sess["id"], agent_id="writer", mark_confidential=_mark_conf)
        q_events = events.subscribe()
        try:
            req = tools_pkg.run_tool(
                "run_coding_agent",
                ctx,
                {"prompt": "TOP_SECRET_PROMPT_MARKER", "repo_ids": ["lazylayout"], "mode": "read_only"},
                ["run_coding_agent"],
            )
            self.assertTrue(req["confidential"])
            self.assertTrue(marked["called"])
            self.assertTrue(db.get_agent_session(sess["id"])["confidential"])

            # Approve without confirm_confidential -> refused
            with self.assertRaises(ValueError):
                coding_agents.approve_run(req["run_id"], expected_hash=req["approval_hash"], confirm_confidential=False)

            # Approve with confirm_confidential=True -> executes
            approved = coding_agents.approve_run(req["run_id"], expected_hash=req["approval_hash"], confirm_confidential=True, wait=True)
            self.assertEqual(approved["status"], "done")

            # Drain events and assert ZERO leak of prompt, log, summary, or repo_ids
            collected = []
            while not q_events.empty():
                collected.append(q_events.get_nowait())
            serialized_events = json.dumps(collected)
            self.assertNotIn("TOP_SECRET_PROMPT_MARKER", serialized_events)
            self.assertNotIn("Step 1 done.", serialized_events)
            self.assertNotIn("lazylayout", serialized_events)
            self.assertIn("Confidential coding run", serialized_events)
        finally:
            events.unsubscribe(q_events)

        # Mirror records must NEVER contain coding_runs, logs, or confidential prompt
        records = cloud_sync.build_mirror_records()
        serialized_mirror = json.dumps(records)
        self.assertNotIn("TOP_SECRET_PROMPT_MARKER", serialized_mirror)
        self.assertNotIn("coding_run", serialized_mirror)
        self.assertNotIn(req["run_id"], serialized_mirror)
        sess_recs = [r for r in records if r["kind"] == "chat_session" and r["local_id"] == sess["id"]]
        self.assertEqual(len(sess_recs), 1)
        self.assertEqual(sess_recs[0]["title"], "Confidential chat")

    # --- F. Environment allowlist ---

    def test_f_environment_strict_allowlist(self):
        """Child receives only PATH, HOME, USER, LOGNAME, LANG, LC_*, TERM, TMPDIR (no secrets or XDG_*)."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)

        os.environ["NVIDIA_NIM_API_KEY"] = "nvapi-secret"
        os.environ["MAGNM_ADMIN_SECRET"] = "magnm-secret"
        os.environ["OPENAI_API_KEY"] = "sk-openai"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant"
        os.environ["GEMINI_API_KEY"] = "gm-key"
        os.environ["GOOGLE_API_KEY"] = "gg-key"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "sbp-key"
        os.environ["CUSTOM_API_TOKEN"] = "tok"
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg_leak"
        os.environ["RANDOM_ENV_VAR"] = "leak"
        os.environ["LC_ALL"] = "C.UTF-8"
        os.environ["TMPDIR"] = self.tmpdir
        os.environ["USER"] = "testuser"
        os.environ["LOGNAME"] = "testuser"

        res = coding_agents.run("claude", "Check env", wait=True)
        self.assertEqual(res["status"], "done")
        with open(os.path.join(res["workspace"], "out", "_invoked.json"), "r", encoding="utf-8") as f:
            child_env = json.load(f)["env"]

        for forbidden in (
            "NVIDIA_NIM_API_KEY",
            "MAGNM_ADMIN_SECRET",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "CUSTOM_API_TOKEN",
            "XDG_CONFIG_HOME",
            "RANDOM_ENV_VAR",
            "MAGNM_HOME",
            "MAGNM_CODING_WORKSPACES",
        ):
            self.assertNotIn(forbidden, child_env)

        self.assertEqual(child_env.get("HOME"), self.home_dir)
        self.assertEqual(child_env.get("LC_ALL"), "C.UTF-8")
        self.assertEqual(child_env.get("TMPDIR"), self.tmpdir)

    # --- G. Hostile output files ---

    def test_g_hostile_out_files_symlinks_headers_and_save_extension_allowlist(self):
        """Symlinks inside out/ are ignored/refused; downloads use attachment+octet-stream+nosniff; save allows safe exts only."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)

        res = coding_agents.run("claude", "WRITE_OUT_FILES", mode="write_out", wait=True)
        self.assertEqual(res["status"], "done")
        out_dir = os.path.join(res["workspace"], "out")

        # Add hostile symlink inside out/ and symlinked subdir
        outside_file = os.path.join(self.tmpdir, "host_secret.md")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("HOST SECRET")
        os.symlink(outside_file, os.path.join(out_dir, "leak.md"))
        ext_dir = os.path.join(self.tmpdir, "ext_dir")
        os.makedirs(ext_dir, exist_ok=True)
        with open(os.path.join(ext_dir, "nested.txt"), "w", encoding="utf-8") as f:
            f.write("NESTED")
        os.symlink(ext_dir, os.path.join(out_dir, "sub_link"))

        listed = [item["name"] for item in coding_agents.list_out_files(res["workspace"])]
        self.assertIn("report.md", listed)
        self.assertIn("evil.html", listed)
        self.assertNotIn("leak.md", listed)
        self.assertNotIn("sub_link/nested.txt", listed)

        port = self._start_server()

        # Serving evil.html forces attachment + application/octet-stream + nosniff
        st, hdrs, body = self._http_req(port, "GET", f"/api/coding/runs/{res['id']}/out/evil.html")
        self.assertEqual(st, 200)
        hdrs_low = {k.lower(): v for k, v in hdrs.items()}
        self.assertEqual(hdrs_low.get("content-type"), "application/octet-stream")
        self.assertIn("attachment", hdrs_low.get("content-disposition", ""))
        self.assertEqual(hdrs_low.get("x-content-type-options"), "nosniff")
        self.assertIn(b"<script>", body)

        # Refuses symlink & traversal on download
        st_sym, _, _ = self._http_req(port, "GET", f"/api/coding/runs/{res['id']}/out/leak.md")
        self.assertEqual(st_sym, 400)
        st_trav, _, _ = self._http_req(port, "GET", f"/api/coding/runs/{res['id']}/out/..%2Frun.log")
        self.assertEqual(st_trav, 400)

        # Save to Library allows md/txt/json/csv/png/jpg/webp and refuses html/svg/js/symlink
        for ext in ("txt", "json", "csv", "png", "jpg", "webp"):
            with open(os.path.join(out_dir, f"ok.{ext}"), "wb") as f:
                f.write(f"ok-{ext}".encode("utf-8"))
            st_ok, _, _ = self._http_req(
                port,
                "POST",
                f"/api/coding/runs/{res['id']}/out/save",
                {"filename": f"ok.{ext}"},
            )
            self.assertEqual(st_ok, 200, f"failed to save allowed extension .{ext}")

        for bad_name in ("evil.html", "vector.svg", "script.js", "leak.md"):
            st_bad, _, _ = self._http_req(
                port,
                "POST",
                f"/api/coding/runs/{res['id']}/out/save",
                {"filename": bad_name},
            )
            self.assertEqual(st_bad, 400, f"unexpectedly saved disallowed file {bad_name}")

    # --- H. Database migrations ---

    def test_h_migrations_fresh_v19_and_partial_v20_identical_schema_and_preserve_rows(self):
        """Fresh DB, v19 DB, and partial-v20 DB all migrate cleanly to v21 with identical schemas and preserved rows."""
        def _inspect_schema(db_path):
            conn = sqlite3.connect(db_path)
            cols = [(r[1], r[2], r[3], r[4]) for r in conn.execute("PRAGMA table_info(coding_runs)").fetchall()]
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()]
            conn.close()
            return cols, versions

        # 1. Fresh DB
        fresh_path = os.path.join(self.tmpdir, "fresh.db")
        db.set_db_path(fresh_path)
        db.init_db()
        fresh_cols, fresh_vers = _inspect_schema(fresh_path)
        self.assertEqual(fresh_vers, list(range(1, db.SCHEMA_VERSION + 1)))
        self.assertEqual(db.SCHEMA_VERSION, 21)

        # 2. v19 DB with existing row
        v19_path = os.path.join(self.tmpdir, "v19.db")
        c19 = sqlite3.connect(v19_path)
        c19.row_factory = sqlite3.Row
        c19.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        c19.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        c19.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', 'done')")
        for ver, fn in db._MIGRATIONS:
            if ver <= 19:
                fn(c19)
                c19.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, '2026-11-18T00:00:00Z')", (ver,))
        c19.execute("INSERT INTO meta (key, value) VALUES ('keep_v19', 'yes')")
        c19.commit()
        c19.close()

        db.set_db_path(v19_path)
        db.init_db()
        v19_cols, v19_vers = _inspect_schema(v19_path)
        self.assertEqual(v19_cols, fresh_cols)
        self.assertEqual(v19_vers, fresh_vers)
        self.assertEqual(db.get_meta("keep_v19"), "yes")

        # 3. Partial-v20 DB (coding_runs in exact v20 shape + existing row, and schema_version 1..20)
        v20_path = os.path.join(self.tmpdir, "partial_v20.db")
        c20 = sqlite3.connect(v20_path)
        c20.row_factory = sqlite3.Row
        c20.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        c20.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        c20.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', 'done')")
        for ver, fn in db._MIGRATIONS:
            if ver <= 20:
                fn(c20)
                c20.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, '2026-11-19T00:00:00Z')", (ver,))
        c20.execute(
            """
            INSERT INTO coding_runs (
                id, engine, mode, prompt, repo_ids_json, status, workspace, created_at
            ) VALUES ('crun_legacy_20', 'claude', 'read_only', 'Old v20 prompt', '[]', 'done', '/tmp/old', '2026-11-19T00:00:00Z')
            """
        )
        c20.commit()
        c20.close()

        db.set_db_path(v20_path)
        db.init_db()
        # Verify v21 migration is idempotent when called again directly
        db._apply_migration_v21(db.connect())
        v20_cols, v20_vers = _inspect_schema(v20_path)
        self.assertEqual(v20_cols, fresh_cols)
        self.assertEqual(v20_vers, fresh_vers)
        kept_run = db.get_coding_run("crun_legacy_20")
        self.assertIsNotNone(kept_run)
        self.assertEqual(kept_run["prompt"], "Old v20 prompt")

    # --- Additional lifecycle, logs, cancel, timeout, cleanup, token gate ---

    def test_logs_cancel_process_group_cleanup_and_token_gate(self):
        """Logs strip ANSI, cancel kills process group, cleanup deletes repos/, and token gate protects routes."""
        self._write_fake_cli("claude", _FAKE_CLAUDE_BODY)
        coding_agents.detect(refresh=True)

        repo_dir = self._init_git_repo("repo_cleanup", {"main.py": "x = 1\n"})
        self._write_repos_json([
            {
                "id": "cleanup-repo",
                "name": "CleanupRepo",
                "product_id": "cero",
                "kind": "local",
                "confidential": False,
                "local_path": repo_dir,
            }
        ])

        # Normal run strips ANSI and removes repos/ while keeping out/ and run.log
        res = coding_agents.run("claude", "Check cleanup", repo_ids=["cleanup-repo"], wait=True)
        self.assertEqual(res["status"], "done")
        self.assertFalse(os.path.exists(os.path.join(res["workspace"], "repos")))
        self.assertTrue(os.path.isdir(os.path.join(res["workspace"], "out")))
        self.assertTrue(os.path.isfile(res["log_path"]))
        log_payload = coding_agents.read_log_from(res["id"], 0)
        self.assertNotIn("\x1b", log_payload["content"])
        self.assertIn("Working on task", log_payload["content"])

        # Cancel running process kills process group including child
        slow = coding_agents.run("claude", "SLEEP_FOREVER", wait=False)
        child_pid_file = os.path.join(slow["workspace"], "out", "_child.pid")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not os.path.isfile(child_pid_file):
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(child_pid_file))
        with open(child_pid_file, "r", encoding="utf-8") as f:
            child_pid = int(f.read().strip())

        cancelled = coding_agents.cancel(slow["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        # Child process in the same process group must be terminated (gone or zombie awaiting init reap)
        deadline_kill = time.monotonic() + 2.0
        child_gone = False
        while time.monotonic() < deadline_kill:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_gone = True
                break
            stat_path = f"/proc/{child_pid}/stat"
            if os.path.exists(stat_path):
                with open(stat_path, "r", encoding="utf-8", errors="replace") as sf:
                    parts = sf.read().rsplit(")", 1)
                    if len(parts) == 2 and parts[1].strip().startswith("Z"):
                        child_gone = True
                        break
            time.sleep(0.05)
        self.assertTrue(child_gone, f"child process {child_pid} still alive after cancel")

        # Token gate on routes
        port = self._start_server(token="secret-engine-tok")
        st_unauth, _, _ = self._http_req(port, "GET", "/api/coding/agents")
        self.assertEqual(st_unauth, 401)
        st_unauth_post, _, _ = self._http_req(port, "POST", "/api/coding/runs", {"engine": "claude", "prompt": "hi"})
        self.assertEqual(st_unauth_post, 401)
        st_auth, _, raw_auth = self._http_req(port, "GET", "/api/coding/agents", token="secret-engine-tok")
        self.assertEqual(st_auth, 200)
        parsed = json.loads(raw_auth.decode("utf-8"))
        self.assertIn("agents", parsed)


class CodingReviewGapTests(unittest.TestCase):
    """Gaps found in security review."""

    def test_env_and_credential_file_names_are_never_copied(self):
        for name in ("supabase-dev.env", "PROD.ENV", ".netrc", ".npmrc", ".git-credentials", "secrets.toml", "app.keystore"):
            self.assertTrue(coding_agents._is_skipped_filename(name), name)
        self.assertFalse(coding_agents._is_skipped_filename("main.py"))

    def test_home_folder_or_above_is_refused(self):
        home = os.path.realpath(os.path.expanduser("~"))
        for path in (home, os.path.dirname(home)):
            with self.assertRaises(ValueError):
                coding_agents._refuse_broad_source(path, "r")
        coding_agents._refuse_broad_source(os.path.join(home, "some-repo"), "r")

    def test_approval_requires_the_hash_that_was_shown(self):
        row = {"id": "r1", "status": "awaiting_approval", "engine": "claude", "mode": "read_only", "prompt": "p",
               "repo_ids": [], "workspace": "/unused"}
        row["approval_hash"] = coding_agents.compute_approval_hash("claude", "read_only", [], "p")
        with patch.object(coding_agents.db, "get_coding_run", return_value=row), \
                patch.object(coding_agents, "_require_confined_cli"), \
                patch.object(coding_agents, "validate_workspace_path"), \
                patch.object(coding_agents.db, "approve_coding_run") as approve:
            for shown in (None, "", "not-the-hash"):
                with self.assertRaises(ValueError):
                    coding_agents.approve_run("r1", expected_hash=shown)
        approve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
