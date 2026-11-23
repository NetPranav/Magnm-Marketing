#!/usr/bin/env python3
"""Local media preview generation (ffmpeg/ffprobe) and private Supabase Storage upload."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import assets_manager
import cloud_sync
import db
import paths
import team_auth

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCES_PER_CYCLE = 5
MAX_UPLOAD_BYTES_PER_CYCLE = 100 * 1024 * 1024
FFMPEG_TIMEOUT_S = 120
UPLOAD_TIMEOUT_S = 60
VIDEO_EXPIRY_DAYS = 60
CLEANUP_INTERVAL_S = 24 * 3600
LOCAL_CACHE_TTL_S = 7 * 24 * 3600

CLEANUP_CURSOR_KIND = "_media_cleanup"
CLEANUP_CURSOR_ID = "last_run_at"
BACKOFF_STEPS_S = (60.0, 120.0, 300.0, 600.0)
FFMPEG_MISSING_MESSAGE = "FFmpeg is not installed"
STORAGE_LIMIT_BYTES = 1073741824

_state_lock = threading.Lock()
_last_error = None
_failure_count = 0
_next_allowed_at = 0.0


class SignedOutAbort(Exception):
    """Raised when the user signs out mid-cycle."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _log_warning(msg):
    try:
        print(f"[media_previews] {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def _set_last_error(msg):
    global _last_error
    with _state_lock:
        _last_error = msg


def _set_cycle_backoff(err_msg):
    global _failure_count, _last_error, _next_allowed_at
    with _state_lock:
        _failure_count += 1
        idx = min(_failure_count - 1, len(BACKOFF_STEPS_S) - 1)
        _last_error = str(err_msg)
        _next_allowed_at = time.time() + BACKOFF_STEPS_S[idx]


def _clear_cycle_backoff():
    global _failure_count, _next_allowed_at
    with _state_lock:
        _failure_count = 0
        _next_allowed_at = 0.0


def notify_signed_out():
    """Clear in-memory media preview error and backoff state on sign-out."""
    global _failure_count, _last_error, _next_allowed_at
    with _state_lock:
        _last_error = None
        _failure_count = 0
        _next_allowed_at = 0.0


def ffmpeg_available():
    """Return True if both ffmpeg and ffprobe executables are on PATH."""
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _low_priority_preexec():
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass


def _run_subprocess(cmd, timeout=FFMPEG_TIMEOUT_S):
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "shell": False,
    }
    if os.name == "posix":
        kwargs["preexec_fn"] = _low_priority_preexec
    res = subprocess.run(cmd, **kwargs)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or f"exit code {res.returncode}").strip()
        raise RuntimeError(f"{cmd[0]} failed: {err[:300]}")
    return res


# Container formats ffmpeg may read. Playlists (hls), concat lists and similar formats are refused because a
# crafted file in those formats can make ffmpeg read other local files into the preview that gets uploaded.
ALLOWED_INPUT_FORMATS = {
    "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm",
    "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif", "gif_pipe",
}


def probe_media(file_path):
    """Inspect media dimensions, duration, and audio presence using ffprobe."""
    cmd = [
        "ffprobe",
        "-protocol_whitelist",
        "file",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        file_path,
    ]
    res = _run_subprocess(cmd, timeout=FFMPEG_TIMEOUT_S)
    try:
        info = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc

    streams = info.get("streams") if isinstance(info.get("streams"), list) else []
    fmt = info.get("format") if isinstance(info.get("format"), dict) else {}
    format_names = set(str(fmt.get("format_name") or "").split(","))
    if not format_names or not format_names <= ALLOWED_INPUT_FORMATS:
        raise RuntimeError(f"Unsupported media format: {fmt.get('format_name') or 'unknown'}")

    width = None
    height = None
    duration_s = None
    has_audio = False

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        stype = stream.get("codec_type")
        if stype == "video" and width is None:
            w = stream.get("width")
            h = stream.get("height")
            width = int(w) if isinstance(w, (int, float)) and w > 0 else None
            height = int(h) if isinstance(h, (int, float)) and h > 0 else None
            dur = stream.get("duration")
            if dur is not None:
                try:
                    val = float(dur)
                    if val >= 0:
                        duration_s = val
                except (TypeError, ValueError):
                    pass
        elif stype == "audio":
            has_audio = True

    if duration_s is None and fmt.get("duration") is not None:
        try:
            val = float(fmt["duration"])
            if val >= 0:
                duration_s = val
        except (TypeError, ValueError):
            pass

    return {
        "width": width,
        "height": height,
        "duration_s": duration_s,
        "has_audio": has_audio,
    }


def build_ffmpeg_args(src_path, dst_path, kind, media_type, probe_info=None):
    """Build the ffmpeg argument list for thumbnail, image_preview, or video_preview."""
    info = probe_info or {}
    if kind == "thumbnail":
        args = ["ffmpeg", "-nostdin", "-y"]
        if media_type == "video":
            dur = info.get("duration_s")
            if dur is not None and float(dur) >= 1.0:
                args.extend(["-ss", "1"])
        args.extend([
            "-protocol_whitelist",
            "file",
            "-i",
            src_path,
            "-frames:v",
            "1",
            "-vf",
            "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
            "-c:v",
            "libwebp",
            "-quality",
            "70",
            dst_path,
        ])
        return args

    if kind == "image_preview":
        return [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file",
            "-i",
            src_path,
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1080,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
            "-c:v",
            "libwebp",
            "-quality",
            "80",
            dst_path,
        ]

    if kind == "video_preview":
        has_audio = bool(info.get("has_audio"))
        audio_args = ["-c:a", "aac", "-ac", "1", "-b:a", "64k"] if has_audio else ["-an"]
        return [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file",
            "-i",
            src_path,
            "-t",
            "30",
            "-vf",
            "scale=-2:'min(480,trunc(ih/2)*2)'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            *audio_args,
            dst_path,
        ]

    raise ValueError(f"Unsupported preview kind: {kind!r}")


def _kind_meta(kind):
    if kind == "video_preview":
        return "mp4", "video/mp4"
    if kind in ("thumbnail", "image_preview"):
        return "webp", "image/webp"
    raise ValueError(f"Unsupported preview kind: {kind!r}")


def _cache_dir():
    return paths.data("cache", "previews")


def _cache_filename(local_source_id, kind, source_hash):
    ext, _ = _kind_meta(kind)
    sid_hash = hashlib.sha256(local_source_id.encode("utf-8")).hexdigest()
    return f"{sid_hash}_{kind}_{source_hash[:12]}.{ext}"


def _storage_path(user_id, kind, local_source_id, source_hash):
    ext, _ = _kind_meta(kind)
    sid_hash = hashlib.sha256(local_source_id.encode("utf-8")).hexdigest()
    return f"{user_id}/{kind}/{sid_hash}-{source_hash[:12]}.{ext}"


def _resolve_safe_path(rel_path):
    """Resolve a path relative to DATA_ROOT, returning None if outside DATA_ROOT."""
    if not rel_path or not isinstance(rel_path, str):
        return None
    cleaned = rel_path.strip()
    if not cleaned or os.path.isabs(cleaned):
        return None
    real_root = os.path.realpath(paths.DATA_ROOT)
    candidate = os.path.realpath(os.path.join(real_root, cleaned))
    try:
        if os.path.commonpath([real_root, candidate]) != real_root:
            return None
    except ValueError:
        return None
    if candidate == real_root:
        return None
    return candidate


def _compute_source_hash(size_bytes, mtime_ns, norm_rel_path):
    raw = f"{int(size_bytes)}:{int(mtime_ns)}:{norm_rel_path}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _content_source_id(content_id, norm_rel):
    raw = f"content:{content_id}:{norm_rel}"
    if len(raw) <= 200:
        return raw
    digest = hashlib.sha256(norm_rel.encode("utf-8")).hexdigest()[:32]
    return f"content:{str(content_id)[:150]}:{digest}"[:200]


def _mentions_confidential(checker, *parts):
    if cloud_sync._mentions(checker, *parts):
        return True
    normalized = [
        re.sub(r"[_.\-]+", " ", json.dumps(p, ensure_ascii=False) if not isinstance(p, str) else p)
        for p in parts
    ]
    return cloud_sync._mentions(checker, *normalized)


def _collect_sources():
    """Collect eligible local media sources while enforcing confidential rules."""
    sessions = db.list_all_agent_sessions()
    content_items = db.list_all_content_items()
    confidential_sids, confidential_cids, checker = cloud_sync.confidential_scope(
        sessions=sessions,
        content_items=content_items,
    )
    for ver in db.list_all_content_versions():
        cid = ver.get("content_id")
        if cid and cid not in confidential_cids:
            if _mentions_confidential(checker, ver.get("body") or "", ver.get("notes") or ""):
                confidential_cids.add(cid)

    sources = []
    seen_ids = set()

    for item in content_items:
        cid = item["id"]
        if cid in confidential_cids or item.get("session_id") in confidential_sids:
            continue
        for entry in item.get("files") or []:
            rel_path = entry.get("path") if isinstance(entry, dict) else (entry if isinstance(entry, str) else "")
            if not rel_path:
                continue
            full_path = _resolve_safe_path(rel_path)
            if not full_path or not os.path.isfile(full_path):
                continue
            ext = os.path.splitext(full_path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                media_type = "image"
                kinds = ("thumbnail", "image_preview")
            elif ext in VIDEO_EXTENSIONS:
                media_type = "video"
                kinds = ("thumbnail", "video_preview")
            else:
                continue
            try:
                st = os.stat(full_path)
            except OSError:
                continue
            if st.st_size > MAX_SOURCE_BYTES:
                continue
            if _mentions_confidential(
                checker,
                item.get("title") or "",
                item.get("product_id") or "",
                rel_path,
                entry if isinstance(entry, dict) else "",
            ):
                continue
            norm_rel = os.path.normpath(rel_path).replace("\\", "/")
            local_source_id = _content_source_id(cid, norm_rel)
            if local_source_id in seen_ids:
                continue
            seen_ids.add(local_source_id)
            sources.append({
                "local_source_id": local_source_id,
                "source_kind": "content_file",
                "parent_local_id": str(cid)[:200],
                "full_path": full_path,
                "norm_rel": norm_rel,
                "media_type": media_type,
                "kinds": kinds,
                "source_hash": _compute_source_hash(st.st_size, st.st_mtime_ns, norm_rel),
            })

    for asset in assets_manager.get_assets():
        if _mentions_confidential(
            checker,
            asset.get("name") or "",
            asset.get("saved_filename") or "",
            asset.get("relative_path") or "",
            asset.get("description") or "",
            asset.get("tags") or [],
        ):
            continue
        rel_path = asset.get("relative_path") or ""
        full_path = _resolve_safe_path(rel_path)
        if not full_path or not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(full_path)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            media_type = "image"
            kinds = ("thumbnail", "image_preview")
        elif ext in VIDEO_EXTENSIONS:
            media_type = "video"
            kinds = ("thumbnail", "video_preview")
        else:
            continue
        try:
            st = os.stat(full_path)
        except OSError:
            continue
        if st.st_size > MAX_SOURCE_BYTES:
            continue
        norm_rel = os.path.normpath(rel_path).replace("\\", "/")
        local_source_id = f"asset:{asset['id']}"[:200]
        if local_source_id in seen_ids:
            continue
        seen_ids.add(local_source_id)
        sources.append({
            "local_source_id": local_source_id,
            "source_kind": "asset",
            "parent_local_id": None,
            "full_path": full_path,
            "norm_rel": norm_rel,
            "media_type": media_type,
            "kinds": kinds,
            "source_hash": _compute_source_hash(st.st_size, st.st_mtime_ns, norm_rel),
        })

    return sources


def generate_preview(src_path, dst_path, kind, media_type, probe_info=None):
    """Generate a single preview file atomically inside the cache directory."""
    cache_dir = os.path.dirname(dst_path)
    os.makedirs(cache_dir, exist_ok=True)
    ext = os.path.splitext(dst_path)[1] or ".tmp"
    fd, tmp_path = tempfile.mkstemp(prefix="prev_", suffix=ext, dir=cache_dir)
    os.close(fd)
    try:
        cmd = build_ffmpeg_args(src_path, tmp_path, kind, media_type, probe_info=probe_info)
        _run_subprocess(cmd, timeout=FFMPEG_TIMEOUT_S)
        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError(f"ffmpeg produced empty output for {kind}")
        os.replace(tmp_path, dst_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


class StorageBackend:
    """Abstract interface for private preview object storage backends."""

    def upload(self, storage_path, data_bytes, content_type):
        raise NotImplementedError

    def delete(self, storage_paths):
        raise NotImplementedError

    def sign(self, storage_path, expires_in=3600):
        raise NotImplementedError


def _sanitize_storage_path(storage_path):
    cleaned = str(storage_path or "").strip().lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise ValueError("Invalid storage_path.")
    return cleaned


class SupabaseStorageBackend(StorageBackend):
    """Supabase Storage implementation using user JWT and RLS on one private bucket ('previews' unless a subclass says)."""

    BUCKET = "previews"

    def _raw_request(self, method, url, headers=None, data=None, timeout=UPLOAD_TIMEOUT_S):
        cfg = team_auth._require_config()
        token = team_auth.get_access_token()
        for attempt in range(2):
            req_headers = {
                "apikey": cfg["publishable_key"],
                "Authorization": f"Bearer {token}",
            }
            if headers:
                req_headers.update(headers)
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                err_bytes = b""
                try:
                    err_bytes = exc.read()
                except Exception:
                    pass
                msg = team_auth._extract_error_message(err_bytes, exc.code)
                if exc.code == 401 and attempt == 0:
                    token = team_auth.refresh_access_token()
                    continue
                raise team_auth.TeamAuthError(msg, status=exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise team_auth.TeamAuthError(
                    f"Network error contacting Supabase: {exc}",
                    status=0,
                    code="network_error",
                ) from exc

    def upload(self, storage_path, data_bytes, content_type):
        cfg = team_auth._require_config()
        rel = _sanitize_storage_path(storage_path)
        encoded = urllib.parse.quote(rel, safe="/")
        url = f"{cfg['url']}/storage/v1/object/{self.BUCKET}/{encoded}"
        self._raw_request(
            "POST",
            url,
            headers={
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            data=data_bytes,
            timeout=UPLOAD_TIMEOUT_S,
        )

    def delete(self, storage_paths):
        paths_list = [_sanitize_storage_path(p) for p in (storage_paths or []) if p]
        if not paths_list:
            return
        cfg = team_auth._require_config()
        url = f"{cfg['url']}/storage/v1/object/{self.BUCKET}"
        self._raw_request(
            "DELETE",
            url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            data=json.dumps({"prefixes": paths_list}).encode("utf-8"),
            timeout=UPLOAD_TIMEOUT_S,
        )

    def sign(self, storage_path, expires_in=3600):
        cfg = team_auth._require_config()
        rel = _sanitize_storage_path(storage_path)
        try:
            exp = int(expires_in)
        except (TypeError, ValueError) as exc:
            raise ValueError("expires_in must be an integer.") from exc
        if exp <= 0:
            raise ValueError("expires_in must be positive.")
        encoded = urllib.parse.quote(rel, safe="/")
        url = f"{cfg['url']}/storage/v1/object/sign/{self.BUCKET}/{encoded}"
        raw = self._raw_request(
            "POST",
            url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            data=json.dumps({"expiresIn": exp}).encode("utf-8"),
            timeout=team_auth.REQUEST_TIMEOUT_S,
        )
        try:
            parsed = json.loads((raw or b"{}").decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise team_auth.TeamAuthError("Invalid JSON response from Supabase.", status=502, code="bad_gateway") from exc

        signed = ""
        if isinstance(parsed, dict):
            signed = str(parsed.get("signedURL") or parsed.get("signedUrl") or parsed.get("url") or "").strip()
        if not signed:
            raise team_auth.TeamAuthError("Signed URL missing from storage response.", status=502, code="bad_gateway")
        if signed.startswith("http://") or signed.startswith("https://"):
            return signed
        if signed.startswith("/storage/v1/"):
            return f"{cfg['url']}{signed}"
        if signed.startswith("/"):
            return f"{cfg['url']}/storage/v1{signed}"
        return f"{cfg['url']}/storage/v1/{signed}"


def _read_storage_backend_name():
    env_val = os.environ.get("MAGNM_STORAGE_BACKEND", "").strip().lower()
    if env_val:
        return env_val
    cfg_path = team_auth._config_path()
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and loaded.get("storage_backend"):
                return str(loaded["storage_backend"]).strip().lower()
        except Exception:
            pass
    return "supabase"


def get_storage_backend():
    """Return the configured StorageBackend instance (default: supabase)."""
    name = _read_storage_backend_name()
    if name == "supabase":
        return SupabaseStorageBackend()
    raise RuntimeError(f"Unsupported storage_backend: {name!r}")


def signed_url(storage_path, expires_in=3600):
    """Create a short-lived signed URL for a preview object in private storage."""
    return get_storage_backend().sign(storage_path, expires_in=expires_in)


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


def _is_transient_http(exc):
    return exc.status == 0 or (isinstance(exc.status, int) and exc.status >= 500)


def _reconcile_queue(user_id, sources, now_iso):
    queue = db.get_media_upload_queue(user_id)
    updates = []
    for src in sources:
        for kind in src["kinds"]:
            key = (src["local_source_id"], kind)
            prev = queue.get(key)
            if prev is None:
                row = {
                    "local_source_id": src["local_source_id"],
                    "kind": kind,
                    "source_hash": src["source_hash"],
                    "storage_path": "",
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": 0.0,
                    "error": None,
                    "updated_at": now_iso,
                }
                queue[key] = row
                updates.append(row)
            elif prev["source_hash"] != src["source_hash"]:
                # Preserve prev["storage_path"] until the replacement upload succeeds.
                row = {
                    "local_source_id": src["local_source_id"],
                    "kind": kind,
                    "source_hash": src["source_hash"],
                    "storage_path": prev["storage_path"],
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": 0.0,
                    "error": None,
                    "updated_at": now_iso,
                }
                queue[key] = row
                updates.append(row)
    if updates:
        db.upsert_media_upload_queue(user_id, updates)
    return queue


def run_daily_cleanup(user_id, backend=None, force=False):
    """Once a day, delete this user's expired video previews and prune old uploaded cache files."""
    if not user_id:
        return 0
    now_ts = time.time()
    last_raw = db.get_cloud_sync_value(user_id, CLEANUP_CURSOR_KIND, CLEANUP_CURSOR_ID)
    if not force and last_raw is not None:
        try:
            if (now_ts - float(last_raw)) < CLEANUP_INTERVAL_S:
                return 0
        except (TypeError, ValueError):
            pass

    backend = backend or get_storage_backend()
    now_iso = _now_iso()
    q_uid = urllib.parse.quote(str(user_id), safe="")
    q_now = urllib.parse.quote(now_iso, safe="")

    deleted_count = 0
    try:
        rows = _rest_with_auth_retry(
            "GET",
            f"media_previews?owner_id=eq.{q_uid}&kind=eq.video_preview&deleted_at=is.null&expires_at=lte.{q_now}&select=id,storage_path",
        )
    except team_auth.TeamAuthError as exc:
        if exc.status == 401:
            raise SignedOutAbort() from exc
        raise

    if isinstance(rows, list) and rows:
        obj_paths = [r["storage_path"] for r in rows if isinstance(r, dict) and r.get("storage_path")]
        if obj_paths:
            backend.delete(obj_paths)
        ids = [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]
        if ids:
            ids_filter = ",".join(urllib.parse.quote(i, safe="") for i in ids)
            _rest_with_auth_retry(
                "PATCH",
                f"media_previews?owner_id=eq.{q_uid}&id=in.({ids_filter})",
                body={"deleted_at": now_iso},
                headers={"Prefer": "return=minimal"},
            )
            deleted_count = len(ids)

    queue = db.get_media_upload_queue(user_id)
    uploaded_cache_names = {
        _cache_filename(row["local_source_id"], row["kind"], row["source_hash"])
        for row in queue.values()
        if row["state"] == "uploaded"
    }
    cdir = _cache_dir()
    if os.path.isdir(cdir):
        cutoff = now_ts - LOCAL_CACHE_TTL_S
        for entry in os.scandir(cdir):
            if not entry.is_file():
                continue
            if entry.name not in uploaded_cache_names:
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
            except OSError:
                pass

    db.upsert_cloud_sync_state(
        user_id,
        [(CLEANUP_CURSOR_KIND, CLEANUP_CURSOR_ID, str(now_ts), now_iso, None)],
    )
    return deleted_count


def sync_previews(user_id=None, is_signed_out=None, force_cleanup=False):
    """Discover eligible local media, generate previews, upload up to cycle limits, and run daily clean-up."""
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info() if cfg.get("configured") else None
    if not cfg.get("configured") or not sess:
        return False

    uid = str(user_id or sess.get("user_id") or "").strip()
    if not uid:
        return False

    if is_signed_out and is_signed_out():
        raise SignedOutAbort()

    try:
        sources = _collect_sources()
    except Exception as exc:
        _set_last_error(str(exc))
        _log_warning(str(exc))
        return False

    now_iso = _now_iso()
    queue = _reconcile_queue(uid, sources, now_iso)

    if not ffmpeg_available():
        _set_last_error(FFMPEG_MISSING_MESSAGE)
        return False

    with _state_lock:
        allowed_at = _next_allowed_at
    if time.time() < allowed_at:
        return False

    backend = get_storage_backend()
    now_ts = time.time()
    sources_processed = 0
    uploaded_bytes = 0
    had_transient = False

    for src in sources:
        if sources_processed >= MAX_SOURCES_PER_CYCLE or uploaded_bytes >= MAX_UPLOAD_BYTES_PER_CYCLE:
            break
        if is_signed_out and is_signed_out():
            raise SignedOutAbort()

        due_kinds = []
        for kind in src["kinds"]:
            qrow = queue[(src["local_source_id"], kind)]
            if qrow["state"] == "uploaded":
                continue
            if float(qrow["next_attempt_at"] or 0.0) > now_ts:
                continue
            due_kinds.append(kind)

        if not due_kinds:
            continue

        sources_processed += 1
        try:
            src_probe = probe_media(src["full_path"])
        except Exception as exc:
            err_msg = str(exc)
            _set_last_error(err_msg)
            ts_iso = _now_iso()
            fail_rows = []
            for kind in due_kinds:
                qrow = queue[(src["local_source_id"], kind)]
                attempts = int(qrow["attempts"]) + 1
                delay = BACKOFF_STEPS_S[min(attempts - 1, len(BACKOFF_STEPS_S) - 1)]
                fail_rows.append({
                    **qrow,
                    "state": "failed",
                    "attempts": attempts,
                    "next_attempt_at": time.time() + delay,
                    "error": err_msg,
                    "updated_at": ts_iso,
                })
            db.upsert_media_upload_queue(uid, fail_rows)
            continue

        for kind in due_kinds:
            if uploaded_bytes >= MAX_UPLOAD_BYTES_PER_CYCLE:
                break
            if is_signed_out and is_signed_out():
                raise SignedOutAbort()

            qrow = queue[(src["local_source_id"], kind)]
            cache_path = os.path.join(
                _cache_dir(),
                _cache_filename(src["local_source_id"], kind, src["source_hash"]),
            )
            try:
                if not os.path.isfile(cache_path) or os.path.getsize(cache_path) == 0:
                    generate_preview(
                        src["full_path"],
                        cache_path,
                        kind,
                        src["media_type"],
                        probe_info=src_probe,
                    )
                with open(cache_path, "rb") as f:
                    preview_bytes = f.read()

                if uploaded_bytes > 0 and (uploaded_bytes + len(preview_bytes)) > MAX_UPLOAD_BYTES_PER_CYCLE:
                    break

                out_probe = probe_media(cache_path)
                _, content_type = _kind_meta(kind)
                new_storage_path = _storage_path(uid, kind, src["local_source_id"], src["source_hash"])
                old_storage_path = qrow["storage_path"]

                backend.upload(new_storage_path, preview_bytes, content_type)

                expires_at = None
                if kind == "video_preview":
                    expires_at = (datetime.now(timezone.utc) + timedelta(days=VIDEO_EXPIRY_DAYS)).isoformat()

                meta_row = {
                    "owner_id": uid,
                    "local_source_id": src["local_source_id"],
                    "source_kind": src["source_kind"],
                    "parent_local_id": src["parent_local_id"],
                    "kind": kind,
                    "storage_path": new_storage_path,
                    "content_type": content_type,
                    "bytes": len(preview_bytes),
                    "width": out_probe.get("width") or src_probe.get("width"),
                    "height": out_probe.get("height") or src_probe.get("height"),
                    "duration_s": out_probe.get("duration_s") if kind == "video_preview" else None,
                    "expires_at": expires_at,
                    "deleted_at": None,
                }
                _rest_with_auth_retry(
                    "POST",
                    "media_previews?on_conflict=owner_id,local_source_id,kind",
                    body=[meta_row],
                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                )

                updated_row = {
                    **qrow,
                    "storage_path": new_storage_path,
                    "state": "uploaded",
                    "attempts": 0,
                    "next_attempt_at": 0.0,
                    "error": None,
                    "updated_at": _now_iso(),
                }
                queue[(src["local_source_id"], kind)] = updated_row
                db.upsert_media_upload_queue(uid, [updated_row])
                uploaded_bytes += len(preview_bytes)

                if old_storage_path and old_storage_path != new_storage_path:
                    try:
                        backend.delete([old_storage_path])
                    except team_auth.TeamAuthError as del_exc:
                        if del_exc.status == 401:
                            raise SignedOutAbort() from del_exc
                        _log_warning(f"failed to delete superseded preview {old_storage_path}: {del_exc.message}")
            except team_auth.TeamAuthError as exc:
                if exc.status == 401:
                    raise SignedOutAbort() from exc
                err_msg = "Waiting for connection" if exc.status == 0 else exc.message
                attempts = int(qrow["attempts"]) + 1
                delay = BACKOFF_STEPS_S[min(attempts - 1, len(BACKOFF_STEPS_S) - 1)]
                updated_row = {
                    **qrow,
                    "state": "failed",
                    "attempts": attempts,
                    "next_attempt_at": time.time() + delay,
                    "error": err_msg,
                    "updated_at": _now_iso(),
                }
                queue[(src["local_source_id"], kind)] = updated_row
                db.upsert_media_upload_queue(uid, [updated_row])
                _set_last_error(err_msg)
                if _is_transient_http(exc):
                    had_transient = True
                    _set_cycle_backoff(err_msg)
                    break
            except SignedOutAbort:
                raise
            except Exception as exc:
                err_msg = str(exc)
                attempts = int(qrow["attempts"]) + 1
                delay = BACKOFF_STEPS_S[min(attempts - 1, len(BACKOFF_STEPS_S) - 1)]
                updated_row = {
                    **qrow,
                    "state": "failed",
                    "attempts": attempts,
                    "next_attempt_at": time.time() + delay,
                    "error": err_msg,
                    "updated_at": _now_iso(),
                }
                queue[(src["local_source_id"], kind)] = updated_row
                db.upsert_media_upload_queue(uid, [updated_row])
                _set_last_error(err_msg)

        if had_transient:
            break

    if not had_transient:
        try:
            run_daily_cleanup(uid, backend=backend, force=force_cleanup)
        except SignedOutAbort:
            raise
        except team_auth.TeamAuthError as exc:
            if exc.status == 401:
                raise SignedOutAbort() from exc
            _set_last_error(exc.message)
        except Exception as exc:
            _set_last_error(str(exc))

    if not had_transient:
        _clear_cycle_backoff()
        summary = db.get_media_queue_summary(uid)
        if summary["failed"] == 0:
            _set_last_error(None)

    return not had_transient


def status():
    """Return current media preview queue status for GET /api/account/media."""
    has_ffmpeg = ffmpeg_available()
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info() if cfg.get("configured") else None
    uid = str(sess.get("user_id") or "").strip() if sess else ""

    summary = db.get_media_queue_summary(uid) if uid else {
        "uploaded": 0,
        "waiting": 0,
        "failed": 0,
        "last_error": None,
    }
    with _state_lock:
        mod_err = _last_error

    if not has_ffmpeg:
        last_err = FFMPEG_MISSING_MESSAGE
    else:
        last_err = mod_err or summary.get("last_error")

    return {
        "ffmpeg": has_ffmpeg,
        "uploaded": summary["uploaded"],
        "waiting": summary["waiting"],
        "failed": summary["failed"],
        "last_error": last_err,
    }


def storage_usage():
    """Fetch storage usage from public.storage_usage() RPC and compute percent and 80% warning."""
    raw = _rest_with_auth_retry("POST", "rpc/storage_usage", body={})
    data = raw if isinstance(raw, dict) else {}
    used_bytes = int(data.get("bytes") or 0)
    files = int(data.get("files") or 0)
    by_kind = data.get("by_kind") if isinstance(data.get("by_kind"), dict) else {}
    percent = round((used_bytes / STORAGE_LIMIT_BYTES) * 100, 1)
    warning = (used_bytes * 100) >= (STORAGE_LIMIT_BYTES * 80)
    return {
        "bytes": used_bytes,
        "files": files,
        "by_kind": by_kind,
        "limit_bytes": STORAGE_LIMIT_BYTES,
        "percent": percent,
        "warning": warning,
    }
