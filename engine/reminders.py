#!/usr/bin/env python3
"""Reminders parsing, lifecycle, and due notification firing."""

from datetime import datetime, timedelta, timezone
import os
import re
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
from ist import IST
import notifications

VALID_KINDS = ("custom", "results_due", "post_due")
OVERDUE_FLOOD_LIMIT = 5

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_RELATIVE_RE = re.compile(
    r"^in\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)$",
    re.IGNORECASE,
)
_TIME_12H_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.IGNORECASE)
_TIME_24H_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(?:at\s+)?(.+))?$", re.IGNORECASE)
_DAY_WORD_RE = re.compile(
    r"^(?:(this|next|on)\s+)?(today|tomorrow|tonight|yesterday|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)(?:\s+(?:at\s+)?(.+))?$",
    re.IGNORECASE,
)


class ReminderError(Exception):
    """Typed error for reminder validation and lookup failures."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)


def _as_utc(dt=None):
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_utc(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_time_str(raw_time):
    s = " ".join(str(raw_time or "").strip().lower().split())
    if not s:
        return None
    if s == "noon":
        return 12, 0
    if s == "midnight":
        return 0, 0
    m12 = _TIME_12H_RE.match(s)
    if m12:
        hour = int(m12.group(1))
        minute = int(m12.group(2) or 0)
        ampm = m12.group(3).lower()
        if hour < 1 or hour > 12 or minute > 59:
            raise ReminderError(f"Invalid time: '{raw_time}'")
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return hour, minute
    m24 = _TIME_24H_RE.match(s)
    if m24:
        hour = int(m24.group(1))
        minute = int(m24.group(2))
        if hour > 23 or minute > 59:
            raise ReminderError(f"Invalid time: '{raw_time}'")
        return hour, minute
    raise ReminderError(f"Could not parse time '{raw_time}'")


def parse_due_at(raw, now=None, require_future=True):
    """Parse an ISO timestamp or natural IST phrase into a UTC datetime."""
    text = " ".join(str(raw or "").strip().split())
    if not text:
        raise ReminderError("due_at is required")

    now_utc = _as_utc(now)
    now_ist = now_utc.astimezone(IST)
    parsed_utc = None

    # 1. Relative phrases: "in 2 hours", "in 30 minutes", "in 3 days"
    rel = _RELATIVE_RE.match(text)
    if rel:
        qty = float(rel.group(1))
        unit = rel.group(2).lower()
        if unit.startswith("m"):
            delta = timedelta(minutes=qty)
        elif unit.startswith("h"):
            delta = timedelta(hours=qty)
        elif unit.startswith("d"):
            delta = timedelta(days=qty)
        else:
            delta = timedelta(weeks=qty)
        parsed_utc = now_utc + delta

    # 2. ISO 8601 (naive treated as IST per team timezone)
    if parsed_utc is None:
        iso_cand = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(iso_cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            parsed_utc = dt.astimezone(timezone.utc)
        except ValueError:
            parsed_utc = None

    # 3. Date + casual time: "2026-11-25 10am"
    if parsed_utc is None:
        dm = _DATE_PREFIX_RE.match(text)
        if dm:
            date_part = datetime.strptime(dm.group(1), "%Y-%m-%d").date()
            hm = _parse_time_str(dm.group(2)) if dm.group(2) else (9, 0)
            dt_ist = datetime(date_part.year, date_part.month, date_part.day, hm[0], hm[1], tzinfo=IST)
            parsed_utc = dt_ist.astimezone(timezone.utc)

    # 4. Day / weekday phrases: "tomorrow 10am", "Friday 10am", "next Monday 9:30am"
    if parsed_utc is None:
        wm = _DAY_WORD_RE.match(text)
        if wm:
            modifier = (wm.group(1) or "").lower()
            day_word = wm.group(2).lower()
            time_part = wm.group(3)

            if day_word == "tonight":
                if time_part:
                    hm = _parse_time_str(time_part)
                else:
                    hm = (20, 0)
                target_date = now_ist.date()
                dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                parsed_utc = dt_ist.astimezone(timezone.utc)
            elif day_word == "today":
                hm = _parse_time_str(time_part) if time_part else (9, 0)
                target_date = now_ist.date()
                dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                parsed_utc = dt_ist.astimezone(timezone.utc)
            elif day_word == "tomorrow":
                hm = _parse_time_str(time_part) if time_part else (9, 0)
                target_date = now_ist.date() + timedelta(days=1)
                dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                parsed_utc = dt_ist.astimezone(timezone.utc)
            elif day_word == "yesterday":
                hm = _parse_time_str(time_part) if time_part else (9, 0)
                target_date = now_ist.date() - timedelta(days=1)
                dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                parsed_utc = dt_ist.astimezone(timezone.utc)
            elif day_word in _WEEKDAYS:
                hm = _parse_time_str(time_part) if time_part else (9, 0)
                target_wd = _WEEKDAYS[day_word]
                days_ahead = (target_wd - now_ist.weekday()) % 7
                if modifier == "next" and days_ahead == 0:
                    days_ahead = 7
                target_date = now_ist.date() + timedelta(days=days_ahead)
                dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                if days_ahead == 0 and dt_ist <= now_ist:
                    target_date = target_date + timedelta(days=7)
                    dt_ist = datetime(target_date.year, target_date.month, target_date.day, hm[0], hm[1], tzinfo=IST)
                parsed_utc = dt_ist.astimezone(timezone.utc)

    if parsed_utc is None:
        raise ReminderError(f"Could not understand due_at '{raw}'")

    if require_future and parsed_utc <= now_utc:
        raise ReminderError("due_at must be in the future")

    return parsed_utc


def create_reminder(
    title,
    due_at,
    *,
    note="",
    kind="custom",
    content_id=None,
    created_by="user",
    now=None,
):
    """Validate and persist a new reminder (converting IST/phrases to UTC)."""
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ReminderError("title is required")
    clean_kind = str(kind or "custom").strip().lower()
    if clean_kind not in VALID_KINDS:
        raise ReminderError(f"kind must be one of: {', '.join(VALID_KINDS)}")
    due_dt = parse_due_at(due_at, now=now, require_future=True)
    return db.create_reminder(
        clean_title,
        due_dt.isoformat(),
        note=str(note or "").strip(),
        kind=clean_kind,
        content_id=content_id,
        created_by=str(created_by or "user").strip() or "user",
    )


def list_reminders(status="open", limit=200, now=None):
    """List reminders filtered by open|done|all; open reminders sort overdue first."""
    norm = str(status or "open").strip().lower()
    if norm not in ("open", "done", "all"):
        raise ReminderError("status must be 'open', 'done', or 'all'")
    rows = db.list_reminders(status=norm, limit=limit)
    if norm != "open":
        return rows

    now_dt = _as_utc(now)

    def _sort_key(rem):
        due_dt = _parse_utc(rem.get("due_at")) or now_dt
        snooze_dt = _parse_utc(rem.get("snoozed_until")) if rem.get("snoozed_until") else None
        is_snoozed = snooze_dt is not None and snooze_dt > now_dt
        is_overdue = (not is_snoozed) and due_dt <= now_dt
        effective = snooze_dt if is_snoozed else due_dt
        return (0 if is_overdue else 1, effective.timestamp())

    return sorted(rows, key=_sort_key)


def mark_done(reminder_id):
    """Mark reminder_id as done or raise ReminderError(404)."""
    updated = db.mark_reminder_done(reminder_id)
    if updated is None:
        raise ReminderError("Reminder not found", status=404)
    return updated


def snooze_reminder(reminder_id, *, minutes=None, until=None, now=None):
    """Snooze a reminder by minutes or until a future timestamp/phrase."""
    existing = db.get_reminder(reminder_id)
    if existing is None:
        raise ReminderError("Reminder not found", status=404)

    now_utc = _as_utc(now)
    if minutes is not None:
        try:
            mins = float(minutes)
        except (TypeError, ValueError):
            raise ReminderError("minutes must be a positive number")
        if mins <= 0 or mins > 525600:
            raise ReminderError("minutes must be between 0 and 525600")
        until_dt = now_utc + timedelta(minutes=mins)
    elif until is not None and str(until).strip():
        until_dt = parse_due_at(until, now=now_utc, require_future=True)
    else:
        raise ReminderError("minutes or until is required")

    return db.snooze_reminder(reminder_id, until_dt.isoformat())


def delete_reminder(reminder_id):
    """Delete reminder_id or raise ReminderError(404)."""
    if not db.delete_reminder(reminder_id):
        raise ReminderError("Reminder not found", status=404)
    return {"success": True, "id": str(reminder_id)}


def check_due_reminders(now=None):
    """Fire notifications for due, open, unsnoozed, unfired reminders (with >5 flood guard)."""
    now_dt = _as_utc(now)
    now_iso = now_dt.isoformat()
    candidates = db.list_unfired_open_reminders()
    due = []
    for rem in candidates:
        due_dt = _parse_utc(rem.get("due_at"))
        if due_dt is None or due_dt > now_dt:
            continue
        snooze_dt = _parse_utc(rem.get("snoozed_until")) if rem.get("snoozed_until") else None
        if snooze_dt is not None and snooze_dt > now_dt:
            continue
        due.append(rem)

    if not due:
        return []

    db.mark_reminders_fired([r["id"] for r in due], fired_at=now_iso)

    if len(due) > OVERDUE_FLOOD_LIMIT:
        notifications.notify(
            "reminder",
            f"You have {len(due)} overdue reminders",
            body="",
            route="#/today",
        )
    else:
        for rem in due:
            cid = rem.get("content_id")
            route = f"#/review/{urllib.parse.quote(str(cid), safe='')}" if cid else "#/today"
            notifications.notify(
                "reminder",
                rem["title"],
                body=rem.get("note") or "",
                route=route,
            )
    return due
