#!/usr/bin/env python3
"""Desktop/embedded launcher for the Magnm Marketing engine.

Configures MAGNM_HOME before importing engine modules, emits a single
MAGNM_ENGINE_READY handshake line on stdout, and handles clean shutdown via
signals, /api/shutdown, or a parent-PID watchdog.
"""

import argparse
from http.server import ThreadingHTTPServer
import json
import os
import signal
import sys
import threading


def _is_pid_alive(pid):
    """Return True if process pid is alive, False if gone or zombie."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            process_query_limited = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query_limited, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == still_active
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

    stat_path = f"/proc/{pid}/stat"
    if os.path.exists(stat_path):
        try:
            with open(stat_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            rparen = raw.rfind(")")
            if rparen != -1:
                after = raw[rparen + 1:].lstrip()
                if after and after[0] in ("Z", "X"):
                    return False
        except OSError:
            pass
    return True


def parse_args(argv=None):
    """Parse launcher CLI arguments."""
    parser = argparse.ArgumentParser(description="Magnm Marketing Engine Launcher")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Bind port (default: 0 for ephemeral)")
    parser.add_argument("--data-dir", default=None, help="Override MAGNM_HOME user data directory")
    return parser.parse_args(argv)


def main(argv=None):
    """Start the engine HTTP server and emit the READY handshake line."""
    args = parse_args(argv)
    if args.data_dir:
        os.environ["MAGNM_HOME"] = args.data_dir

    engine_dir = os.path.dirname(os.path.abspath(__file__))
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)

    import paths
    paths.ensure_data_root()

    import server

    httpd = ThreadingHTTPServer((args.host, args.port), server.CommandCenterHandler)
    httpd.daemon_threads = True

    stop_event = threading.Event()
    shutdown_started = threading.Event()

    def request_shutdown(reason=""):
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        stop_event.set()
        if reason:
            print(f"[magnm-engine] Shutting down: {reason}", file=sys.stderr, flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    def handle_signal(signum, _frame):
        request_shutdown(f"signal {signum}")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    parent_raw = os.environ.get("MAGNM_PARENT_PID", "").strip()
    if parent_raw:
        try:
            parent_pid = int(parent_raw)
        except ValueError:
            parent_pid = None
        if parent_pid is not None:
            def _watchdog():
                while not stop_event.wait(2.0):
                    if not _is_pid_alive(parent_pid):
                        request_shutdown(f"parent pid {parent_pid} exited")
                        break

            threading.Thread(target=_watchdog, daemon=True).start()

    actual_port = httpd.server_address[1]
    ready_payload = json.dumps({
        "port": actual_port,
        "pid": os.getpid(),
        "data_root": paths.DATA_ROOT,
    })
    print(f"MAGNM_ENGINE_READY {ready_payload}", flush=True)

    try:
        httpd.serve_forever()
    finally:
        stop_event.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
