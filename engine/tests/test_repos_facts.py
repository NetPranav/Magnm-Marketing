"""Unit tests for repos, GitHub/local sync, shipped feed, confidential taint, facts, and scheduler_loop."""

from datetime import datetime, timedelta, timezone
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_runtime
import agents
import ai_gateway
import db
import jobs
import paths
import repos
import scheduler_loop
import server
import tools
from tools import ToolContext


class _FakeHTTPResponse:
    """Minimal context-manager mock for urllib.request.urlopen responses."""

    def __init__(self, body, status=200):
        if isinstance(body, (dict, list)):
            self._raw = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            self._raw = body.encode("utf-8")
        else:
            self._raw = body
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_http_error(url, code, body_str="", headers=None):
    return urllib.error.HTTPError(
        url=url,
        code=code,
        msg=f"HTTP {code}",
        hdrs=headers or {},
        fp=io.BytesIO(body_str.encode("utf-8")),
    )


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing GET, POST, and DELETE endpoints."""

    def __init__(self, path, method="GET", body_dict=None, headers=None):
        self.path = path
        self.command = method
        raw = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = dict(headers or {})
        if body_dict is not None:
            self.headers.setdefault("Content-Length", str(len(raw)))
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


def _init_temp_git_repo(repo_dir):
    """Initialize a temporary git repo with author identity configured."""
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)


def _git_commit(repo_dir, filename, content, message, date_dt=None):
    full = os.path.join(repo_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(full, mode, **kwargs) as f:
        f.write(content)
    subprocess.run(["git", "add", filename], cwd=repo_dir, check=True, capture_output=True)
    env = dict(os.environ)
    if date_dt is not None:
        iso = date_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
    )


class ReposFactsSchedulerTests(unittest.TestCase):
    """Tests for Task T2.3: repos, facts, confidential taint, tools, HTTP API, and scheduler_loop."""

    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_gh_tok = os.environ.get("GITHUB_TOKEN")
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        os.environ.pop("GITHUB_TOKEN", None)
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        ai_gateway.ENV_PATH = paths.data(".env")

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        if self._prev_gh_tok is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = self._prev_gh_tok
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def test_01_add_local_and_github_repos_validation_and_duplicates(self):
        """add_repo validates local git dirs, duplicates, GitHub 200/404, and sends GITHUB_TOKEN."""
        local_repo = os.path.join(self.tmpdir, "my-local-repo")
        _init_temp_git_repo(local_repo)
        _git_commit(local_repo, "README.md", "# Demo", "init")

        not_a_repo = os.path.join(self.tmpdir, "plain-folder")
        os.makedirs(not_a_repo, exist_ok=True)

        with self.assertRaises(ValueError) as ctx_bad:
            repos.add_repo({"kind": "local", "name": "Bad Repo", "local_path": not_a_repo})
        self.assertIn("valid git", str(ctx_bad.exception).lower())

        added = repos.add_repo({"kind": "local", "name": "Demo App", "local_path": local_repo})
        self.assertEqual(added["id"], "demo-app")
        self.assertEqual(added["kind"], "local")

        with self.assertRaises(ValueError) as ctx_dup:
            repos.add_repo({"kind": "local", "name": "Demo App", "local_path": local_repo})
        self.assertIn("duplicate", str(ctx_dup.exception).lower())

        seen_headers = []

        def fake_urlopen(req, timeout=20):
            seen_headers.append(dict(req.header_items()))
            url = req.full_url
            if url.endswith("/repos/acme/missing"):
                raise _make_http_error(url, 404, '{"message":"Not Found"}')
            if url.endswith("/repos/acme/public-cli"):
                return _FakeHTTPResponse({"full_name": "acme/public-cli", "default_branch": "main"})
            if url.endswith("/repos/acme/private-app"):
                return _FakeHTTPResponse({"full_name": "acme/private-app", "default_branch": "dev"})
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            pub = repos.add_repo({"kind": "github", "name": "Public CLI", "github": "acme/public-cli"})
            self.assertEqual(pub["id"], "public-cli")
            self.assertEqual(pub["branch"], "main")

            with self.assertRaises(ValueError) as ctx_404:
                repos.add_repo({"kind": "github", "name": "Missing", "github": "acme/missing"})
            self.assertIn("404", str(ctx_404.exception))

            with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test_token_123"}):
                priv = repos.add_repo({"kind": "github", "name": "Private App", "github": "acme/private-app"})
                self.assertEqual(priv["id"], "private-app")
                self.assertEqual(priv["branch"], "dev")

        # Verify Authorization header was sent on private-app call
        priv_hdrs = {k.lower(): v for k, v in seen_headers[-1].items()}
        self.assertEqual(priv_hdrs.get("authorization"), "Bearer ghp_test_token_123")
        self.assertTrue(repos.remove_repo("public-cli"))
        self.assertFalse(repos.remove_repo("nonexistent"))

    def test_02_sync_local_commits_tags_version_readme_and_media_size_limit(self):
        """sync_repo collects 14-day commits, tags, package.json version, README, and media <= 50 MB."""
        repo_dir = os.path.join(self.tmpdir, "cero-demo-repo")
        _init_temp_git_repo(repo_dir)

        now = datetime.now(timezone.utc)
        old_dt = now - timedelta(days=25)
        recent_dt = now - timedelta(days=2)

        _git_commit(repo_dir, "README.md", "# Demo Product\nFast terminal tool.", "old commit", date_dt=old_dt)
        _git_commit(
            repo_dir,
            "package.json",
            json.dumps({"name": "demo", "version": "3.4.5"}),
            "feat: add package json",
            date_dt=recent_dt,
        )
        subprocess.run(["git", "tag", "v3.4.5"], cwd=repo_dir, check=True, capture_output=True)

        # Create demo media files: one small png under docs/, one > 50 MB sparse file under assets/
        docs_img = os.path.join(repo_dir, "docs", "demo.png")
        os.makedirs(os.path.dirname(docs_img), exist_ok=True)
        with open(docs_img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

        big_vid = os.path.join(repo_dir, "assets", "too-big.mp4")
        os.makedirs(os.path.dirname(big_vid), exist_ok=True)
        with open(big_vid, "wb") as f:
            f.seek(51 * 1024 * 1024)
            f.write(b"\x00")

        small_gif = os.path.join(repo_dir, "assets", "clip.gif")
        with open(small_gif, "wb") as f:
            f.write(b"GIF89a" + b"0" * 32)

        repos.add_repo({"kind": "local", "name": "Demo Local", "local_path": repo_dir, "github": "acme/demo"})
        snap = repos.sync_repo("demo-local")

        self.assertTrue(snap["synced"])
        self.assertEqual(snap["version"], "3.4.5")
        self.assertIn("Fast terminal tool.", snap["readme"])
        self.assertTrue(any(t["name"] == "v3.4.5" for t in snap["tags"]))

        # Only commits within 14 days appear
        commit_msgs = [c["message"] for c in snap["recent_commits"]]
        self.assertIn("feat: add package json", commit_msgs)
        self.assertNotIn("old commit", commit_msgs)

        # Media includes small files and excludes > 50 MB file
        media_paths = [m["path"] for m in snap["media"]]
        self.assertIn("docs/demo.png", media_paths)
        self.assertIn("assets/clip.gif", media_paths)
        self.assertNotIn("assets/too-big.mp4", media_paths)

        # Compatibility files written
        self.assertTrue(os.path.isfile(paths.data("research", "repo-sync.json")))
        self.assertTrue(os.path.isfile(paths.data("research", "repo-sync.md")))

        # Snapshot stored in DB (and shipped includes recent commit + tag)
        feed = repos.shipped(repo_id="demo-local", days=7)
        self.assertTrue(any(x["kind"] == "commit" and "add package json" in x["title"] for x in feed))
        self.assertTrue(any(x["kind"] == "tag" and x["title"] == "v3.4.5" for x in feed))

    def test_03_sync_github_shipped_dedup_and_rate_limit_fallback(self):
        """sync_repo on GitHub merges releases/PRs/commits into shipped(), drops PR-covered commits, and falls back on rate limit."""
        now = datetime.now(timezone.utc)
        d_rel = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d_pr = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d_commit_pr = (now - timedelta(days=2, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d_commit_solo = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def fake_urlopen_ok(req, timeout=20):
            u = req.full_url
            if u.endswith("/repos/acme/cloud-tool"):
                return _FakeHTTPResponse({"full_name": "acme/cloud-tool", "default_branch": "main", "stargazers_count": 42})
            if "/releases" in u:
                return _FakeHTTPResponse([
                    {
                        "tag_name": "v1.2.0",
                        "name": "Release v1.2.0",
                        "html_url": "https://github.com/acme/cloud-tool/releases/tag/v1.2.0",
                        "published_at": d_rel,
                        "body": "Big release",
                        "draft": False,
                        "prerelease": False,
                    }
                ])
            if "/search/issues" in u:
                return _FakeHTTPResponse({
                    "items": [
                        {
                            "number": 77,
                            "title": "Add streaming mode",
                            "html_url": "https://github.com/acme/cloud-tool/pull/77",
                            "closed_at": d_pr,
                            "pull_request": {"merged_at": d_pr},
                        }
                    ]
                })
            if "/commits" in u:
                return _FakeHTTPResponse([
                    {
                        "sha": "aaaa11112222",
                        "html_url": "https://github.com/acme/cloud-tool/commit/aaaa11112222",
                        "commit": {"message": "Merge pull request #77 from acme/stream", "author": {"name": "Dev", "date": d_pr}},
                        "parents": [{"sha": "p1"}, {"sha": "p2"}],
                    },
                    {
                        "sha": "bbbb11112222",
                        "html_url": "https://github.com/acme/cloud-tool/commit/bbbb11112222",
                        "commit": {"message": "Add streaming mode (#77)", "author": {"name": "Dev", "date": d_commit_pr}},
                        "parents": [{"sha": "p1"}],
                    },
                    {
                        "sha": "cccc11112222",
                        "html_url": "https://github.com/acme/cloud-tool/commit/cccc11112222",
                        "commit": {"message": "Fix typo in CLI help", "author": {"name": "Dev", "date": d_commit_solo}},
                        "parents": [{"sha": "p1"}],
                    },
                ])
            if u.endswith("/readme"):
                return _FakeHTTPResponse("# Cloud Tool\nReadme text")
            raise AssertionError(f"Unexpected URL: {u}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen_ok):
            repos.add_repo({"kind": "github", "name": "Cloud Tool", "github": "acme/cloud-tool"})
            snap = repos.sync_repo("cloud-tool")

        self.assertTrue(snap["synced"])
        self.assertEqual(snap["stars"], 42)
        self.assertEqual(snap["version"], "v1.2.0")

        feed = repos.shipped(repo_id="cloud-tool", days=7)
        titles = [x["title"] for x in feed]
        self.assertEqual(feed[0]["kind"], "release")
        self.assertIn("Release v1.2.0", titles)
        self.assertIn("Add streaming mode", titles)
        self.assertIn("Fix typo in CLI help", titles)
        # Merge commit and PR-covered commit (#77) must be dropped
        self.assertFalse(any("Merge pull request" in t for t in titles))
        self.assertFalse(any("(#77)" in t for t in titles))

        # Now simulate rate limit (403 with X-RateLimit-Remaining: 0)
        reset_ts = int((now + timedelta(minutes=30)).timestamp())

        def fake_urlopen_rl(req, timeout=20):
            raise _make_http_error(
                req.full_url,
                403,
                '{"message":"API rate limit exceeded"}',
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)},
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen_rl):
            cached = repos.sync_repo("cloud-tool")

        self.assertTrue(cached["rate_limited"])
        self.assertIn("rate limited until", cached["rate_limit_message"])
        self.assertEqual(cached["stars"], 42)

    def test_04_read_repo_file_security_and_size_limit(self):
        """read_repo_file rejects .. traversal, absolute paths, outside symlinks, and > 40 KB files."""
        repo_dir = os.path.join(self.tmpdir, "safe-repo")
        _init_temp_git_repo(repo_dir)
        _git_commit(repo_dir, "README.md", "hello safe repo", "init")

        outside_file = os.path.join(self.tmpdir, "secret-outside.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("TOP SECRET")

        symlink_path = os.path.join(repo_dir, "escape_link.txt")
        os.symlink(outside_file, symlink_path)

        large_file = os.path.join(repo_dir, "large.txt")
        with open(large_file, "w", encoding="utf-8") as f:
            f.write("A" * (41 * 1024))

        repos.add_repo({"kind": "local", "name": "Safe Repo", "local_path": repo_dir})
        ctx = ToolContext(session_id="sess_file_1", agent_id="researcher")
        allowed = ["read_repo_file"]

        ok = tools.run_tool("read_repo_file", ctx, {"repo_id": "safe-repo", "path": "README.md"}, allowed)
        self.assertEqual(ok["content"], "hello safe repo")

        trav = tools.run_tool("read_repo_file", ctx, {"repo_id": "safe-repo", "path": "../secret-outside.txt"}, allowed)
        self.assertIn("error", trav)

        abs_res = tools.run_tool("read_repo_file", ctx, {"repo_id": "safe-repo", "path": outside_file}, allowed)
        self.assertIn("error", abs_res)

        sym_res = tools.run_tool("read_repo_file", ctx, {"repo_id": "safe-repo", "path": "escape_link.txt"}, allowed)
        self.assertIn("error", sym_res)

        big_res = tools.run_tool("read_repo_file", ctx, {"repo_id": "safe-repo", "path": "large.txt"}, allowed)
        self.assertIn("error", big_res)
        self.assertIn("40 KB", big_res["error"])

    def test_05_confidential_taint_routes_next_call_to_ollama_and_fails_if_unavailable(self):
        """Reading a confidential repo taints the session so the next chat_with_tools call has confidential=True."""
        conf_dir = os.path.join(self.tmpdir, "secret-project")
        _init_temp_git_repo(conf_dir)
        _git_commit(conf_dir, "README.md", "confidential spec", "init")
        repos.add_repo({
            "kind": "local",
            "name": "Secret Project",
            "local_path": conf_dir,
            "confidential": True,
        })
        repos.sync_repo("secret-project")

        seen_conf = []

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            seen_conf.append(bool(confidential))
            if len(seen_conf) == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {"id": "c1", "name": "read_repo_file", "arguments": {"repo_id": "secret-project", "path": "README.md"}}
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "m1",
                    "model_used": "m1",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "warnings": [],
                    "usage": {},
                }
            return {
                "text": "Read confidential spec locally.",
                "tool_calls": [],
                "provider": "ollama",
                "model_requested": "llama3.2",
                "model_used": "ollama/llama3.2",
                "fallback_used": False,
                "fallback_reason": None,
                "warnings": [],
                "usage": {},
            }

        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session(
                "researcher",
                "Inspect the project readme.",
                system_prompt="You are researcher.",
                allowed_tools=["read_repo_file", "read_repo_changes"],
            )

        self.assertEqual(seen_conf, [False, True])
        self.assertTrue(sess["confidential"])
        self.assertEqual(sess["status"], "done")

        # Now verify that when Ollama is unavailable, taint causes the next model call to fail with plain message
        def fake_chat_first_only(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            if not confidential:
                return {
                    "text": "",
                    "tool_calls": [
                        {"id": "c2", "name": "read_repo_changes", "arguments": {"repo_id": "secret-project"}}
                    ],
                    "provider": "nvidia_nim",
                    "model_requested": "m1",
                    "model_used": "m1",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "warnings": [],
                    "usage": {},
                }
            raise ai_gateway.AINotConnected(
                "This is confidential work and needs local AI (Ollama), which is not running."
            )

        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat_first_only):
            failed_sess = agent_runtime.start_session(
                "researcher",
                "Check repo changes.",
                system_prompt="You are researcher.",
                allowed_tools=["read_repo_changes"],
            )

        self.assertEqual(failed_sess["status"], "failed")
        self.assertTrue(failed_sess["confidential"])
        self.assertIn("Ollama", failed_sess["error"])

    def test_06_mentions_confidential_starts_session_confidential(self):
        """start_session sets confidential=True automatically when user_message mentions a confidential product."""
        seen_conf = []

        def fake_chat(messages, tools=None, agent_role="", confidential=False, max_tokens=None):
            seen_conf.append(bool(confidential))
            return {
                "text": "Handled confidentially.",
                "tool_calls": [],
                "provider": "ollama",
                "model_requested": "llama3.2",
                "model_used": "ollama/llama3.2",
                "fallback_used": False,
                "fallback_reason": None,
                "warnings": [],
                "usage": {},
            }

        with patch("ai_gateway.chat_with_tools", side_effect=fake_chat):
            sess = agent_runtime.start_session(
                "researcher",
                "Summarize LazyLayout status.",
                system_prompt="You are researcher.",
                allowed_tools=[],
            )

        self.assertEqual(seen_conf, [True])
        self.assertTrue(sess["confidential"])

    def test_07_facts_propose_pending_approve_edit_and_reject(self):
        """Proposed facts stay pending until approved (with optional edit) or rejected."""
        ctx = ToolContext(session_id="sess_facts_1", agent_id="researcher")
        allowed = ["propose_fact", "read_product_facts"]

        prop = tools.run_tool(
            "propose_fact",
            ctx,
            {
                "product_id": "cero",
                "text": "Cero supports .flow automation files.",
                "source_url": "https://github.com/NetPranav/Cero-Terminal#flow",
                "source_kind": "readme",
            },
            allowed,
        )
        self.assertTrue(prop["proposed"])
        fact_id = prop["fact"]["id"]
        self.assertEqual(prop["fact"]["status"], "pending")

        # Pending fact must NOT be in approved_facts
        read_before = tools.run_tool("read_product_facts", ctx, {"product_id": "cero"}, allowed)
        self.assertEqual(read_before["approved_facts"], [])
        self.assertEqual(read_before["pending_count"], 1)
        self.assertIn("not usable yet", read_before["note"].lower())

        # Approve with edited text via HTTP API
        h_decide = _StubHandler(
            f"/api/facts/{fact_id}/decide",
            method="POST",
            body_dict={"status": "approved", "text": "Cero runs cross-OS .flow automation scripts."},
        )
        h_decide.do_POST()
        self.assertEqual(h_decide.status_code, 200)
        decided = h_decide.response_json()
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(decided["text"], "Cero runs cross-OS .flow automation scripts.")

        read_after = tools.run_tool("read_product_facts", ctx, {"product_id": "cero"}, allowed)
        self.assertEqual(len(read_after["approved_facts"]), 1)
        self.assertEqual(read_after["approved_facts"][0]["text"], "Cero runs cross-OS .flow automation scripts.")
        self.assertEqual(
            read_after["approved_facts"][0]["source_url"],
            "https://github.com/NetPranav/Cero-Terminal#flow",
        )

        # Propose second fact and reject it -> hidden from approved_facts
        prop2 = tools.run_tool(
            "propose_fact",
            ctx,
            {
                "product_id": "cero",
                "text": "Unverified claim.",
                "source_url": None,
                "source_kind": "commit",
            },
            allowed,
        )
        fact2_id = prop2["fact"]["id"]
        h_rej = _StubHandler(
            f"/api/facts/{fact2_id}/decide",
            method="POST",
            body_dict={"status": "rejected"},
        )
        h_rej.do_POST()
        self.assertEqual(h_rej.status_code, 200)

        read_final = tools.run_tool("read_product_facts", ctx, {"product_id": "cero"}, allowed)
        self.assertEqual(len(read_final["approved_facts"]), 1)
        self.assertEqual(read_final["pending_count"], 0)

    def test_08_scheduler_loop_due_no_duplicate_and_ist_cron(self):
        """scheduler_loop enqueues due jobs, skips non-due and active jobs, and respects 08:46 Asia/Kolkata."""
        ist = ZoneInfo("Asia/Kolkata")
        t_before_ist = datetime(2026, 11, 19, 8, 45, tzinfo=ist).astimezone(timezone.utc)
        t_at_ist = datetime(2026, 11, 19, 8, 46, tzinfo=ist).astimezone(timezone.utc)
        t_1h_later = t_at_ist + timedelta(hours=1)
        t_2h_later = t_at_ist + timedelta(hours=2)

        db.save_schedules([
            {
                "id": "cmo_daily",
                "name": "Daily CMO Cycle",
                "command": "daily_cycle",
                "cron": "46 8 * * *",
                "enabled": True,
                "last_run": None,
            },
            {
                "id": "repo_sync",
                "name": "Repo Sync",
                "command": "repo_sync",
                "interval_hours": 2,
                "enabled": True,
                "last_run": t_before_ist.isoformat(),
            },
        ])

        # At 08:45 IST: daily_cycle not due yet; repo_sync only 0h since last_run -> neither due
        enq1 = scheduler_loop.tick(now=t_before_ist)
        self.assertEqual(enq1, [])

        # At 08:46 IST: daily_cycle becomes due!
        enq2 = scheduler_loop.tick(now=t_at_ist)
        self.assertEqual([j["name"] for j in enq2], ["daily_cycle"])

        # Same day 1h later: daily_cycle already ran today, repo_sync only 1h -> nothing enqueued
        enq3 = scheduler_loop.tick(now=t_1h_later)
        self.assertEqual(enq3, [])

        # At +2h: repo_sync is due
        enq4 = scheduler_loop.tick(now=t_2h_later)
        self.assertEqual([j["name"] for j in enq4], ["repo_sync"])

        # If repo_sync is still queued/running and we force last_run old, no duplicate is enqueued
        scheds = db.load_schedules()
        scheds[1]["last_run"] = (t_2h_later - timedelta(hours=5)).isoformat()
        db.save_schedules(scheds)
        enq5 = scheduler_loop.tick(now=t_2h_later + timedelta(minutes=5))
        self.assertEqual(enq5, [])

    def test_09_migration_of_legacy_repos_json_entries(self):
        """Legacy repos.json entries with remote_url/primary_branch migrate cleanly to kind=local + github slug."""
        legacy = {
            "repositories": [
                {
                    "id": "cero",
                    "product_id": "cero",
                    "name": "Cero Terminal",
                    "product": "Cero",
                    "status": "released",
                    "version": "2.2.3",
                    "local_path": "/tmp/Cero",
                    "remote_url": "https://github.com/NetPranav/Cero-Terminal",
                    "primary_branch": "linux-v2-update",
                    "marketing_angle": "Safe AI shell",
                },
                {
                    "id": "lazylayout",
                    "product_id": "lazylayout",
                    "name": "LazyLayout",
                    "product": "LazyLayout",
                    "status": "in_development",
                    "version": "Phase 77",
                    "local_path": "/tmp/lazylayouts",
                    "remote_url": "https://github.com/NetPranav/LazyLayout.git",
                    "primary_branch": "b/phase-77-expressions",
                    "marketing_angle": "Teasers only",
                },
            ]
        }
        cfg_path = paths.data("config", "repos.json")
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, indent=2)

        migrated = repos.migrate_repos_config(cfg_path)
        self.assertEqual(len(migrated), 2)

        cero, ll = migrated[0], migrated[1]
        self.assertEqual(cero["kind"], "local")
        self.assertEqual(cero["github"], "NetPranav/Cero-Terminal")
        self.assertEqual(cero["branch"], "linux-v2-update")
        self.assertFalse(cero["confidential"])

        self.assertEqual(ll["kind"], "local")
        self.assertEqual(ll["github"], "NetPranav/LazyLayout")
        self.assertEqual(ll["branch"], "b/phase-77-expressions")
        self.assertTrue(ll["confidential"])

    def test_10_http_api_repos_shipped_facts_and_delete(self):
        """HTTP endpoints for /api/repos, DELETE /api/repos/<id>, /api/shipped, and /api/facts work end-to-end."""
        repo_dir = os.path.join(self.tmpdir, "api-repo")
        _init_temp_git_repo(repo_dir)
        _git_commit(repo_dir, "README.md", "# API Repo", "feat: initial api repo")

        h_add = _StubHandler(
            "/api/repos",
            method="POST",
            body_dict={"kind": "local", "name": "API Repo", "local_path": repo_dir},
        )
        h_add.do_POST()
        self.assertEqual(h_add.status_code, 200)
        self.assertEqual(h_add.response_json()["repo"]["id"], "api-repo")

        h_sync = _StubHandler("/api/repos/api-repo/sync", method="POST", body_dict={})
        h_sync.do_POST()
        self.assertEqual(h_sync.status_code, 200)
        self.assertTrue(h_sync.response_json()["success"])

        h_list = _StubHandler("/api/repos")
        h_list.do_GET()
        self.assertEqual(h_list.status_code, 200)
        ids = {r["id"] for r in h_list.response_json()}
        self.assertIn("api-repo", ids)

        h_ship = _StubHandler("/api/shipped?days=7")
        h_ship.do_GET()
        self.assertEqual(h_ship.status_code, 200)
        self.assertTrue(any("initial api repo" in x["title"] for x in h_ship.response_json()))

        h_fact = _StubHandler(
            "/api/facts",
            method="POST",
            body_dict={"product_id": "api-repo", "text": "Manual verified fact"},
        )
        h_fact.do_POST()
        self.assertEqual(h_fact.status_code, 200)
        self.assertEqual(h_fact.response_json()["status"], "approved")

        h_facts_get = _StubHandler("/api/facts?product_id=api-repo&status=approved")
        h_facts_get.do_GET()
        self.assertEqual(len(h_facts_get.response_json()), 1)

        h_del = _StubHandler("/api/repos/api-repo", method="DELETE")
        h_del.do_DELETE()
        self.assertEqual(h_del.status_code, 200)
        self.assertIsNone(repos.get_repo("api-repo"))

    def test_11_propose_fact_limit_15_and_new_release_hook(self):
        """propose_fact caps at 15 per session, and syncing a new release enqueues a researcher session."""
        ctx = ToolContext(session_id="sess_limit_1", agent_id="researcher")
        allowed = ["propose_fact"]
        for i in range(15):
            res = tools.run_tool(
                "propose_fact",
                ctx,
                {"product_id": "cero", "text": f"Fact #{i}", "source_kind": "readme"},
                allowed,
            )
            self.assertTrue(res.get("proposed"))
        over = tools.run_tool(
            "propose_fact",
            ctx,
            {"product_id": "cero", "text": "Fact #16", "source_kind": "readme"},
            allowed,
        )
        self.assertIn("error", over)

        repo_dir = os.path.join(self.tmpdir, "rel-hook-repo")
        _init_temp_git_repo(repo_dir)
        _git_commit(repo_dir, "README.md", "# Hook Repo", "v1 commit")
        subprocess.run(["git", "tag", "v1.0.0"], cwd=repo_dir, check=True, capture_output=True)
        repos.add_repo({"kind": "local", "name": "Hook Repo", "local_path": repo_dir})

        with patch.object(repos, "enqueue_fact_draft_session") as m_hook:
            repos.sync_repo("hook-repo")
            m_hook.assert_not_called()

            _git_commit(repo_dir, "README.md", "# Hook Repo v2", "v2 commit")
            subprocess.run(["git", "tag", "v2.0.0"], cwd=repo_dir, check=True, capture_output=True)
            repos.sync_repo("hook-repo")
            m_hook.assert_called_once()
            self.assertEqual(m_hook.call_args[0][1], "v2.0.0")
