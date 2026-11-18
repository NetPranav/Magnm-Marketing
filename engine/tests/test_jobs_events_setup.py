"""Tests for job queue, live SSE events, keep-awake, setup_check, and keychain mode."""

import importlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import ai_gateway
import cmo_runtime
import db
import events
import jobs
import keepawake
import model_registry
import paths
import server
import setup_check


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


class _StubPostHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing GET and POST routes."""

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


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class JobsEventsSetupTests(unittest.TestCase):
    """Verify job queue, SSE, keepawake, setup_check, and desktop keychain mode."""

    def setUp(self):
        jobs.stop_worker(timeout=2.0)
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_secrets_mode = os.environ.get("MAGNM_SECRETS_MODE")
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        ai_gateway.ENV_PATH = paths.data(".env")

    def tearDown(self):
        jobs.stop_worker(timeout=2.0)
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        if self._prev_secrets_mode is None:
            os.environ.pop("MAGNM_SECRETS_MODE", None)
        else:
            os.environ["MAGNM_SECRETS_MODE"] = self._prev_secrets_mode
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def test_01_enqueue_runs_job_writes_log_and_publishes_progress_events(self):
        """enqueue runs a patched job body, writes its log, marks done, and publishes events."""
        with self.assertRaises(ValueError):
            jobs.enqueue("not_a_real_job")

        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        def fake_body(job_name, job_id=None, actor="UI", log_file=None):
            jobs.progress(job_id, 40, "Halfway")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("fake job output\n")
            return "fake job output\n"

        with patch.object(cmo_runtime, "run_job_body", side_effect=fake_body), patch.object(
            keepawake, "inhibit"
        ):
            jobs.start_worker()
            created = jobs.enqueue("repo_sync", actor="Test")
            self.assertEqual(created["status"], "queued")

            finished = _wait_until(
                lambda: (jobs.get_job(created["id"]) or {}).get("status") == "done"
            )
            self.assertTrue(finished, "Job did not finish in time")

        final_job = jobs.get_job(created["id"])
        self.assertEqual(final_job["status"], "done")
        self.assertEqual(final_job["progress"], 100)
        self.assertTrue(os.path.isfile(final_job["output_path"]))

        seen_events = []
        while not q.empty():
            seen_events.append(q.get_nowait())

        job_events = [e["data"] for e in seen_events if e["type"] == "job" and e["data"]["id"] == created["id"]]
        self.assertTrue(any(ev["progress"] == 40 and ev["message"] == "Halfway" for ev in job_events))
        self.assertEqual(job_events[-1]["status"], "done")

        legacy = jobs.get_job_status_legacy()
        self.assertFalse(legacy["running"])
        self.assertEqual(legacy["job_id"], created["id"])
        self.assertIn("fake job output", legacy["output"])

    def test_02_transient_retries_and_max_attempts_failure_without_real_sleep(self):
        """Failed job retries with 5s/15s waits via injectable sleep until done or failed."""
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        attempt_counter = {"n": 0}

        def flaky_once(job_name, **_kwargs):
            attempt_counter["n"] += 1
            if attempt_counter["n"] == 1:
                raise RuntimeError("transient glitch")
            return "recovered"

        with patch.object(cmo_runtime, "run_job_body", side_effect=flaky_once), patch.object(
            keepawake, "inhibit"
        ):
            jobs.start_worker(sleep_fn=fake_sleep)
            j1 = jobs.enqueue("market_scan", max_attempts=2)
            self.assertTrue(_wait_until(lambda: jobs.get_job(j1["id"])["status"] == "done"))
            j1_done = jobs.get_job(j1["id"])
            self.assertEqual(j1_done["attempts"], 2)
            self.assertEqual(sleep_calls, [5])

        sleep_calls.clear()

        def always_fail(job_name, **_kwargs):
            raise RuntimeError("boom")

        with patch.object(cmo_runtime, "run_job_body", side_effect=always_fail), patch.object(
            keepawake, "inhibit"
        ):
            j2 = jobs.enqueue("daily_cycle", max_attempts=3)
            self.assertTrue(_wait_until(lambda: jobs.get_job(j2["id"])["status"] == "failed"))
            j2_fail = jobs.get_job(j2["id"])
            self.assertEqual(j2_fail["attempts"], 3)
            self.assertEqual(j2_fail["error"], "boom")
            self.assertEqual(sleep_calls, [5, 15])

    def test_03_fifo_order_and_cancel_queued_job(self):
        """Worker runs jobs in FIFO order and skips cancelled queued jobs."""
        ran_order = []
        first_started = threading.Event()
        release_first = threading.Event()

        def controlled_job(job_name, job_id=None, **_kwargs):
            ran_order.append(job_id)
            if len(ran_order) == 1:
                first_started.set()
                release_first.wait(timeout=3.0)

        with patch.object(cmo_runtime, "run_job_body", side_effect=controlled_job), patch.object(
            keepawake, "inhibit"
        ):
            jobs.start_worker()
            j1 = jobs.enqueue("daily_cycle")
            self.assertTrue(first_started.wait(timeout=2.0))

            j2 = jobs.enqueue("market_scan")
            j3 = jobs.enqueue("repo_sync")

            cancelled = jobs.cancel(j2["id"])
            self.assertEqual(cancelled["status"], "cancelled")

            release_first.set()
            self.assertTrue(_wait_until(lambda: jobs.get_job(j3["id"])["status"] == "done"))

        self.assertEqual(ran_order, [j1["id"], j3["id"]])
        self.assertEqual(jobs.get_job(j2["id"])["status"], "cancelled")

    def test_04_stale_running_job_marked_failed_on_startup(self):
        """Jobs left in 'running' status from a prior crash fail with 'engine restarted'."""
        stale = db.create_job(
            {
                "id": "stale_job_1",
                "name": "daily_cycle",
                "status": "running",
                "progress": 30,
                "message": "Mid-flight",
                "attempts": 1,
                "max_attempts": 2,
                "created_at": "2026-11-19T00:00:00+00:00",
                "started_at": "2026-11-19T00:00:01+00:00",
                "actor": "UI",
            }
        )
        self.assertEqual(stale["status"], "running")

        jobs.start_worker()
        recovered = jobs.get_job("stale_job_1")
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error"], "engine restarted")
        self.assertIsNotNone(recovered["finished_at"])

    def test_05_sse_events_stream_delivers_team_room_message(self):
        """GET /api/events streams a 'message' SSE event within 5s when a message is posted."""
        env = os.environ.copy()
        env.pop("MAGNM_ENGINE_TOKEN", None)
        env.pop("MAGNM_PARENT_PID", None)
        env.pop("MAGNM_SECRETS_MODE", None)
        env["MAGNM_HOME"] = self.tmpdir

        proc = subprocess.Popen(
            [sys.executable, os.path.join(ENGINE_DIR, "main.py"), "--port", "0", "--data-dir", self.tmpdir],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _cleanup_proc():
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

        self.addCleanup(_cleanup_proc)

        line = proc.stdout.readline().strip()
        self.assertTrue(line.startswith("MAGNM_ENGINE_READY "), f"Unexpected output: {line!r}")
        info = json.loads(line.split(" ", 1)[1])
        base = f"http://127.0.0.1:{info['port']}"

        received = {}
        reader_error = {}
        connected = threading.Event()

        def read_sse():
            try:
                with urllib.request.urlopen(f"{base}/api/events", timeout=5) as resp:
                    connected.set()
                    current_event = None
                    while True:
                        raw_line = resp.readline()
                        if not raw_line:
                            break
                        text_line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if text_line.startswith("event: "):
                            current_event = text_line[len("event: "):].strip()
                        elif text_line.startswith("data: ") and current_event == "message":
                            received["event"] = current_event
                            received["data"] = json.loads(text_line[len("data: "):])
                            break
            except Exception as exc:
                reader_error["exc"] = exc

        t = threading.Thread(target=read_sse, daemon=True)
        t.start()
        self.assertTrue(connected.wait(timeout=3.0), "SSE stream did not connect")

        post_req = urllib.request.Request(
            f"{base}/api/messages",
            data=json.dumps({"sender": "CEO", "text": "hello live sse"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(post_req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

        t.join(timeout=5.0)
        self.assertNotIn("exc", reader_error)
        self.assertEqual(received.get("event"), "message")
        self.assertEqual(received.get("data", {}).get("text"), "hello live sse")

        shutdown_req = urllib.request.Request(
            f"{base}/api/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(shutdown_req, timeout=5):
            pass
        proc.wait(timeout=5)

    def test_06_keepawake_inhibit_safe_when_binaries_missing(self):
        """keepawake.inhibit() does not raise when system binaries are unavailable."""
        with patch.object(shutil, "which", return_value=None):
            with keepawake.inhibit():
                ran = True
        self.assertTrue(ran)

        with patch.object(shutil, "which", return_value="/usr/bin/false"), patch.object(
            subprocess, "Popen", side_effect=OSError("spawn failed")
        ), patch("sys.stderr", new=io.StringIO()):
            with keepawake.inhibit():
                ran_after_err = True
        self.assertTrue(ran_after_err)

    def test_07_setup_check_mocked_shapes_statuses_and_timeout(self):
        """setup_check returns expected check shape, statuses, and finishes within 3s."""
        fake_disk = shutil._ntuple_diskusage(total=100 * 1024**3, used=80 * 1024**3, free=20 * 1024**3)

        def fake_which(cmd):
            mapping = {
                "ffmpeg": "/usr/bin/ffmpeg",
                "node": "/usr/bin/node",
                "git": "/usr/bin/git",
                "google-chrome": "/usr/bin/google-chrome",
            }
            return mapping.get(cmd)

        def fake_urlopen(req, timeout=2):
            return _FakeHTTPResponse({"models": [{"name": "llama3.2:latest"}]})

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-fake-test"}, clear=False), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(shutil, "disk_usage", return_value=fake_disk), patch.object(
            shutil, "which", side_effect=fake_which
        ), patch.object(ai_gateway, "_ollama_reachable", return_value=True), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ), patch.object(model_registry, "get_catalog", return_value={"meta/llama-3.3-70b-instruct"}):
            t0 = time.monotonic()
            res = setup_check.run_setup_checks()
            elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 3.0)
        self.assertTrue(res["ok"])
        checks = {c["id"]: c for c in res["checks"]}
        self.assertEqual(
            set(checks.keys()),
            {"python", "disk", "ffmpeg", "browser", "node", "git", "ollama", "nvidia_key"},
        )
        for item in checks.values():
            self.assertEqual(set(item.keys()), {"id", "label", "status", "detail", "fix"})
            self.assertEqual(item["status"], "ok")
        self.assertNotIn("nvapi-fake-test", json.dumps(res))

        # Verify Ollama missing-model warn and NVIDIA rejected-key warn + missing ffmpeg -> ok=False
        def fake_urlopen_no_model(req, timeout=2):
            return _FakeHTTPResponse({"models": [{"name": "other-model:latest"}]})

        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "nvapi-bad"}, clear=False), patch.object(
            ai_gateway, "load_env", return_value={}
        ), patch.object(shutil, "disk_usage", return_value=fake_disk), patch.object(
            shutil, "which", return_value=None
        ), patch.object(setup_check, "_playwright_cache_dirs", return_value=[]), patch.object(
            ai_gateway, "_ollama_reachable", return_value=True
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen_no_model), patch.object(
            model_registry,
            "get_catalog",
            side_effect=urllib.error.HTTPError("https://example.test", 401, "Unauthorized", {}, None),
        ):
            res2 = setup_check.run_setup_checks()

        self.assertFalse(res2["ok"])
        checks2 = {c["id"]: c for c in res2["checks"]}
        self.assertEqual(checks2["ffmpeg"]["status"], "missing")
        self.assertEqual(checks2["ollama"]["status"], "warn")
        self.assertIn("ollama pull", checks2["ollama"]["fix"])
        self.assertEqual(checks2["nvidia_key"]["status"], "warn")
        self.assertEqual(checks2["nvidia_key"]["detail"], "key rejected")

        # Verify a stalled check does not exceed timeout_s
        with patch.object(setup_check, "_check_ollama", side_effect=lambda: time.sleep(2.0)):
            t1 = time.monotonic()
            res3 = setup_check.run_setup_checks(timeout_s=0.3)
            self.assertLess(time.monotonic() - t1, 1.5)
        checks3 = {c["id"]: c for c in res3["checks"]}
        self.assertEqual(checks3["ollama"]["status"], "warn")

    def test_08_keychain_mode_blocks_secret_writes_to_env(self):
        """In MAGNM_SECRETS_MODE=keychain, secret keys are rejected and .env is untouched."""
        env_file = paths.data(".env")
        self.assertFalse(os.path.exists(env_file))

        with patch.dict(os.environ, {"MAGNM_SECRETS_MODE": "keychain"}):
            h1 = _StubPostHandler(
                "/api/save_env",
                method="POST",
                body_dict={"key_name": "NVIDIA_NIM_API_KEY", "key_val": "nvapi-should-not-write"},
            )
            h1.do_POST()
            payload1 = h1.response_json()
            self.assertFalse(payload1["success"])
            self.assertEqual(payload1["error_code"], "use_desktop_keychain")
            self.assertFalse(os.path.exists(env_file))

            # Non-secret setting via /api/save_env_config is still allowed
            h2 = _StubPostHandler(
                "/api/save_env_config",
                method="POST",
                body_dict={"updates": {"AI_DEFAULT_PROVIDER": "ollama"}},
            )
            h2.do_POST()
            payload2 = h2.response_json()
            self.assertTrue(payload2["success"])
            self.assertTrue(os.path.exists(env_file))

            # Secret key inside /api/save_env_config is rejected without writing the secret
            h3 = _StubPostHandler(
                "/api/save_env_config",
                method="POST",
                body_dict={"updates": {"OPENAI_API_KEY": "sk-secret"}},
            )
            h3.do_POST()
            payload3 = h3.response_json()
            self.assertFalse(payload3["success"])
            self.assertEqual(payload3["error_code"], "use_desktop_keychain")
            with open(env_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("sk-secret", content)
            self.assertIn("AI_DEFAULT_PROVIDER=ollama", content)

    def test_09_job_api_routes_and_events_cookie_auth_and_queue_overflow(self):
        """HTTP job routes, running-job cancel, SSE token/cookie gate, and 500-item overflow."""
        # Queue overflow drops oldest
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))
        for i in range(events.MAX_QUEUE_SIZE + 5):
            events.publish("job", {"seq": i})
        self.assertEqual(q.qsize(), events.MAX_QUEUE_SIZE)
        first = q.get_nowait()
        self.assertEqual(first["data"]["seq"], 5)

        # HTTP /api/run, /api/jobs, /api/jobs/cancel, /api/job_status
        gate_started = threading.Event()
        gate_release = threading.Event()

        def cancellable_job(job_name, job_id=None, **_kwargs):
            gate_started.set()
            gate_release.wait(timeout=3.0)
            if jobs.is_cancel_requested(job_id):
                raise jobs.JobCancelled("cancelled")

        with patch.object(cmo_runtime, "run_job_body", side_effect=cancellable_job), patch.object(
            keepawake, "inhibit"
        ):
            h_run = _StubPostHandler("/api/run", method="POST", body_dict={"job": "daily_cycle"})
            h_run.do_POST()
            self.assertEqual(h_run.status_code, 200)
            j_run = h_run.response_json()["job"]
            self.assertTrue(gate_started.wait(timeout=2.0))

            h_cancel = _StubPostHandler("/api/jobs/cancel", method="POST", body_dict={"id": j_run["id"]})
            h_cancel.do_POST()
            self.assertEqual(h_cancel.status_code, 200)
            self.assertTrue(h_cancel.response_json()["job"]["cancel_requested"])

            gate_release.set()
            self.assertTrue(_wait_until(lambda: jobs.get_job(j_run["id"])["status"] == "cancelled"))

            h_list = _StubPostHandler("/api/jobs?limit=5")
            h_list.do_GET()
            self.assertEqual(h_list.status_code, 200)
            self.assertEqual(h_list.response_json()[0]["id"], j_run["id"])

            h_legacy = _StubPostHandler("/api/job_status")
            h_legacy.do_GET()
            self.assertEqual(h_legacy.status_code, 200)
            self.assertFalse(h_legacy.response_json()["running"])
            self.assertEqual(h_legacy.response_json()["job_id"], j_run["id"])

        # Token gate on /api/events: 401 without token, 200 with cookie
        with patch.object(server, "ENGINE_TOKEN", "secret-tok"):
            unauth = _StubPostHandler("/api/events")
            unauth.do_GET()
            self.assertEqual(unauth.status_code, 401)

            authed = _StubPostHandler("/api/events", headers={"Cookie": "magnm_token=secret-tok"})
            with patch.object(authed, "_serve_sse") as mock_sse:
                authed.do_GET()
                mock_sse.assert_called_once()


if __name__ == "__main__":
    unittest.main()
