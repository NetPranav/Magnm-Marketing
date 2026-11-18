#!/usr/bin/env python3
"""Magnm Marketing Scheduler
Manages background recurring tasks (Daily CMO, Market Scan, Repo Sync).
Supports both systemd user timers generator and in-process timer loop.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULES_FILE = os.path.join(ROOT, "state", "schedules.json")
SYSTEMD_DIR = os.path.join(ROOT, "systemd")
PYTHON_BIN = sys.executable

def load_schedules():
    if not os.path.exists(SCHEDULES_FILE):
        return []
    with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("schedules", [])

def save_schedules(schedules):
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump({"schedules": schedules}, f, indent=2)

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
WorkingDirectory={ROOT}
ExecStart={PYTHON_BIN} {ROOT}/engine/cmo_runtime.py run-job {cmd}
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
