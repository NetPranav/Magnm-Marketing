#!/usr/bin/env python3
"""Cross-platform end-to-end smoke test for the Magnm Studio engine (source or frozen binary)."""

import argparse
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse

_SAFE_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "WINDIR",
    "PATHEXT",
    "OS",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)

_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")

_SUBSYSTEM_GET_ROUTES = (
    ("status", "/api/status", dict),
    ("jobs", "/api/jobs?limit=5", list),
    ("key_pool", "/api/key_pool", list),
    ("roster", "/api/roster", (list, dict)),
    ("content_review", "/api/content?review_counts=1&limit=5", list),
    ("assets", "/api/assets", list),
    ("messages", "/api/messages", (list, dict)),
    ("product_rules", "/api/product_rules", dict),
    ("workflows", "/api/workflows?limit=5", list),
    ("sessions", "/api/sessions?limit=5", list),
    ("repos", "/api/repos", list),
    ("watch_topics", "/api/watch_topics", list),
    ("pitch", "/api/pitch/catalog", dict),
    ("account", "/api/account", dict),
    ("team", "/api/team/knowledge", dict),
    ("notifications", "/api/notifications", dict),
    ("notifications_settings", "/api/notifications/settings", dict),
    ("reminders", "/api/reminders", dict),
    ("results", "/api/results/proposals", dict),
    ("summaries", "/api/summaries", dict),
    ("learnings", "/api/learnings", dict),
    ("coding", "/api/coding/agents", dict),
    ("social", "/api/social/status", dict),
    ("posting", "/api/posting/status", dict),
    ("phone", "/api/phone", dict),
)


class SmokeTestError(RuntimeError):
    """Raised when a smoke test check fails."""

    def __init__(self, check_name, message):
        super().__init__(message)
        self.check_name = check_name
        self.message = str(message)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_clean_env(data_dir):
    env = {}
    for name in _SAFE_ENV_KEYS:
        val = os.environ.get(name)
        if val is None:
            continue
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            continue
        env[name] = val
    env["MAGNM_HOME"] = data_dir
    env["MAGNM_DISABLE_SCHEDULER"] = "1"
    env["MAGNM_DISABLE_CLOUD_SYNC"] = "1"
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _http_request(host, port, method, path, headers=None, body=None, timeout=10.0):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, resp_headers, raw
    finally:
        conn.close()


def _wait_for_endpoint(proc, endpoint_path, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise SmokeTestError("startup", f"Engine exited early with code {rc}")
        if os.path.isfile(endpoint_path):
            try:
                with open(endpoint_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                url = str((data or {}).get("url") or "").strip()
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port:
                    return parsed.port, data
            except Exception:
                pass
        time.sleep(0.1)
    raise SmokeTestError("endpoint_file", f"Timed out waiting for {endpoint_path}")


def _tail_log(log_path, max_lines=40):
    if not log_path or not os.path.isfile(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _stop_process_tree(proc, port=None):
    if proc is None:
        return
    if port and proc.poll() is None:
        try:
            _http_request(
                "127.0.0.1",
                port,
                "POST",
                "/api/shutdown",
                headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"},
                body=b"{}",
                timeout=3.0,
            )
            proc.wait(timeout=5.0)
            return
        except Exception:
            pass

    if proc.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                )
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if proc.poll() is not None:
                    break
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                except Exception:
                    try:
                        proc.send_signal(sig)
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                    break
                except subprocess.TimeoutExpired:
                    continue

    if proc.poll() is None:
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass


def _run_checks(port, data_dir):
    host_hdr = {"Host": f"127.0.0.1:{port}"}

    # 1. Health check & isolated data_root
    status, _, raw = _http_request("127.0.0.1", port, "GET", "/api/health", headers=host_hdr)
    if status != 200:
        raise SmokeTestError("health", f"GET /api/health returned HTTP {status}")
    try:
        health = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SmokeTestError("health", f"Invalid JSON from /api/health: {exc}") from exc
    if not health.get("ok") or health.get("service") != "magnm-engine":
        raise SmokeTestError("health", f"Unexpected /api/health payload: {health!r}")
    if os.path.realpath(str(health.get("data_root") or "")) != os.path.realpath(data_dir):
        raise SmokeTestError(
            "health",
            f"data_root mismatch: expected {data_dir!r}, got {health.get('data_root')!r}",
        )

    # 2. Web index + one bundled asset
    status, hdrs, raw_html = _http_request("127.0.0.1", port, "GET", "/", headers=host_hdr)
    if status != 200:
        raise SmokeTestError("web_index", f"GET / returned HTTP {status}")
    ctype = hdrs.get("content-type", "")
    html_text = raw_html.decode("utf-8", errors="replace")
    if "text/html" not in ctype or '<div id="root">' not in html_text:
        raise SmokeTestError(
            "web_index",
            f"GET / did not serve built web UI (content-type={ctype!r})",
        )

    asset_match = re.search(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html_text)
    if not asset_match:
        raise SmokeTestError("web_asset", "Could not find /assets/... reference in index.html")
    asset_path = asset_match.group(1)
    status, _, asset_bytes = _http_request("127.0.0.1", port, "GET", asset_path, headers=host_hdr)
    if status != 200 or not asset_bytes:
        raise SmokeTestError("web_asset", f"GET {asset_path} returned HTTP {status} ({len(asset_bytes)} bytes)")

    # 3. Read-only GET across every subsystem
    for check_name, route, expected_type in _SUBSYSTEM_GET_ROUTES:
        status, _, body = _http_request("127.0.0.1", port, "GET", route, headers=host_hdr)
        if status != 200:
            raise SmokeTestError(check_name, f"GET {route} returned HTTP {status}: {body[:200]!r}")
        try:
            parsed = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise SmokeTestError(check_name, f"GET {route} returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, expected_type):
            raise SmokeTestError(
                check_name,
                f"GET {route} returned {type(parsed).__name__}, expected {expected_type}",
            )
        if check_name == "notifications_settings":
            kinds = {item.get("kind") for item in (parsed.get("settings") or []) if isinstance(item, dict)}
            for required_kind in ("key_rejected", "keys_exhausted", "models_changed"):
                if required_kind not in kinds:
                    raise SmokeTestError(
                        check_name,
                        f"Missing {required_kind!r} in /api/notifications/settings",
                    )

    # 4. Request guard: foreign Host and cross-site POST must return 403
    status, _, _ = _http_request(
        "127.0.0.1",
        port,
        "GET",
        "/api/health",
        headers={"Host": f"evil.example:{port}"},
    )
    if status != 403:
        raise SmokeTestError("guard_foreign_host", f"Foreign Host expected 403, got HTTP {status}")

    status, _, _ = _http_request(
        "127.0.0.1",
        port,
        "POST",
        "/api/messages",
        headers={
            "Host": f"127.0.0.1:{port}",
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
            "Content-Type": "application/json",
        },
        body=b'{"sender":"test","text":"blocked"}',
    )
    if status != 403:
        raise SmokeTestError("guard_cross_site_post", f"Cross-site POST expected 403, got HTTP {status}")


def run_smoke_test(engine_bin=None, timeout_s=45.0):
    """Run the full engine smoke test against source or a frozen binary."""
    repo_root = _repo_root()
    temp_root = tempfile.mkdtemp(prefix="magnm-smoke-")
    data_dir = os.path.join(temp_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(temp_root, "engine.log")

    if os.path.realpath(data_dir) == os.path.realpath(repo_root):
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SmokeTestError("setup", "Temporary data dir resolved to repository root")

    if engine_bin:
        bin_abs = os.path.abspath(engine_bin)
        if not os.path.isfile(bin_abs):
            shutil.rmtree(temp_root, ignore_errors=True)
            raise SmokeTestError("setup", f"Engine binary not found: {bin_abs}")
        cmd = [bin_abs, "--host", "127.0.0.1", "--port", "0", "--data-dir", data_dir]
    else:
        main_py = os.path.join(repo_root, "engine", "main.py")
        cmd = [sys.executable, main_py, "--host", "127.0.0.1", "--port", "0", "--data-dir", data_dir]

    env = _build_clean_env(data_dir)
    endpoint_path = os.path.join(data_dir, "state", "engine_endpoint.json")

    proc = None
    port = None
    log_fh = None
    try:
        log_fh = open(log_path, "w", encoding="utf-8")
        popen_kwargs = {
            "cwd": temp_root,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fh,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        port, _ = _wait_for_endpoint(proc, endpoint_path, timeout_s)
        if port == 5050:
            raise SmokeTestError("port", "Engine bound port 5050 instead of ephemeral port 0")
        _run_checks(port, data_dir)
    except SmokeTestError as exc:
        _stop_process_tree(proc, port=port)
        if log_fh is not None:
            log_fh.close()
            log_fh = None
        tail = _tail_log(log_path)
        shutil.rmtree(temp_root, ignore_errors=True)
        tail_block = "\n".join(tail) if tail else "(no log output)"
        raise SmokeTestError(exc.check_name, f"{exc.message}\n--- Last engine log lines ---\n{tail_block}") from exc
    except Exception as exc:
        _stop_process_tree(proc, port=port)
        if log_fh is not None:
            log_fh.close()
            log_fh = None
        tail = _tail_log(log_path)
        shutil.rmtree(temp_root, ignore_errors=True)
        tail_block = "\n".join(tail) if tail else "(no log output)"
        raise SmokeTestError("unexpected", f"{exc}\n--- Last engine log lines ---\n{tail_block}") from exc
    finally:
        _stop_process_tree(proc, port=port)
        if log_fh is not None:
            log_fh.close()
        shutil.rmtree(temp_root, ignore_errors=True)

    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description="Magnm Studio engine smoke test")
    parser.add_argument("--engine-bin", default=None, help="Path to frozen magnm-engine binary")
    parser.add_argument("--timeout", type=float, default=45.0, help="Startup timeout in seconds")
    args = parser.parse_args(argv)

    try:
        port = run_smoke_test(engine_bin=args.engine_bin, timeout_s=args.timeout)
    except SmokeTestError as exc:
        print(f"[smoke_test] FAILED check={exc.check_name}: {exc.message}", file=sys.stderr)
        return 1

    mode = f"binary ({args.engine_bin})" if args.engine_bin else "source (engine/main.py)"
    print(f"[smoke_test] PASS ({mode}, ephemeral port {port}, {len(_SUBSYSTEM_GET_ROUTES)} subsystem routes + guard checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
