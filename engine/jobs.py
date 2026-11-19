#!/usr/bin/env python3
"""SQLite-backed FIFO job queue, worker thread, retry logic, and live progress."""

from datetime import datetime, timezone
import inspect
import os
import sys
import threading
import time
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cmo_runtime
import db
import events
import keepawake
import paths

RETRY_DELAYS = (5, 15)

_sleep = time.sleep
_worker_lock = threading.Lock()
_worker_thread = None
_worker_sleep_fn = None
_wake_event = threading.Event()
_stop_event = threading.Event()


class JobCancelled(Exception):
    """Raised when a running job observes a cancellation request."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _publish_job(job):
    if job is not None:
        events.publish("job", job)
    return job


def enqueue(name, actor="UI", max_attempts=2, params=None):
    """Validate and enqueue a job in FIFO order, returning the job dictionary."""
    if name not in cmo_runtime.ALLOWED_JOBS:
        raise ValueError(f"Unknown job '{name}'. Allowed jobs: {list(cmo_runtime.ALLOWED_JOBS.keys())}")
    attempts_limit = max(1, int(max_attempts))
    job_id = f"{name}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    output_path = paths.data("logs", "jobs", f"{job_id}.log")
    job = db.create_job(
        {
            "id": job_id,
            "name": name,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "attempts": 0,
            "max_attempts": attempts_limit,
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "output_path": output_path,
            "error": None,
            "actor": actor,
            "cancel_requested": False,
            "params": params,
        }
    )
    _publish_job(job)
    _wake_event.set()
    return job


def get_job(job_id):
    """Return a single job dictionary by ID, or None."""
    return db.get_job(job_id)


def list_jobs(limit=20):
    """Return recent jobs ordered newest-first."""
    return db.list_jobs(limit=limit)


def is_cancel_requested(job_id):
    """Return True if cancellation was requested for job_id."""
    if not job_id:
        return False
    job = db.get_job(job_id)
    return bool(job and job.get("cancel_requested"))


def progress(job_id, pct, message=None):
    """Update a job's progress (0-100) and optional message, publishing a job event."""
    if not job_id or db.get_job(job_id) is None:
        return None
    clamped = max(0, min(100, int(pct)))
    fields = {"progress": clamped}
    if message is not None:
        fields["message"] = str(message)
    job = db.update_job(job_id, **fields)
    return _publish_job(job)


def cancel(job_id):
    """Cancel a queued job immediately or mark a running job cancel-requested."""
    if not job_id or db.get_job(job_id) is None:
        return None
    job = db.cancel_job_record(job_id, _now_iso())
    return _publish_job(job)


def recover_stale_jobs():
    """Mark any jobs left in 'running' state after a crash as failed."""
    recovered = db.fail_stale_running_jobs(_now_iso(), error="engine restarted")
    for job in recovered:
        _publish_job(job)
    return recovered


def get_job_status_legacy():
    """Derive the legacy /api/job_status payload from the latest SQLite job."""
    job = db.get_latest_job()
    if job is None:
        return {
            "running": False,
            "job_name": None,
            "job_id": None,
            "output": "",
            "error": None,
            "status": "idle",
            "progress": 0,
            "message": "",
        }

    output = ""
    out_path = job.get("output_path")
    if out_path and os.path.isfile(out_path):
        try:
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                output = f.read(65536)
        except OSError:
            output = ""
    if not output:
        output = job.get("message") or ""

    return {
        "running": job["status"] == "running",
        "job_name": job["name"],
        "job_id": job["id"],
        "output": output,
        "error": job.get("error"),
        "status": job["status"],
        "progress": job["progress"],
        "message": job.get("message") or "",
    }


def _invoke_job_body(job):
    """Call cmo_runtime.run_job_body, adapting arguments if patched by tests."""
    fn = cmo_runtime.run_job_body
    kwargs = {
        "job_id": job["id"],
        "actor": job.get("actor") or "UI",
        "log_file": job.get("output_path"),
    }
    if job.get("params") or job["name"] == "workflow_step":
        kwargs["params"] = job.get("params") or {}
    call_kwargs = None
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if accepts_var_kw:
            call_kwargs = kwargs
        else:
            call_kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        call_kwargs = None

    if call_kwargs is not None:
        result = fn(job["name"], **call_kwargs)
    else:
        result = fn(job["name"])

    out_path = job.get("output_path")
    if isinstance(result, str) and out_path and not os.path.exists(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result)
    return result


def _sleep_for_retry(delay_s):
    fn = _worker_sleep_fn or _sleep
    fn(delay_s)


def _execute_one(job):
    """Run a single job with keep-awake inhibition and retry handling."""
    job_id = job["id"]
    max_attempts = max(1, int(job.get("max_attempts") or 2))

    job = db.update_job(
        job_id,
        status="running",
        started_at=job.get("started_at") or _now_iso(),
        progress=0,
        message="Starting...",
        error=None,
    )
    _publish_job(job)

    with keepawake.inhibit():
        for attempt in range(1, max_attempts + 1):
            if _stop_event.is_set():
                return
            if is_cancel_requested(job_id):
                done_job = db.update_job(
                    job_id,
                    status="cancelled",
                    finished_at=_now_iso(),
                    message="Cancelled",
                )
                _publish_job(done_job)
                return

            job = db.update_job(
                job_id,
                attempts=attempt,
                message=f"Running (attempt {attempt}/{max_attempts})",
            )
            _publish_job(job)

            try:
                _invoke_job_body(job)
            except JobCancelled:
                done_job = db.update_job(
                    job_id,
                    status="cancelled",
                    finished_at=_now_iso(),
                    message="Cancelled",
                )
                _publish_job(done_job)
                return
            except Exception as exc:
                if is_cancel_requested(job_id):
                    done_job = db.update_job(
                        job_id,
                        status="cancelled",
                        finished_at=_now_iso(),
                        message="Cancelled",
                    )
                    _publish_job(done_job)
                    return

                err_str = str(exc)
                if attempt < max_attempts:
                    delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                    retry_job = db.update_job(
                        job_id,
                        error=err_str,
                        message=f"Attempt {attempt} failed ({err_str}); retrying in {delay}s",
                    )
                    _publish_job(retry_job)
                    _sleep_for_retry(delay)
                    continue

                fail_job = db.update_job(
                    job_id,
                    status="failed",
                    finished_at=_now_iso(),
                    error=err_str,
                    message=f"Failed after {attempt} attempt(s): {err_str}",
                )
                _publish_job(fail_job)
                return

            if is_cancel_requested(job_id):
                done_job = db.update_job(
                    job_id,
                    status="cancelled",
                    finished_at=_now_iso(),
                    message="Cancelled",
                )
                _publish_job(done_job)
                return

            done_job = db.update_job(
                job_id,
                status="done",
                progress=100,
                finished_at=_now_iso(),
                error=None,
                message="Completed",
            )
            _publish_job(done_job)
            return


def _worker_loop():
    while not _stop_event.is_set():
        try:
            job = db.get_next_queued_job()
        except Exception:
            job = None

        if job is not None:
            _execute_one(job)
            continue

        _wake_event.wait(timeout=0.5)
        _wake_event.clear()


def start_worker(sleep_fn=None):
    """Idempotently recover stale running jobs and start the single FIFO worker thread."""
    global _worker_thread, _worker_sleep_fn
    with _worker_lock:
        if sleep_fn is not None:
            _worker_sleep_fn = sleep_fn
        if _worker_thread is not None and _worker_thread.is_alive():
            return _worker_thread

        recover_stale_jobs()
        try:
            import workflows
            workflows.recover_stale_runs(start_worker=False)
        except Exception:
            pass
        _stop_event.clear()
        _wake_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="magnm-job-worker",
            daemon=True,
        )
        _worker_thread.start()
        return _worker_thread


def stop_worker(timeout=5.0):
    """Signal the worker thread to exit and wait up to timeout seconds."""
    global _worker_thread, _worker_sleep_fn
    with _worker_lock:
        thread = _worker_thread
        _stop_event.set()
        _wake_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    with _worker_lock:
        _worker_thread = None
        _worker_sleep_fn = None
