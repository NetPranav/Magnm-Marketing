#!/usr/bin/env python3
"""One-way background cloud sync of local workspace records, activity, and device status to Supabase."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths
import product_rules
import team_auth

SYNC_INTERVAL_S = 60.0
DEBOUNCE_S = 5.0
MAX_RECORD_DATA_BYTES = 200 * 1024
MAX_OUTBOX_DATA_BYTES = 32 * 1024
MAX_BATCH_RECORDS = 50
MAX_BATCH_BYTES = 1024 * 1024
BACKOFF_STEPS_S = (60.0, 120.0, 300.0, 600.0)
SHUTDOWN_OFFLINE_TIMEOUT_S = 1.0

DELETED_HASH = "__deleted__"
ACTIVITY_CURSOR_KIND = "_activity_cursor"
ACTIVITY_CURSOR_ID = "activity"
OUTBOX_ERROR_KIND = "_outbox_error"

_lock = threading.Lock()
_cycle_lock = threading.Lock()
_stop_event = threading.Event()
_wake_event = threading.Event()
_thread = None

_running = False
_last_sync_at = None
_last_error = None
_failure_count = 0
_next_allowed_at = 0.0
_pending_debounce_until = 0.0
_immediate_requested = False
_auth_generation = 0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _log_warning(msg):
    try:
        print(f"[cloud_sync] {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def is_enabled():
    """Return True unless MAGNM_DISABLE_CLOUD_SYNC disables background cloud sync."""
    raw = os.environ.get("MAGNM_DISABLE_CLOUD_SYNC", "").strip().lower()
    return raw not in ("1", "true", "yes")


def _app_version():
    conf_path = paths.resource("apps", "desktop", "src-tauri", "tauri.conf.json")
    if os.path.isfile(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ver = str(data.get("version") or "").strip()
            if ver:
                return ver
        except Exception:
            pass
    return "0.1.0"


def get_device_id():
    """Return the persistent random device_id stored in <DATA_ROOT>/state/device_id."""
    dev_path = paths.data("state", "device_id")
    if os.path.isfile(dev_path):
        try:
            with open(dev_path, "r", encoding="utf-8") as f:
                existing = f.read().strip()
            if existing:
                return existing[:128]
        except OSError:
            pass

    dev_id = f"dev_{uuid.uuid4().hex}"
    state_dir = os.path.dirname(dev_path)
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="device_id_", suffix=".tmp", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dev_id + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dev_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    return dev_id


def _json_utf8_len(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_record(rec):
    hashable = {
        "kind": rec["kind"],
        "local_id": rec["local_id"],
        "parent_local_id": rec["parent_local_id"],
        "title": rec["title"],
        "data": rec["data"],
        "local_updated_at": rec["local_updated_at"],
    }
    return hashlib.sha256(_canonical_bytes(hashable)).hexdigest()


def _trunc_str(value, max_len):
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _first_chars(value, max_len=80):
    if value is None:
        return None
    single = " ".join(str(value).splitlines()).strip()
    if not single:
        return None
    return single[:max_len]


def _brief_title(kind, inputs):
    base = str(kind or "brief").strip() or "brief"
    first_line = ""
    if isinstance(inputs, dict):
        priority = ("topic", "goal", "brief", "title", "prompt", "product", "query")
        ordered_vals = [inputs.get(k) for k in priority if k in inputs] + [
            v for k, v in inputs.items() if k not in priority
        ]
        for val in ordered_vals:
            if isinstance(val, str) and val.strip():
                first_line = val.strip().splitlines()[0].strip()
                break
    elif isinstance(inputs, str) and inputs.strip():
        first_line = inputs.strip().splitlines()[0].strip()

    if first_line:
        return _trunc_str(f"{base}: {first_line}", 300)
    return _trunc_str(base, 300)


def _extract_text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                txt = part.get("text")
                if isinstance(txt, str) and txt:
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _extract_session_messages(raw_messages):
    cleaned = []
    for msg in raw_messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            text = _extract_text_content(msg.get("content"))
            if text:
                cleaned.append({"role": "user", "text": text})
        elif role == "assistant":
            text = _extract_text_content(msg.get("content"))
            tool_names = []
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") or tc.get("name")
                if name:
                    tool_names.append(str(name))
            entry = {"role": "assistant"}
            if text:
                entry["text"] = text
            if tool_names:
                entry["note"] = f"used tool {', '.join(tool_names)}"
            if len(entry) > 1:
                cleaned.append(entry)
    return cleaned


def _find_longest_string(obj):
    best = (0, None, None)

    def _walk(node):
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    if len(v) > best[0]:
                        best = (len(v), node, k)
                elif isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                if isinstance(v, str):
                    if len(v) > best[0]:
                        best = (len(v), node, idx)
                elif isinstance(v, (dict, list)):
                    _walk(v)

    _walk(obj)
    return best


def _cap_record_data(kind, data, limit_bytes=MAX_RECORD_DATA_BYTES):
    if _json_utf8_len(data) <= limit_bytes:
        return data

    capped = copy.deepcopy(data)
    if kind == "chat_session" and isinstance(capped.get("messages"), list):
        msgs = capped["messages"]
        while msgs and _json_utf8_len(capped) > limit_bytes:
            msgs.pop(0)
            capped["truncated"] = True
        if _json_utf8_len(capped) <= limit_bytes:
            return capped

    capped["truncated"] = True
    while _json_utf8_len(capped) > limit_bytes:
        length, container, key = _find_longest_string(capped)
        if container is None or length <= 16:
            break
        cur = container[key]
        new_len = max(12, length // 2) if length > 256 else max(8, int(length * 0.7))
        if new_len >= length:
            new_len = max(0, length - 4)
        container[key] = cur[:new_len] + "..."
    return capped


def _make_record(kind, local_id, parent_local_id, title, data, local_updated_at):
    capped_data = _cap_record_data(kind, data)
    return {
        "kind": str(kind),
        "local_id": str(local_id)[:200],
        "parent_local_id": str(parent_local_id)[:200] if parent_local_id is not None else None,
        "title": _trunc_str(title, 300),
        "data": capped_data,
        "local_updated_at": str(local_updated_at),
    }


def build_mirror_records(now_iso=None):
    """Build mirror_records entries from local SQLite tables, enforcing privacy and size caps."""
    fallback_ts = now_iso or _now_iso()
    records = []

    sessions = db.list_all_agent_sessions()
    content_items = db.list_all_content_items()
    confidential_sids, confidential_cids, checker = confidential_scope(
        sessions=sessions,
        content_items=content_items,
    )
    for s in sessions:
        if s["id"] in confidential_sids:
            s["confidential"] = True

    for s in sessions:
        updated_at = s.get("updated_at") or s.get("created_at") or fallback_ts
        if s.get("confidential"):
            data = {
                "confidential": True,
                "agent_id": s.get("agent_id"),
                "status": s.get("status"),
                "updated_at": s.get("updated_at"),
            }
            title = "Confidential chat"
        else:
            data = {
                "agent_id": s.get("agent_id"),
                "status": s.get("status"),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "messages": _extract_session_messages(s.get("messages")),
            }
            title = s.get("title")
        records.append(
            _make_record(
                "chat_session",
                s["id"],
                s.get("parent_session_id") or None,
                title,
                data,
                updated_at,
            )
        )

    for m in db.load_messages(limit=0):
        ts_val = m.get("ts") or m.get("timestamp") or fallback_ts
        text = m.get("text") or ""
        records.append(
            _make_record(
                "team_message",
                m["id"],
                None,
                _first_chars(text, 80),
                {
                    "sender": m.get("sender"),
                    "sender_name": m.get("sender_name"),
                    "text": text,
                    "mentions": m.get("mentions") or [],
                    "ts": ts_val,
                },
                ts_val,
            )
        )

    for turn in db.list_all_chat_turns():
        ts_val = turn.get("ts") or fallback_ts
        records.append(
            _make_record(
                "chat_turn",
                str(turn["rowid"]),
                turn.get("agent_id"),
                None,
                {
                    "sender": turn.get("sender"),
                    "name": turn.get("name"),
                    "text": turn.get("text") or "",
                    "ts": turn.get("ts"),
                },
                ts_val,
            )
        )

    for item in content_items:
        cid = item["id"]
        is_conf = cid in confidential_cids or (item.get("session_id") in confidential_sids)
        if is_conf:
            confidential_cids.add(cid)
            records.append(
                _make_record(
                    "content_item",
                    cid,
                    item.get("session_id") or None,
                    "Confidential item",
                    {
                        "confidential": True,
                        "type": item.get("type"),
                        "platform": item.get("platform"),
                        "status": item.get("status"),
                        "updated_at": item.get("updated_at"),
                    },
                    item.get("updated_at") or item.get("created_at") or fallback_ts,
                )
            )
        else:
            records.append(
                _make_record(
                    "content_item",
                    cid,
                    item.get("session_id") or None,
                    item.get("title"),
                    {
                        "type": item.get("type"),
                        "platform": item.get("platform"),
                        "status": item.get("status"),
                        "current_version": item.get("current_version"),
                        "posted_url": item.get("posted_url"),
                        "posted_at": item.get("posted_at"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                    },
                    item.get("updated_at") or item.get("created_at") or fallback_ts,
                )
            )

        if (item.get("posted_url") or "").strip():
            posted_ts = item.get("posted_at") or item.get("updated_at") or item.get("created_at") or fallback_ts
            if is_conf:
                link_title = "Confidential item"
                link_data = {
                    "confidential": True,
                    "platform": item.get("platform"),
                    "posted_at": item.get("posted_at"),
                }
            else:
                link_title = item.get("title")
                link_data = {
                    "platform": item.get("platform"),
                    "posted_url": item.get("posted_url"),
                    "posted_at": item.get("posted_at"),
                }
            records.append(_make_record("posted_link", cid, cid, link_title, link_data, posted_ts))

    for ver in db.list_all_content_versions():
        cid = ver["content_id"]
        if cid in confidential_cids:
            title = "Confidential item"
            data = {
                "confidential": True,
                "version": ver.get("version"),
                "created_at": ver.get("created_at"),
            }
        else:
            title = f"v{ver['version']}"
            data = {
                "version": ver.get("version"),
                "body": ver.get("body") or "",
                "notes": ver.get("notes"),
                "created_by": ver.get("created_by"),
                "created_at": ver.get("created_at"),
            }
        records.append(
            _make_record(
                "content_version",
                ver["id"],
                cid,
                title,
                data,
                ver.get("created_at") or fallback_ts,
            )
        )

    for note in db.list_all_research_notes():
        records.append(
            _make_record(
                "research_note",
                note["id"],
                None,
                note.get("title"),
                {
                    "body": note.get("body") or "",
                    "url": note.get("url"),
                    "source": note.get("source"),
                    "agent_id": note.get("agent_id"),
                    "created_at": note.get("created_at"),
                },
                note.get("created_at") or fallback_ts,
            )
        )

    for cmt in db.list_all_review_comments():
        cid = cmt["content_id"]
        ts_val = cmt.get("updated_at") or cmt.get("created_at") or fallback_ts
        if cid in confidential_cids:
            title = "Confidential item"
            data = {
                "confidential": True,
                "version": cmt.get("version"),
                "status": cmt.get("status"),
                "updated_at": cmt.get("updated_at"),
            }
        else:
            title = _first_chars(cmt.get("body"), 80)
            data = {
                "version": cmt.get("version"),
                "kind": cmt.get("kind"),
                "body": cmt.get("body") or "",
                "author": cmt.get("author"),
                "status": cmt.get("status"),
                "parent_id": cmt.get("parent_id"),
                "created_at": cmt.get("created_at"),
                "updated_at": cmt.get("updated_at"),
            }
        records.append(_make_record("review_comment", cmt["id"], cid, title, data, ts_val))

    for met in db.list_all_metrics():
        cid = met.get("content_id")
        ts_val = met.get("captured_at") or met.get("created_at") or fallback_ts
        if cid and cid in confidential_cids:
            title = "Confidential item"
            data = {
                "confidential": True,
                "platform": met.get("platform"),
                "captured_at": met.get("captured_at"),
            }
        else:
            title = met.get("platform")
            data = {
                "metrics": met.get("metrics") or {},
                "source": met.get("source"),
                "captured_at": met.get("captured_at"),
            }
        records.append(_make_record("metric", met["id"], cid or None, title, data, ts_val))

    for wf in db.list_all_workflow_runs():
        records.append(
            _make_record(
                "brief",
                wf["id"],
                None,
                _brief_title(wf.get("kind"), wf.get("inputs")),
                {
                    "kind": wf.get("kind"),
                    "status": wf.get("status"),
                    "current_step": wf.get("current_step"),
                    "inputs": wf.get("inputs") or {},
                    "created_at": wf.get("created_at"),
                    "updated_at": wf.get("updated_at"),
                },
                wf.get("updated_at") or wf.get("created_at") or fallback_ts,
            )
        )

    for job in db.list_all_jobs():
        ts_val = job.get("finished_at") or job.get("started_at") or job.get("created_at") or fallback_ts
        records.append(
            _make_record(
                "task",
                job["id"],
                None,
                job.get("name"),
                {
                    "status": job.get("status"),
                    "progress": job.get("progress"),
                    "message": job.get("message"),
                    "error": job.get("error"),
                    "created_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                },
                ts_val,
            )
        )

    for rem in db.list_all_reminders():
        cid = rem.get("content_id")
        ts_val = rem.get("done_at") or rem.get("snoozed_until") or rem.get("due_at") or rem.get("created_at") or fallback_ts
        if cid and cid in confidential_cids:
            title = "Confidential item"
            data = {
                "kind": "reminder",
                "confidential": True,
                "reminder_kind": rem.get("kind"),
                "due_at": rem.get("due_at"),
                "done_at": rem.get("done_at"),
            }
        else:
            title = rem.get("title")
            data = {
                "kind": "reminder",
                "reminder_kind": rem.get("kind"),
                "note": rem.get("note") or "",
                "due_at": rem.get("due_at"),
                "content_id": cid,
                "created_by": rem.get("created_by"),
                "created_at": rem.get("created_at"),
                "done_at": rem.get("done_at"),
                "snoozed_until": rem.get("snoozed_until"),
            }
        records.append(_make_record("task", rem["id"], cid or None, title, data, ts_val))

    import weekly_summary
    for s in db.list_all_weekly_summaries():
        title = weekly_summary.format_week_title(s["week_start"])
        data = {
            "kind": "weekly_summary",
            "week_start": s.get("week_start"),
            "status": s.get("status"),
            "highlights": s.get("highlights") or {},
            "narrative": s.get("narrative") or "",
            "created_at": s.get("created_at"),
        }
        records.append(
            _make_record(
                "brief",
                s["id"],
                None,
                title,
                data,
                s.get("created_at") or fallback_ts,
            )
        )

    for lr in db.list_all_learnings():
        ts_val = lr.get("decided_at") or lr.get("created_at") or fallback_ts
        data = {
            "kind": "learning",
            "status": lr.get("status"),
            "applies_to": lr.get("applies_to") or [],
            "evidence": lr.get("evidence") or [],
            "source_summary_id": lr.get("source_summary_id"),
            "created_at": lr.get("created_at"),
            "decided_at": lr.get("decided_at"),
        }
        records.append(
            _make_record(
                "research_note",
                lr["id"],
                lr.get("source_summary_id") or None,
                lr.get("text") or "",
                data,
                ts_val,
            )
        )

    return [_stub_if_mentions_confidential(rec, checker) for rec in records]


def confidential_scope(sessions=None, content_items=None, checker=None):
    """Return (confidential_session_ids, confidential_content_ids, checker).

    Raises RuntimeError if product rules cannot be read.
    """
    if checker is None:
        checker = confidential_checker()
    if sessions is None:
        sessions = db.list_all_agent_sessions()
    inherited = _confidential_session_ids(sessions)
    confidential_sids = {
        s["id"] for s in sessions if s.get("confidential") or s["id"] in inherited
    }
    confidential_cids = set()
    for sid in confidential_sids:
        confidential_cids.update(db.list_content_ids_for_session(sid))
    if content_items is None:
        content_items = db.list_all_content_items()
    for item in content_items:
        cid = item["id"]
        if cid in confidential_cids or item.get("session_id") in confidential_sids:
            confidential_cids.add(cid)
        elif _mentions(checker, item.get("title") or "", item.get("product_id") or ""):
            confidential_cids.add(cid)
    return confidential_sids, confidential_cids, checker


def _confidential_session_ids(sessions):
    """Return ids of sessions that are confidential themselves or have a confidential ancestor."""
    parent = {s["id"]: s.get("parent_session_id") for s in sessions}
    flagged = {s["id"] for s in sessions if s.get("confidential")}
    result = set()
    for sid in parent:
        seen, cur = set(), sid
        while cur and cur not in seen:
            if cur in flagged:
                result.add(sid)
                break
            seen.add(cur)
            cur = parent.get(cur)
    return result


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    return cand if os.path.exists(cand) else paths.resource("config", "product_rules.json")


def confidential_checker():
    """Return a function telling whether text names a confidential product.

    Raises if the product rules cannot be read, so the sync cycle pauses instead of uploading text.
    """
    try:
        rules = product_rules.load_rules(_rules_path())
    except Exception as exc:
        raise RuntimeError("Sync paused: product rules could not be read.") from exc
    names = [p.get("name") or p.get("id") for p in rules.get("products", []) if p.get("confidential")]
    patterns = [product_rules._word_pattern(n) for n in names if n]
    return lambda text: product_rules.any_match(patterns, text)


def _mentions(checker, *parts):
    return checker("\n".join(json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x for x in parts))


def _stub_if_mentions_confidential(rec, checker):
    """Replace a record whose title or data names a confidential product with a text-free stub."""
    if isinstance(rec.get("data"), dict) and rec["data"].get("confidential"):
        return rec
    if not _mentions(checker, rec.get("title") or "", rec.get("data")):
        return rec
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    stub = {"confidential": True}
    if rec.get("kind") == "task" and data.get("kind") == "reminder":
        stub["kind"] = "reminder"
    elif rec.get("kind") == "brief" and data.get("kind") == "weekly_summary":
        stub["kind"] = "weekly_summary"
    elif rec.get("kind") == "research_note" and data.get("kind") == "learning":
        stub["kind"] = "learning"
    for key in ("agent_id", "status", "platform", "version", "updated_at", "created_at", "ts"):
        if key in data and not isinstance(data[key], (dict, list)):
            stub[key] = data[key]
    title = "Confidential chat" if rec["kind"] == "chat_session" else "Confidential item"
    return dict(rec, title=title, data=stub)


def _chunk_payloads(items, extract_payload, max_count=MAX_BATCH_RECORDS, max_bytes=MAX_BATCH_BYTES):
    batch = []
    for item in items:
        payload = extract_payload(item)
        if not batch:
            batch.append(item)
            continue
        if len(batch) >= max_count:
            yield batch
            batch = [item]
            continue
        candidate = [extract_payload(x) for x in batch] + [payload]
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > max_bytes:
            yield batch
            batch = [item]
        else:
            batch.append(item)
    if batch:
        yield batch


def _outbox_summary(kind, payload):
    if isinstance(payload, dict):
        for key in ("title", "slug", "client_name", "business_name", "share_id", "id"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return f"{kind}: {val.strip()}"[:400]
    return str(kind)[:400]


def _cap_outbox_data(event_id, kind, payload):
    data = payload if isinstance(payload, dict) else {"value": payload}
    if _json_utf8_len(data) <= MAX_OUTBOX_DATA_BYTES:
        return data
    return {
        "truncated": True,
        "event_id": str(event_id),
        "summary": _outbox_summary(kind, payload),
    }


def _compute_pending_items(user_id):
    if not user_id:
        return [], {}
    state = db.get_cloud_sync_state(user_id)
    now_iso = _now_iso()
    records = build_mirror_records(now_iso=now_iso)

    pending = []
    current_keys = set()
    for rec in records:
        key = (rec["kind"], rec["local_id"])
        current_keys.add(key)
        rec_hash = _hash_record(rec)
        prev = state.get(key)
        if prev is not None and prev["hash"] == rec_hash:
            continue
        payload = {
            "owner_id": user_id,
            "kind": rec["kind"],
            "local_id": rec["local_id"],
            "parent_local_id": rec["parent_local_id"],
            "title": rec["title"],
            "data": rec["data"],
            "local_updated_at": rec["local_updated_at"],
            "deleted_at": None,
        }
        pending.append((rec["kind"], rec["local_id"], rec_hash, payload))

    for (kind, local_id), prev in state.items():
        if kind.startswith("_") or (kind, local_id) in current_keys:
            continue
        if prev["hash"] == DELETED_HASH:
            continue
        if prev.get("error") and prev["hash"].startswith(DELETED_HASH + ":"):
            continue
        del_hash = DELETED_HASH
        payload = {
            "owner_id": user_id,
            "kind": kind,
            "local_id": local_id,
            "parent_local_id": None,
            "title": None,
            "data": {},
            "local_updated_at": now_iso,
            "deleted_at": now_iso,
        }
        pending.append((kind, local_id, del_hash, payload))

    return pending, state


def _is_signed_out_or_stale(expected_gen):
    with _lock:
        if _auth_generation != expected_gen:
            return True
    return team_auth.get_session_info() is None


def _rest_with_auth_retry(method, path, body=None, headers=None, timeout=team_auth.REQUEST_TIMEOUT_S):
    try:
        return team_auth.rest(method, path, body=body, headers=headers, timeout=timeout)
    except team_auth.TeamAuthError as exc:
        if exc.status != 401:
            raise
        if exc.code in ("not_signed_in", "session_expired", "not_configured"):
            raise
        team_auth.refresh_access_token()
        return team_auth.rest(method, path, body=body, headers=headers, timeout=timeout)


class _SignedOutAbort(Exception):
    pass


class _TransientError(Exception):
    def __init__(self, message, is_network=False):
        super().__init__(message)
        self.message = message
        self.is_network = is_network


def _handle_rest_error(exc):
    if exc.status == 401:
        raise _SignedOutAbort() from exc
    if exc.status in (0, 502, 503, 504) or (isinstance(exc.status, int) and exc.status >= 500):
        msg = "Waiting for connection" if exc.status == 0 else exc.message
        raise _TransientError(msg, is_network=(exc.status == 0)) from exc
    raise exc


def _upload_mirror_batches(user_id, pending, expected_gen):
    now_iso = _now_iso()
    for batch in _chunk_payloads(pending, lambda item: item[3]):
        if _is_signed_out_or_stale(expected_gen):
            raise _SignedOutAbort()

        body = [item[3] for item in batch]
        try:
            _rest_with_auth_retry(
                "POST",
                "mirror_records?on_conflict=owner_id,kind,local_id",
                body=body,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            db.upsert_cloud_sync_state(
                user_id,
                [(k, lid, h, now_iso, None) for k, lid, h, _ in batch],
            )
        except team_auth.TeamAuthError as exc:
            if exc.status in (400, 409, 422):
                if len(batch) > 1:
                    for item in batch:
                        if _is_signed_out_or_stale(expected_gen):
                            raise _SignedOutAbort()
                        k, lid, h, rec_payload = item
                        try:
                            _rest_with_auth_retry(
                                "POST",
                                "mirror_records?on_conflict=owner_id,kind,local_id",
                                body=[rec_payload],
                                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                            )
                            db.upsert_cloud_sync_state(user_id, [(k, lid, h, now_iso, None)])
                        except team_auth.TeamAuthError as single_exc:
                            if single_exc.status in (400, 409, 422):
                                _log_warning(f"mirror_records rejected {k}:{lid} ({single_exc.status}): {single_exc.message}")
                                err_hash = f"{DELETED_HASH}:{h}" if h == DELETED_HASH else h
                                db.upsert_cloud_sync_state(user_id, [(k, lid, err_hash, now_iso, single_exc.message)])
                            else:
                                _handle_rest_error(single_exc)
                else:
                    k, lid, h, _ = batch[0]
                    _log_warning(f"mirror_records rejected {k}:{lid} ({exc.status}): {exc.message}")
                    err_hash = f"{DELETED_HASH}:{h}" if h == DELETED_HASH else h
                    db.upsert_cloud_sync_state(user_id, [(k, lid, err_hash, now_iso, exc.message)])
            else:
                _handle_rest_error(exc)


def _upload_activity_batches(user_id, expected_gen):
    checker = confidential_checker()
    cursor_raw = db.get_cloud_sync_value(user_id, ACTIVITY_CURSOR_KIND, ACTIVITY_CURSOR_ID)
    last_rowid = int(cursor_raw or 0)

    while True:
        rows = db.list_activity_since(last_rowid=last_rowid, limit=500)
        if not rows:
            break

        mapped = []
        for r in rows:
            summary = f"{r['actor']} -> {r['target']}: {r['task']}"[:500]
            data = _cap_record_data(
                "activity",
                {
                    "actor": r["actor"],
                    "target": r["target"],
                    "task": r["task"],
                    "file": r["file"],
                },
                limit_bytes=MAX_OUTBOX_DATA_BYTES,
            )
            if _mentions(checker, summary, data):
                summary, data = "Confidential activity", {"confidential": True}
            payload = {
                "owner_id": user_id,
                "ts": r["ts"],
                "kind": "activity",
                "agent_id": r["actor"] or None,
                "summary": summary,
                "data": data,
            }
            mapped.append((r["rowid"], payload))

        for batch in _chunk_payloads(mapped, lambda x: x[1]):
            if _is_signed_out_or_stale(expected_gen):
                raise _SignedOutAbort()
            body = [x[1] for x in batch]
            try:
                _rest_with_auth_retry(
                    "POST",
                    "activity_events",
                    body=body,
                    headers={"Prefer": "return=minimal"},
                )
                last_rowid = batch[-1][0]
                db.upsert_cloud_sync_state(
                    user_id,
                    [(ACTIVITY_CURSOR_KIND, ACTIVITY_CURSOR_ID, str(last_rowid), _now_iso(), None)],
                )
            except team_auth.TeamAuthError as exc:
                if exc.status in (400, 409, 422):
                    if len(batch) > 1:
                        for rowid, single_payload in batch:
                            if _is_signed_out_or_stale(expected_gen):
                                raise _SignedOutAbort()
                            try:
                                _rest_with_auth_retry(
                                    "POST",
                                    "activity_events",
                                    body=[single_payload],
                                    headers={"Prefer": "return=minimal"},
                                )
                            except team_auth.TeamAuthError as single_exc:
                                if single_exc.status in (400, 409, 422):
                                    _log_warning(f"activity row {rowid} rejected ({single_exc.status}): {single_exc.message}")
                                else:
                                    _handle_rest_error(single_exc)
                            last_rowid = rowid
                            db.upsert_cloud_sync_state(
                                user_id,
                                [(ACTIVITY_CURSOR_KIND, ACTIVITY_CURSOR_ID, str(last_rowid), _now_iso(), None)],
                            )
                    else:
                        rowid = batch[0][0]
                        _log_warning(f"activity row {rowid} rejected ({exc.status}): {exc.message}")
                        last_rowid = rowid
                        db.upsert_cloud_sync_state(
                            user_id,
                            [(ACTIVITY_CURSOR_KIND, ACTIVITY_CURSOR_ID, str(last_rowid), _now_iso(), None)],
                        )
                else:
                    _handle_rest_error(exc)


def _upload_outbox_batches(user_id, expected_gen):
    checker = confidential_checker()
    state = db.get_cloud_sync_state(user_id)
    pending_rows = db.list_pending_sync_outbox(limit=500)
    filtered = [
        r for r in pending_rows
        if not (state.get((OUTBOX_ERROR_KIND, r["id"])) or {}).get("error")
    ]
    if not filtered:
        return

    mapped = []
    for r in filtered:
        kind = f"outbox.{r['kind']}"[:64]
        data = _cap_outbox_data(r["id"], r["kind"], r["payload"])
        summary = _outbox_summary(r["kind"], r["payload"])[:500]
        if _mentions(checker, summary, data):
            summary, data = "Confidential event", {"confidential": True}
        payload = {
            "owner_id": user_id,
            "ts": r["created_at"],
            "kind": kind,
            "agent_id": None,
            "summary": summary,
            "data": data,
        }
        mapped.append((r["id"], payload))

    now_iso = _now_iso()
    for batch in _chunk_payloads(mapped, lambda x: x[1]):
        if _is_signed_out_or_stale(expected_gen):
            raise _SignedOutAbort()
        body = [x[1] for x in batch]
        try:
            _rest_with_auth_retry(
                "POST",
                "activity_events",
                body=body,
                headers={"Prefer": "return=minimal"},
            )
            db.mark_sync_outbox_sent([x[0] for x in batch], sent_at=now_iso)
        except team_auth.TeamAuthError as exc:
            if exc.status in (400, 409, 422):
                if len(batch) > 1:
                    for eid, single_payload in batch:
                        if _is_signed_out_or_stale(expected_gen):
                            raise _SignedOutAbort()
                        try:
                            _rest_with_auth_retry(
                                "POST",
                                "activity_events",
                                body=[single_payload],
                                headers={"Prefer": "return=minimal"},
                            )
                            db.mark_sync_outbox_sent([eid], sent_at=now_iso)
                        except team_auth.TeamAuthError as single_exc:
                            if single_exc.status in (400, 409, 422):
                                _log_warning(f"sync_outbox {eid} rejected ({single_exc.status}): {single_exc.message}")
                                db.upsert_cloud_sync_state(
                                    user_id,
                                    [(OUTBOX_ERROR_KIND, eid, eid, now_iso, single_exc.message)],
                                )
                            else:
                                _handle_rest_error(single_exc)
                else:
                    eid = batch[0][0]
                    _log_warning(f"sync_outbox {eid} rejected ({exc.status}): {exc.message}")
                    db.upsert_cloud_sync_state(
                        user_id,
                        [(OUTBOX_ERROR_KIND, eid, eid, now_iso, exc.message)],
                    )
            else:
                _handle_rest_error(exc)


def _upsert_device_status(user_id, offline=False, timeout=team_auth.REQUEST_TIMEOUT_S):
    device_id = get_device_id()
    if offline:
        status_val = "offline"
        running_agents = []
    else:
        live = db.get_agent_live_statuses()
        running_agents = [aid for aid, st in live.items() if st == "working"]
        has_job = db.has_running_jobs()
        status_val = "working" if (running_agents or has_job) else "online"

    payload = {
        "owner_id": user_id,
        "device_id": device_id,
        "device_kind": "desktop",
        "status": status_val,
        "running_agents": running_agents,
        "app_version": _app_version(),
        "last_seen_at": _now_iso(),
    }
    _rest_with_auth_retry(
        "POST",
        "device_status?on_conflict=owner_id,device_id",
        body=[payload],
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=timeout,
    )


def _set_failure_backoff(err_msg):
    global _failure_count, _last_error, _next_allowed_at
    with _lock:
        _failure_count += 1
        idx = min(_failure_count - 1, len(BACKOFF_STEPS_S) - 1)
        _last_error = str(err_msg)
        _next_allowed_at = time.monotonic() + BACKOFF_STEPS_S[idx]
    try:
        import notifications
        notifications.record_sync_failure(err_msg)
    except Exception:
        pass


def sync_once():
    """Run a single one-way sync cycle if configured, signed in, and active."""
    global _running, _last_sync_at, _last_error, _failure_count, _next_allowed_at

    if not _cycle_lock.acquire(blocking=False):
        return False

    try:
        with _lock:
            expected_gen = _auth_generation
            _running = True

        cfg = team_auth.get_config()
        sess = team_auth.get_session_info()
        if not cfg.get("configured") or not sess:
            _clear_team_repos()
            return False

        try:
            acct = team_auth.current_account()
        except team_auth.TeamAuthError as exc:
            if exc.status == 0:
                _set_failure_backoff("Waiting for connection")
            elif exc.status and exc.status >= 500:
                _set_failure_backoff(exc.message)
            elif exc.status == 401:
                return False
            else:
                _set_failure_backoff(exc.message)
            return False

        if acct.get("signed_in") and acct.get("deactivated"):
            _clear_team_repos()
        if not acct.get("signed_in") or acct.get("deactivated"):
            return False

        user = acct.get("user") or {}
        user_id = str(user.get("id") or sess.get("user_id") or "").strip()
        if not user_id:
            return False
        user_role = str(user.get("role") or "member").strip().lower()

        try:
            confidential_checker()
            _pull_team_repos(user_id, user_role, expected_gen)
            import team_knowledge
            try:
                team_knowledge.sync(
                    user_id,
                    user_role,
                    is_signed_out=lambda: _is_signed_out_or_stale(expected_gen),
                )
            except team_knowledge.SignedOutAbort as exc:
                raise _SignedOutAbort() from exc
            except team_auth.TeamAuthError as exc:
                _handle_rest_error(exc)

            _apply_phone_actions(user_id, expected_gen)

            pending, _ = _compute_pending_items(user_id)
            if pending:
                _upload_mirror_batches(user_id, pending, expected_gen)

            _upload_activity_batches(user_id, expected_gen)
            _upload_outbox_batches(user_id, expected_gen)

            import media_previews
            try:
                media_previews.sync_previews(
                    user_id,
                    is_signed_out=lambda: _is_signed_out_or_stale(expected_gen),
                )
            except media_previews.SignedOutAbort as exc:
                raise _SignedOutAbort() from exc
            except Exception as exc:
                _log_warning(f"media previews sync error: {exc}")

            import pitch_links
            pitch_links.sync_live_links(
                user_id,
                is_signed_out=lambda: _is_signed_out_or_stale(expected_gen),
            )
            pitch_links.poll_quote_requests(
                user_id,
                is_signed_out=lambda: _is_signed_out_or_stale(expected_gen),
            )

            if _is_signed_out_or_stale(expected_gen):
                raise _SignedOutAbort()
            try:
                _upsert_device_status(user_id, offline=False)
            except team_auth.TeamAuthError as exc:
                if exc.status not in (400, 409, 422):
                    _handle_rest_error(exc)
                _log_warning(f"device_status rejected ({exc.status}): {exc.message}")

            import notifications
            notifications.record_sync_success()
            try:
                notifications.run_sync_checks(
                    user_id,
                    user_role,
                    is_signed_out=lambda: _is_signed_out_or_stale(expected_gen),
                )
            except team_auth.TeamAuthError as exc:
                if exc.status == 401:
                    raise _SignedOutAbort() from exc
            except _SignedOutAbort:
                raise
            except Exception as exc:
                _log_warning(f"notifications sync check error: {exc}")

            with _lock:
                _failure_count = 0
                _last_error = None
                _last_sync_at = _now_iso()
                _next_allowed_at = 0.0
            return True
        except _SignedOutAbort:
            return False
        except _TransientError as exc:
            _log_warning(f"sync paused: {exc.message}")
            _set_failure_backoff(exc.message)
            return False
        except Exception as exc:
            _log_warning(f"unexpected sync error: {exc}")
            _set_failure_backoff(str(exc))
            return False
    finally:
        with _lock:
            _running = False
        _cycle_lock.release()


def _clear_team_repos():
    """Drop the cached global repos (signed out or deactivated). Never raises."""
    try:
        import team_repos
        team_repos.clear()
    except Exception as exc:
        _log_warning(f"team repos clear error: {exc}")


def _pull_team_repos(user_id, role, expected_gen):
    """Every member gets the admin's global repos on the normal sync cycle. A failed pull keeps the last list."""
    import team_repos
    try:
        team_repos.sync(user_id, role, is_signed_out=lambda: _is_signed_out_or_stale(expected_gen))
    except team_repos.SignedOutAbort as exc:
        raise _SignedOutAbort() from exc
    except team_repos.TeamReposError as exc:
        if exc.status == 401:
            raise _SignedOutAbort() from exc
        _log_warning(f"team repos pull failed: {exc.message}")


def _apply_phone_actions(user_id, expected_gen):
    """Apply what the user queued from the phone, before the mirror upload so the result shows in the same cycle.

    A failed pull or a bug in one action never stops the rest of the sync; sign-out always does.
    """
    import phone_queue
    try:
        phone_queue.sync(user_id, is_signed_out=lambda: _is_signed_out_or_stale(expected_gen))
    except phone_queue.SignedOutAbort as exc:
        raise _SignedOutAbort() from exc
    except phone_queue.PhoneQueueError as exc:
        _log_warning(f"phone actions: {exc.message}")
    except Exception as exc:
        _log_warning(f"phone actions error: {exc}")


def notify_signed_out():
    """Immediately halt active sync state when the user signs out."""
    global _auth_generation, _last_error, _last_sync_at, _failure_count, _next_allowed_at
    with _lock:
        _auth_generation += 1
        _last_error = None
        _last_sync_at = None
        _failure_count = 0
        _next_allowed_at = 0.0
    try:
        import media_previews
        media_previews.notify_signed_out()
    except Exception:
        pass
    try:
        import team_knowledge
        team_knowledge.notify_signed_out()
    except Exception:
        pass
    _clear_team_repos()
    try:
        import notifications
        notifications.record_sync_success()
    except Exception:
        pass
    _wake_event.set()


def request_sync(immediate=False):
    """Request a background sync cycle (debounced by 5s unless immediate=True)."""
    global _pending_debounce_until, _immediate_requested, _next_allowed_at
    if not is_enabled():
        return
    with _lock:
        if immediate:
            _immediate_requested = True
            _pending_debounce_until = 0.0
            _next_allowed_at = 0.0
        elif _pending_debounce_until <= 0.0:
            _pending_debounce_until = time.monotonic() + DEBOUNCE_S
        thread_alive = _thread is not None and _thread.is_alive()
    _wake_event.set()
    if immediate and not thread_alive:
        threading.Thread(target=sync_once, name="magnm-cloud-sync-once", daemon=True).start()


def status():
    """Return current cloud sync status dict for UI and API consumers."""
    enabled = is_enabled()
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info() if cfg.get("configured") else None
    signed_in = bool(cfg.get("configured") and sess and sess.get("user_id"))

    with _lock:
        running = _running
        last_sync_at = _last_sync_at
        last_error = _last_error

    pending_count = 0
    synced_count = 0
    if signed_in:
        uid = sess["user_id"]
        try:
            pending_mirror, state = _compute_pending_items(uid)
            synced_count = sum(
                1
                for (k, _), row in state.items()
                if not k.startswith("_") and row.get("hash") != DELETED_HASH and not row.get("error")
            )
            cursor_raw = state.get((ACTIVITY_CURSOR_KIND, ACTIVITY_CURSOR_ID), {}).get("hash")
            act_pending = db.count_activity_since(int(cursor_raw or 0))
            outbox_pending = db.count_pending_sync_outbox()
            import pitch_links
            links_pending = pitch_links.count_pending_republishes()
            pending_count = len(pending_mirror) + act_pending + outbox_pending + links_pending
        except Exception:
            pass

    return {
        "enabled": enabled,
        "signed_in": signed_in,
        "last_sync_at": last_sync_at,
        "last_error": last_error,
        "pending": pending_count,
        "synced_count": synced_count,
        "running": running,
    }


def _loop(interval_s):
    global _pending_debounce_until, _immediate_requested
    next_periodic = time.monotonic()
    while not _stop_event.is_set():
        now = time.monotonic()
        with _lock:
            imm = _immediate_requested
            debounce_at = _pending_debounce_until
            allowed_at = _next_allowed_at

        due_at = next_periodic
        if imm:
            due_at = now
        else:
            if debounce_at > 0.0 and debounce_at < due_at:
                due_at = debounce_at
            if allowed_at > due_at:
                due_at = allowed_at

        wait_s = max(0.0, due_at - now)
        if wait_s > 0.0:
            _wake_event.wait(timeout=wait_s)
            _wake_event.clear()
            if _stop_event.is_set():
                break
            now = time.monotonic()
            with _lock:
                imm = _immediate_requested
                debounce_at = _pending_debounce_until
                allowed_at = _next_allowed_at
            if not imm:
                if debounce_at > 0.0 and now < debounce_at:
                    continue
                if now < next_periodic and (debounce_at <= 0.0 or now < debounce_at):
                    continue
                if now < allowed_at:
                    continue

        with _lock:
            _immediate_requested = False
            _pending_debounce_until = 0.0

        try:
            sync_once()
        except Exception as exc:
            _log_warning(f"sync loop error: {exc}")

        next_periodic = time.monotonic() + interval_s


def start(interval_s=SYNC_INTERVAL_S):
    """Idempotently start the background cloud sync daemon thread."""
    global _thread
    if not is_enabled():
        return None
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _stop_event.clear()
        _wake_event.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(max(1.0, float(interval_s)),),
            name="magnm-cloud-sync",
            daemon=True,
        )
        _thread.start()
        return _thread


def _send_offline_best_effort(timeout=SHUTDOWN_OFFLINE_TIMEOUT_S):
    try:
        cfg = team_auth.get_config()
        sess = team_auth.get_session_info()
        if not cfg.get("configured") or not sess or not sess.get("user_id"):
            return
        _upsert_device_status(sess["user_id"], offline=True, timeout=timeout)
    except Exception:
        pass


def stop(timeout=1.0):
    """Signal cloud sync to stop and best-effort mark this device offline."""
    global _thread
    with _lock:
        t = _thread
        _stop_event.set()
        _wake_event.set()
    if t is not None and t.is_alive():
        t.join(timeout=max(0.1, float(timeout) * 0.5))
    with _lock:
        _thread = None
    _send_offline_best_effort(timeout=min(SHUTDOWN_OFFLINE_TIMEOUT_S, float(timeout)))
