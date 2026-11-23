#!/usr/bin/env python3
"""Library on the phone.

Three small jobs:

* apply(): a library_upload phone action names a file the phone put in the private phone-uploads bucket. This laptop
  downloads it, checks it, adds it to its Library (assets_manager) and removes the cloud copy so the shared 1 GB
  budget is freed. Uploads are untrusted data: the type is decided by the first bytes (never by the name or the
  content type the phone sent), the size is capped, a PDF with active content is refused, nothing is ever executed,
  the saved name is rebuilt from safe characters, and text naming a confidential product is refused.
* sync_expired(): removes the owner's own uploads that waited past their 14 days (object first, then the row).
* mirror_records() / list_assets() / resolve_asset_file(): the laptop's Library, as names and sizes only (never a
  path), for the cloud mirror and for the live phone route. A confidential asset is only a stub.
"""

import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import assets_manager
import confidential_guard
import db
import file_serving
import media_previews
import paths
import phone_files
import phone_pitch
import phone_server
import team_auth

KIND = "library_upload"
KINDS = (KIND,)
MIRROR_KIND = "asset"
BUCKET = "phone-uploads"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_MIRRORED = 100
MAX_ROWS = 200
CATEGORIES = ("cero", "brand", "screenshot", "recording", "document")
MAX_DESCRIPTION_CHARS = 300
MAX_NAME_STEM = 80
DONE_TAG_PREFIX = "phone-action:"
PDF_SCAN_BYTES = 256 * 1024
# Parts of a PDF that make a viewer run or open something. A PDF is stored as data, but it is not accepted with these.
_PDF_ACTIVE = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/RichMedia", b"/SubmitForm")

CONFIDENTIAL_TEXT_REASON = "Not applied: the text names a confidential product."
NOT_FOUND_REASON = "Not applied: that upload was not found in your cloud (it may have been removed)."
EXPIRED_REASON = "Not applied: the upload expired before the laptop took it. Add it again."
BAD_FILE_REASON = "Not applied: the file is not an image, video or PDF that this laptop accepts."
SIZE_MISMATCH_REASON = "Not applied: the file is not the size the phone said it was."
ACTIVE_PDF_REASON = "Not applied: the PDF has active content, so it was not added."

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_BAD_TEXT_RE = re.compile(r"[\x00-\x1f\x7f]")
_ASSET_ID_RE = re.compile(r"asset_[A-Za-z0-9_]{1,100}")
_SELECT = "id,owner_id,file_name,kind,content_type,bytes,storage_path,expires_at,removing"
_KIND_BY_EXT = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".mp4": "video", ".m4v": "video",
                ".webm": "video", ".pdf": "pdf"}
_CONTENT_TYPES = {"image": ("image/png", "image/jpeg", "image/webp"), "video": ("video/mp4", "video/webm"),
                  "pdf": ("application/pdf",)}


class PhoneLibraryError(Exception):
    """A failed cloud call or an unusable answer, with an HTTP-style status."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out or the session could not be refreshed."""


class PhoneUploadsStorage(media_previews.SupabaseStorageBackend):
    """The private phone-uploads bucket (the owner's own token, the owner's own folder only)."""

    BUCKET = BUCKET

    def download(self, storage_path):
        """The bytes of one object. Only used for a name that passed is_own_object()."""
        cfg = team_auth._require_config()
        rel = media_previews._sanitize_storage_path(storage_path)
        url = f"{cfg['url']}/storage/v1/object/authenticated/{self.BUCKET}/{urllib.parse.quote(rel, safe='/')}"
        return self._raw_request("GET", url)


def get_backend():
    """The storage backend for the phone-uploads bucket."""
    if media_previews._read_storage_backend_name() != "supabase":
        raise RuntimeError("Unsupported storage_backend for phone uploads.")
    return PhoneUploadsStorage()


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
        raise PhoneLibraryError(exc.message, status=exc.status if exc.status else 0, code=exc.code) from exc


def _parse_time(raw):
    text = str(raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def is_own_object(user_id, storage_path):
    """True only for an object name of the exact form <user id>/<64 hex>.<extension>."""
    return bool(user_id) and phone_files.is_own_object(str(user_id), storage_path)


# --- Checking what the phone queued -------------------------------------------------------------------------------

def validate_payload(payload):
    """Return the checked parameters of a library_upload action, or raise Reject."""
    if not isinstance(payload, dict) or not set(payload) <= {"category", "description"}:
        raise phone_pitch.Reject("Not applied: the upload details were malformed.")
    category = payload.get("category")
    if not isinstance(category, str) or category not in CATEGORIES:
        raise phone_pitch.Reject("Not applied: the Library category was not recognised.")
    out = {"category": category, "description": ""}
    if "description" in payload:
        text = payload["description"]
        if not isinstance(text, str) or len(text) > MAX_DESCRIPTION_CHARS or _BAD_TEXT_RE.search(text):
            raise phone_pitch.Reject("Not applied: the description was malformed.")
        out["description"] = text.strip()
    return out


def names_confidential(guard, *texts):
    """True if any text names a confidential product or repo (the phone guard and confidential_guard)."""
    _, _, _, checker, repo_patterns = guard
    for text in texts:
        if text and (phone_server._text_is_confidential(text, checker, repo_patterns) or confidential_guard.mentions(text)):
            return True
    return False


def _clean_upload(raw, user_id, target):
    """Rebuild the cloud row from checked fields, or None if it is not a usable upload of this user."""
    if not isinstance(raw, dict) or raw.get("id") != target or raw.get("owner_id") != user_id:
        return None
    path, name, kind, ctype, size = raw.get("storage_path"), raw.get("file_name"), raw.get("kind"), raw.get("content_type"), raw.get("bytes")
    if not is_own_object(user_id, path) or not isinstance(name, str) or not 1 <= len(name) <= 200:
        return None
    ext = os.path.splitext(path)[1].lower()
    if _KIND_BY_EXT.get(ext) != kind or ctype not in _CONTENT_TYPES.get(kind, ()):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_FILE_BYTES:
        return None
    return {"id": target, "name": name, "ext": ext, "kind": kind, "path": path, "bytes": size,
            "expires_at": _parse_time(raw.get("expires_at"))}


def _safe_name(name, ext):
    """A name rebuilt from safe characters only, with the extension the bytes proved."""
    base = re.split(r"[\\/]", str(name))[-1]
    stem = os.path.splitext(base)[0]
    stem = re.sub(r"[^A-Za-z0-9 ._()-]", "_", _BAD_TEXT_RE.sub("", stem)).strip(" ._")[:MAX_NAME_STEM].strip(" ._")
    return (stem or "upload") + ext


def _check_bytes(data, ext, guard):
    """Raise Reject unless the bytes are what the extension says and are safe to keep."""
    if not data or len(data) > MAX_FILE_BYTES or not phone_files._sniff(ext, data[:1024]):
        raise phone_pitch.Reject(BAD_FILE_REASON)
    if ext == ".pdf":
        if any(marker in data for marker in _PDF_ACTIVE):
            raise phone_pitch.Reject(ACTIVE_PDF_REASON)
        for chunk in (data[:PDF_SCAN_BYTES], data[-PDF_SCAN_BYTES:]):
            if names_confidential(guard, chunk.decode("latin-1")):
                raise phone_pitch.Reject(CONFIDENTIAL_TEXT_REASON)


def _already_added(action_id):
    tag = DONE_TAG_PREFIX + action_id
    return next((a for a in assets_manager.get_assets() if tag in (a.get("tags") or [])), None)


def _remove_cloud_copy(user_id, backend, row):
    """Best effort: the object first, then its row. A failure only means the 14-day expiry removes it instead."""
    try:
        backend.delete([row["path"]])
        uid = urllib.parse.quote(user_id, safe="")
        _rest("DELETE", f"phone_uploads?id=eq.{urllib.parse.quote(row['id'], safe='')}&owner_id=eq.{uid}",
              headers={"Prefer": "return=minimal"})
    except SignedOutAbort:
        raise
    except Exception:
        pass


def apply(user_id, action_id, target, params, guard):
    """Add one phone upload to the Library. Returns (outcome, result); raises Reject to refuse it. Safe to repeat."""
    if not isinstance(target, str) or not _UUID_RE.fullmatch(target):
        raise phone_pitch.Reject(NOT_FOUND_REASON)
    done = _already_added(action_id)
    if done is not None:
        return "applied", "Already in your Library."
    uid = urllib.parse.quote(user_id, safe="")
    try:
        rows = _rest("GET", f"phone_uploads?select={_SELECT}&id=eq.{target}&owner_id=eq.{uid}&limit=1")
    except PhoneLibraryError as exc:
        if exc.status == 404:
            raise phone_pitch.Reject(NOT_FOUND_REASON)
        raise
    raw = rows[0] if isinstance(rows, list) and rows else None
    if raw is None:
        raise phone_pitch.Reject(NOT_FOUND_REASON)
    row = _clean_upload(raw, user_id, target)
    if row is None:
        raise phone_pitch.Reject(BAD_FILE_REASON)
    backend = get_backend()
    try:
        return _import(user_id, action_id, row, params, guard, backend)
    except phone_pitch.Reject:
        # A refused file of this user is not kept in the cloud, where it would only use up the shared budget.
        _remove_cloud_copy(user_id, backend, row)
        raise


def _import(user_id, action_id, row, params, guard, backend):
    """Check one of the user's own uploads and add it to the Library; raises Reject to refuse it."""
    if row["expires_at"] is not None and row["expires_at"] <= datetime.now(timezone.utc):
        raise phone_pitch.Reject(EXPIRED_REASON)
    if names_confidential(guard, row["name"], params["description"]):
        raise phone_pitch.Reject(CONFIDENTIAL_TEXT_REASON)

    try:
        data = backend.download(row["path"])
    except team_auth.TeamAuthError as exc:
        if exc.status == 404:
            raise phone_pitch.Reject(NOT_FOUND_REASON)
        if exc.status == 401:
            raise SignedOutAbort() from exc
        raise RuntimeError("upload download failed")  # retried a few times by the queue, then given up
    if len(data) != row["bytes"]:
        # The shared budget counts the size the phone declared, so a file of another size is refused.
        raise phone_pitch.Reject(SIZE_MISMATCH_REASON)
    _check_bytes(data, row["ext"], guard)

    name = _safe_name(row["name"], row["ext"])
    tags = ["from-phone", DONE_TAG_PREFIX + action_id]
    try:
        assets_manager.register_asset(name, data, category=params["category"], description=params["description"], tags=tags)
    except sqlite3.IntegrityError:
        # The same bytes in the same second get the same asset id: it is the same file added twice at once.
        _remove_cloud_copy(user_id, backend, row)
        return "applied", "Already in your Library."
    _remove_cloud_copy(user_id, backend, row)
    return "applied", f"Added to your Library as {name}."[:300]


def sync_expired(user_id, is_signed_out=None, backend=None):
    """Remove the owner's own uploads that waited past their expiry. Returns {'removed': n, 'errors': n}."""
    uid = str(user_id or "").strip()
    out = {"removed": 0, "errors": 0}
    if not uid:
        return out
    if is_signed_out is not None and is_signed_out():
        raise SignedOutAbort()
    quoted = urllib.parse.quote(uid, safe="")
    try:
        rows = _rest("GET", f"phone_uploads?select={_SELECT}&owner_id=eq.{quoted}&order=created_at.asc&limit={MAX_ROWS}")
    except PhoneLibraryError as exc:
        if exc.status == 404 or "phone_uploads" in exc.message.lower():
            return out  # an older cloud without the table
        raise
    if not isinstance(rows, list):
        return out
    now = datetime.now(timezone.utc)
    backend = backend or get_backend()
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("owner_id") != uid or not isinstance(raw.get("id"), str):
            continue
        expires = _parse_time(raw.get("expires_at"))
        path = raw.get("storage_path")
        if expires is None or expires > now or not is_own_object(uid, path) or not _UUID_RE.fullmatch(raw["id"]):
            continue
        rid = urllib.parse.quote(raw["id"], safe="")
        try:
            # The owner may only see an expired object after the row says "removing" (the server allows that for expired rows).
            _rest("PATCH", f"phone_uploads?id=eq.{rid}&owner_id=eq.{quoted}", body={"removing": True},
                  headers={"Prefer": "return=minimal"})
            backend.delete([path])
            _rest("DELETE", f"phone_uploads?id=eq.{rid}&owner_id=eq.{quoted}", headers={"Prefer": "return=minimal"})
            out["removed"] += 1
        except SignedOutAbort:
            raise
        except Exception:
            out["errors"] += 1
    return out


# --- The Library as the phone sees it ------------------------------------------------------------------------------

def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _text(value, limit):
    return _BAD_TEXT_RE.sub(" ", value)[:limit] if isinstance(value, str) else ""


def _is_confidential_asset(asset, guard, scope):
    if guard is None or scope is None or scope.broken:
        return True
    texts = [asset.get("name"), asset.get("description"), asset.get("category"), asset.get("relative_path"), asset.get("tags")]
    flat = [t if isinstance(t, str) else str(t) for t in texts if t]
    return names_confidential(guard, *flat) or scope.text(*flat) or scope.path(asset.get("relative_path"))


def _public_asset(asset):
    """What the phone may know about an asset: name, kind, size, category. Never a path."""
    rel = str(asset.get("relative_path") or "")
    ext = os.path.splitext(rel)[1].lower() or os.path.splitext(str(asset.get("name") or ""))[1].lower()
    return {
        "id": str(asset.get("id")),
        "name": _text(asset.get("name"), 200) or "Untitled",
        "file_group": _text(asset.get("file_group"), 20) or "document",
        "mime_type": _text(asset.get("mime_type"), 100),
        "size_bytes": _int(asset.get("size_bytes")),
        "category": _text(asset.get("category"), 40),
        "description": _text(asset.get("description"), MAX_DESCRIPTION_CHARS),
        "from_phone": "from-phone" in (asset.get("tags") or []),
        "uploaded_at": _text(asset.get("uploaded_at"), 40) or None,
        # Only images, videos and PDFs can be sent to the phone.
        "fetchable": ext in phone_files._TYPES,
    }


def _recent_assets():
    assets = [a for a in assets_manager.get_assets() if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]]
    assets.sort(key=lambda a: str(a.get("uploaded_at") or ""), reverse=True)
    return assets[:MAX_MIRRORED]


def list_assets(guard):
    """The Library for the live phone route. A confidential asset is only its id and a flag."""
    scope = confidential_guard.scope()
    out = []
    for asset in _recent_assets():
        try:
            if _is_confidential_asset(asset, guard, scope):
                out.append({"id": asset["id"], "confidential": True})
            else:
                out.append(_public_asset(asset))
        except Exception:
            out.append({"id": asset["id"], "confidential": True})
    return out


def mirror_records(make_record, fallback_ts, guard):
    """One mirror record per asset (the newest 100). Confidential, unreadable or odd ones are bare stubs."""
    scope = confidential_guard.scope()
    records = []
    for asset in _recent_assets():
        stamp = asset.get("uploaded_at") or fallback_ts
        stub = make_record(MIRROR_KIND, asset["id"], None, "Confidential item",
                           {"confidential": True, "updated_at": asset.get("uploaded_at")}, stamp)
        try:
            if _is_confidential_asset(asset, guard, scope):
                records.append(stub)
                continue
            data = _public_asset(asset)
            records.append(make_record(MIRROR_KIND, asset["id"], None, data["name"], data, stamp))
        except Exception:
            records.append(stub)
    return records


def resolve_asset_file(asset_id, guard):
    """(real path, size, mime) of a Library file the phone may fetch, or a PhoneApiError. Found by registered id only."""
    if not isinstance(asset_id, str) or not _ASSET_ID_RE.fullmatch(asset_id):
        raise phone_server.PhoneApiError("Invalid asset id", status=400)
    asset = next((a for a in assets_manager.get_assets() if isinstance(a, dict) and a.get("id") == asset_id), None)
    if asset is None:
        raise phone_server.PhoneApiError("Asset not found", status=404)
    if _is_confidential_asset(asset, guard, confidential_guard.scope()):
        raise phone_server.PhoneApiError("Confidential files are not available on phone", status=403)
    rel = str(asset.get("relative_path") or "")
    ext = os.path.splitext(rel)[1].lower()
    if ext not in phone_files._TYPES:
        raise phone_server.PhoneApiError("Only images, videos and PDFs can be sent to the phone", status=403)
    real = file_serving.servable_path(os.path.join(paths.DATA_ROOT, rel), "assets", "uploads")
    if not real or os.path.splitext(real)[1].lower() != ext:
        raise phone_server.PhoneApiError("File not found", status=404)
    size = os.path.getsize(real)
    if size > phone_server.MAX_FILE_BYTES:
        raise phone_server.PhoneApiError("File exceeds 200 MB limit", status=413)
    return real, size, phone_files._TYPES[ext][1]
