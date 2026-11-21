#!/usr/bin/env python3
"""Admin dashboard queries and member management via Supabase REST using the admin's token."""

from datetime import datetime, timedelta, timezone
import os
import re
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import media_previews
import pitch_host
import team_auth

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
DAILY_ACTIVITY_ROW_LIMIT = 2000
MAX_DAILY_DAYS = 90
PREVIEW_SIGN_EXPIRES_S = 3600

ONLINE_WINDOW_S = 3 * 60
IDLE_WINDOW_S = 30 * 60

NETWORK_ERROR_MESSAGE = "Can't reach the team server. Check your connection."
ADMINS_ONLY_MESSAGE = "Admins only."
LAST_ADMIN_MESSAGE = "You can't remove the last admin."
OWN_ROLE_MESSAGE = "Ask another admin to change your own role."

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_KIND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,200}$")
_PRESENCE_RANK = {"offline": 0, "idle": 1, "online": 2, "working": 3}


class AdminDashboardError(Exception):
    """Error raised when an admin dashboard request fails validation, auth, or Supabase calls."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = code


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
    return dt.astimezone(timezone.utc)


def _short_error_message(message, fallback="Team server request failed."):
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return fallback
    if len(text) > 180:
        return text[:177] + "..."
    return text


def _map_team_auth_error(exc, signed_in_context=True):
    if exc.status == 0 or exc.code == "network_error":
        return AdminDashboardError(NETWORK_ERROR_MESSAGE, status=502, code="network_error")
    lowered = str(exc.message or "").lower()
    if "last active admin" in lowered or "cannot demote or deactivate" in lowered:
        return AdminDashboardError(LAST_ADMIN_MESSAGE, status=400, code="last_admin")
    if exc.status == 401 or exc.code in ("not_signed_in", "session_expired"):
        return AdminDashboardError("Not signed in.", status=401, code="not_signed_in")
    if exc.status == 403:
        return AdminDashboardError(ADMINS_ONLY_MESSAGE, status=403, code="forbidden")
    if exc.code == "not_configured":
        status = 401 if not signed_in_context else 503
        msg = "Not signed in." if not signed_in_context else "Team sign-in is not configured."
        return AdminDashboardError(msg, status=status, code="not_configured")
    if isinstance(exc.status, int) and exc.status >= 500:
        return AdminDashboardError("Team server returned an error. Please try again.", status=502, code="upstream_error")
    status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 500 else 400
    return AdminDashboardError(_short_error_message(exc.message), status=status, code=exc.code)


def _rest_with_retry(method, path, body=None, headers=None):
    try:
        return team_auth.rest(method, path, body=body, headers=headers)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
            try:
                team_auth.refresh_access_token()
                return team_auth.rest(method, path, body=body, headers=headers)
            except team_auth.TeamAuthError as retry_exc:
                raise _map_team_auth_error(retry_exc) from retry_exc
        raise _map_team_auth_error(exc) from exc


def require_admin():
    """Verify the current account is signed in, active, and has role 'admin'."""
    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        raise _map_team_auth_error(exc, signed_in_context=False) from exc

    if not acct.get("configured") or not acct.get("signed_in"):
        raise AdminDashboardError("Not signed in.", status=401, code="not_signed_in")
    if acct.get("deactivated"):
        raise AdminDashboardError(ADMINS_ONLY_MESSAGE, status=403, code="forbidden")

    user = acct.get("user") or {}
    if user.get("role") != "admin" or not bool(user.get("active", True)):
        raise AdminDashboardError(ADMINS_ONLY_MESSAGE, status=403, code="forbidden")
    return user


def validate_uuid(value, label="member id"):
    """Validate and normalize a UUID path or query parameter."""
    raw = str(value or "").strip()
    if not _UUID_RE.match(raw):
        raise AdminDashboardError(f"Invalid {label}.", status=400, code="invalid_uuid")
    return raw.lower()


def validate_safe_kind(value):
    """Validate a record or activity kind string."""
    raw = str(value or "").strip()
    if not raw or ".." in raw or not _SAFE_KIND_RE.match(raw):
        raise AdminDashboardError("Invalid kind.", status=400, code="invalid_kind")
    return raw


def validate_safe_local_id(value, label="local id"):
    """Validate a local record or parent id string."""
    raw = str(value or "").strip()
    if not raw or ".." in raw or raw.startswith(".") or not _SAFE_LOCAL_ID_RE.match(raw):
        raise AdminDashboardError(f"Invalid {label}.", status=400, code="invalid_local_id")
    return raw


def validate_before(value):
    """Validate an ISO-8601 timestamp cursor."""
    raw = str(value or "").strip()
    if not raw or ".." in raw or any(ch in raw for ch in (",", "(", ")", "*", "%")):
        raise AdminDashboardError("Invalid before timestamp.", status=400, code="invalid_before")
    if _parse_iso(raw) is None:
        raise AdminDashboardError("Invalid before timestamp.", status=400, code="invalid_before")
    return raw


def parse_limit(value, default=DEFAULT_LIMIT, max_limit=MAX_LIMIT):
    """Parse and cap a positive integer limit query parameter."""
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AdminDashboardError("Invalid limit.", status=400, code="invalid_limit") from exc
    if parsed <= 0:
        raise AdminDashboardError("Invalid limit.", status=400, code="invalid_limit")
    return min(parsed, max_limit)


def escape_ilike(value):
    """Escape SQL LIKE/ILIKE and PostgREST syntax characters in search input."""
    text = str(value or "")
    for ch in ("\\", "%", "_", "*", ",", "(", ")"):
        text = text.replace(ch, "\\" + ch)
    return text


def _quote(value):
    return urllib.parse.quote(str(value), safe="")


def _profile_label(profile):
    if not isinstance(profile, dict):
        return ""
    name = str(profile.get("display_name") or "").strip()
    email = str(profile.get("email") or "").strip()
    return name or email


def _load_profile_map():
    rows = _rest_with_retry("GET", "profiles?select=id,email,display_name,avatar_url")
    profiles = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                profiles[str(row["id"]).lower()] = row
    return profiles


def derive_presence(devices, active=True, now=None):
    """Derive member presence ('working', 'online', 'idle', 'offline') from device heartbeats."""
    if not active:
        return "offline"
    now_dt = now or datetime.now(timezone.utc)
    best = "offline"
    for dev in devices or []:
        if not isinstance(dev, dict):
            continue
        raw_status = str(dev.get("status") or "").strip().lower()
        if raw_status == "offline":
            continue
        seen_dt = _parse_iso(dev.get("last_seen_at"))
        if seen_dt is None:
            continue
        age_s = (now_dt - seen_dt).total_seconds()
        if age_s <= ONLINE_WINDOW_S:
            if raw_status in ("working", "online", "idle"):
                cand = raw_status
            else:
                cand = "offline"
        elif age_s <= IDLE_WINDOW_S:
            if raw_status in ("working", "online", "idle"):
                cand = "idle"
            else:
                cand = "offline"
        else:
            cand = "offline"

        if _PRESENCE_RANK[cand] > _PRESENCE_RANK[best]:
            best = cand
    return best


def _newest_iso(candidates):
    best_raw = None
    best_dt = None
    for raw in candidates:
        if not raw:
            continue
        dt = _parse_iso(raw)
        if dt is not None:
            if best_dt is None or dt > best_dt:
                best_dt = dt
                best_raw = raw
        elif best_raw is None:
            best_raw = raw
    return best_raw


def get_team(now=None):
    """Return active and inactive profiles joined with device_status and latest activity timestamp."""
    admin = require_admin()
    profiles = _rest_with_retry(
        "GET",
        "profiles?select=id,email,display_name,avatar_url,role,active,created_at,last_seen_at&order=created_at.asc",
    )
    devices = _rest_with_retry(
        "GET",
        "device_status?select=owner_id,device_id,device_kind,status,running_agents,app_version,last_seen_at&order=last_seen_at.desc",
    )
    latest_events = _rest_with_retry(
        "GET",
        "activity_events?select=owner_id,ts&order=ts.desc&limit=1000",
    )

    devices_by_owner = {}
    if isinstance(devices, list):
        for dev in devices:
            if not isinstance(dev, dict) or not dev.get("owner_id"):
                continue
            oid = str(dev["owner_id"]).lower()
            devices_by_owner.setdefault(oid, []).append({
                "device_id": dev.get("device_id"),
                "device_kind": dev.get("device_kind"),
                "status": dev.get("status"),
                "running_agents": dev.get("running_agents") if isinstance(dev.get("running_agents"), list) else [],
                "app_version": dev.get("app_version"),
                "last_seen_at": dev.get("last_seen_at"),
            })

    latest_activity_by_owner = {}
    if isinstance(latest_events, list):
        for ev in latest_events:
            if not isinstance(ev, dict) or not ev.get("owner_id"):
                continue
            oid = str(ev["owner_id"]).lower()
            if oid not in latest_activity_by_owner:
                latest_activity_by_owner[oid] = ev.get("ts")

    now_dt = now or datetime.now(timezone.utc)
    members = []
    for prof in profiles if isinstance(profiles, list) else []:
        if not isinstance(prof, dict) or not prof.get("id"):
            continue
        mid = str(prof["id"]).lower()
        active = bool(prof.get("active", True))
        member_devices = devices_by_owner.get(mid, [])
        presence = derive_presence(member_devices, active=active, now=now_dt)

        running_agents = []
        seen_agents = set()
        if active:
            for dev in member_devices:
                raw_status = str(dev.get("status") or "").strip().lower()
                seen_dt = _parse_iso(dev.get("last_seen_at"))
                if seen_dt is None or raw_status not in ("working", "online"):
                    continue
                if (now_dt - seen_dt).total_seconds() > ONLINE_WINDOW_S:
                    continue
                for agent_id in dev.get("running_agents") or []:
                    aid = str(agent_id).strip()
                    if aid and aid not in seen_agents:
                        seen_agents.add(aid)
                        running_agents.append(aid)

        latest_dev = member_devices[0] if member_devices else {}
        latest_act_ts = latest_activity_by_owner.get(mid)
        last_active_at = _newest_iso([
            prof.get("last_seen_at"),
            latest_dev.get("last_seen_at"),
            latest_act_ts,
        ])

        members.append({
            "id": prof.get("id"),
            "email": prof.get("email"),
            "display_name": prof.get("display_name"),
            "avatar_url": prof.get("avatar_url"),
            "role": prof.get("role"),
            "active": active,
            "created_at": prof.get("created_at"),
            "last_seen_at": prof.get("last_seen_at"),
            "device_last_seen_at": latest_dev.get("last_seen_at"),
            "latest_activity_ts": latest_act_ts,
            "last_active_at": last_active_at,
            "app_version": latest_dev.get("app_version"),
            "presence": presence,
            "status": presence,
            "running_agents": running_agents,
            "devices": member_devices,
        })

    return {
        "members": members,
        "current_user_id": admin.get("id"),
    }


def get_activity(member="", kind="", before="", limit=None):
    """Return newest-first activity_events with member display names attached."""
    require_admin()
    cap_limit = parse_limit(limit, default=DEFAULT_LIMIT, max_limit=MAX_LIMIT)
    filters = [
        "select=id,owner_id,ts,kind,agent_id,summary,data",
        "order=ts.desc,id.desc",
        f"limit={cap_limit}",
    ]
    if member:
        mid = validate_uuid(member, "member")
        filters.append(f"owner_id=eq.{_quote(mid)}")
    if kind:
        clean_kind = validate_safe_kind(kind)
        filters.append(f"kind=eq.{_quote(clean_kind)}")
    if before:
        clean_before = validate_before(before)
        filters.append(f"ts=lt.{_quote(clean_before)}")

    rows = _rest_with_retry("GET", f"activity_events?{'&'.join(filters)}")
    profile_map = _load_profile_map()

    events = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            oid = str(row.get("owner_id") or "").lower()
            prof = profile_map.get(oid)
            events.append({
                "id": row.get("id"),
                "owner_id": row.get("owner_id"),
                "member_name": _profile_label(prof) or str(row.get("owner_id") or ""),
                "member_email": prof.get("email") if prof else None,
                "ts": row.get("ts"),
                "kind": row.get("kind"),
                "agent_id": row.get("agent_id"),
                "summary": row.get("summary"),
                "data": row.get("data") if isinstance(row.get("data"), dict) else {},
            })

    next_before = events[-1]["ts"] if len(events) == cap_limit else None
    return {
        "events": events,
        "next_before": next_before,
    }


def get_daily_activity(days=14, now=None):
    """Aggregate activity_events counts per day per member from up to 2000 recent rows.

    Aggregating up to 2000 recent rows in Python is fine for a small team.
    """
    require_admin()
    if days is None or str(days).strip() == "":
        num_days = 14
    else:
        try:
            num_days = int(str(days).strip())
        except (TypeError, ValueError) as exc:
            raise AdminDashboardError("Invalid days.", status=400, code="invalid_days") from exc
        if num_days <= 0:
            raise AdminDashboardError("Invalid days.", status=400, code="invalid_days")
        num_days = min(num_days, MAX_DAILY_DAYS)

    now_dt = now or datetime.now(timezone.utc)
    today_date = now_dt.date()
    start_date = today_date - timedelta(days=num_days - 1)
    since_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    since_iso = since_dt.isoformat()

    # Fetching up to 2000 recent rows and aggregating in Python is fine for a small team.
    rows = _rest_with_retry(
        "GET",
        f"activity_events?select=owner_id,ts&ts=gte.{_quote(since_iso)}&order=ts.desc&limit={DAILY_ACTIVITY_ROW_LIMIT}",
    )
    profile_map = _load_profile_map()

    dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(num_days)]
    counts_by_day = {d: {} for d in dates}
    seen_members = set()

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            dt = _parse_iso(row.get("ts"))
            if dt is None:
                continue
            day_key = dt.date().isoformat()
            if day_key not in counts_by_day:
                continue
            oid = str(row.get("owner_id") or "").lower()
            if not oid:
                continue
            seen_members.add(oid)
            day_map = counts_by_day[day_key]
            day_map[oid] = day_map.get(oid, 0) + 1

    series = []
    for day_key in dates:
        by_member = counts_by_day[day_key]
        series.append({
            "date": day_key,
            "total": sum(by_member.values()),
            "by_member": by_member,
        })

    members_out = []
    all_member_ids = sorted(set(profile_map.keys()) | seen_members)
    for oid in all_member_ids:
        prof = profile_map.get(oid)
        members_out.append({
            "id": prof.get("id") if prof else oid,
            "display_name": _profile_label(prof) or oid,
        })

    return {
        "days": num_days,
        "dates": dates,
        "series": series,
        "members": members_out,
    }


def list_member_records(member_id, kind="", q="", before="", limit=None):
    """List non-deleted mirror_records metadata (no data) for a member, newest local_updated_at first."""
    require_admin()
    mid = validate_uuid(member_id, "member id")
    cap_limit = parse_limit(limit, default=DEFAULT_LIMIT, max_limit=MAX_LIMIT)

    filters = [
        f"owner_id=eq.{_quote(mid)}",
        "deleted_at=is.null",
        "select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at",
        "order=local_updated_at.desc",
        f"limit={cap_limit}",
    ]
    if kind:
        clean_kind = validate_safe_kind(kind)
        filters.append(f"kind=eq.{_quote(clean_kind)}")
    if q:
        escaped = escape_ilike(q)
        filters.append(f"title=ilike.*{_quote(escaped)}*")
    if before:
        clean_before = validate_before(before)
        filters.append(f"local_updated_at=lt.{_quote(clean_before)}")

    rows = _rest_with_retry("GET", f"mirror_records?{'&'.join(filters)}")
    records = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            records.append({
                "id": row.get("id"),
                "kind": row.get("kind"),
                "local_id": row.get("local_id"),
                "parent_local_id": row.get("parent_local_id"),
                "title": row.get("title"),
                "local_updated_at": row.get("local_updated_at"),
                "synced_at": row.get("synced_at"),
            })

    next_before = records[-1]["local_updated_at"] if len(records) == cap_limit else None
    return {
        "records": records,
        "next_before": next_before,
    }


def get_member_record_detail(member_id, kind, local_id):
    """Fetch one mirror_records row with data, plus list rows for its children."""
    require_admin()
    mid = validate_uuid(member_id, "member id")
    clean_kind = validate_safe_kind(kind)
    clean_local_id = validate_safe_local_id(local_id, "local id")

    rows = _rest_with_retry(
        "GET",
        (
            f"mirror_records?owner_id=eq.{_quote(mid)}"
            f"&kind=eq.{_quote(clean_kind)}"
            f"&local_id=eq.{_quote(clean_local_id)}"
            "&deleted_at=is.null"
            "&select=id,owner_id,kind,local_id,parent_local_id,title,data,local_updated_at,synced_at"
            "&limit=1"
        ),
    )
    if not isinstance(rows, list) or not rows:
        raise AdminDashboardError("Record not found.", status=404, code="not_found")
    record = rows[0]

    children = []
    child_kinds = None
    if clean_kind == "content_item":
        child_kinds = "in.(content_version,review_comment,metric,posted_link)"
    elif clean_kind == "chat_session":
        child_kinds = "eq.chat_session"

    if child_kinds is not None:
        child_rows = _rest_with_retry(
            "GET",
            (
                f"mirror_records?owner_id=eq.{_quote(mid)}"
                f"&parent_local_id=eq.{_quote(clean_local_id)}"
                f"&kind={child_kinds}"
                "&deleted_at=is.null"
                "&select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at"
                "&order=local_updated_at.asc"
                f"&limit={MAX_LIMIT}"
            ),
        )
        if isinstance(child_rows, list):
            for crow in child_rows:
                if not isinstance(crow, dict):
                    continue
                children.append({
                    "id": crow.get("id"),
                    "kind": crow.get("kind"),
                    "local_id": crow.get("local_id"),
                    "parent_local_id": crow.get("parent_local_id"),
                    "title": crow.get("title"),
                    "local_updated_at": crow.get("local_updated_at"),
                    "synced_at": crow.get("synced_at"),
                })

    return {
        "record": record,
        "children": children,
    }


def get_member_previews(member_id, parent="", limit=None):
    """Return non-deleted media_previews for a member, each signed for 1 hour."""
    require_admin()
    mid = validate_uuid(member_id, "member id")
    cap_limit = parse_limit(limit, default=DEFAULT_LIMIT, max_limit=MAX_LIMIT)

    filters = [
        f"owner_id=eq.{_quote(mid)}",
        "deleted_at=is.null",
        "select=id,owner_id,local_source_id,source_kind,parent_local_id,kind,storage_path,content_type,bytes,width,height,duration_s,created_at,expires_at",
        "order=created_at.desc",
        f"limit={cap_limit}",
    ]
    if parent:
        clean_parent = validate_safe_local_id(parent, "parent id")
        filters.append(f"parent_local_id=eq.{_quote(clean_parent)}")

    rows = _rest_with_retry("GET", f"media_previews?{'&'.join(filters)}")
    previews = []
    expected_prefix = f"{mid}/"

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            storage_path = str(row.get("storage_path") or "").strip()
            if not storage_path.lower().startswith(expected_prefix):
                continue
            if ".." in storage_path.split("/"):
                continue
            try:
                url = media_previews.signed_url(storage_path, expires_in=PREVIEW_SIGN_EXPIRES_S)
            except team_auth.TeamAuthError as exc:
                raise _map_team_auth_error(exc) from exc
            previews.append({
                "id": row.get("id"),
                "owner_id": row.get("owner_id"),
                "local_source_id": row.get("local_source_id"),
                "source_kind": row.get("source_kind"),
                "parent_local_id": row.get("parent_local_id"),
                "kind": row.get("kind"),
                "storage_path": storage_path,
                "content_type": row.get("content_type"),
                "bytes": row.get("bytes"),
                "width": row.get("width"),
                "height": row.get("height"),
                "duration_s": row.get("duration_s"),
                "created_at": row.get("created_at"),
                "expires_at": row.get("expires_at"),
                "signed_url": url,
            })

    return {"previews": previews}


def _load_catalog_names():
    try:
        cat = pitch_host.load_catalog()
        items = cat.get("items") or {}
        return {str(iid): str(item.get("n") or iid) for iid, item in items.items() if isinstance(item, dict)}
    except Exception:
        return {}


def get_member_pitch(member_id):
    """Return read-only published proposals (shortened share_id) and quote submissions for a member."""
    require_admin()
    mid = validate_uuid(member_id, "member id")

    prop_rows = _rest_with_retry(
        "GET",
        (
            f"published_proposals?owner_id=eq.{_quote(mid)}"
            "&select=share_id,local_proposal_id,kind,payload,status,expires_at,created_at,updated_at"
            "&order=updated_at.desc"
            f"&limit={MAX_LIMIT}"
        ),
    )
    sub_rows = _rest_with_retry(
        "GET",
        (
            f"quote_submissions?owner_id=eq.{_quote(mid)}"
            "&select=id,share_id,choices,contact,created_at,seen_at"
            "&order=created_at.desc"
            f"&limit={MAX_LIMIT}"
        ),
    )

    catalog_names = _load_catalog_names()
    custom_names_by_share = {}
    proposals_out = []

    for row in prop_rows if isinstance(prop_rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw_share = str(row.get("share_id") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
        preset = payload.get("preset") if isinstance(payload.get("preset"), dict) else {}

        custom_map = {}
        for citem in payload.get("custom_items") or []:
            if isinstance(citem, dict) and citem.get("id"):
                custom_map[str(citem["id"])] = str(citem.get("n") or citem["id"])
        if raw_share:
            custom_names_by_share[raw_share] = custom_map

        proposals_out.append({
            "share_id": raw_share[:6],
            "local_proposal_id": row.get("local_proposal_id"),
            "kind": row.get("kind"),
            "status": row.get("status"),
            "client_name": str(contact.get("name") or preset.get("clientName") or "").strip(),
            "business_name": str(contact.get("business") or preset.get("businessName") or "").strip(),
            "updated_at": row.get("updated_at"),
            "created_at": row.get("created_at"),
            "expires_at": row.get("expires_at"),
        })

    submissions_out = []
    for row in sub_rows if isinstance(sub_rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw_share = str(row.get("share_id") or "")
        contact = row.get("contact") if isinstance(row.get("contact"), dict) else {}
        choices = row.get("choices") if isinstance(row.get("choices"), dict) else {}
        custom_map = custom_names_by_share.get(raw_share, {})

        selected_ids = list(
            dict.fromkeys(
                str(x).strip()
                for x in (choices.get("selected") or []) + (choices.get("custom_selected") or [])
                if isinstance(x, str) and x.strip()
            )
        )
        if selected_ids:
            items = [custom_map.get(iid) or catalog_names.get(iid) or iid for iid in selected_ids]
        else:
            items = [str(x) for x in (choices.get("items") or []) if x]

        submissions_out.append({
            "id": row.get("id"),
            "share_id": raw_share[:6],
            "name": str(contact.get("name") or ""),
            "business": str(contact.get("business") or ""),
            "phone": str(contact.get("phone") or ""),
            "notes": str(contact.get("notes") or ""),
            "items": items,
            "total_shown": choices.get("total_shown"),
            "created_at": row.get("created_at"),
            "seen_at": row.get("seen_at"),
        })

    return {
        "proposals": proposals_out,
        "submissions": submissions_out,
    }


def get_storage():
    """Return team preview storage usage with limit_bytes, percent, and 80% warning."""
    require_admin()
    try:
        return media_previews.storage_usage()
    except team_auth.TeamAuthError as exc:
        raise _map_team_auth_error(exc) from exc


def get_audit(before="", limit=None):
    """Return newest-first audit_log entries with actor display names attached."""
    require_admin()
    cap_limit = parse_limit(limit, default=DEFAULT_LIMIT, max_limit=MAX_LIMIT)
    filters = [
        "select=id,actor_id,action,target,data,created_at",
        "order=created_at.desc,id.desc",
        f"limit={cap_limit}",
    ]
    if before:
        clean_before = validate_before(before)
        filters.append(f"created_at=lt.{_quote(clean_before)}")

    rows = _rest_with_retry("GET", f"audit_log?{'&'.join(filters)}")
    profile_map = _load_profile_map()

    entries = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            actor_id = str(row.get("actor_id") or "").lower()
            actor_prof = profile_map.get(actor_id) if actor_id else None
            actor_name = _profile_label(actor_prof) or (str(row.get("actor_id")) if row.get("actor_id") else "System")

            target = str(row.get("target") or "")
            target_prof = profile_map.get(target.lower()) if _UUID_RE.match(target) else None
            target_label = _profile_label(target_prof) if target_prof else target

            entries.append({
                "id": row.get("id"),
                "actor_id": row.get("actor_id"),
                "actor_name": actor_name,
                "action": row.get("action"),
                "target": target,
                "target_label": target_label,
                "data": row.get("data") if isinstance(row.get("data"), dict) else {},
                "created_at": row.get("created_at"),
            })

    next_before = entries[-1]["created_at"] if len(entries) == cap_limit else None
    return {
        "entries": entries,
        "next_before": next_before,
    }


def update_member_role(member_id, role):
    """Update another member's role ('admin' or 'member')."""
    admin = require_admin()
    mid = validate_uuid(member_id, "member id")
    if mid == str(admin.get("id") or "").strip().lower():
        raise AdminDashboardError(OWN_ROLE_MESSAGE, status=400, code="own_account")

    clean_role = str(role or "").strip().lower()
    if clean_role not in ("admin", "member"):
        raise AdminDashboardError("Role must be 'admin' or 'member'.", status=400, code="invalid_role")

    rows = _rest_with_retry(
        "PATCH",
        f"profiles?id=eq.{_quote(mid)}",
        body={"role": clean_role},
        headers={"Prefer": "return=representation"},
    )
    if not isinstance(rows, list) or not rows:
        raise AdminDashboardError("Member not found.", status=404, code="not_found")
    return {"member": rows[0]}


def update_member_active(member_id, active):
    """Activate or deactivate another member's profile."""
    admin = require_admin()
    mid = validate_uuid(member_id, "member id")
    if mid == str(admin.get("id") or "").strip().lower():
        raise AdminDashboardError(OWN_ROLE_MESSAGE, status=400, code="own_account")

    if not isinstance(active, bool):
        raise AdminDashboardError("active must be true or false.", status=400, code="invalid_active")

    rows = _rest_with_retry(
        "PATCH",
        f"profiles?id=eq.{_quote(mid)}",
        body={"active": active},
        headers={"Prefer": "return=representation"},
    )
    if not isinstance(rows, list) or not rows:
        raise AdminDashboardError("Member not found.", status=404, code="not_found")
    return {"member": rows[0]}
