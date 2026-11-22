#!/usr/bin/env python3
"""Magnm Marketing Scheduler
Manages background recurring tasks (Daily CMO, Market Scan, Repo Sync).
Supports both systemd user timers generator and in-process timer loop.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths

ROOT = paths.DATA_ROOT
SCHEDULES_FILE = paths.data("state", "schedules.json")
SYSTEMD_DIR = paths.resource("systemd")
PYTHON_BIN = sys.executable

def load_schedules():
    """Load configured schedules from SQLite in preserved order."""
    return db.load_schedules()

def save_schedules(schedules):
    """Save configured schedules to SQLite, preserving order."""
    db.save_schedules(schedules)

MAX_INTERVAL_HOURS = 24 * 30


def _whole_hours(value):
    """Return value as a whole number of hours, refusing booleans, fractions and out-of-range numbers."""
    if isinstance(value, bool):
        raise ValueError("Interval must be a whole number of hours.")
    if isinstance(value, float):
        hours = int(value) if value.is_integer() else None
    else:
        try:
            hours = int(str(value).strip())
        except ValueError:
            hours = None
    if hours is None:
        raise ValueError("Interval must be a whole number of hours.")
    if not 1 <= hours <= MAX_INTERVAL_HOURS:
        raise ValueError(f"Interval must be between 1 and {MAX_INTERVAL_HOURS} hours.")
    return hours


def update_schedule(sched_id, enabled=None, interval_hours=None, time_of_day=None, day=None):
    """Change one schedule: on/off, interval, or IST time and weekday ('*' or 0-6 with Sunday 0).

    All values are checked before anything is saved, so a refused edit changes nothing.
    """
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false.")

    def change(schedules):
        current = next((s for s in schedules if s.get("id") == sched_id), None)
        if current is None:
            raise LookupError("Schedule not found")
        sched = dict(current)
        if interval_hours is not None:
            if str(sched.get("cron") or "").strip():
                raise ValueError("This schedule runs at a time, not on an interval.")
            sched["interval_hours"] = _whole_hours(interval_hours)
        if time_of_day is not None or day is not None:
            parts = str(sched.get("cron") or "").split()
            if len(parts) != 5:
                raise ValueError("This schedule runs on an interval, not at a time.")
            if time_of_day is not None:
                match = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", str(time_of_day))
                if not match:
                    raise ValueError("Time must look like 08:30.")
                parts[:2] = [str(int(match.group(2))), str(int(match.group(1)))]
            if day is not None:
                weekly = sched.get("command") == "weekly_summary"
                allowed = [str(i) for i in range(7)] + ([] if weekly else ["*"])
                if str(day) not in allowed:
                    raise ValueError("Choose a weekday." if weekly else "Choose every day or a weekday.")
                parts[4] = str(day)
            sched["cron"] = " ".join(parts)
        if enabled is not None:
            sched["enabled"] = enabled
        current.update(sched)
        return dict(current)

    return db.modify_schedules(change)


def toggle_schedule(sched_id):
    """Flip one schedule on or off and return it."""

    def flip(schedules):
        sched = next((s for s in schedules if s.get("id") == sched_id), None)
        if sched is None:
            raise LookupError("Schedule not found")
        sched["enabled"] = not sched.get("enabled", True)
        return dict(sched)

    return db.modify_schedules(flip)


def generate_systemd_units():
    """Generate systemd user service and timer files."""
    os.makedirs(SYSTEMD_DIR, exist_ok=True)
    schedules = load_schedules()
    generated = []

    for s in schedules:
        job_id = s["id"]
        cmd = s["command"]

        # Service unit
        service_content = f"""[Unit]
Description=Magnm Marketing Job - {s['name']}
After=network.target

[Service]
Type=oneshot
WorkingDirectory={paths.DATA_ROOT}
ExecStart={PYTHON_BIN} {paths.resource("engine", "cmo_runtime.py")} run-job {cmd}
StandardOutput=journal
StandardError=journal
"""
        service_path = os.path.join(SYSTEMD_DIR, f"magnm-{job_id}.service")
        with open(service_path, "w") as f:
            f.write(service_content)

        # Timer unit
        timer_spec = "OnCalendar=*-*-* 08:46:00" if s.get("cron") else f"OnUnitActiveSec={s.get('interval_hours', 6)}h"
        timer_content = f"""[Unit]
Description=Timer for Magnm Marketing Job - {s['name']}

[Timer]
{timer_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""
        timer_path = os.path.join(SYSTEMD_DIR, f"magnm-{job_id}.timer")
        with open(timer_path, "w") as f:
            f.write(timer_content)

        generated.append(f"magnm-{job_id}.timer")

    print(f"[OK] Generated systemd user timers in {SYSTEMD_DIR}:")
    for g in generated:
        print(f"  - {g}")
    print("\nTo activate in systemd user session:")
    print(f"  mkdir -p ~/.config/systemd/user")
    print(f"  cp {SYSTEMD_DIR}/* ~/.config/systemd/user/")
    print(f"  systemctl --user daemon-reload")
    for g in generated:
        print(f"  systemctl --user enable --now {g}")

def main():
    parser = argparse.ArgumentParser(description="Magnm Marketing Scheduler")
    parser.add_argument("--generate-systemd", action="store_true", help="Generate systemd user units")
    parser.add_argument("--list", action="store_true", help="List configured schedules")
    args = parser.parse_args()

    if args.generate_systemd:
        generate_systemd_units()
    else:
        schedules = load_schedules()
        print(f"Configured Schedules ({len(schedules)}):")
        for s in schedules:
            status = "ENABLED" if s.get("enabled") else "DISABLED"
            print(f"  [{status}] {s['name']} ({s['id']}) - Command: {s['command']} | Last: {s.get('last_run')}")

if __name__ == "__main__":
    main()
