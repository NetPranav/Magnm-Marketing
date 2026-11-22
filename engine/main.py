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


def _detach_stdio():
    """Point stdout/stderr at devnull (used when the parent app is gone)."""
    try:
        devnull = open(os.devnull, "w")
        sys.stdout = devnull
        sys.stderr = devnull
    except OSError:
        pass


def _safe_log(message):
    """Write a line to stderr, ignoring closed or broken pipes (parent app gone)."""
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        # The pipe is gone; send further output to devnull so the interpreter's
        # final flush at exit doesn't fail too (that would exit with code 120).
        try:
            devnull = open(os.devnull, "w")
            sys.stdout = devnull
            sys.stderr = devnull
        except OSError:
            pass


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

    import db
    db.init_db()

    import agent_runtime
    agent_runtime.recover_stale_sessions()

    import coding_agents
    coding_agents.recover_stale_runs()

    from social import service as social_service
    social_service.recover_stale_runs()

    from posting import service as posting_service
    posting_service.recover_stale_attempts()

    import jobs
    jobs.start_worker()

    import scheduler_loop
    # Tests (and anyone who wants a quiet engine) can turn the background scheduler off.
    if os.environ.get("MAGNM_DISABLE_SCHEDULER", "").strip() not in ("1", "true", "yes"):
        scheduler_loop.start()

    import cloud_sync
    if cloud_sync.is_enabled():
        cloud_sync.start()

    import model_registry
    model_registry.start_catalog_monitor()

    import phone_server
    phone_server.start_if_enabled()

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
        # Start the server shutdown first: if the desktop app died, its stdout/stderr
        # pipes are gone and any print() raises BrokenPipeError.
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        if reason:
            _safe_log(f"[magnm-engine] Shutting down: {reason}")
        try:
            scheduler_loop.stop(timeout=1.0)
        except Exception:
            pass
        try:
            cloud_sync.stop(timeout=1.0)
        except Exception:
            pass
        try:
            model_registry.stop_catalog_monitor(timeout=1.0)
        except Exception:
            pass
        try:
            phone_server.stop(timeout=1.0)
        except Exception:
            pass
        try:
            jobs.stop_worker(timeout=1.0)
        except Exception:
            pass

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
                        # Nobody reads our stdout/stderr any more: detach them first so
                        # no later write (or the interpreter's final flush) hits a dead pipe.
                        _detach_stdio()
                        request_shutdown(f"parent pid {parent_pid} exited")
                        break

            threading.Thread(target=_watchdog, daemon=True).start()

    actual_port = httpd.server_address[1]
    server.write_engine_endpoint(actual_port)
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
        if not shutdown_started.is_set():
            try:
                scheduler_loop.stop(timeout=1.0)
            except Exception:
                pass
            try:
                cloud_sync.stop(timeout=1.0)
            except Exception:
                pass
            try:
                model_registry.stop_catalog_monitor(timeout=1.0)
            except Exception:
                pass
            try:
                phone_server.stop(timeout=1.0)
            except Exception:
                pass
            try:
                jobs.stop_worker(timeout=1.0)
            except Exception:
                pass
        try:
            server.remove_engine_endpoint(expected_pid=os.getpid())
        except Exception:
            pass
        httpd.server_close()


if __name__ == "__main__":
    main()
