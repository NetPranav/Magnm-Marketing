#!/usr/bin/env python3
"""Plan and Create on the phone without the laptop.

Two halves:

* The cloud mirror gets the schedules, the editorial calendar and the weekly plan's proposed slots (with the brief),
  so the phone can show Plan without the laptop. Text naming a confidential product is hidden.
* apply() carries out what the phone queued: schedule_update (through scheduler.update_schedule, so the same rules
  as the web app), draft_create (a text-only result Phone Lite wrote, saved as an ordinary draft) and
  workflow_request (a request that needs laptop tools: research, images, video, blog grounding, weekly plan).

Everything from the cloud is hostile and phone-made text is data. A workflow request only CREATES a run through
workflows.start, exactly like the Create screen on the laptop: the run stops at its normal approval gates, nothing is
approved, drafted or posted by the request itself. A confidential repo, a confidential draft to repurpose, or text
naming a confidential product refuses the request. Every action applies exactly once, even across a crash.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import confidential_guard
import db
import paths
import phone_library
import phone_pitch
import phone_server
import repos
import review
import scheduler
import workflows
from tools import content as content_tools

KINDS = ("schedule_update", "draft_create", "workflow_request")
SCHEDULE_MIRROR_KIND = "schedule"
CALENDAR_MIRROR_KIND = "editorial_plan"
CALENDAR_ID = "plan"
MAX_CALENDAR_ROWS = 200
MAX_CALENDAR_BYTES = 256 * 1024
MAX_SLOTS = 30
MAX_SLOT_TEXT = 300
MAX_PLAN_RUNS = 3
DRAFT_TITLE_CHARS = 200
DRAFT_BODY_CHARS = 6000
BRIEF_CHARS = 2000
TOPIC_CHARS = 1000
DRAFT_MARKER = "_phone_draft_made"
WORKFLOW_MARKER = "_phone_workflow_started"
ACTOR = "phone:cloud"
WORKFLOW_KINDS = ("post", "blog", "video_script", "repurpose", "weekly_plan")
PLATFORMS = ("x", "linkedin", "instagram", "youtube")
DRAFT_MODES = ("post", "thread", "rewrite")

CONFIDENTIAL_TEXT_REASON = "Not applied: the text names a confidential product."
CONFIDENTIAL_REASON = "Confidential items can only be changed on the laptop."
CONFIDENTIAL_REPO_REASON = "Not applied: confidential repositories can only be used on the laptop."
NOT_FOUND_REASON = "Not applied: it was not found on this laptop."

_BAD_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ONE_LINE_BAD_RE = re.compile(r"[\x00-\x1f\x7f]")
_SCHEDULE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_REPO_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_TARGET_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_ACTION_TAG = "phone-action:"


def _reject(reason):
    return phone_pitch.Reject(reason)


# --- Checking what the phone queued -------------------------------------------------------------------------------

def _text(value, what, limit, multiline=False):
    bad = _BAD_CHARS_RE if multiline else _ONE_LINE_BAD_RE
    if not isinstance(value, str) or bad.search(value) or not value.strip() or len(value.strip()) > limit:
        raise _reject(f"Not applied: the {what} was empty, too long or malformed.")
    return value.strip()


def _platforms(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 4 or any(not isinstance(p, str) or p not in PLATFORMS for p in value):
        raise _reject("Not applied: the platforms were malformed.")
    return list(dict.fromkeys(value))


def _bool(value, what):
    if not isinstance(value, bool):
        raise _reject(f"Not applied: {what} was malformed.")
    return value


def _int_in(value, low, high, what):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _reject(f"Not applied: {what} was malformed.")
    return value


def _validate_schedule(payload):
    allowed = {"enabled", "interval_hours", "time", "day"}
    if not isinstance(payload, dict) or not payload or not set(payload) <= allowed:
        raise _reject("Not applied: the schedule change was malformed.")
    out = {}
    if "enabled" in payload:
        out["enabled"] = _bool(payload["enabled"], "the on/off setting")
    if "interval_hours" in payload:
        out["interval_hours"] = _int_in(payload["interval_hours"], 1, 720, "the interval")
    if "time" in payload:
        if not isinstance(payload["time"], str) or not _TIME_RE.fullmatch(payload["time"]):
            raise _reject("Not applied: the time was malformed.")
        out["time"] = payload["time"]
    if "day" in payload:
        if not isinstance(payload["day"], str) or not re.fullmatch(r"[*0-6]", payload["day"]):
            raise _reject("Not applied: the weekday was malformed.")
        out["day"] = payload["day"]
    return out


def _validate_draft(payload):
    allowed = {"platform", "title", "body", "mode"}
    if not isinstance(payload, dict) or not {"platform", "title", "body"} <= set(payload) or not set(payload) <= allowed:
        raise _reject("Not applied: the draft was malformed.")
    platform = payload["platform"]
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise _reject("Not applied: the platform was not recognised.")
    mode = payload.get("mode", "post")
    if mode not in DRAFT_MODES:
        raise _reject("Not applied: the draft kind was not recognised.")
    return {
        "platform": platform,
        "title": _text(payload["title"], "title", DRAFT_TITLE_CHARS),
        "body": _text(payload["body"], "text", DRAFT_BODY_CHARS, multiline=True),
        "mode": mode,
    }


def _validate_workflow(kind, payload):
    keys = {
        "post": {"brief", "platforms", "with_image", "repo_id"},
        "blog": {"topic", "repo_id"},
        "video_script": {"topic", "duration_s", "aspect", "repo_id"},
        "repurpose": {"content_id", "targets"},
        "weekly_plan": {"platforms", "posts_per_week", "week_start", "repo_id"},
    }.get(kind)
    if keys is None:
        raise _reject("Not applied: this laptop does not start that kind of work from the phone.")
    if not isinstance(payload, dict) or not set(payload) <= keys:
        raise _reject("Not applied: the request had details a phone may not send.")
    out = {}
    if kind == "post":
        out["brief"] = _text(payload.get("brief"), "brief", BRIEF_CHARS, multiline=True)
    elif kind in ("blog", "video_script"):
        out["topic"] = _text(payload.get("topic"), "topic", TOPIC_CHARS, multiline=True)
    elif kind == "repurpose":
        cid = payload.get("content_id")
        if not isinstance(cid, str) or not 1 <= len(cid) <= 200 or _ONE_LINE_BAD_RE.search(cid):
            raise _reject("Not applied: the draft to repurpose was malformed.")
        out["content_id"] = cid
    if "platforms" in payload:
        out["platforms"] = _platforms(payload["platforms"])
    if "with_image" in payload:
        out["with_image"] = _bool(payload["with_image"], "the image setting")
    if "repo_id" in payload:
        repo = payload["repo_id"]
        if not isinstance(repo, str) or not _REPO_ID_RE.fullmatch(repo):
            raise _reject("Not applied: the repository was malformed.")
        out["repo_id"] = repo
    if "duration_s" in payload:
        if payload["duration_s"] not in (15, 30, 60) or isinstance(payload["duration_s"], bool):
            raise _reject("Not applied: the video length was malformed.")
        out["duration_s"] = payload["duration_s"]
    if "aspect" in payload:
        if payload["aspect"] not in ("9:16", "16:9"):
            raise _reject("Not applied: the video shape was malformed.")
        out["aspect"] = payload["aspect"]
    if "targets" in payload:
        targets = payload["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 6 or any(not isinstance(t, str) or not _TARGET_RE.fullmatch(t) for t in targets):
            raise _reject("Not applied: the repurpose targets were malformed.")
        out["targets"] = list(dict.fromkeys(targets))
    if "posts_per_week" in payload:
        out["posts_per_week"] = _int_in(payload["posts_per_week"], 1, 14, "the number of posts")
    if "week_start" in payload:
        raw = payload["week_start"]
        try:
            if not isinstance(raw, str) or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", raw):
                raise ValueError
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            raise _reject("Not applied: the week start was malformed.")
        out["week_start"] = raw
    return out


def validate_payload(kind, payload, target=None):
    """Return the checked parameters for a plan or create kind, or raise Reject. Never trusts the shape it is given."""
    if kind == "schedule_update":
        if target is not None and not (isinstance(target, str) and _SCHEDULE_ID_RE.fullmatch(target)):
            raise _reject("Not applied: the schedule id was malformed.")
        return _validate_schedule(payload)
    if kind == "draft_create":
        if target not in (None, "new"):
            raise _reject("Not applied: a new draft has no target.")
        return _validate_draft(payload)
    if kind == "workflow_request":
        if target is None:
            return payload  # the generic shape check happens again with the workflow kind
        return {"kind": target, "inputs": _validate_workflow(target, payload)}
    raise _reject("Not applied: this laptop does not support that kind of action.")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _names_confidential(guard, value):
    return phone_library.names_confidential(guard, *list(_strings(value)))


# --- Applying -----------------------------------------------------------------------------------------------------

def _schedule(target, params):
    if not any(s.get("id") == target for s in db.load_schedules()):
        raise _reject(NOT_FOUND_REASON)
    try:
        scheduler.update_schedule(
            target, enabled=params.get("enabled"), interval_hours=params.get("interval_hours"),
            time_of_day=params.get("time"), day=params.get("day"),
        )
    except LookupError:
        raise _reject(NOT_FOUND_REASON)
    except ValueError as exc:
        # The scheduler's own messages are fixed texts about the rule that was broken, never the value sent.
        raise _reject(f"Not applied: {str(exc)[:200]}")
    return "applied", "Schedule changed."


def _marker(user_id, kind, action_id):
    return db.get_cloud_sync_value(user_id, kind, action_id)


def _set_marker(user_id, kind, action_id, value):
    db.upsert_cloud_sync_state(user_id, [(kind, action_id, str(value), datetime.now(timezone.utc).isoformat(), None)])


def _find_draft(action_id):
    tag = _ACTION_TAG + action_id
    for item in db.list_all_content_items():
        if (item.get("created_by") or "") != ACTOR:
            continue
        full = db.get_content_item(item["id"], include_versions=True) or {}
        notes = [full.get("notes") or ""] + [v.get("notes") or "" for v in full.get("versions") or []]
        if any(tag in n for n in notes):
            return item["id"]
    return None


def _draft(user_id, action_id, params, guard):
    if _names_confidential(guard, [params["title"], params["body"]]):
        raise _reject(CONFIDENTIAL_TEXT_REASON)
    existing = _marker(user_id, DRAFT_MARKER, action_id)
    if existing and db.get_content_item(existing) is not None:
        return "applied", "Already saved as a draft."
    found = _find_draft(action_id) if existing == "starting" else None
    if found:
        _set_marker(user_id, DRAFT_MARKER, action_id, found)
        return "applied", "Already saved as a draft."
    _set_marker(user_id, DRAFT_MARKER, action_id, "starting")
    ctx = SimpleNamespace(agent_id=ACTOR, session_id=None)
    res = content_tools.write_draft(
        ctx, type="post", platform=params["platform"], title=params["title"], body=params["body"],
        notes=f"Written on the phone with Phone Lite ({params['mode']}) and saved from the queue. [{_ACTION_TAG}{action_id}]",
    )
    if not isinstance(res, dict) or res.get("error") or not res.get("content_id"):
        raise _reject("Not applied: the laptop could not save the draft.")
    cid = res["content_id"]
    _set_marker(user_id, DRAFT_MARKER, action_id, cid)
    try:
        review.auto_review(cid)
    except Exception:
        pass  # the draft is saved; its review can be run on the laptop
    count = len(res.get("warnings") or [])
    note = f" {count} product rule note{'s' if count != 1 else ''}: check it on the laptop." if count else ""
    return "applied", ("Draft saved on the laptop." + note)[:300]


def _run_for_action(action_id):
    for run in db.list_workflow_runs(limit=500):
        if (run.get("inputs") or {}).get("phone_action") == action_id:
            return run
    return None


def _workflow(user_id, action_id, kind, inputs, guard):
    if _names_confidential(guard, inputs):
        raise _reject(CONFIDENTIAL_TEXT_REASON)
    repo_id = inputs.get("repo_id")
    if repo_id:
        try:
            repo = repos.get_repo(repo_id)
        except Exception:
            raise _reject(CONFIDENTIAL_REPO_REASON)
        if repo is None:
            raise _reject("Not applied: that repository is not on this laptop.")
        if confidential_guard.is_confidential_repo(repo) or _names_confidential(guard, [repo.get("id"), repo.get("name")]):
            raise _reject(CONFIDENTIAL_REPO_REASON)
    if kind == "repurpose":
        item = db.get_content_item(inputs["content_id"], include_versions=True)
        if item is None:
            raise _reject(NOT_FOUND_REASON)
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = guard
        if phone_server._content_item_is_confidential(item, conf_sids, conf_cids, conf_pids, checker, repo_patterns) \
                or confidential_guard.scope().item(item):
            raise _reject(CONFIDENTIAL_REASON)

    marker = _marker(user_id, WORKFLOW_MARKER, action_id)
    if marker:
        if marker != "starting":
            return "applied", "Already started on the laptop."
        if _run_for_action(action_id) is not None:
            _set_marker(user_id, WORKFLOW_MARKER, action_id, "done")
            return "applied", "Already started on the laptop."
    else:
        _set_marker(user_id, WORKFLOW_MARKER, action_id, "starting")
    # phone_action names this action on the run, so a crash after the run exists can never start a second one.
    try:
        workflows.start(kind, dict(inputs, phone_action=action_id), actor=ACTOR)
    except ValueError:
        raise _reject("Not applied: the workflow rules refused the request.")
    _set_marker(user_id, WORKFLOW_MARKER, action_id, "done")
    return "applied", "Started on the laptop. It will ask for your approval as usual."


def apply(kind, user_id, action_id, target, payload, guard):
    """Apply one plan or create action. Returns (outcome, result); raises Reject to refuse it. Safe to repeat."""
    params = validate_payload(kind, payload, target)
    if kind == "schedule_update":
        return _schedule(target, params)
    if kind == "draft_create":
        return _draft(user_id, action_id, params, guard)
    return _workflow(user_id, action_id, params["kind"], params["inputs"], guard)


# --- The cloud mirror ---------------------------------------------------------------------------------------------

def _int(value, low, high):
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _clip(value, limit):
    return _ONE_LINE_BAD_RE.sub(" ", value)[:limit] if isinstance(value, str) else ""


def schedule_records(make_record, fallback_ts):
    """One mirror record per schedule: what it is called, when it runs, whether it is on. Never its command."""
    records = []
    for sched in db.load_schedules():
        sid = sched.get("id")
        if not isinstance(sid, str) or not _SCHEDULE_ID_RE.fullmatch(sid):
            continue
        cron = _clip(sched.get("cron"), 40) or None
        data = {
            "name": _clip(sched.get("name"), 120) or sid,
            "description": _clip(sched.get("description"), 300),
            "enabled": sched.get("enabled", True) is not False,
            "interval_hours": _int(sched.get("interval_hours"), 1, 720),
            "cron": cron,
            "weekly": sched.get("command") == "weekly_summary",
            "last_run": _clip(sched.get("last_run"), 40) or None,
            "last_status": _clip(sched.get("last_status"), 40) or None,
        }
        records.append(make_record(SCHEDULE_MIRROR_KIND, sid, None, data["name"], data, sched.get("last_run") or fallback_ts))
    return records


def _read_calendar_rows():
    """Rows of plan/plan.md (a markdown table), read safely: a regular file inside the data folder, size capped."""
    root = os.path.realpath(paths.DATA_ROOT)
    path = os.path.realpath(os.path.join(root, "plan", "plan.md"))
    try:
        if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
            return []
        with open(path, "rb") as f:
            text = f.read(MAX_CALENDAR_BYTES).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or s.startswith("|---") or s.lower().startswith("| slot"):
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) >= 4:
            rows.append({"slot": _clip(parts[0], 80), "platforms": _clip(parts[1], 80), "topic": _clip(parts[2], 300), "status": _clip(parts[3], 80)})
        if len(rows) >= MAX_CALENDAR_ROWS:
            break
    return rows


def calendar_records(make_record, fallback_ts, guard):
    """The editorial calendar as one record (none if there are no rows). A row naming a confidential product shows as a stub row."""
    rows = []
    for row in _read_calendar_rows():
        if guard is None or _names_confidential(guard, row):
            rows.append({"slot": row["slot"], "platforms": "", "topic": "Confidential item", "status": ""})
        else:
            rows.append(row)
    if not rows:
        return []  # no calendar: no record (and an emptied calendar is removed from the cloud copy like any deleted record)
    return [make_record(CALENDAR_MIRROR_KIND, CALENDAR_ID, None, "Editorial calendar", {"rows": rows}, fallback_ts)]


def plan_slots(run_id):
    """The proposed slots of a weekly plan run, cleaned and capped, or []. A slot naming a confidential product shows
    only its date and platform. Odd entries are dropped; if the rules cannot be read nothing is shown."""
    run = db.get_workflow_run(run_id)
    raw = ((run or {}).get("state") or {}).get("proposed_slots")
    if not isinstance(raw, list) or not raw:
        return []
    try:
        guard = phone_server._build_confidential_guard()
    except Exception:
        return []
    slots = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slot = {key: _clip(item.get(key), MAX_SLOT_TEXT) for key in ("date", "platform", "topic", "angle", "product_id", "reason")}
        if not (slot["date"] or slot["topic"]):
            continue
        if _names_confidential(guard, slot):
            slot = {"date": slot["date"], "platform": slot["platform"], "topic": "Confidential item", "angle": "", "product_id": "", "reason": ""}
        slots.append(slot)
        if len(slots) >= MAX_SLOTS:
            break
    return slots


def mirror_records(make_record, fallback_ts, guard):
    """Schedules and the calendar for the phone. Raises on a broken install; the caller keeps what is in the cloud."""
    return schedule_records(make_record, fallback_ts) + calendar_records(make_record, fallback_ts, guard)


def recent_plan_run_ids():
    """The ids of the latest weekly plan runs whose slots travel with their brief record."""
    return [r["id"] for r in db.list_workflow_runs(limit=100) if r.get("kind") == "weekly_plan"][:MAX_PLAN_RUNS]
