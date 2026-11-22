#!/usr/bin/env python3
"""Local notifications store, per-event settings, SSE dispatch, and team/admin polling."""

from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import urllib.parse
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events
import team_auth

MAX_NOTIFICATIONS = 200
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 300
SYNC_PAUSED_THRESHOLD_S = 30 * 60
KEY_FAILING_INTERVAL_S = 10 * 60
STORAGE_CHECK_INTERVAL_S = 60 * 60
STORAGE_WARNING_PERCENT = 80
LINK_FAILURE_THRESHOLD = 3
CURSOR_LIMIT = 200
MAX_CURSOR_IDS = 500

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SECRET_SCRUB_RE = re.compile(
    r"(?:nvapi-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|sbp_[A-Za-z0-9]{12,})"
)

NOTIFICATION_KINDS = (
    {
        "kind": "quote_request",
        "label": "New quote requests",
        "description": "When a client submits a quote request on a live link",
        "default": True,
        "admin_only": False,
        "route": "#/pitch",
    },
    {
        "kind": "link_failed",
        "label": "Link publish failures",
        "description": "When a live client link fails to update 3 times in a row",
        "default": True,
        "admin_only": False,
        "route": "#/pitch",
    },
    {
        "kind": "ai_limit",
        "label": "Team AI limit alerts",
        "description": "When your daily team AI usage reaches 90% or 100%",
        "default": True,
        "admin_only": False,
        "route": "#/settings",
    },
    {
        "kind": "team_knowledge",
        "label": "Team knowledge updates",
        "description": "When shared brand kit, rules, or base prices update",
        "default": True,
        "admin_only": False,
        "route": "#/settings",
    },
    {
        "kind": "sync_paused",
        "label": "Sync paused alerts",
        "description": "When team sync fails for 30 minutes or product rules cannot be read",
        "default": True,
        "admin_only": False,
        "route": "#/settings",
    },
    {
        "kind": "member_joined",
        "label": "New team members",
        "description": "When a new team member joins",
        "default": True,
        "admin_only": True,
        "route": "#/admin",
    },
    {
        "kind": "member_posted",
        "label": "Member posts",
        "description": "When another team member publishes a post link",
        "default": True,
        "admin_only": True,
        "route": "#/admin",
    },
    {
        "kind": "member_draft",
        "label": "Member drafts",
        "description": "When another team member starts a new draft",
        "default": False,
        "admin_only": True,
        "route": "#/admin",
    },
    {
        "kind": "key_failing",
        "label": "Shared AI key errors",
        "description": "When a shared AI key reports 401 or 403",
        "default": True,
        "admin_only": True,
        "route": "#/admin",
    },
    {
        "kind": "storage_full",
        "label": "Team storage warnings",
        "description": "When team preview storage reaches 80%",
        "default": True,
        "admin_only": True,
        "route": "#/admin",
    },
    {
        "kind": "reminder",
        "label": "Reminders",
        "description": "When a scheduled or results reminder becomes due",
        "default": True,
        "admin_only": False,
        "route": "#/today",
    },
    {
        "kind": "results_pending",
        "label": "Results ready to confirm",
        "description": "When metrics extracted from a screenshot are waiting for confirmation",
        "default": True,
        "admin_only": False,
        "route": "#/today",
    },
    {
        "kind": "weekly_summary",
        "label": "Weekly summary",
        "description": "When the Analyst finishes a weekly performance summary",
        "default": True,
        "admin_only": False,
        "route": "#/today/summary",
    },
    {
        "kind": "learning_proposed",
        "label": "Proposed learnings",
        "description": "When the Analyst proposes new learnings for approval",
        "default": True,
        "admin_only": False,
        "route": "#/today/learnings",
    },
    {
        "kind": "sent_to_phone",
        "label": "Sent to my phone",
        "description": "When you send a proposal or draft from your laptop to your phone",
        "default": True,
        "admin_only": False,
        "route": "#/today",
    },
    {
        "kind": "social_challenge",
        "label": "Social research checks",
        "description": "When social research pauses for a login or security check",
        "default": True,
        "admin_only": False,
        "route": "#/research",
    },
)

KINDS_BY_ID = {item["kind"]: item for item in NOTIFICATION_KINDS}


class NotificationError(Exception):
    """Error raised when reading or updating notification settings fails."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = code


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today_utc(now_dt=None):
    dt = now_dt or datetime.now(timezone.utc)
    return dt.date().isoformat()


def _clean_text(value, max_len):
    text = str(value or "")
    text = _CONTROL_CHAR_RE.sub(" ", text)
    text = " ".join(text.split()).strip()
    text = _SECRET_SCRUB_RE.sub("[REDACTED]", text)
    if len(text) > max_len:
        return text[:max_len].rstrip()
    return text


def _clean_route(route, default_route):
    raw = _clean_text(route or "", 200)
    if raw.startswith("#/"):
        return raw
    return default_route


def _get_json_meta(key, default=None):
    raw = db.get_meta(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _set_json_meta(key, value):
    if value is None:
        db.delete_meta(key)
    else:
        db.set_meta(key, json.dumps(value, ensure_ascii=False))


def _cursor_key(user_id, name):
    return f"notif:{str(user_id or '').lower()}:{name}"


def is_kind_enabled(kind):
    """Return True if notification kind is enabled (explicit override or default)."""
    spec = KINDS_BY_ID.get(kind)
    if not spec:
        return False
    stored = db.get_notification_settings()
    if kind in stored:
        return bool(stored[kind])
    return bool(spec["default"])


def is_current_user_admin():
    """Return True if the signed-in account is an active admin."""
    try:
        cfg = team_auth.get_config()
        sess = team_auth.get_session_info() if cfg.get("configured") else None
        if not cfg.get("configured") or not sess:
            return False
        acct = team_auth.current_account()
    except Exception:
        return False
    if not isinstance(acct, dict) or not acct.get("signed_in") or acct.get("deactivated"):
        return False
    user = acct.get("user") or {}
    return user.get("role") == "admin" and bool(user.get("active", True))


def get_settings(is_admin=None):
    """Return notification settings list (filtering admin-only kinds for non-admins)."""
    admin = is_current_user_admin() if is_admin is None else bool(is_admin)
    stored = db.get_notification_settings()
    items = []
    for spec in NOTIFICATION_KINDS:
        if spec["admin_only"] and not admin:
            continue
        kind = spec["kind"]
        enabled = bool(stored[kind]) if kind in stored else bool(spec["default"])
        items.append({
            "kind": kind,
            "label": spec["label"],
            "description": spec["description"],
            "admin_only": spec["admin_only"],
            "default": spec["default"],
            "enabled": enabled,
        })
    return {"settings": items, "is_admin": admin}


def update_setting(kind, enabled, is_admin=None):
    """Update a notification kind setting, refusing admin-only kinds for non-admins."""
    clean_kind = str(kind or "").strip()
    spec = KINDS_BY_ID.get(clean_kind)
    if not spec:
        raise NotificationError("Unknown notification kind.", status=400, code="invalid_kind")
    if not isinstance(enabled, bool):
        raise NotificationError("enabled must be true or false.", status=400, code="invalid_enabled")

    admin = is_current_user_admin() if is_admin is None else bool(is_admin)
    if spec["admin_only"] and not admin:
        raise NotificationError("Admins only.", status=403, code="forbidden")

    was_enabled = is_kind_enabled(clean_kind)
    db.set_notification_setting(clean_kind, enabled)

    # Reset admin polling cursor when turning a kind on so first run sets cursor without flooding.
    if enabled and not was_enabled and spec["admin_only"]:
        _clear_cursor_for_current_user(clean_kind)

    return {
        "ok": True,
        "kind": clean_kind,
        "enabled": enabled,
        **get_settings(is_admin=admin),
    }


def _clear_cursor_for_current_user(kind):
    try:
        sess = team_auth.get_session_info()
    except Exception:
        sess = None
    uid = str((sess or {}).get("user_id") or "").strip().lower()
    if uid:
        db.delete_meta(_cursor_key(uid, f"cursor_{kind}"))


def notify(kind, title, body="", route=None, created_at=None):
    """Store and publish a notification if kind is enabled, returning the stored row or None."""
    spec = KINDS_BY_ID.get(kind)
    if not spec:
        raise ValueError(f"Unknown notification kind: {kind}")
    if not is_kind_enabled(kind):
        return None

    clean_title = _clean_text(title, MAX_TITLE_CHARS)
    if not clean_title:
        clean_title = spec["label"]
    clean_body = _clean_text(body, MAX_BODY_CHARS)
    clean_route = _clean_route(route, spec["route"])
    ts = str(created_at or _now_iso())
    notif_id = f"notif_{uuid.uuid4().hex[:16]}"

    row = db.insert_notification(
        notif_id=notif_id,
        kind=kind,
        title=clean_title,
        body=clean_body,
        route=clean_route,
        created_at=ts,
        max_keep=MAX_NOTIFICATIONS,
    )
    if row is not None:
        events.publish("notification", row)
    return row


def send_test():
    """Store and publish one fixed test notification (ignores per-kind settings) so people can check delivery."""
    row = db.insert_notification(
        notif_id=f"notif_{uuid.uuid4().hex[:16]}",
        kind="test",
        title="Test notification",
        body="Notifications from Magnm Studio are working.",
        route="#/settings",
        created_at=_now_iso(),
        max_keep=MAX_NOTIFICATIONS,
    )
    if row is not None:
        events.publish("notification", row)
    return row


def list_notifications(limit=50):
    """Return newest-first notifications and unread count."""
    return {
        "notifications": db.list_notifications(limit=limit),
        "unread_count": db.count_unread_notifications(),
    }


def mark_read(ids=None, mark_all=False):
    """Mark specific ids or all notifications read and return updated unread count."""
    if not mark_all and not isinstance(ids, list):
        raise NotificationError("Provide ids array or all=true.", status=400, code="invalid_body")
    unread = db.mark_notifications_read(ids=ids, mark_all=bool(mark_all))
    return {"ok": True, "unread_count": unread}


# --- Trigger helpers ---

def notify_quote_request(enriched):
    """Emit quote_request notification for a newly stored pitch quote submission."""
    if not isinstance(enriched, dict):
        return None
    contact = enriched.get("contact") if isinstance(enriched.get("contact"), dict) else {}
    name = str(contact.get("name") or "").strip()
    business = str(contact.get("business") or "").strip()
    proposal_title = str(enriched.get("proposal_title") or "Proposal").strip()

    if name and business:
        who = f"{name} ({business})"
    elif name or business:
        who = name or business
    else:
        who = "Client"

    body = f"{who} for {proposal_title}"
    return notify("quote_request", "New quote request", body=body, route="#/pitch")


def record_link_republish_success(proposal_id):
    """Reset consecutive republish failure streak for a proposal."""
    pid = str(proposal_id or "").strip()
    if pid:
        db.delete_meta(f"notif_link_fail:{pid}")


def record_link_republish_failure(proposal_id, title="", reason=""):
    """Increment republish failure streak and notify on the 3rd consecutive failure."""
    pid = str(proposal_id or "").strip()
    if not pid:
        return None
    meta_key = f"notif_link_fail:{pid}"
    state = _get_json_meta(meta_key) or {"count": 0, "notified": False}
    count = int(state.get("count") or 0) + 1
    notified = bool(state.get("notified"))

    created = None
    if count >= LINK_FAILURE_THRESHOLD and not notified:
        label = str(title or "").strip()
        short_reason = str(reason or "").strip()
        if label and short_reason:
            body = f"{label}: {short_reason}"
        else:
            body = label or short_reason
        created = notify(
            "link_failed",
            "A client link could not be updated",
            body=body,
            route="#/pitch",
        )
        notified = True

    _set_json_meta(meta_key, {"count": count, "notified": notified})
    return created


def notify_team_knowledge_updated(label, updated_by_name):
    """Notify a team member when shared team knowledge has been applied."""
    clean_label = str(label or "Team knowledge").strip() or "Team knowledge"
    actor = str(updated_by_name or "Admin").strip() or "Admin"
    return notify(
        "team_knowledge",
        f"{clean_label} updated by {actor}",
        body="",
        route="#/settings",
    )


def evaluate_ai_limit(status_dict, today_utc=None):
    """Check /api/account/ai data and notify once per day per level (90%, 100%) in team mode."""
    if not isinstance(status_dict, dict) or status_dict.get("mode") != "team":
        return None
    today = status_dict.get("today") if isinstance(status_dict.get("today"), dict) else {}
    limit = status_dict.get("limit") if isinstance(status_dict.get("limit"), dict) else {}

    req_limit = int(limit.get("daily_requests") or 0)
    tok_limit = int(limit.get("daily_tokens") or 0)
    reqs = int(today.get("requests") or 0)
    toks = int(today.get("prompt_tokens") or 0) + int(today.get("completion_tokens") or 0)

    req_pct = (reqs * 100.0 / req_limit) if req_limit > 0 else 0.0
    tok_pct = (toks * 100.0 / tok_limit) if tok_limit > 0 else 0.0
    max_pct = max(req_pct, tok_pct)

    day_key = str(today_utc or _today_utc())
    if max_pct >= 100.0:
        return _notify_ai_level(day_key, "100", "AI limit reached", "Daily team AI limit reached (100%).")
    if max_pct >= 90.0:
        pct_int = min(99, int(max_pct))
        return _notify_ai_level(
            day_key,
            "90",
            "AI limit almost reached",
            f"You have used {pct_int}% of your daily team AI limit.",
        )
    return None


def notify_ai_limit_100(reason="", today_utc=None):
    """Notify once per day when team AI proxy rejects a request with 429 quota limit."""
    day_key = str(today_utc or _today_utc())
    body = str(reason or "").strip() or "Daily team AI limit reached."
    return _notify_ai_level(day_key, "100", "AI limit reached", body)


def _notify_ai_level(day_key, level, title, body):
    meta_key = f"notif_ai_limit:{day_key}:{level}"
    if db.get_meta(meta_key):
        return None
    row = notify("ai_limit", title, body=body, route="#/settings")
    if row is not None or not is_kind_enabled("ai_limit"):
        db.set_meta(meta_key, "1")
    return row


def record_sync_success():
    """Clear ongoing cloud sync failure tracking after a successful sync cycle."""
    db.delete_meta("notif_sync_failure")


def record_sync_failure(reason, now_ts=None):
    """Track cloud sync failures and emit sync_paused if failing >= 30 min or product rules unreadable."""
    now = float(time.time() if now_ts is None else now_ts)
    msg = str(reason or "").strip() or "Sync failed."
    is_rules_err = "product rules" in msg.lower()

    state = _get_json_meta("notif_sync_failure") or {
        "first_failed_at": now,
        "notified": False,
    }
    first_at = float(state.get("first_failed_at") or now)
    notified = bool(state.get("notified"))

    created = None
    if not notified and (is_rules_err or (now - first_at) >= SYNC_PAUSED_THRESHOLD_S):
        created = notify(
            "sync_paused",
            "Team sync is paused",
            body=msg,
            route="#/settings",
        )
        notified = True

    _set_json_meta("notif_sync_failure", {"first_failed_at": first_at, "notified": notified})
    return created


# --- Admin & sync-cycle checks ---

def _rest(method, path, body=None):
    import cloud_sync
    return cloud_sync._rest_with_auth_retry(method, path, body=body)


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    cleaned = ts.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_newer_ts(cand, base):
    if not cand:
        return False
    if not base:
        return True
    dc = _parse_iso(cand)
    db_dt = _parse_iso(base)
    if dc is not None and db_dt is not None:
        return dc > db_dt
    return str(cand) > str(base)


def _load_profile_names(rest_fn=_rest):
    rows = rest_fn("GET", "profiles?select=id,email,display_name,created_at&order=created_at.asc&limit=200")
    out = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            uid = str(row["id"]).lower()
            name = str(row.get("display_name") or row.get("email") or "").strip()
            out[uid] = {
                "id": row["id"],
                "name": name or "A team member",
                "email": str(row.get("email") or "").strip(),
                "created_at": row.get("created_at"),
            }
    return out, rows if isinstance(rows, list) else []


def poll_member_joined(user_id, profiles_rows=None, rest_fn=_rest):
    """Poll profiles with a local cursor; first run sets cursor only, later new profiles notify."""
    if not is_kind_enabled("member_joined"):
        return []
    cursor_key = _cursor_key(user_id, "cursor_member_joined")
    cursor = _get_json_meta(cursor_key)

    if profiles_rows is None:
        if cursor and cursor.get("ts"):
            q_ts = urllib.parse.quote(str(cursor["ts"]), safe="")
            rows = rest_fn(
                "GET",
                f"profiles?select=id,email,display_name,created_at&created_at=gte.{q_ts}&order=created_at.asc&limit={CURSOR_LIMIT}",
            )
        else:
            rows = rest_fn(
                "GET",
                f"profiles?select=id,email,display_name,created_at&order=created_at.asc&limit={CURSOR_LIMIT}",
            )
    else:
        rows = profiles_rows

    if not isinstance(rows, list):
        return []

    seen_ids = list(cursor.get("seen_ids") or []) if isinstance(cursor, dict) else []
    seen_set = {str(x).lower() for x in seen_ids}
    cursor_ts = cursor.get("ts") if isinstance(cursor, dict) else None
    self_uid = str(user_id or "").lower()

    if cursor is None:
        max_ts = None
        new_seen = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            rid = str(row["id"]).lower()
            new_seen.append(rid)
            ts_val = row.get("created_at")
            if ts_val and _is_newer_ts(ts_val, max_ts):
                max_ts = ts_val
        _set_json_meta(cursor_key, {
            "ts": max_ts,
            "seen_ids": new_seen[-MAX_CURSOR_IDS:],
        })
        return []

    created_notifs = []
    max_ts = cursor_ts
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        rid = str(row["id"]).lower()
        ts_val = row.get("created_at")
        if rid in seen_set:
            continue
        if cursor_ts and ts_val and not _is_newer_ts(ts_val, cursor_ts) and ts_val != cursor_ts:
            continue
        seen_set.add(rid)
        seen_ids.append(rid)
        if ts_val and _is_newer_ts(ts_val, max_ts):
            max_ts = ts_val
        if rid == self_uid:
            continue

        display = str(row.get("display_name") or "").strip()
        email = str(row.get("email") or "").strip()
        label = display or email or "A new member"
        body = email if display and email else ""
        notif = notify("member_joined", f"{label} joined the team", body=body, route="#/admin")
        if notif is not None:
            created_notifs.append(notif)

    _set_json_meta(cursor_key, {
        "ts": max_ts or cursor_ts,
        "seen_ids": seen_ids[-MAX_CURSOR_IDS:],
    })
    return created_notifs


def _poll_mirror_kind(user_id, notif_kind, mirror_kind, profile_map=None, rest_fn=_rest):
    if not is_kind_enabled(notif_kind):
        return []
    cursor_key = _cursor_key(user_id, f"cursor_{notif_kind}")
    cursor = _get_json_meta(cursor_key)
    q_owner = urllib.parse.quote(str(user_id), safe="")
    q_kind = urllib.parse.quote(mirror_kind, safe="")

    filters = [
        f"owner_id=neq.{q_owner}",
        f"kind=eq.{q_kind}",
        "deleted_at=is.null",
        "select=id,owner_id,kind,local_id,title,data,local_updated_at,synced_at",
        "order=synced_at.asc",
        f"limit={CURSOR_LIMIT}",
    ]
    if cursor and cursor.get("ts"):
        q_ts = urllib.parse.quote(str(cursor["ts"]), safe="")
        filters.append(f"synced_at=gte.{q_ts}")

    rows = rest_fn("GET", f"mirror_records?{'&'.join(filters)}")
    if not isinstance(rows, list):
        return []

    seen_ids = list(cursor.get("seen_ids") or []) if isinstance(cursor, dict) else []
    seen_set = set(seen_ids)
    cursor_ts = cursor.get("ts") if isinstance(cursor, dict) else None
    self_uid = str(user_id or "").lower()

    def _row_key(r):
        if r.get("id"):
            return str(r["id"])
        return f"{str(r.get('owner_id') or '').lower()}:{r.get('local_id') or ''}"

    def _row_ts(r):
        return r.get("synced_at") or r.get("local_updated_at")

    if cursor is None:
        max_ts = None
        init_seen = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rk = _row_key(row)
            if rk:
                init_seen.append(rk)
            rts = _row_ts(row)
            if rts and _is_newer_ts(rts, max_ts):
                max_ts = rts
        _set_json_meta(cursor_key, {
            "ts": max_ts,
            "seen_ids": init_seen[-MAX_CURSOR_IDS:],
        })
        return []

    if profile_map is None:
        profile_map, _ = _load_profile_names(rest_fn=rest_fn)

    created_notifs = []
    max_ts = cursor_ts
    for row in rows:
        if not isinstance(row, dict):
            continue
        owner_id = str(row.get("owner_id") or "").lower()
        if not owner_id or owner_id == self_uid:
            continue
        rk = _row_key(row)
        if not rk or rk in seen_set:
            continue
        rts = _row_ts(row)
        if cursor_ts and rts and not _is_newer_ts(rts, cursor_ts) and rts != cursor_ts:
            continue

        seen_set.add(rk)
        seen_ids.append(rk)
        if rts and _is_newer_ts(rts, max_ts):
            max_ts = rts

        member_name = (profile_map.get(owner_id) or {}).get("name") or "A team member"
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if data.get("confidential"):
            title = f"{member_name} worked on a confidential item"
            body = ""
        elif notif_kind == "member_posted":
            platform = str(data.get("platform") or "").strip()
            title = f"{member_name} posted on {platform}" if platform else f"{member_name} published a post"
            body = str(row.get("title") or "").strip()
        else:
            title = f"{member_name} started a draft"
            body = str(row.get("title") or "").strip()

        notif = notify(notif_kind, title, body=body, route="#/admin")
        if notif is not None:
            created_notifs.append(notif)

    _set_json_meta(cursor_key, {
        "ts": max_ts or cursor_ts,
        "seen_ids": seen_ids[-MAX_CURSOR_IDS:],
    })
    return created_notifs


def poll_key_failing(user_id, now_ts=None, rest_fn=_rest):
    """Check admin_ai_key_labels at most every 10 minutes; notify label-only on 401/403."""
    if not is_kind_enabled("key_failing"):
        return []
    now = float(time.time() if now_ts is None else now_ts)
    state_key = _cursor_key(user_id, "key_failing")
    state = _get_json_meta(state_key) or {"last_check_at": 0.0, "alerted": []}
    last_check = float(state.get("last_check_at") or 0.0)
    if (now - last_check) < KEY_FAILING_INTERVAL_S:
        return []

    rows = rest_fn("POST", "rpc/admin_ai_key_labels", body={})
    if not isinstance(rows, list):
        _set_json_meta(state_key, {"last_check_at": now, "alerted": list(state.get("alerted") or [])})
        return []

    prev_alerted = set(str(x) for x in (state.get("alerted") or []))
    still_failing = set()
    created_notifs = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        try:
            status_code = int(row.get("last_status")) if row.get("last_status") is not None else None
        except (TypeError, ValueError):
            status_code = None

        if status_code in (401, 403):
            still_failing.add(label)
            if label not in prev_alerted:
                notif = notify(
                    "key_failing",
                    "A shared AI key is failing",
                    body=label,
                    route="#/admin",
                )
                if notif is not None:
                    created_notifs.append(notif)

    _set_json_meta(state_key, {
        "last_check_at": now,
        "alerted": sorted(still_failing),
    })
    return created_notifs


def poll_storage_full(user_id, now_ts=None, storage_fn=None):
    """Check storage usage at most once per hour and notify once per >= 80% crossing."""
    if not is_kind_enabled("storage_full"):
        return None
    now = float(time.time() if now_ts is None else now_ts)
    state_key = _cursor_key(user_id, "storage_full")
    state = _get_json_meta(state_key) or {"last_check_at": 0.0, "above_80": False}
    last_check = float(state.get("last_check_at") or 0.0)
    if (now - last_check) < STORAGE_CHECK_INTERVAL_S:
        return None

    if storage_fn is None:
        import media_previews
        usage = media_previews.storage_usage()
    else:
        usage = storage_fn()

    if not isinstance(usage, dict):
        _set_json_meta(state_key, {"last_check_at": now, "above_80": bool(state.get("above_80"))})
        return None

    percent = float(usage.get("percent") or 0.0)
    is_above = bool(usage.get("warning")) or percent >= STORAGE_WARNING_PERCENT
    was_above = bool(state.get("above_80"))

    created = None
    if is_above and not was_above:
        pct_label = int(round(percent))
        created = notify(
            "storage_full",
            f"Team storage is {pct_label}% full",
            body="Review or prune old media previews in Admin.",
            route="#/admin",
        )

    _set_json_meta(state_key, {"last_check_at": now, "above_80": is_above})
    return created


def run_sync_checks(user_id, user_role, is_signed_out=None, now_ts=None):
    """Run periodic notification checks inside cloud_sync after workspace sync succeeds."""
    if not user_id:
        return
    if is_signed_out and is_signed_out():
        return

    # 1. Own team AI limit check (when using team AI)
    try:
        import ai_gateway
        if not ai_gateway.has_local_nim_keys():
            ai_status = ai_gateway.get_account_ai_status()
            evaluate_ai_limit(ai_status)
    except Exception:
        pass

    if str(user_role or "").strip().lower() != "admin":
        return

    if is_signed_out and is_signed_out():
        return

    profile_map = None
    try:
        if is_kind_enabled("member_joined") or is_kind_enabled("member_posted") or is_kind_enabled("member_draft"):
            profile_map, profile_rows = _load_profile_names()
            if is_kind_enabled("member_joined"):
                poll_member_joined(user_id, profiles_rows=profile_rows)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise
    except Exception:
        pass

    if is_signed_out and is_signed_out():
        return

    try:
        if is_kind_enabled("member_posted"):
            _poll_mirror_kind(user_id, "member_posted", "posted_link", profile_map=profile_map)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise
    except Exception:
        pass

    if is_signed_out and is_signed_out():
        return

    try:
        if is_kind_enabled("member_draft"):
            _poll_mirror_kind(user_id, "member_draft", "content_item", profile_map=profile_map)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise
    except Exception:
        pass

    if is_signed_out and is_signed_out():
        return

    try:
        poll_key_failing(user_id, now_ts=now_ts)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise
    except Exception:
        pass

    if is_signed_out and is_signed_out():
        return

    try:
        poll_storage_full(user_id, now_ts=now_ts)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise
    except Exception:
        pass
