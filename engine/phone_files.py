#!/usr/bin/env python3
"""Get on phone: full-quality copies of finished draft files, kept in a private bucket for 14 days.

On each sync cycle this laptop uploads a copy of every image, video and PDF attached to a finished (ready or
posted) draft that is not confidential, so the phone can fetch it when the laptop is away. The local file is only ever
read: never moved, changed or deleted. A copy is removed after 14 days, when its draft becomes confidential or is
deleted, when the file is no longer attached, or when the owner asks.

Rules that keep it safe:
- The same confidential decision as everywhere else (phone_server's guard, confidential_guard and the preview
  checks); if the rules cannot be read, nothing is copied and nothing is deleted.
- The same safe-file checks as the phone file route and media previews: the path must stay inside the data folder,
  never under state/, .git or an .env file, and the file must really be what its extension says (first bytes).
- At most 50 MB per file, a few files per cycle, and the 1 GB cloud budget: when it is full the oldest drafts are skipped
  and the status says so, nothing fails.
- Everything that comes back from the cloud is untrusted: only the signed-in user's rows are used, and only object
  names of the exact form <user id>/<64 hex>.<extension> are ever passed to storage.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import confidential_guard
import db
import file_serving
import media_previews
import phone_server
import team_auth

BUCKET = "phone-files"
MAX_FILE_BYTES = 50 * 1024 * 1024
COPY_DAYS = 14
MAX_FILES_PER_CYCLE = 3
MAX_BYTES_PER_CYCLE = 100 * 1024 * 1024
MAX_FAILED_ATTEMPTS = 3
BUDGET_BYTES = media_previews.STORAGE_LIMIT_BYTES
MAX_PATH_CHARS = 500
MAX_NAME_CHARS = 200
MAX_ROWS = 1000
FINISHED_STATUSES = ("ready", "posted")
DONE_KIND = "_phone_file_done"
FAIL_KIND = "_phone_file_fail"
# The object name this laptop finished uploading for a file, so a row left by a crash before its upload is healed.
UPLOADED_KIND = "_phone_file_uploaded"
BACKOFF_STEPS_S = (60.0, 120.0, 300.0, 600.0)
PDF_SCAN_BYTES = 256 * 1024

# extension -> (kind, content type, extension used in the object name)
_TYPES = {
    ".png": ("image", "image/png", "png"),
    ".jpg": ("image", "image/jpeg", "jpg"),
    ".jpeg": ("image", "image/jpeg", "jpg"),
    ".webp": ("image", "image/webp", "webp"),
    ".mp4": ("video", "video/mp4", "mp4"),
    ".m4v": ("video", "video/mp4", "mp4"),
    ".webm": ("video", "video/webm", "webm"),
    ".pdf": ("pdf", "application/pdf", "pdf"),
}
_OBJECT_EXT = "png|jpg|jpeg|webp|mp4|webm|pdf"
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SELECT = "id,owner_id,content_id,file_path,storage_path,source_hash,bytes,expires_at"

_state_lock = threading.Lock()
_next_allowed_at = 0.0
_failure_count = 0
_status = {}


class PhoneFilesError(Exception):
    """A failed cloud call or an unusable answer, with an HTTP-style status."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out or switched account while copies were being made."""


class _Paused(Exception):
    """Product rules or repos could not be read: touch nothing."""


class PhoneFilesStorage(media_previews.SupabaseStorageBackend):
    """The private phone-files bucket (same user-token access as the previews bucket, a different bucket)."""

    BUCKET = BUCKET


def get_backend():
    """The storage backend for the phone-files bucket."""
    if media_previews._read_storage_backend_name() != "supabase":
        raise RuntimeError("Unsupported storage_backend for phone files.")
    return PhoneFilesStorage()


# --- state ---------------------------------------------------------------------------------------------------------

def _new_summary():
    return {
        "uploaded": 0, "removed": 0, "skipped_budget": 0, "errors": 0, "paused": False, "unavailable": False,
        "copies": 0, "bytes": 0,
    }


def _message(summary, last_error):
    if summary.get("unavailable"):
        return "Phone copies need a newer cloud setup."
    if summary.get("paused"):
        return "Phone copies are paused: product rules could not be read."
    if summary.get("skipped_budget"):
        return (
            f"Cloud storage is full: {summary['skipped_budget']} older file(s) were not copied to your phone. "
            "Remove copies or previews to make room."
        )
    return last_error or ""


def _set_status(summary, last_error=None):
    clean = dict(_new_summary(), **{k: v for k, v in (summary or {}).items() if k in _new_summary()})
    clean["last_error"] = last_error
    clean["message"] = _message(clean, last_error)
    with _state_lock:
        _status.clear()
        _status.update(clean)


def status():
    """What the last cycle did, for Settings. No file names, no text from the cloud."""
    with _state_lock:
        current = dict(_status)
    if not current:
        current = dict(_new_summary(), last_error=None, message="")
    return current


def notify_signed_out():
    """Forget status and backoff when the user signs out."""
    global _next_allowed_at, _failure_count
    with _state_lock:
        _status.clear()
        _next_allowed_at = 0.0
        _failure_count = 0


def _reset_backoff():
    global _next_allowed_at, _failure_count
    with _state_lock:
        _next_allowed_at = 0.0
        _failure_count = 0


def _set_backoff():
    global _next_allowed_at, _failure_count
    with _state_lock:
        _failure_count += 1
        _next_allowed_at = time.time() + BACKOFF_STEPS_S[min(_failure_count - 1, len(BACKOFF_STEPS_S) - 1)]


def _clear_backoff():
    _reset_backoff()


# --- cloud ---------------------------------------------------------------------------------------------------------

def _check(user_id, is_signed_out):
    """Stop at once if the user signed out or another account is signed in."""
    if is_signed_out is not None and is_signed_out():
        raise SignedOutAbort()
    sess = team_auth.get_session_info()
    if not sess or str(sess.get("user_id") or "") != user_id:
        raise SignedOutAbort()


def _rest(method, path, body=None, headers=None):
    try:
        try:
            return team_auth.rest(method, path, body=body, headers=headers)
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
                try:
                    team_auth.refresh_access_token()
                except team_auth.TeamAuthError as refresh_exc:
                    raise SignedOutAbort() from refresh_exc
                return team_auth.rest(method, path, body=body, headers=headers)
            raise
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise SignedOutAbort() from exc
        raise PhoneFilesError(exc.message, status=exc.status if exc.status else 0, code=exc.code) from exc


def _table_missing(exc):
    text = exc.message.lower()
    return exc.status == 404 or (exc.status == 400 and "phone_files" in text and "does not exist" in text)


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


def is_own_object(user_id, storage_path):
    """True only for an object name of the exact form <user id>/<64 hex>.<extension>."""
    if not isinstance(storage_path, str) or not user_id:
        return False
    return re.fullmatch(re.escape(str(user_id)) + r"/[0-9a-f]{64}\.(?:" + _OBJECT_EXT + ")", storage_path) is not None


def _clean_row(raw, user_id):
    """Rebuild one cloud row from checked fields, or None if it is not a usable copy of this user."""
    if not isinstance(raw, dict):
        return None
    row_id, owner = raw.get("id"), raw.get("owner_id")
    content_id, file_path = raw.get("content_id"), raw.get("file_path")
    if not isinstance(row_id, str) or not row_id or len(row_id) > 64 or re.search(r"[^A-Za-z0-9_-]", row_id):
        return None
    if not isinstance(owner, str) or owner != user_id:
        return None
    if not isinstance(content_id, str) or not isinstance(file_path, str) or not content_id or not file_path:
        return None
    expires = _parse_time(raw.get("expires_at"))
    source_hash = raw.get("source_hash")
    size = raw.get("bytes")
    return {
        "id": row_id,
        "content_id": content_id,
        "file_path": file_path,
        "storage_path": raw.get("storage_path"),
        "source_hash": source_hash if isinstance(source_hash, str) and _HEX64_RE.fullmatch(source_hash) else "",
        "bytes": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
        "expires_at": expires,
    }


def _list_rows(user_id):
    """The user's own copies. None when an older server has no phone_files table."""
    uid = urllib.parse.quote(user_id, safe="")
    path = f"phone_files?select={_SELECT}&owner_id=eq.{uid}&order=created_at.asc&limit={MAX_ROWS}"
    try:
        rows = _rest("GET", path)
    except PhoneFilesError as exc:
        if _table_missing(exc):
            return None
        raise
    if not isinstance(rows, list):
        raise PhoneFilesError("The phone copy list was not a list.", status=502, code="bad_payload")
    return rows


def _key(content_id, file_path):
    return hashlib.sha256(f"{content_id}\n{file_path}".encode("utf-8")).hexdigest()


def _object_name(user_id, content_id, file_path, source_hash, ext):
    digest = hashlib.sha256(f"{user_id}\n{content_id}\n{file_path}\n{source_hash}".encode("utf-8")).hexdigest()
    return f"{user_id}/{digest}.{ext}"


# --- what may be copied --------------------------------------------------------------------------------------------

def _sniff(ext, head):
    """True if the first bytes match what the extension claims."""
    if ext == ".png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in (".jpg", ".jpeg"):
        return head[:3] == b"\xff\xd8\xff"
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".mp4", ".m4v"):
        return head[4:8] == b"ftyp"
    if ext == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext == ".pdf":
        return b"%PDF-" in head[:1024]
    return False


def _entry_path(entry):
    raw = entry.get("path") if isinstance(entry, dict) else entry
    return raw.strip() if isinstance(raw, str) else ""


def _norm_rel(rel_path):
    return os.path.normpath(rel_path).replace("\\", "/")


def _collect(guard, scope):
    """Eligible files: {(content_id, file_path): candidate}. Raises _Paused if the rules cannot be read."""
    conf_sids, conf_cids, conf_pids, checker, repo_patterns = guard
    found = {}
    for item in db.list_all_content_items():
        if item.get("status") not in FINISHED_STATUSES:
            continue
        content_id = str(item.get("id") or "")
        if not content_id or len(content_id) > 200 or _CONTROL_RE.search(content_id):
            continue
        if content_id in conf_cids or item.get("session_id") in conf_sids:
            continue
        full = db.get_content_item(content_id, include_versions=True)
        if full is None or phone_server._content_item_is_confidential(full, conf_sids, conf_cids, conf_pids, checker, repo_patterns):
            continue
        if scope.item(full):
            continue
        version_text = [full.get("title") or "", full.get("product_id") or "", full.get("notes") or ""]
        for ver in full.get("versions") or []:
            if isinstance(ver, dict):
                version_text += [ver.get("body") or "", ver.get("notes") or ""]
        if media_previews._mentions_confidential(checker, *version_text):
            continue
        for entry in item.get("files") or []:
            rel = _entry_path(entry)
            if not rel or len(rel) > MAX_PATH_CHARS or _CONTROL_RE.search(rel):
                continue
            ext = os.path.splitext(rel)[1].lower()
            if ext not in _TYPES:
                continue
            full_path = media_previews._resolve_safe_path(rel)
            if not full_path:
                continue
            real = file_serving.servable_path(full_path)
            # A link must not change what kind of file it claims to be.
            if not real or os.path.splitext(real)[1].lower() != ext:
                continue
            norm = _norm_rel(rel)
            # The name shown on the phone is the draft's own name for the file, never the target of a link.
            name = os.path.basename(norm)
            if len(norm) > MAX_PATH_CHARS or not name or len(name) > MAX_NAME_CHARS or _CONTROL_RE.search(name):
                continue
            if scope.path(norm) or media_previews._mentions_confidential(checker, norm, entry if isinstance(entry, dict) else ""):
                continue
            try:
                if phone_server._file_is_confidential(real, checker, repo_patterns):
                    continue
                st = os.stat(real)
            except OSError:
                continue
            if st.st_size <= 0 or st.st_size > MAX_FILE_BYTES:
                continue
            key = (content_id, norm)
            if key in found:
                continue
            found[key] = {
                "content_id": content_id,
                "file_path": norm,
                "full_path": real,
                "ext": ext,
                "name": name,
                "size": st.st_size,
                "source_hash": media_previews._compute_source_hash(st.st_size, st.st_mtime_ns, norm),
                "updated_at": str(item.get("updated_at") or ""),
            }
    return found


def _read_checked(cand, guard):
    """Read one candidate once. Returns (bytes, source_hash) or None if it is no longer safe to copy."""
    try:
        with open(cand["full_path"], "rb") as f:
            st = os.fstat(f.fileno())
            if st.st_size <= 0 or st.st_size > MAX_FILE_BYTES:
                return None
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if not data or len(data) > MAX_FILE_BYTES or not _sniff(cand["ext"], data[:1024]):
        return None
    if cand["ext"] == ".pdf":
        # Plain-text parts of a PDF (title, author, uncompressed text) must not name a confidential product either.
        _, _, _, checker, repo_patterns = guard
        for chunk in (data[:PDF_SCAN_BYTES], data[-PDF_SCAN_BYTES:]):
            text = chunk.decode("latin-1")
            if phone_server._text_is_confidential(text, checker, repo_patterns) or confidential_guard.mentions(text):
                return None
    return data, media_previews._compute_source_hash(st.st_size, st.st_mtime_ns, cand["file_path"])


# --- markers kept on this laptop ----------------------------------------------------------------------------------

def _marker(user_id, kind, key):
    return db.get_cloud_sync_value(user_id, kind, key)


def _set_marker(user_id, kind, key, value):
    db.upsert_cloud_sync_state(user_id, [(kind, key, str(value), datetime.now(timezone.utc).isoformat(), None)])


def _failed_attempts(user_id, key, source_hash):
    raw = _marker(user_id, FAIL_KIND, key) or ""
    seen_hash, _, count = raw.partition(":")
    if seen_hash != source_hash:
        return 0
    try:
        return int(count)
    except ValueError:
        return 0


# --- deleting ------------------------------------------------------------------------------------------------------

def _delete_copy(user_id, backend, row, summary, remember=False):
    """Delete a copy: object first, then its row. Returns True if it is gone. Never touches an object that is not ours.

    The storage policies let the owner see an object only while its copy is unexpired, so an expired copy is first
    marked for removal (the server allows that for expired copies only). The laptop never reads an expired object.
    """
    if not is_own_object(user_id, row["storage_path"]):
        summary["errors"] += 1
        return False
    if row["expires_at"] is not None and row["expires_at"] <= datetime.now(timezone.utc):
        try:
            _rest(
                "PATCH",
                f"phone_files?id=eq.{urllib.parse.quote(row['id'], safe='')}&owner_id=eq.{urllib.parse.quote(user_id, safe='')}",
                body={"removing": True}, headers={"Prefer": "return=minimal"},
            )
        except PhoneFilesError:
            summary["errors"] += 1
            return False
    try:
        backend.delete([row["storage_path"]])
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise SignedOutAbort() from exc
        summary["errors"] += 1
        return False
    uid = urllib.parse.quote(user_id, safe="")
    rid = urllib.parse.quote(row["id"], safe="")
    try:
        _rest("DELETE", f"phone_files?owner_id=eq.{uid}&id=in.({rid})", headers={"Prefer": "return=minimal"})
    except PhoneFilesError:
        summary["errors"] += 1
        return False
    if remember and row["source_hash"]:
        _set_marker(user_id, DONE_KIND, _key(row["content_id"], row["file_path"]), row["source_hash"])
    return True


def remove(user_id, content_id=None, file_path=None, remove_all=False, backend=None):
    """The owner's request: remove one copy or all of them. Local files are untouched and the same version of a
    file is not copied again (an edited file is)."""
    uid = str(user_id or "").strip()
    out = {"removed": 0, "errors": 0}
    if not uid:
        return out
    if not remove_all and not (isinstance(content_id, str) and isinstance(file_path, str) and content_id and file_path):
        raise ValueError("Say which copy to remove, or remove all.")
    _check(uid, None)
    rows = _list_rows(uid)
    if rows is None:
        return out
    backend = backend or get_backend()
    summary = _new_summary()
    for raw in rows:
        row = _clean_row(raw, uid)
        if row is None:
            continue
        if not remove_all and (row["content_id"], row["file_path"]) != (content_id, file_path):
            continue
        if _delete_copy(uid, backend, row, summary, remember=True):
            out["removed"] += 1
    out["errors"] = summary["errors"]
    return out


# --- the sync step -------------------------------------------------------------------------------------------------

def _used_bytes():
    """What the whole project has used (previews and copies of every member): the 1 GB budget is one shared pool."""
    used = _rest("POST", "rpc/team_storage_used", body={})
    if isinstance(used, bool) or not isinstance(used, int) or used < 0:
        raise PhoneFilesError("The storage total was not a number.", status=502, code="bad_payload")
    return used


def sync(user_id, is_signed_out=None, backend=None):
    """One sync-cycle step. Returns a small summary dict.

    Raises SignedOutAbort if the user signed out or the account changed, PhoneFilesError if the cloud failed.
    """
    uid = str(user_id or "").strip()
    summary = _new_summary()
    if not uid:
        return summary
    _check(uid, is_signed_out)
    with _state_lock:
        waiting = time.time() < _next_allowed_at
    if waiting:
        return summary
    try:
        raw_rows = _list_rows(uid)
    except PhoneFilesError as exc:
        _set_status(summary, exc.message if exc.status else "Waiting for connection")
        if exc.status == 0 or exc.status >= 500:
            _set_backoff()
        raise
    if raw_rows is None:
        summary["unavailable"] = True
        _set_status(summary)
        return summary
    backend = backend or get_backend()
    last_error = None

    rows = {}
    for raw in raw_rows:
        row = _clean_row(raw, uid)
        if row is not None:
            rows.setdefault((row["content_id"], row["file_path"]), row)

    now = datetime.now(timezone.utc)
    for key, row in list(rows.items()):
        # 14 days: the copy goes, and this version is not copied again.
        if row["expires_at"] is not None and row["expires_at"] <= now:
            _check(uid, is_signed_out)
            if _delete_copy(uid, backend, row, summary, remember=True):
                del rows[key]
                summary["removed"] += 1

    try:
        guard = phone_server._build_confidential_guard()
        scope = confidential_guard.scope()
        if scope.broken:
            raise _Paused()
        candidates = _collect(guard, scope)
    except (phone_server.PhoneApiError, _Paused):
        summary["paused"] = True
        _finish(summary, rows, "Waiting for product rules")
        return summary

    # A copy that is no longer allowed (confidential now, draft deleted or not finished, file gone or off the draft).
    for key, row in list(rows.items()):
        if key not in candidates:
            _check(uid, is_signed_out)
            if _delete_copy(uid, backend, row, summary):
                del rows[key]
                summary["removed"] += 1

    ordered = sorted(candidates.values(), key=lambda c: (c["updated_at"], c["content_id"], c["file_path"]), reverse=True)
    used = None
    budget_full = False
    uploaded_bytes = 0
    for cand in ordered:
        key = _key(cand["content_id"], cand["file_path"])
        row = rows.get((cand["content_id"], cand["file_path"]))
        if row is not None and row["source_hash"] == cand["source_hash"]:
            if not row.get("id") or _marker(uid, UPLOADED_KIND, key) == row["storage_path"]:
                continue
            # The row exists but this laptop never finished its upload (it stopped in between): upload it again.
            _check(uid, is_signed_out)
            if _heal_upload(uid, backend, cand, row, key, summary) == "stop":
                last_error = "Waiting for connection"
                break
            continue
        if _marker(uid, DONE_KIND, key) == cand["source_hash"]:
            continue
        if _failed_attempts(uid, key, cand["source_hash"]) >= MAX_FAILED_ATTEMPTS:
            continue
        if budget_full:
            summary["skipped_budget"] += 1
            continue
        if summary["uploaded"] >= MAX_FILES_PER_CYCLE or uploaded_bytes + cand["size"] > MAX_BYTES_PER_CYCLE:
            break
        _check(uid, is_signed_out)
        if used is None:
            try:
                used = _used_bytes()
            except PhoneFilesError:
                summary["errors"] += 1
                last_error = "Could not read cloud storage use"
                break
        if used + cand["size"] > BUDGET_BYTES:
            budget_full = True
            summary["skipped_budget"] += 1
            continue
        read = _read_checked(cand, guard)
        if read is None:
            continue
        data, source_hash = read
        ext_info = _TYPES[cand["ext"]]
        name = _object_name(uid, cand["content_id"], cand["file_path"], source_hash, ext_info[2])
        outcome = _upload_one(uid, backend, cand, row, data, source_hash, ext_info, name, key, summary)
        if outcome == "stop":
            last_error = "Waiting for connection"
            break
        if outcome == "budget":
            budget_full = True
            continue
        if outcome == "ok":
            used += len(data)
            uploaded_bytes += len(data)
            rows[(cand["content_id"], cand["file_path"])] = {
                "id": "", "content_id": cand["content_id"], "file_path": cand["file_path"],
                "storage_path": name, "source_hash": source_hash, "bytes": len(data), "expires_at": None,
            }
        elif outcome == "failed":
            last_error = "A file could not be copied"
    _finish(summary, rows, last_error)
    return summary


def _finish(summary, rows, last_error):
    summary["copies"] = len(rows)
    summary["bytes"] = sum(r["bytes"] for r in rows.values())
    if last_error is None and not summary["errors"]:
        _clear_backoff()
    _set_status(summary, last_error)


def _upload_one(uid, backend, cand, row, data, source_hash, ext_info, name, key, summary):
    """Add one copy: row first (the server checks the budget and sets the 14 days), then the object. Returns 'ok',
    'budget', 'failed' or 'stop' (transient: end the cycle). A copy being replaced is removed completely first."""
    kind, content_type, _ = ext_info
    # No dates are sent: the server alone sets created_at and the 14-day expiry when a new file is stored.
    body = {
        "content_id": cand["content_id"], "file_path": cand["file_path"], "file_name": cand["name"], "kind": kind,
        "content_type": content_type, "bytes": len(data), "storage_path": name, "source_hash": source_hash,
    }
    if row is not None and row.get("id"):
        # The old object is only visible while its row still points at it, so it goes before the row changes.
        if not _delete_copy(uid, backend, row, summary):
            return "failed"
    try:
        _rest("POST", "phone_files", body=body, headers={"Prefer": "return=minimal"})
    except PhoneFilesError as exc:
        if "budget" in exc.message.lower():
            summary["skipped_budget"] += 1
            return "budget"
        summary["errors"] += 1
        if exc.status == 0 or exc.status >= 500:
            _set_backoff()
            return "stop"
        _count_failure(uid, key, source_hash)
        return "failed"
    try:
        backend.upload(name, data, content_type)
    except team_auth.TeamAuthError as exc:
        _drop_row(uid, name)  # never leave a row that points at a missing object
        if exc.status == 401:
            raise SignedOutAbort() from exc
        summary["errors"] += 1
        if exc.status == 0 or exc.status >= 500 or exc.status in (408, 429):
            _set_backoff()
            return "stop"
        _count_failure(uid, key, source_hash)
        return "failed"
    summary["uploaded"] += 1
    _set_marker(uid, UPLOADED_KIND, key, name)
    return "ok"


def _heal_upload(uid, backend, cand, row, key, summary):
    """Upload the object of a row left behind by a crash. An object that is already there counts as done."""
    read = _read_checked(cand, phone_server._build_confidential_guard())
    if read is None or read[1] != cand["source_hash"]:
        return "skip"  # the file changed meanwhile: the normal path replaces the copy
    data, _ = read
    content_type = _TYPES[cand["ext"]][1]
    try:
        backend.upload(row["storage_path"], data, content_type)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise SignedOutAbort() from exc
        if exc.status in (400, 409) and "exist" in exc.message.lower():
            _set_marker(uid, UPLOADED_KIND, key, row["storage_path"])
            return "ok"
        summary["errors"] += 1
        if exc.status == 0 or exc.status >= 500 or exc.status in (408, 429):
            _set_backoff()
            return "stop"
        _count_failure(uid, key, cand["source_hash"])
        return "failed"
    _set_marker(uid, UPLOADED_KIND, key, row["storage_path"])
    summary["uploaded"] += 1
    return "ok"


def _drop_row(uid, storage_path):
    try:
        _rest(
            "DELETE",
            f"phone_files?owner_id=eq.{urllib.parse.quote(uid, safe='')}&storage_path=eq.{urllib.parse.quote(storage_path, safe='')}",
            headers={"Prefer": "return=minimal"},
        )
    except PhoneFilesError:
        pass


def _count_failure(uid, key, source_hash):
    count = _failed_attempts(uid, key, source_hash) + 1
    _set_marker(uid, FAIL_KIND, key, f"{source_hash}:{count}")
