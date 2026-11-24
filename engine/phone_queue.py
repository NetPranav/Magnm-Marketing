#!/usr/bin/env python3
"""Apply the actions the signed-in user queued from their phone while this laptop was away.

The phone writes small actions (comment on a draft, mark it posted, finish a reminder) to the cloud table
phone_actions. On each sync cycle this engine pulls the user's own queue and applies what it can through the same
functions and checks as the live phone routes in phone_server.

Everything that comes back from the cloud is treated as hostile: each row is rebuilt from validated fields, only
rows owned by the signed-in user are used, unknown kinds are refused, and the text is checked against the one
definition of confidential (confidential_guard plus the phone guard) before anything is written. Phone-made text is
stored as data: it never becomes an agent instruction and never starts an @agent run.

A local receipt table makes every action apply exactly once, even across a crash or a repeated claim.

agent_task is the one kind that runs an agent: the phone asks an agent that needs a laptop tool (web research,
images, saving numbers, delegation, repo reading) and the laptop answers when it is back. The text is handled exactly
like a chat message typed by the owner (agent_chat.chat_with_agent: the same confidentiality decision, tools and
approvals), but only after the same checks as every other kind, one run at a time, at most 20 an hour, and never
twice. The run happens on a background thread so a long answer never blocks the sync cycle.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_chat
import agents
import cloud_sync
import confidential_guard
import db
import notifications
import phone_control
import phone_library
import phone_pitch
import phone_plan
import phone_server
import review_studio
import team_auth

MAX_PER_CYCLE = 50
MAX_PULL = 200
MAX_BODY_CHARS = 4000
MAX_URL_CHARS = 2000
MAX_TARGET_CHARS = 200
MAX_VERSION = 999_999_999
MAX_RESULT_CHARS = 300
MAX_ATTEMPTS = 3
# After this many failures of any kind an action is left alone, so it can never use up every cycle's budget.
MAX_TOUCHES = MAX_ATTEMPTS + 3
ATTEMPTS_KIND = "_phone_action_attempts"
FAILED_REASON = "Not applied: this laptop could not apply it."
KINDS = (
    ("draft_comment", "draft_posted", "reminder_done", "agent_task")
    + phone_pitch.KINDS + phone_library.KINDS + phone_plan.KINDS + phone_control.KINDS
)
COMMENT_AUTHOR = "phone:cloud"
AGENT_TASK = "agent_task"
MAX_AGENT_MESSAGE_CHARS = 4000
MAX_AGENT_TASKS_PER_HOUR = 20
AGENT_STARTED_KIND = "_phone_agent_started"
AGENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
RESTARTED_REASON = "Not applied: this laptop restarted before the agent finished. Send it again."
NO_AGENT_REASON = "Not applied: this laptop has no agent with that id."
AGENT_FAILED_REASON = "Not applied: the agent could not answer. Check AI access on the laptop and send it again."
LAPTOP_ONLY_RESULT = "Replied on the laptop only."
_Thread = threading.Thread

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_BODY_BAD_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_BAD_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_SELECT = "id,owner_id,kind,target_id,payload,status,applied_by_device,expires_at"

CONFIDENTIAL_REASON = "Confidential items can only be changed on the laptop."
CONFIDENTIAL_TEXT_REASON = "Not applied: the text names a confidential product."


class PhoneQueueError(Exception):
    """A failed cloud call or an unusable answer, with an HTTP-style status."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out or switched account while the queue was being worked."""


# One refusal type for every kind (the pitch kinds raise it from phone_pitch).
_Reject = phone_pitch.Reject


def _check(user_id, is_signed_out):
    """Stop at once if the user signed out or another account is signed in."""
    if is_signed_out is not None and is_signed_out():
        raise SignedOutAbort()
    sess = team_auth.get_session_info()
    if not sess or str(sess.get("user_id") or "") != user_id:
        raise SignedOutAbort()


def _rest(method, path, body=None):
    try:
        try:
            return team_auth.rest(method, path, body=body)
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
                try:
                    team_auth.refresh_access_token()
                except team_auth.TeamAuthError as refresh_exc:
                    # A 401 that cannot be refreshed means the session is gone: stop, never retry blindly.
                    raise SignedOutAbort() from refresh_exc
                return team_auth.rest(method, path, body=body)
            raise
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise SignedOutAbort() from exc
        raise PhoneQueueError(exc.message, status=exc.status if exc.status else 0, code=exc.code) from exc


def _table_missing(exc):
    """True when an older server has no phone_actions table yet."""
    text = exc.message.lower()
    return exc.status == 404 or (exc.status == 400 and "phone_actions" in text and "does not exist" in text)


def _parse_time(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def clean_row(raw, user_id):
    """Rebuild one cloud row from checked fields, or None if it is not a usable action of this user."""
    if not isinstance(raw, dict):
        return None
    action_id, owner = raw.get("id"), raw.get("owner_id")
    kind, target, status = raw.get("kind"), raw.get("target_id"), raw.get("status")
    if not (isinstance(action_id, str) and _UUID_RE.fullmatch(action_id.lower())):
        return None
    if not isinstance(owner, str) or owner != user_id:
        return None
    if status not in ("queued", "applying") or not isinstance(kind, str) or not kind or len(kind) > 64:
        return None
    if not isinstance(target, str) or not 1 <= len(target) <= MAX_TARGET_CHARS or _CONTROL_RE.search(target):
        return None
    device = raw.get("applied_by_device")
    if device is not None and not (isinstance(device, str) and 1 <= len(device) <= 128):
        return None
    expires = _parse_time(raw.get("expires_at"))
    if expires is None:
        return None
    return {
        "id": action_id.lower(),
        "kind": kind,
        "target_id": target,
        "payload": raw.get("payload"),
        "status": status,
        "device": device,
        "expires_at": expires,
    }


def validate_payload(kind, payload, target=None):
    """Return the checked parameters for a known kind, or raise _Reject. Never trusts the shape it is given."""
    if kind in phone_library.KINDS:
        return phone_library.validate_payload(payload)
    if kind in phone_plan.KINDS:
        return phone_plan.validate_payload(kind, payload, target)
    if kind in phone_control.KINDS:
        return phone_control.validate_payload(kind, payload, target)
    if kind == "draft_comment":
        if not isinstance(payload, dict) or not set(payload) <= {"body", "version"}:
            raise _Reject("Not applied: the comment was malformed.")
        body = payload.get("body")
        if not isinstance(body, str) or _BODY_BAD_RE.search(body) or not body.strip() or len(body.strip()) > MAX_BODY_CHARS:
            raise _Reject("Not applied: the comment text was empty, too long or malformed.")
        params = {"body": body.strip()}
        if "version" in payload:
            version = payload["version"]
            if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= MAX_VERSION:
                raise _Reject("Not applied: the draft version was malformed.")
            params["version"] = version
        return params
    if kind == "draft_posted":
        if not isinstance(payload, dict) or not set(payload) <= {"url"}:
            raise _Reject("Not applied: the posted details were malformed.")
        if "url" not in payload:
            return {}
        url = payload["url"]
        if (
            not isinstance(url, str) or not 1 <= len(url) <= MAX_URL_CHARS or _URL_BAD_RE.search(url)
            or not phone_server._is_valid_http_url(url)
        ):
            raise _Reject("Not applied: the link is not a valid http or https address.")
        return {"url": url}
    if kind == "reminder_done":
        if payload != {}:
            raise _Reject("Not applied: the reminder details were malformed.")
        return {}
    if kind == AGENT_TASK:
        if not isinstance(payload, dict) or set(payload) != {"message"}:
            raise _Reject("Not applied: the request was malformed.")
        message = payload["message"]
        if (
            not isinstance(message, str) or _BODY_BAD_RE.search(message) or not message.strip()
            or len(message.strip()) > MAX_AGENT_MESSAGE_CHARS
        ):
            raise _Reject("Not applied: the request text was empty, too long or malformed.")
        return {"message": message.strip()}
    if kind in phone_pitch.KINDS:
        return phone_pitch.validate_payload(kind, payload)
    raise _Reject("Not applied: this laptop does not support that kind of action.")


def target_exists(kind, target_id):
    """True if the draft or reminder an action is about exists in THIS laptop's data."""
    if kind in ("draft_comment", "draft_posted"):
        return db.get_content_item(target_id) is not None
    if kind == "reminder_done":
        return db.get_reminder(target_id) is not None
    if kind in phone_pitch.KINDS:
        return phone_pitch.target_exists(kind, target_id)
    if kind in phone_library.KINDS or kind in phone_plan.KINDS or kind in phone_control.KINDS:
        return True  # checked after the claim, so a bad or unknown target is rejected with a reason
    # An agent task is claimed and then checked, so a bad or unknown agent id is rejected with a reason.
    return True


def _names_confidential(guard, *texts):
    """True if any text names a confidential product or repo. Uses the phone guard and confidential_guard."""
    _, _, _, checker, repo_patterns = guard
    for text in texts:
        if text and (phone_server._text_is_confidential(text, checker, repo_patterns) or confidential_guard.mentions(text)):
            return True
    return False


def _apply(user_id, row, guard):
    """Apply one claimed action. Returns (outcome, result); outcome is 'applied' or 'rejected'. Repeatable."""
    kind, target = row["kind"], row["target_id"]
    try:
        if kind in phone_plan.KINDS:
            return phone_plan.apply(kind, user_id, row["id"], target, row["payload"], guard)
        if kind in phone_control.KINDS:
            return phone_control.apply(kind, user_id, row["id"], target, row["payload"], guard)
        params = validate_payload(kind, row["payload"])
        if kind in phone_library.KINDS:
            return phone_library.apply(user_id, row["id"], target, params, guard)
        if kind in phone_pitch.KINDS:
            return phone_pitch.apply(kind, user_id, row["id"], target, params, guard)
        if kind == "draft_comment":
            if _names_confidential(guard, params["body"]):
                raise _Reject(CONFIDENTIAL_TEXT_REASON)
            phone_server.apply_draft_comment(
                target, params, author=COMMENT_AUTHOR, guard=guard,
                comment_id="cmt_" + row["id"].replace("-", ""),
            )
            return "applied", "Comment added."
        if kind == "draft_posted":
            if _names_confidential(guard, params.get("url")):
                raise _Reject(CONFIDENTIAL_TEXT_REASON)
            item = db.get_content_item(target)
            if item is not None and item.get("status") == "posted" and params.get("url") in (None, item.get("posted_url")):
                phone_server._require_open_item(target, guard)
                return "applied", "Already marked as posted."
            phone_server.apply_draft_posted(target, params, guard=guard)
            return "applied", "Marked as posted."
        reminder = db.get_reminder(target)
        if reminder is None:
            raise _Reject("Not applied: the reminder was not found on this laptop.")
        _, conf_cids, _, checker, repo_patterns = guard
        scope = confidential_guard.scope()
        if phone_server._reminder_is_confidential(reminder, conf_cids, checker, repo_patterns) or scope.linked(
            reminder, "title", "note"
        ):
            raise _Reject(CONFIDENTIAL_REASON)
        phone_server.apply_reminder_done(target, guard=guard)
        return "applied", "Reminder done."
    except _Reject as exc:
        return "rejected", exc.reason
    except phone_library.SignedOutAbort as exc:
        raise SignedOutAbort() from exc
    except phone_library.PhoneLibraryError as exc:
        raise PhoneQueueError(exc.message, status=exc.status, code=exc.code) from exc
    except phone_server.PhoneApiError as exc:
        if exc.status == 403:
            return "rejected", CONFIDENTIAL_REASON
        if exc.status == 404:
            return "rejected", "Not applied: it was not found on this laptop."
        if exc.status == 503:
            raise
        return "rejected", "Not applied: the laptop could not accept it."
    except (review_studio.NotFoundError, review_studio.ValidationError):
        return "rejected", "Not applied: it was not found or not valid on this laptop."


_agent_lock = threading.Lock()
_agent_running = {}
_agent_threads = []


def _reset_agent_state():
    """Forget running tasks (tests and sign-out only)."""
    with _agent_lock:
        _agent_running.clear()
        del _agent_threads[:]


def wait_for_agent_tasks(timeout=30.0):
    """Wait for background agent runs to end. Returns True if none is still running."""
    with _agent_lock:
        threads = list(_agent_threads)
    for thread in threads:
        join = getattr(thread, "join", None)
        if join is not None:
            join(timeout)
    with _agent_lock:
        return not _agent_running


def _recent_agent_starts(user_id):
    """How many agent tasks this laptop started for the user in the last hour."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    count = 0
    for (kind, _local_id), info in db.get_cloud_sync_state(user_id).items():
        if kind != AGENT_STARTED_KIND:
            continue
        stamp = _parse_time(info.get("synced_at"))
        if stamp is not None and stamp >= cutoff:
            count += 1
    return count


def _agent_slot_free(user_id):
    """One run at a time, and a cap per hour, so a flooded queue cannot burn the AI quota."""
    with _agent_lock:
        if _agent_running:
            return False
    return _recent_agent_starts(user_id) < MAX_AGENT_TASKS_PER_HOUR


def _agent_outcome(res):
    """Turn what the chat function returned into (outcome, short result). Never carries reply text or error text."""
    if not isinstance(res, dict) or not res.get("success"):
        return "rejected", AGENT_FAILED_REASON
    session_id = str(res.get("session_id") or "")
    try:
        conf_sids, _, _, checker, repo_patterns = phone_server._build_confidential_guard()
    except phone_server.PhoneApiError:
        return "applied", LAPTOP_ONLY_RESULT  # rules unreadable: say nothing that points at the reply
    session = db.get_agent_session(session_id) if session_id else None
    extras = json.dumps([res.get("question"), res.get("options")], ensure_ascii=False, default=str)
    confidential = (
        session_id in conf_sids
        or bool(session and session.get("confidential"))
        or phone_server._chat_read_confidential(session, checker, repo_patterns)
        or _names_confidential((conf_sids, None, None, checker, repo_patterns), str(res.get("response") or ""), extras)
    )
    if confidential or not SESSION_ID_RE.fullmatch(session_id):
        return "applied", LAPTOP_ONLY_RESULT
    return "applied", ("Needs your answer. " if res.get("waiting_user") else "Replied. ") + f"session:{session_id}"


def _run_agent_task(user_id, row, agent, message, device_id):
    """Run one claimed agent task as an ordinary chat message and record the outcome. Runs on its own thread."""
    outcome, result = "rejected", AGENT_FAILED_REASON
    receipt = None
    try:
        try:
            outcome, result = _agent_outcome(agent_chat.chat_with_agent(agent["id"], message))
        except Exception:
            outcome, result = "rejected", AGENT_FAILED_REASON
        receipt = _record_receipt(user_id, dict(row, kind=AGENT_TASK), outcome, result)
    finally:
        # Only after the receipt is written, so no cycle can mistake a finished run for a crashed one.
        with _agent_lock:
            _agent_running.pop(row["id"], None)
    try:
        if receipt is not None and receipt["outcome"] == "applied":
            notifications.notify("agent_reply", f"{agent.get('name') or agent['id']} replied", route="#/team")
    except Exception:
        pass
    try:
        _check(user_id, None)
        _finish(row, device_id, receipt["outcome"], receipt["result"])
    except Exception:
        pass  # the next cycle finishes it from the receipt
    try:
        cloud_sync.request_sync()
    except Exception:
        pass


def _begin_agent_task(user_id, row, guard, device_id):
    """Check a claimed agent task and start it. Returns (outcome, reason) to refuse it, or None once it is running."""
    try:
        # Started before and not running now: the laptop stopped mid-run. Never run it a second time.
        if db.get_cloud_sync_value(user_id, AGENT_STARTED_KIND, row["id"]) is not None:
            raise _Reject(RESTARTED_REASON)
        params = validate_payload(AGENT_TASK, row["payload"])
        agent_id = row["target_id"]
        agent = agents.get_agent(agent_id) if AGENT_ID_RE.fullmatch(agent_id) else None
        if agent is None or agent.get("id") != agent_id:
            raise _Reject(NO_AGENT_REASON)
        if _names_confidential(guard, params["message"]):
            raise _Reject(CONFIDENTIAL_TEXT_REASON)
    except _Reject as exc:
        return "rejected", exc.reason
    db.upsert_cloud_sync_state(
        user_id, [(AGENT_STARTED_KIND, row["id"], "1", datetime.now(timezone.utc).isoformat(), None)]
    )
    with _agent_lock:
        _agent_running[row["id"]] = True
    thread = _Thread(target=_run_agent_task, args=(user_id, row, agent, params["message"], device_id), daemon=True)
    with _agent_lock:
        _agent_threads[:] = [th for th in _agent_threads if getattr(th, "is_alive", lambda: False)()]
        _agent_threads.append(thread)
    thread.start()
    return None


def _record_receipt(user_id, row, outcome, result):
    """Remember the outcome on this laptop before telling the cloud, so a crash can never apply it twice."""
    return db.record_phone_action_receipt(user_id, row["id"], row["kind"], row["target_id"], outcome, result)


def _pull(user_id):
    """The user's own waiting actions, oldest first. An older server without the table gives an empty list."""
    uid = urllib.parse.quote(user_id, safe="")
    path = (
        f"phone_actions?select={_SELECT}&owner_id=eq.{uid}&status=in.(queued,applying)"
        f"&kind=in.({','.join(KINDS)})&order=created_at.asc&limit={MAX_PULL}"
    )
    try:
        rows = _rest("GET", path)
    except PhoneQueueError as exc:
        if _table_missing(exc):
            return []
        raise
    if not isinstance(rows, list):
        raise PhoneQueueError("The phone action list was not a list.", status=502, code="bad_payload")
    return rows


def _claim(user_id, row, device_id):
    """Claim an action for this device. Returns True if this device now owns it."""
    got = _rest("POST", "rpc/claim_phone_action", body={"p_id": row["id"], "p_device": device_id})
    claimed = clean_row(got, user_id)
    return claimed is not None and claimed["id"] == row["id"] and claimed["status"] == "applying" and claimed["device"] == device_id


def _finish(row, device_id, outcome, result):
    _rest(
        "POST",
        "rpc/finish_phone_action",
        body={
            "p_id": row["id"],
            "p_device": device_id,
            "p_status": outcome,
            "p_result": str(result or "")[:MAX_RESULT_CHARS],
        },
    )


def _per_row_error(exc):
    """True for a refusal about one row (a 4xx), which must not stop the others. 401, network and 5xx stop the cycle."""
    status = getattr(exc, "status", 0)
    return isinstance(status, int) and 400 <= status < 500 and status not in (401, 408, 429)


def _attempts(user_id, action_id):
    raw = db.get_cloud_sync_value(user_id, ATTEMPTS_KIND, action_id)
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def _count_attempt(user_id, action_id):
    """Persist one more failed attempt on this action and return the new count."""
    count = _attempts(user_id, action_id) + 1
    db.upsert_cloud_sync_state(
        user_id, [(ATTEMPTS_KIND, action_id, str(count), datetime.now(timezone.utc).isoformat(), None)]
    )
    return count


def _give_up(user_id, row, device_id, summary):
    """After repeated failures: reject the action with a fixed reason (never any exception text) and tell the cloud."""
    receipt = _record_receipt(user_id, row, "rejected", FAILED_REASON)
    try:
        _finish(row, device_id, receipt["outcome"], receipt["result"])
    except PhoneQueueError as exc:
        if not _per_row_error(exc):
            raise
    summary["rejected"] += 1


def sync(user_id, is_signed_out=None):
    """One sync-cycle step: pull the user's queue and apply what this laptop can. Returns a small summary dict.

    Raises SignedOutAbort if the user signed out or the account changed, and PhoneQueueError if the cloud could not
    be reached or failed (what was already applied is recorded locally and finished on the next cycle). One broken
    action never blocks the others: a refusal about one row (4xx) or an unexpected error in one action moves on to
    the next, and an action that keeps failing is rejected after MAX_ATTEMPTS with a fixed reason.
    """
    user_id = str(user_id or "").strip()
    summary = {"applied": 0, "rejected": 0, "skipped": 0, "errors": 0, "started": 0, "paused": False}
    if not user_id:
        return summary
    _check(user_id, is_signed_out)
    raw_rows = _pull(user_id)
    # A kind this engine does not know belongs to a newer engine (one user can run both): leave it untouched.
    rows = [r for r in (clean_row(raw, user_id) for raw in raw_rows) if r is not None and r["kind"] in KINDS]
    if not rows:
        return summary

    guard = None
    device_id = cloud_sync.get_device_id()
    now = datetime.now(timezone.utc)
    # Every action we start on counts, whether it succeeds, fails or is refused: a cycle does bounded work.
    worked = 0
    for row in rows:
        if worked >= MAX_PER_CYCLE:
            break
        _check(user_id, is_signed_out)
        try:
            if _attempts(user_id, row["id"]) >= MAX_TOUCHES:
                summary["skipped"] += 1  # gave up on it here; expiry closes it in the cloud
                continue
            receipt = db.get_phone_action_receipt(user_id, row["id"])
            if row["status"] == "applying" and row["device"] != device_id:
                summary["skipped"] += 1
                continue
            if row["kind"] == AGENT_TASK and receipt is None:
                with _agent_lock:
                    running = row["id"] in _agent_running
                # Running now, or no free slot: leave it (a queued one stays unclaimed so another laptop could take it).
                if running or (row["status"] == "queued" and not _agent_slot_free(user_id)):
                    summary["skipped"] += 1
                    continue
            if row["status"] == "queued":
                if row["expires_at"] <= now:
                    summary["skipped"] += 1
                    continue
                if receipt is None and not target_exists(row["kind"], row["target_id"]):
                    summary["skipped"] += 1
                    continue
            if receipt is None and guard is None:
                try:
                    guard = phone_server._build_confidential_guard()
                except phone_server.PhoneApiError:
                    # Rules or repos unreadable: touch nothing (claim nothing) until they can be read.
                    summary["paused"] = True
                    break
            worked += 1
            if row["status"] == "queued":
                if not _claim(user_id, row, device_id):
                    summary["skipped"] += 1
                    continue
                _check(user_id, is_signed_out)
            if receipt is None:
                if row["kind"] == AGENT_TASK:
                    refused = _begin_agent_task(user_id, row, guard, device_id)
                    if refused is None:
                        summary["started"] += 1
                        _check(user_id, is_signed_out)
                        continue
                    outcome, result = refused
                else:
                    outcome, result = _apply(user_id, row, guard)
                receipt = _record_receipt(user_id, row, outcome, result)
            _check(user_id, is_signed_out)
            _finish(row, device_id, receipt["outcome"], receipt["result"])
            summary["applied" if receipt["outcome"] == "applied" else "rejected"] += 1
        except SignedOutAbort:
            raise
        except PhoneQueueError as exc:
            if not _per_row_error(exc):
                raise
            summary["errors"] += 1
            _count_attempt(user_id, row["id"])
        except phone_server.PhoneApiError:
            summary["paused"] = True
            break
        except Exception:
            summary["errors"] += 1
            if _count_attempt(user_id, row["id"]) >= MAX_ATTEMPTS:
                _give_up(user_id, row, device_id, summary)
    return summary
