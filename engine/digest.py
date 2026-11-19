#!/usr/bin/env python3
"""Pure-Python CMO daily digest builder (replaces scripts/cmo-digest.sh)."""

from datetime import datetime, timedelta, timezone
import glob
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths


def _ist_now():
    """Return current datetime in Asia/Kolkata, falling back to fixed +05:30 offset."""
    try:
        tz = ZoneInfo("Asia/Kolkata")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(tz)


def _read_head(path, max_lines):
    """Return up to max_lines from path (without trailing newlines), or None if missing."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
            return lines
    except OSError:
        return None


def _read_tail(path, max_lines):
    """Return up to the last max_lines from path, or None if missing."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\r\n") for ln in f]
        return lines[-max_lines:] if max_lines > 0 else []
    except OSError:
        return None


def _rel(path):
    """Return path relative to DATA_ROOT when possible, using forward slashes."""
    try:
        rel = os.path.relpath(path, paths.DATA_ROOT)
        return rel.replace("\\", "/")
    except ValueError:
        return path


def _extract_output_field(md_path):
    """Extract first 'output: ...' header value with trailing '#...' comment stripped."""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("output:"):
                    val = line[len("output:"):].strip()
                    if "#" in val:
                        val = val.split("#", 1)[0].strip()
                    return val
    except OSError:
        pass
    return ""


def build_digest() -> str:
    """Build the CMO daily state digest string using paths.data(...) and SQLite."""
    now_ist = _ist_now()
    out = [f"=== DIGEST {now_ist:%Y-%m-%d} (IST {now_ist:%H:%M}) ==="]

    out.append("--- board")
    board_lines = _read_head(paths.data("state", "board.md"), 25)
    if board_lines:
        out.extend(board_lines)
    else:
        out.append("(none)")

    out.append("--- plan")
    plan_lines = _read_head(paths.data("plan", "plan.md"), 30)
    if plan_lines:
        out.extend(plan_lines)
    else:
        out.append("(none)")

    out.append("--- last log")
    log_files = [p for p in glob.glob(paths.data("logs", "*.md")) if os.path.isfile(p)]
    last_log = max(log_files, key=os.path.getmtime) if log_files else None
    last_mtime = os.path.getmtime(last_log) if last_log else None
    if last_log:
        out.append(_rel(last_log))
        log_lines = _read_head(last_log, 40)
        if log_lines:
            out.extend(log_lines)
    else:
        out.append("(none)")

    out.append("--- agy done since last log")
    done_files = sorted(p for p in glob.glob(paths.data("queue", "agy", "done", "*.md")) if os.path.isfile(p))
    done_entries = []
    for p in done_files:
        if last_mtime is None or os.path.getmtime(p) > last_mtime:
            done_entries.append(f"{_rel(p)} -> {_extract_output_field(p)}")
    if done_entries:
        out.extend(done_entries)
    else:
        out.append("(none)")

    out.append("--- agy failed")
    failed_files = sorted(p for p in glob.glob(paths.data("queue", "agy", "failed", "*.md")) if os.path.isfile(p))
    out.extend([_rel(p) for p in failed_files] if failed_files else ["(none)"])

    out.append("--- agy inbox (pending)")
    inbox_files = sorted(p for p in glob.glob(paths.data("queue", "agy", "inbox", "*.md")) if os.path.isfile(p))
    out.extend([_rel(p) for p in inbox_files] if inbox_files else ["(none)"])

    out.append("--- runner")
    runner_lines = _read_tail(paths.data("queue", "agy", "runner.log"), 3)
    out.extend(runner_lines if runner_lines else ["(runner never ran - agy not installed?)"])

    out.append("--- awaiting yes")
    awaiting = []
    for p in sorted(glob.glob(paths.data("outputs", "posts", "*", "READY.md"))):
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    if "AWAITING YES" in f.read():
                        awaiting.append(_rel(p))
            except OSError:
                pass
    out.extend(awaiting if awaiting else ["(none)"])

    out.append("--- palette log")
    palette_lines = _read_tail(paths.data("state", "palette-log.md"), 3)
    if palette_lines:
        out.extend(palette_lines)
    else:
        out.append("(none)")

    out.append("--- activity (last 5)")
    activity_lines = db.get_activity_lines(limit=5)
    out.extend(activity_lines if activity_lines else ["(none)"])

    return "\n".join(out)
