#!/usr/bin/env python3
"""In-process daemon scheduler loop for desktop and standalone engine runs."""

from datetime import datetime, timedelta, timezone
import os
import sys
import threading

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cmo_runtime
import db
from ist import IST
import jobs
import reminders

CHECK_INTERVAL_S = 60

DEFAULT_SCHEDULES = [
    {
        "id": "cmo_daily",
        "name": "Daily CMO Cycle",
        "command": "daily_cycle",
        "cron": "46 8 * * *",
        "description": "Runs daily CMO digest, metrics, and plan checks at 08:46 IST",
        "enabled": True,
        "last_run": None,
        "last_status": "idle",
    },
    {
        "id": "market_scan",
        "name": "Market & Hacker News Scanner",
        "command": "market_scan",
        "interval_hours": 6,
        "description": "Scans HN Algolia API for developer discussions",
        "enabled": True,
        "last_run": None,
        "last_status": "idle",
    },
    {
        "id": "repo_sync",
        "name": "Codebase & GitHub Intelligence Sync",
        "command": "repo_sync",
        "interval_hours": 2,
        "description": "Syncs local Git commits, releases, and PRs for configured repositories",
        "enabled": True,
        "last_run": None,
        "last_status": "idle",
    },
    {
        "id": "watch_topics",
        "name": "Watch Topics Radar",
        "command": "watch_topics",
        "interval_hours": 1,
        "description": "Runs due research watch topics hourly",
        "enabled": True,
        "last_run": None,
        "last_status": "idle",
    },
    {
        "id": "weekly_summary",
        "name": "Weekly Analyst Summary",
        "command": "weekly_summary",
        "cron": "0 9 * * 1",
        "description": "Generates the previous week's Analyst summary and learnings on Monday at 09:00 IST",
        "enabled": True,
        "last_run": None,
        "last_status": "idle",
    },
]

_lock = threading.Lock()
_thread = None
_stop_event = threading.Event()


def _as_utc(dt):
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return _as_utc(dt)
    except ValueError:
        return None


def _cron_field_matches(field, value):
    raw = str(field or "*").strip()
    if raw == "*":
        return True
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if int(part) == int(value):
                return True
        except ValueError:
            continue
    return False


def is_due(schedule, now=None):
    """Return True if an enabled schedule entry is due at `now`."""
    if not isinstance(schedule, dict) or not schedule.get("enabled", True):
        return False

    now_utc = _as_utc(now)
    last_run_utc = _parse_iso(schedule.get("last_run"))

    cron_expr = (schedule.get("cron") or "").strip()
    if cron_expr:
        parts = cron_expr.split()
        if len(parts) < 5:
            return False
        minute_s, hour_s, dom_s, month_s, dow_s = parts[:5]
        try:
            minute = int(minute_s)
            hour = int(hour_s)
        except ValueError:
            return False

        now_ist = now_utc.astimezone(IST)
        # The weekly summary runs on its day (Monday 09:00 IST by default) and catches up once if that was missed.
        if schedule.get("id") == "weekly_summary" or schedule.get("command") == "weekly_summary":
            try:
                days_after_monday = (int(dow_s) - 1) % 7
            except ValueError:
                days_after_monday = 0
            run_date = now_ist.date() - timedelta(days=now_ist.weekday()) + timedelta(days=days_after_monday)
            target = datetime(run_date.year, run_date.month, run_date.day, hour, minute, 0, tzinfo=IST)
            if now_ist < target:
                return False
            if last_run_utc is None:
                return True
            return last_run_utc.astimezone(IST) < target

        if not _cron_field_matches(dom_s, now_ist.day):
            return False
        if not _cron_field_matches(month_s, now_ist.month):
            return False
        # Standard cron Sunday=0/7; Python weekday(): Monday=0..Sunday=6
        cron_dow = (now_ist.weekday() + 1) % 7
        if dow_s != "*" and not (
            _cron_field_matches(dow_s, cron_dow)
            or (cron_dow == 0 and _cron_field_matches(dow_s, 7))
        ):
            return False

        target_today = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_ist < target_today:
            return False
        if last_run_utc is None:
            return True
        last_run_ist = last_run_utc.astimezone(IST)
        return last_run_ist < target_today

    interval_hours = schedule.get("interval_hours")
    if interval_hours is None:
        return False
    try:
        interval_s = float(interval_hours) * 3600.0
    except (TypeError, ValueError):
        return False
    if interval_s <= 0:
        return False
    if last_run_utc is None:
        return True
    return (now_utc - last_run_utc).total_seconds() >= interval_s


def ensure_default_schedules(commands=None):
    """Seed missing DEFAULT_SCHEDULES, limited to `commands` when given."""
    wanted = [s for s in DEFAULT_SCHEDULES if commands is None or s["command"] in commands]

    def seed(existing):
        existing_ids = {s.get("id") for s in existing if isinstance(s, dict)}
        existing.extend(dict(s) for s in wanted if s["id"] not in existing_ids)
        return list(existing)

    return db.modify_schedules(seed)


def _record_runs(runs):
    """Mark schedules as queued, merging into the schedules as they are now so a concurrent edit survives."""
    if not runs:
        return

    def mark(schedules):
        for sched in schedules:
            when = runs.get(sched.get("id"))
            if when:
                sched["last_run"] = when
                sched["last_status"] = "queued"

    db.modify_schedules(mark)


def tick(now=None, commands=None):
    """Evaluate schedules (only `commands` when given) and due reminders at `now`, enqueueing due jobs without duplicates."""
    now_utc = _as_utc(now)
    reminders.check_due_reminders(now=now_utc)
    schedules = db.load_schedules()
    if not schedules:
        return []

    runs = {}
    enqueued = []
    for sched in schedules:
        cmd = sched.get("command")
        if not cmd or cmd not in cmo_runtime.ALLOWED_JOBS:
            continue
        if commands is not None and cmd not in commands:
            continue
        if not is_due(sched, now_utc):
            continue
        if db.has_active_job(cmd):
            continue

        job = jobs.enqueue(cmd, actor="scheduler")
        runs[sched.get("id")] = now_utc.isoformat()
        enqueued.append(job)

    _record_runs(runs)
    return enqueued


def _startup_delay_s():
    """Seconds to wait before the first tick, so app start stays fast and quiet.
    Override with MAGNM_SCHEDULER_DELAY_S (0 = check immediately)."""
    try:
        return max(0.0, float(os.environ.get("MAGNM_SCHEDULER_DELAY_S", "120")))
    except ValueError:
        return 120.0


def is_disabled():
    """Return True when MAGNM_DISABLE_SCHEDULER disables the background scheduler loop."""
    return os.environ.get("MAGNM_DISABLE_SCHEDULER", "").strip().lower() in ("1", "true", "yes")


def check_missed_weekly_summary(now=None):
    """Run missed Monday 09:00 IST weekly_summary once at startup if overdue."""
    if is_disabled():
        return []
    now_utc = _as_utc(now)
    schedules = db.load_schedules()
    if not schedules:
        return []
    runs = {}
    enqueued = []
    for sched in schedules:
        if sched.get("command") != "weekly_summary":
            continue
        if not is_due(sched, now_utc):
            continue
        if db.has_active_job("weekly_summary"):
            continue
        job = jobs.enqueue("weekly_summary", actor="scheduler")
        runs[sched.get("id")] = now_utc.isoformat()
        enqueued.append(job)
    _record_runs(runs)
    return enqueued


def _loop(interval_s, commands=None):
    try:
        reminders.check_due_reminders()
    except Exception:
        pass
    try:
        check_missed_weekly_summary()
    except Exception:
        pass
    remaining = _startup_delay_s()
    while remaining > 0 and not _stop_event.is_set():
        step = min(interval_s, remaining)
        if _stop_event.wait(step):
            return
        remaining -= step
        if remaining > 0:
            try:
                reminders.check_due_reminders()
            except Exception:
                pass
    while not _stop_event.is_set():
        try:
            tick(commands=commands)
        except Exception:
            pass
        if _stop_event.wait(interval_s):
            break


def start(interval_s=CHECK_INTERVAL_S, seed_defaults=True, commands=None):
    """Idempotently start the scheduler thread: reminders, plus every schedule or only `commands`."""
    global _thread
    if is_disabled():
        return None
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        if seed_defaults:
            try:
                ensure_default_schedules(commands=commands)
            except Exception:
                pass
        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(max(1.0, float(interval_s)), commands),
            name="magnm-scheduler-loop",
            daemon=True,
        )
        _thread.start()
        return _thread


def stop(timeout=2.0):
    """Signal the scheduler daemon thread to stop and wait up to timeout seconds."""
    global _thread
    with _lock:
        t = _thread
        _stop_event.set()
    if t is not None and t.is_alive():
        t.join(timeout=timeout)
    with _lock:
        _thread = None
