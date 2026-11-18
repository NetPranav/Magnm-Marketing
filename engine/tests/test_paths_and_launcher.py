"""Tests for engine.paths, engine.main launcher, token auth, and parent watchdog."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import paths


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following 3xx redirects so we can inspect 302 headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_request(url, method="GET", headers=None, json_body=None, follow_redirects=True):
    """Perform an HTTP request with urllib and return (status, headers_dict, body_bytes)."""
    req_headers = dict(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    opener = (
        urllib.request.build_opener()
        if follow_redirects
        else urllib.request.build_opener(_NoRedirectHandler())
    )
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as err:
        try:
            body = err.read()
            hdrs = dict(err.headers.items()) if err.headers else {}
            return err.code, hdrs, body
        finally:
            err.close()


class PathsAndLauncherTests(unittest.TestCase):
    """Verify path separation, data seeding, READY handshake, token auth, and watchdog."""

    def _spawn_engine(self, data_dir, extra_env=None):
        """Start engine/main.py on port 0 and return (proc, ready_info)."""
        env = os.environ.copy()
        env.pop("MAGNM_ENGINE_TOKEN", None)
        env.pop("MAGNM_PARENT_PID", None)
        env.pop("MAGNM_HOME", None)
        if extra_env:
            env.update(extra_env)

        proc = subprocess.Popen(
            [sys.executable, os.path.join(ENGINE_DIR, "main.py"), "--port", "0", "--data-dir", data_dir],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _cleanup():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        self.addCleanup(_cleanup)

        line = proc.stdout.readline().strip()
        if not line.startswith("MAGNM_ENGINE_READY "):
            stderr_out = proc.stderr.read() if proc.stderr else ""
            self.fail(f"Expected MAGNM_ENGINE_READY line, got {line!r} (stderr={stderr_out!r})")

        info = json.loads(line.split(" ", 1)[1])
        return proc, info

    def test_01_ensure_data_root_seeds_and_preserves_existing(self):
        """ensure_data_root seeds config/ and agents/ into MAGNM_HOME without overwriting."""
        prev_home = os.environ.get("MAGNM_HOME")

        def _restore_paths():
            if prev_home is None:
                os.environ.pop("MAGNM_HOME", None)
            else:
                os.environ["MAGNM_HOME"] = prev_home
            importlib.reload(paths)

        self.addCleanup(_restore_paths)

        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["MAGNM_HOME"] = tmpdir
            importlib.reload(paths)

            paths.ensure_data_root()

            rules_dst = os.path.join(tmpdir, "config", "product_rules.json")
            self.assertTrue(os.path.isfile(rules_dst))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "agents", "cmo.md")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "playbooks", "crisis.md")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "research", "product-facts.md")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "config.md")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "state", "chats")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "logs", "jobs")))

            # Modify config file and verify a second ensure_data_root() leaves it intact.
            custom_content = '{"custom": true}\n'
            with open(rules_dst, "w", encoding="utf-8") as f:
                f.write(custom_content)

            paths.ensure_data_root()
            with open(rules_dst, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), custom_content)

    def test_02_data_and_resource_roots(self):
        """data() and resource() resolve under DATA_ROOT and RESOURCE_ROOT respectively."""
        prev_home = os.environ.get("MAGNM_HOME")

        def _restore_paths():
            if prev_home is None:
                os.environ.pop("MAGNM_HOME", None)
            else:
                os.environ["MAGNM_HOME"] = prev_home
            importlib.reload(paths)

        self.addCleanup(_restore_paths)

        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["MAGNM_HOME"] = tmpdir
            importlib.reload(paths)

            self.assertEqual(os.path.abspath(paths.RESOURCE_ROOT), os.path.abspath(REPO_ROOT))
            self.assertEqual(os.path.abspath(paths.DATA_ROOT), os.path.abspath(tmpdir))
            self.assertEqual(
                paths.resource("engine", "server.py"),
                os.path.join(os.path.abspath(REPO_ROOT), "engine", "server.py"),
            )
            self.assertEqual(
                paths.data("state", "global_messages.json"),
                os.path.join(os.path.abspath(tmpdir), "state", "global_messages.json"),
            )

    def test_03_launcher_token_auth_and_shutdown(self):
        """Token-protected engine enforces auth, sets HttpOnly cookie on /?token=, and shuts down."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc, info = self._spawn_engine(tmpdir, extra_env={"MAGNM_ENGINE_TOKEN": "abc"})
            port = info["port"]
            self.assertIsInstance(port, int)
            self.assertGreater(port, 0)
            self.assertEqual(os.path.abspath(info["data_root"]), os.path.abspath(tmpdir))
            base = f"http://127.0.0.1:{port}"

            # /api/health is always allowed without token
            status, _, body = _http_request(f"{base}/api/health")
            self.assertEqual(status, 200)
            health = json.loads(body.decode("utf-8"))
            self.assertTrue(health["ok"])
            self.assertEqual(health["service"], "magnm-engine")
            self.assertEqual(os.path.abspath(health["data_root"]), os.path.abspath(tmpdir))

            # /api/status without token -> 401
            status, _, body = _http_request(f"{base}/api/status")
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body.decode("utf-8")), {"error": "unauthorized"})

            # /api/status with X-Magnm-Token header -> 200
            status, _, _ = _http_request(f"{base}/api/status", headers={"X-Magnm-Token": "abc"})
            self.assertEqual(status, 200)

            # GET /?token=abc -> 302 with Set-Cookie containing HttpOnly
            status, hdrs, _ = _http_request(f"{base}/?token=abc", follow_redirects=False)
            self.assertEqual(status, 302)
            set_cookie = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
            self.assertIn("magnm_token=abc", set_cookie)
            self.assertIn("HttpOnly", set_cookie)

            # POST /api/shutdown with token -> 200 and clean exit within 5s
            status, _, body = _http_request(
                f"{base}/api/shutdown",
                method="POST",
                headers={"X-Magnm-Token": "abc"},
                json_body={},
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True})
            rc = proc.wait(timeout=5)
            self.assertEqual(rc, 0)

    def test_04_parent_watchdog_exits_after_parent_dies(self):
        """Engine exits cleanly within ~6s when MAGNM_PARENT_PID terminates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_proc = subprocess.Popen(["sleep", "1"])
            self.addCleanup(lambda: parent_proc.poll() is None and parent_proc.kill())

            proc, _ = self._spawn_engine(
                tmpdir,
                extra_env={"MAGNM_PARENT_PID": str(parent_proc.pid)},
            )
            parent_proc.wait(timeout=5)
            rc = proc.wait(timeout=6)
            self.assertEqual(rc, 0)

    def test_05_dev_mode_without_token_allows_status(self):
        """Without MAGNM_ENGINE_TOKEN, GET /api/status returns 200."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc, info = self._spawn_engine(tmpdir)
            base = f"http://127.0.0.1:{info['port']}"

            status, _, _ = _http_request(f"{base}/api/status")
            self.assertEqual(status, 200)

            status, _, _ = _http_request(f"{base}/api/shutdown", method="POST", json_body={})
            self.assertEqual(status, 200)
            self.assertEqual(proc.wait(timeout=5), 0)

    def test_06_messages_isolated_to_data_dir(self):
        """POST /api/messages writes under --data-dir and leaves repo state untouched."""
        repo_msgs = os.path.join(REPO_ROOT, "state", "global_messages.json")
        before_bytes = None
        if os.path.exists(repo_msgs):
            with open(repo_msgs, "rb") as f:
                before_bytes = f.read()

        with tempfile.TemporaryDirectory() as tmpdir:
            proc, info = self._spawn_engine(tmpdir)
            base = f"http://127.0.0.1:{info['port']}"

            status, _, body = _http_request(
                f"{base}/api/messages",
                method="POST",
                json_body={"sender": "CEO", "text": "hello"},
            )
            self.assertEqual(status, 200)
            payload = json.loads(body.decode("utf-8"))
            self.assertTrue(payload["success"])

            _http_request(f"{base}/api/shutdown", method="POST", json_body={})
            proc.wait(timeout=5)

            tmp_msgs = os.path.join(tmpdir, "state", "global_messages.json")
            self.assertTrue(os.path.isfile(tmp_msgs))
            with open(tmp_msgs, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertTrue(
                any(m.get("sender") == "CEO" and m.get("text") == "hello" for m in saved.get("messages", []))
            )

        if before_bytes is not None:
            with open(repo_msgs, "rb") as f:
                self.assertEqual(before_bytes, f.read())


if __name__ == "__main__":
    unittest.main()
