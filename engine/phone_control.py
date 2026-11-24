#!/usr/bin/env python3
"""Pause and resume from the phone, and the board and automation state the phone reads.

Two phone action kinds:
  run_control         target = a workflow run id; payload {"action": "pause" | "resume"}
  automation_control  target = "all";             payload {"action": "pause" | "resume"} (the "Pause automatic work" switch)

Everything from the cloud is hostile: the payload is rebuilt from checked fields, a confidential run can only be
changed on the laptop, and every action applies exactly once (the queue's receipts). Pausing or resuming starts
nothing, approves nothing and posts nothing. Cancel is not offered to a phone.

mirror_records() builds what the phone shows: the task board (a confidential item is a stub with no text) and one
"automation" record with the switch, what was missed, and the pipeline's schedule, settings and last run.
"""

import os
import re
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import automation
import board
import confidential_guard
import db
import phone_library
import phone_pitch
import workflows

KINDS = ("run_control", "automation_control")
BOARD_MIRROR_KIND = "board_item"
AUTOMATION_MIRROR_KIND = "automation"
AUTOMATION_ID = "state"
AUTOMATION_TARGET = "all"
MAX_BOARD_ITEMS = board.MAX_BOARD_ITEMS
RUN_ID_RE = re.compile(r"wf_[a-z_]{1,32}_[0-9]{1,16}_[0-9a-f]{6}")
ACTIONS = ("pause", "resume")

CONFIDENTIAL_REASON = "Confidential items can only be changed on the laptop."
NOT_FOUND_REASON = "Not applied: it was not found on this laptop."
WAITING_REASON = "Not applied: that run is waiting for your answer or has ended, so it cannot be paused."


def _reject(reason):
    return phone_pitch.Reject(reason)


def validate_payload(kind, payload, target=None):
    """Return the checked parameters, or raise Reject. Never trusts the shape it is given."""
    if not isinstance(payload, dict) or set(payload) != {"action"} or payload["action"] not in ACTIONS:
        raise _reject("Not applied: the request was malformed.")
    if kind == "run_control":
        if target is not None and not (isinstance(target, str) and RUN_ID_RE.fullmatch(target)):
            raise _reject("Not applied: the run was malformed.")
    elif kind == "automation_control":
        if target is not None and target != AUTOMATION_TARGET:
            raise _reject("Not applied: the target was malformed.")
    else:
        raise _reject("Not applied: this laptop does not support that kind of action.")
    return {"action": payload["action"]}


def _run_is_confidential(run, guard):
    repo, repo_confidential = workflows._run_repo(run)
    if repo_confidential:
        return True
    texts = [run.get("kind"), run.get("inputs") or {}]
    if repo:
        texts += [repo.get("id"), repo.get("name")]
    return phone_library.names_confidential(guard, *[t if isinstance(t, str) else str(t) for t in texts]) or confidential_guard.scope().text(
        run.get("inputs") or {}
    )


def _run_control(target, params, guard):
    run = db.get_workflow_run(target)
    if run is None:
        raise _reject(NOT_FOUND_REASON)
    if _run_is_confidential(run, guard):
        raise _reject(CONFIDENTIAL_REASON)
    status = run["status"]
    if params["action"] == "pause":
        if status == "paused":
            return "applied", "Already paused."
        try:
            workflows.pause(target, by="phone")
        except ValueError:
            raise _reject(WAITING_REASON)
        return "applied", "Paused. It stops after the step that is running."
    if status != "paused":
        if status in ("done", "failed", "cancelled"):
            raise _reject("Not applied: that run has ended.")
        return "applied", "Already running."
    try:
        workflows.resume(target)
    except ValueError:
        raise _reject("Not applied: that run has ended.")
    return "applied", "Resumed."


def _automation_control(params):
    paused = params["action"] == "pause"
    if automation.is_paused() == paused:
        return "applied", "Already paused." if paused else "Already running."
    automation.set_paused(paused, by="phone")
    return "applied", "Automatic work paused." if paused else "Automatic work resumed. Missed runs are listed, not run."


def apply(kind, user_id, action_id, target, payload, guard):
    """Apply one action. Returns (outcome, result); raises Reject to refuse it. Safe to repeat."""
    params = validate_payload(kind, payload, target)
    if kind == "run_control":
        return _run_control(target, params, guard)
    return _automation_control(params)


def _automation_record(make_record, fallback_ts):
    state = automation.pause_state()
    summary = automation.summary_for_phone()
    data = {
        "paused": state["paused"],
        "paused_since": state["since"],
        "paused_by": state["by"],
        "missed": [{"id": m["id"], "name": m["name"], "due_at": m["due_at"]} for m in automation.missed_runs()],
        "pipeline": summary,
    }
    return make_record(AUTOMATION_MIRROR_KIND, AUTOMATION_ID, None, "Automatic work", data, state["since"] or fallback_ts)


def mirror_records(make_record, fallback_ts, guard):
    """The board and the automation state for the phone. Raises on a broken install; the caller keeps what is in the cloud."""
    records = board.mirror_records(make_record, fallback_ts, guard)
    # A fresh install with nothing set up has no automation record; it appears once there is something to show.
    if automation.has_state():
        records.append(_automation_record(make_record, fallback_ts))
    return records
