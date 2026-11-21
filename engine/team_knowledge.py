#!/usr/bin/env python3
"""Shared team knowledge sync: brand kit, product rules, agent instructions, templates, and Pitch base prices."""

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agents
import db
import events
import paths
import product_rules
import team_auth

SHARED_KNOWLEDGE_KEYS = ("brand_kit", "product_rules", "agent_instructions", "templates")
BASE_PRICES_KEY = "pitch_base_prices"
ALL_KEYS = SHARED_KNOWLEDGE_KEYS + (BASE_PRICES_KEY,)

LABELS = {
    "brand_kit": "Brand kit",
    "product_rules": "Product rules",
    "agent_instructions": "Agent instructions",
    "templates": "Templates",
    "pitch_base_prices": "Pitch base prices",
}

BRAND_KIT_FILES = (
    "research/brand-direction.md",
    "research/reference-style.md",
)

MAX_FILE_BYTES = 200 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_BACKUPS_PER_KIND = 10
MAX_PRICE = 10_000_000
DELETE_CHUNK_SIZE = 50

_SAFE_MD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
_SAFE_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_lock = threading.Lock()
_remote_meta = {}
_last_errors = {}
_pending_auto_publish = set()
_cached_role = None  # (user_id, role)


class TeamKnowledgeError(ValueError):
    """Error raised when shared knowledge validation, auth, or Supabase sync fails."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = code


class SignedOutAbort(Exception):
    """Raised internally when the user signs out mid-sync."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def _is_newer_iso(candidate_ts, baseline_ts):
    if not candidate_ts:
        return False
    if not baseline_ts:
        return True
    dt_cand = _parse_iso(candidate_ts)
    dt_base = _parse_iso(baseline_ts)
    if dt_cand is not None and dt_base is not None:
        return dt_cand > dt_base
    return str(candidate_ts) > str(baseline_ts)


def normalize_key(key):
    """Normalize and validate a team knowledge key name."""
    raw = str(key or "").strip()
    if raw == "base_prices":
        raw = BASE_PRICES_KEY
    if raw not in ALL_KEYS:
        raise TeamKnowledgeError(f"Unknown knowledge key '{key}'.", status=404, code="unknown_key")
    return raw


def _canonical_json_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _content_hash(key, data):
    norm_key = normalize_key(key)
    if norm_key in ("brand_kit", "agent_instructions", "templates"):
        payload = {"files": dict(data.get("files") or {}), "version": 1}
    elif norm_key == "product_rules":
        payload = {"rules": data.get("rules") or {}, "version": 1}
    else:
        raw_prices = data.get("prices") if isinstance(data, dict) else {}
        payload = {"prices": {str(k): int(v) for k, v in (raw_prices or {}).items()}}
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _resolve_readable_path(*parts):
    candidate = paths.data(*parts)
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource(*parts)
    if os.path.exists(fallback):
        return fallback
    return candidate


def _collect_md_files(rel_dir, prefix):
    """Collect *.md files directly in rel_dir from RESOURCE_ROOT then DATA_ROOT (DATA_ROOT wins)."""
    found = {}
    for root_fn in (paths.resource, paths.data):
        dir_path = root_fn(*rel_dir)
        if not os.path.isdir(dir_path):
            continue
        for name in sorted(os.listdir(dir_path)):
            if not name.endswith(".md"):
                continue
            full_path = os.path.join(dir_path, name)
            if os.path.isfile(full_path):
                found[f"{prefix}/{name}"] = full_path
    out = {}
    for rel_path in sorted(found):
        with open(found[rel_path], "r", encoding="utf-8") as f:
            out[rel_path] = f.read()
    return out


def local_snapshot(key):
    """Build the local snapshot dict for key (reading DATA_ROOT with RESOURCE_ROOT fallback)."""
    norm_key = normalize_key(key)

    if norm_key == "brand_kit":
        files = {}
        for rel in BRAND_KIT_FILES:
            p = _resolve_readable_path(*rel.split("/"))
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    files[rel] = f.read()
        return {"files": files, "version": 1}

    if norm_key == "product_rules":
        p = _resolve_readable_path("config", "product_rules.json")
        if not os.path.isfile(p):
            raise TeamKnowledgeError("Product rules file not found.", status=400, code="missing_rules")
        try:
            with open(p, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            product_rules.validate_rules(parsed)
        except Exception as exc:
            raise TeamKnowledgeError(f"Invalid product rules: {exc}", status=400, code="invalid_rules") from exc
        return {"rules": parsed, "version": 1}

    if norm_key == "agent_instructions":
        files = _collect_md_files(("agents",), "agents")
        files.update(_collect_md_files(("agents", "skills"), "agents/skills"))
        return {"files": dict(sorted(files.items())), "version": 1}

    if norm_key == "templates":
        files = _collect_md_files(("playbooks",), "playbooks")
        return {"files": files, "version": 1}

    rows = db.list_pitch_base_prices()
    prices = {str(item_id): int(entry["price"]) for item_id, entry in sorted(rows.items())}
    return {"prices": prices}


def local_hash(key):
    """Return sha256 hex digest of the canonical local snapshot for key."""
    norm_key = normalize_key(key)
    snap = local_snapshot(norm_key)
    return _content_hash(norm_key, snap)


def _safe_local_hash(key):
    try:
        return local_hash(key)
    except Exception:
        return None


def _validate_rel_path_for_key(key, rel_path):
    if not isinstance(rel_path, str) or not rel_path:
        raise TeamKnowledgeError("File path must be a non-empty string.", status=400, code="unsafe_path")
    if "\x00" in rel_path or "\\" in rel_path or rel_path.startswith("/"):
        raise TeamKnowledgeError(f"Unsafe file path '{rel_path}'.", status=400, code="unsafe_path")
    if ":" in rel_path.split("/")[0]:
        raise TeamKnowledgeError(f"Unsafe file path '{rel_path}'.", status=400, code="unsafe_path")

    parts = rel_path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise TeamKnowledgeError(f"Unsafe file path '{rel_path}'.", status=400, code="unsafe_path")

    if key == "brand_kit":
        if rel_path not in BRAND_KIT_FILES:
            raise TeamKnowledgeError(
                f"File '{rel_path}' is not allowed in brand_kit.",
                status=400,
                code="unsafe_path",
            )
        allowed_root = os.path.realpath(paths.data("research"))
    elif key == "agent_instructions":
        if parts == ["agents", parts[-1]] and _SAFE_MD_NAME_RE.match(parts[-1]):
            pass
        elif len(parts) == 3 and parts[0] == "agents" and parts[1] == "skills" and _SAFE_MD_NAME_RE.match(parts[2]):
            pass
        else:
            raise TeamKnowledgeError(
                f"File '{rel_path}' is not allowed in agent_instructions.",
                status=400,
                code="unsafe_path",
            )
        allowed_root = os.path.realpath(paths.data("agents"))
    elif key == "templates":
        if len(parts) != 2 or parts[0] != "playbooks" or not _SAFE_MD_NAME_RE.match(parts[1]):
            raise TeamKnowledgeError(
                f"File '{rel_path}' is not allowed in templates.",
                status=400,
                code="unsafe_path",
            )
        allowed_root = os.path.realpath(paths.data("playbooks"))
    else:
        raise TeamKnowledgeError(f"File paths are not valid for '{key}'.", status=400, code="invalid_data")

    data_root_real = os.path.realpath(paths.DATA_ROOT)
    target_real = os.path.realpath(os.path.join(data_root_real, *parts))
    if os.path.commonpath([allowed_root, target_real]) != allowed_root or target_real == allowed_root:
        raise TeamKnowledgeError(f"Path '{rel_path}' resolves outside allowed directory.", status=400, code="unsafe_path")
    return parts


def validate_snapshot(key, data):
    """Validate a snapshot/remote payload for key on both publish and apply."""
    norm_key = normalize_key(key)
    if not isinstance(data, dict):
        raise TeamKnowledgeError(f"Invalid {norm_key} payload: expected an object.", status=400, code="invalid_data")

    if norm_key == BASE_PRICES_KEY:
        prices = data.get("prices")
        if not isinstance(prices, dict):
            raise TeamKnowledgeError("Invalid base prices payload: expected prices object.", status=400, code="invalid_data")
        cleaned = {}
        for item_id, price in prices.items():
            if not isinstance(item_id, str) or not _SAFE_ITEM_ID_RE.match(item_id):
                raise TeamKnowledgeError(f"Invalid pitch base price item_id '{item_id}'.", status=400, code="invalid_data")
            if not isinstance(price, int) or isinstance(price, bool) or price < 0 or price > MAX_PRICE:
                raise TeamKnowledgeError(f"Invalid price for '{item_id}'.", status=400, code="invalid_data")
            cleaned[item_id] = int(price)
        if len(_canonical_json_bytes({"prices": cleaned})) > MAX_TOTAL_BYTES:
            raise TeamKnowledgeError("Base prices payload exceeds 1 MB limit.", status=400, code="payload_too_large")
        return {"prices": cleaned}

    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise TeamKnowledgeError("Invalid version number in shared knowledge.", status=400, code="invalid_data")

    if norm_key == "product_rules":
        rules = data.get("rules")
        if not isinstance(rules, dict):
            raise TeamKnowledgeError("product_rules payload must include a 'rules' object.", status=400, code="invalid_data")
        try:
            product_rules.validate_rules(rules)
        except Exception as exc:
            raise TeamKnowledgeError(f"Invalid product rules: {exc}", status=400, code="invalid_rules") from exc
        rules_bytes = len(json.dumps(rules, ensure_ascii=False).encode("utf-8"))
        if rules_bytes > MAX_FILE_BYTES:
            raise TeamKnowledgeError("product_rules exceeds 200 KB file limit.", status=400, code="file_too_large")
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_TOTAL_BYTES:
            raise TeamKnowledgeError("product_rules exceeds 1 MB total limit.", status=400, code="payload_too_large")
        return {"rules": rules, "version": int(version)}

    files = data.get("files")
    if not isinstance(files, dict):
        raise TeamKnowledgeError(f"{norm_key} payload must include a 'files' object.", status=400, code="invalid_data")

    total_bytes = 0
    cleaned_files = {}
    for rel_path, content in files.items():
        _validate_rel_path_for_key(norm_key, rel_path)
        if not isinstance(content, str) or "\x00" in content:
            raise TeamKnowledgeError(f"File '{rel_path}' must be UTF-8 text.", status=400, code="invalid_data")
        b_len = len(content.encode("utf-8"))
        if b_len > MAX_FILE_BYTES:
            raise TeamKnowledgeError(
                f"File '{rel_path}' exceeds 200 KB limit ({b_len} bytes).",
                status=400,
                code="file_too_large",
            )
        total_bytes += b_len
        if total_bytes > MAX_TOTAL_BYTES:
            raise TeamKnowledgeError(
                f"Total size for {norm_key} exceeds 1 MB limit.",
                status=400,
                code="payload_too_large",
            )
        cleaned_files[rel_path] = content

    payload = {"files": cleaned_files, "version": int(version)}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_TOTAL_BYTES:
        raise TeamKnowledgeError(f"Total payload for {norm_key} exceeds 1 MB limit.", status=400, code="payload_too_large")
    return payload


def validate_base_prices_rows(rows):
    """Validate remote pitch_base_prices rows and return normalized {prices, updated_at, updated_by}."""
    if not isinstance(rows, list):
        raise TeamKnowledgeError("Invalid pitch_base_prices response.", status=400, code="invalid_data")
    prices = {}
    latest_at = None
    latest_by = None
    for row in rows:
        if not isinstance(row, dict):
            raise TeamKnowledgeError("Invalid pitch_base_prices row.", status=400, code="invalid_data")
        item_id = row.get("item_id")
        price = row.get("price")
        if not isinstance(item_id, str) or not _SAFE_ITEM_ID_RE.match(item_id):
            raise TeamKnowledgeError(f"Invalid pitch base price item_id '{item_id}'.", status=400, code="invalid_data")
        if not isinstance(price, int) or isinstance(price, bool) or price < 0 or price > MAX_PRICE:
            raise TeamKnowledgeError(f"Invalid price for '{item_id}'.", status=400, code="invalid_data")
        prices[item_id] = int(price)
        row_at = row.get("updated_at")
        if row_at and _is_newer_iso(row_at, latest_at):
            latest_at = str(row_at)
            latest_by = row.get("updated_by")
    validate_snapshot(BASE_PRICES_KEY, {"prices": prices})
    return {
        "prices": dict(sorted(prices.items())),
        "updated_at": latest_at,
        "updated_by": str(latest_by) if latest_by else None,
    }


def _atomic_write_text(target_path, text):
    parent = os.path.dirname(target_path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tk_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def _make_backup_dir(kind):
    kind_root = paths.data("state", "knowledge_backups", kind)
    os.makedirs(kind_root, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cand = os.path.join(kind_root, stamp)
    suffix = 1
    while os.path.exists(cand):
        cand = os.path.join(kind_root, f"{stamp}-{suffix}")
        suffix += 1
    os.makedirs(cand, exist_ok=True)
    return kind_root, cand


def _prune_old_backups(kind_root, keep=MAX_BACKUPS_PER_KIND):
    if not os.path.isdir(kind_root):
        return
    dirs = sorted(
        name
        for name in os.listdir(kind_root)
        if os.path.isdir(os.path.join(kind_root, name))
    )
    if len(dirs) <= keep:
        return
    for old_name in dirs[:-keep]:
        old_path = os.path.join(kind_root, old_name)
        shutil.rmtree(old_path, ignore_errors=True)


def backup_local(key):
    """Copy the current local files or base prices for key into knowledge_backups/<kind>/<timestamp>/."""
    norm_key = normalize_key(key)
    kind_root, backup_dir = _make_backup_dir(norm_key)

    if norm_key == BASE_PRICES_KEY:
        current = db.list_pitch_base_prices()
        payload = json.dumps({"prices": current, "backed_up_at": _now_iso()}, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(os.path.join(backup_dir, "pitch_base_prices.json"), payload)
    elif norm_key == "product_rules":
        src = _resolve_readable_path("config", "product_rules.json")
        if os.path.isfile(src):
            dst = os.path.join(backup_dir, "config", "product_rules.json")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    elif norm_key == "brand_kit":
        for rel in BRAND_KIT_FILES:
            src = _resolve_readable_path(*rel.split("/"))
            if os.path.isfile(src):
                dst = os.path.join(backup_dir, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
    elif norm_key == "agent_instructions":
        for rel_dir, prefix in ((("agents",), "agents"), (("agents", "skills"), "agents/skills")):
            seen = set()
            for root_fn in (paths.data, paths.resource):
                dir_path = root_fn(*rel_dir)
                if not os.path.isdir(dir_path):
                    continue
                for name in sorted(os.listdir(dir_path)):
                    if not name.endswith(".md") or name in seen:
                        continue
                    src = os.path.join(dir_path, name)
                    if os.path.isfile(src):
                        seen.add(name)
                        dst = os.path.join(backup_dir, *prefix.split("/"), name)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
    elif norm_key == "templates":
        seen = set()
        for root_fn in (paths.data, paths.resource):
            dir_path = root_fn("playbooks")
            if not os.path.isdir(dir_path):
                continue
            for name in sorted(os.listdir(dir_path)):
                if not name.endswith(".md") or name in seen:
                    continue
                src = os.path.join(dir_path, name)
                if os.path.isfile(src):
                    seen.add(name)
                    dst = os.path.join(backup_dir, "playbooks", name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

    _prune_old_backups(kind_root, keep=MAX_BACKUPS_PER_KIND)
    return backup_dir


def write_local(key, validated_data, updated_by="team", updated_at=None):
    """Write validated team knowledge locally using atomic writes and clear engine caches."""
    norm_key = normalize_key(key)
    if norm_key == BASE_PRICES_KEY:
        items = [(item_id, price) for item_id, price in sorted((validated_data.get("prices") or {}).items())]
        db.replace_pitch_base_prices(items, updated_by=updated_by or "team", updated_at=updated_at)
        return

    if norm_key == "product_rules":
        target = paths.data("config", "product_rules.json")
        rendered = json.dumps(validated_data["rules"], indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(target, rendered)
        product_rules.clear_cache()
        return

    for rel_path, content in (validated_data.get("files") or {}).items():
        parts = _validate_rel_path_for_key(norm_key, rel_path)
        target = paths.data(*parts)
        _atomic_write_text(target, content)

    if norm_key == "agent_instructions":
        agents.clear_cache()


def _rest_with_auth_retry(method, path, body=None, headers=None):
    try:
        return team_auth.rest(method, path, body=body, headers=headers)
    except team_auth.TeamAuthError as exc:
        if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
            team_auth.refresh_access_token()
            return team_auth.rest(method, path, body=body, headers=headers)
        raise


def _require_account(admin_only=False):
    global _cached_role
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info()
    if not cfg.get("configured") or not sess:
        raise TeamKnowledgeError("Sign in to manage team knowledge.", status=403, code="forbidden")

    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        if exc.status in (401, 403):
            raise TeamKnowledgeError("Sign in to manage team knowledge.", status=403, code="forbidden") from exc
        status = 502 if exc.status in (0, None) or exc.status >= 500 else 400
        raise TeamKnowledgeError(exc.message, status=status, code=exc.code) from exc

    if not acct.get("configured") or not acct.get("signed_in") or acct.get("deactivated"):
        raise TeamKnowledgeError("Only active team members can access team knowledge.", status=403, code="forbidden")

    user = acct.get("user") or {}
    uid = str(user.get("id") or sess.get("user_id") or "").strip()
    role = str(user.get("role") or "member").strip().lower()
    if not uid or not bool(user.get("active", True)):
        raise TeamKnowledgeError("Only active team members can access team knowledge.", status=403, code="forbidden")

    with _lock:
        _cached_role = (uid, role)

    if admin_only and role != "admin":
        raise TeamKnowledgeError("Only admins can publish team knowledge.", status=403, code="forbidden")
    return user, uid, role


def check_write_access():
    """Check whether local edits to product rules or base prices are permitted.

    Signed-out users keep local editing; active members are refused with 403;
    active admins return 'admin'.
    """
    global _cached_role
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info()
    if not cfg.get("configured") or not sess:
        return None

    sess_uid = str(sess.get("user_id") or "").strip()
    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        if exc.status in (401, 403):
            return None
        with _lock:
            cached = _cached_role
        if cached and cached[0] == sess_uid:
            if cached[1] == "member":
                raise TeamKnowledgeError("Managed by your admin.", status=403, code="managed_by_admin") from exc
            if cached[1] == "admin":
                return "admin"
        return None

    if not acct.get("configured") or not acct.get("signed_in"):
        return None
    if acct.get("deactivated"):
        raise TeamKnowledgeError("Managed by your admin.", status=403, code="managed_by_admin")

    user = acct.get("user") or {}
    uid = str(user.get("id") or sess_uid).strip()
    role = str(user.get("role") or "member").strip().lower()
    with _lock:
        _cached_role = (uid, role)

    if role != "admin":
        raise TeamKnowledgeError("Managed by your admin.", status=403, code="managed_by_admin")
    return "admin"


def queue_auto_publish(key):
    """Queue key for background publishing in the next cloud_sync cycle."""
    norm_key = normalize_key(key)
    with _lock:
        _pending_auto_publish.add(norm_key)
    try:
        import cloud_sync
        cloud_sync.request_sync()
    except Exception:
        pass


def pending_auto_publish_keys():
    """Return sorted list of keys currently queued for background auto-publish."""
    with _lock:
        return sorted(_pending_auto_publish)


def _fetch_directory_names():
    try:
        rows = _rest_with_auth_retry("GET", "team_directory?select=id,display_name")
    except Exception:
        return {}
    names = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("id"):
                dname = str(r.get("display_name") or "").strip()
                if dname:
                    names[str(r["id"]).lower()] = dname
    return names


def _resolve_actor_name(updated_by_id, dir_names, fallback="Admin"):
    if not updated_by_id:
        return None
    return dir_names.get(str(updated_by_id).lower()) or fallback


def _set_error(key, message):
    with _lock:
        if message:
            _last_errors[key] = str(message)
        else:
            _last_errors.pop(key, None)


def _set_remote_meta(key, *, updated_at, updated_by_id, updated_by_name, content_hash, valid=True, empty_remote=False):
    with _lock:
        _remote_meta[key] = {
            "updated_at": updated_at,
            "updated_by_id": updated_by_id,
            "updated_by_name": updated_by_name,
            "hash": content_hash,
            "valid": valid,
            "empty_remote": empty_remote,
        }


def publish_base_prices():
    """Publish all local pitch_base_prices rows to Supabase and delete removed remote rows."""
    user, uid, _role = _require_account(admin_only=True)
    snap = validate_snapshot(BASE_PRICES_KEY, local_snapshot(BASE_PRICES_KEY))
    snap_hash = _content_hash(BASE_PRICES_KEY, snap)
    local_prices = snap["prices"]

    remote_rows = _rest_with_auth_retry("GET", "pitch_base_prices?select=item_id")
    remote_ids = {
        str(r["item_id"])
        for r in (remote_rows if isinstance(remote_rows, list) else [])
        if isinstance(r, dict) and r.get("item_id")
    }

    now_iso = _now_iso()
    latest_at = None
    if local_prices:
        upsert_body = [{"item_id": iid, "price": int(price)} for iid, price in local_prices.items()]
        upserted = _rest_with_auth_retry(
            "POST",
            "pitch_base_prices?on_conflict=item_id",
            body=upsert_body,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        if isinstance(upserted, list):
            for r in upserted:
                if isinstance(r, dict) and r.get("updated_at") and _is_newer_iso(r["updated_at"], latest_at):
                    latest_at = str(r["updated_at"])

    to_delete = sorted(remote_ids - set(local_prices.keys()))
    for idx in range(0, len(to_delete), DELETE_CHUNK_SIZE):
        chunk = to_delete[idx : idx + DELETE_CHUNK_SIZE]
        in_list = ",".join(urllib.parse.quote(iid, safe="") for iid in chunk)
        _rest_with_auth_retry(
            "DELETE",
            f"pitch_base_prices?item_id=in.({in_list})",
            headers={"Prefer": "return=minimal"},
        )

    pub_at = latest_at or now_iso
    db.upsert_team_knowledge_state(
        uid,
        BASE_PRICES_KEY,
        applied_hash=snap_hash,
        applied_remote_updated_at=pub_at,
        published_hash=snap_hash,
        published_at=pub_at,
    )
    actor_name = str(user.get("display_name") or user.get("email") or "Admin")
    _set_remote_meta(
        BASE_PRICES_KEY,
        updated_at=latest_at if local_prices else None,
        updated_by_id=uid if local_prices else None,
        updated_by_name=actor_name if local_prices else None,
        content_hash=snap_hash,
        valid=True,
        empty_remote=(not local_prices),
    )
    _set_error(BASE_PRICES_KEY, None)
    with _lock:
        _pending_auto_publish.discard(BASE_PRICES_KEY)
    return _status_item(BASE_PRICES_KEY, uid, "admin")


def publish(key):
    """Publish local snapshot for key to Supabase (admins only)."""
    norm_key = normalize_key(key)
    if norm_key == BASE_PRICES_KEY:
        return publish_base_prices()

    user, uid, _role = _require_account(admin_only=True)
    snap = validate_snapshot(norm_key, local_snapshot(norm_key))
    snap_hash = _content_hash(norm_key, snap)

    rows = _rest_with_auth_retry(
        "POST",
        "shared_knowledge?on_conflict=key",
        body={"key": norm_key, "data": snap},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    row = rows[0] if isinstance(rows, list) and rows else {}
    pub_at = str(row.get("updated_at") or _now_iso())

    db.upsert_team_knowledge_state(
        uid,
        norm_key,
        applied_hash=snap_hash,
        applied_remote_updated_at=pub_at,
        published_hash=snap_hash,
        published_at=pub_at,
    )
    actor_name = str(user.get("display_name") or user.get("email") or "Admin")
    _set_remote_meta(
        norm_key,
        updated_at=pub_at,
        updated_by_id=str(row.get("updated_by") or uid),
        updated_by_name=actor_name,
        content_hash=snap_hash,
        valid=True,
    )
    _set_error(norm_key, None)
    with _lock:
        _pending_auto_publish.discard(norm_key)
    return _status_item(norm_key, uid, "admin")


def _emit_updated_event(key, updated_by_name, updated_at, notify_member=False):
    events.publish(
        "team_knowledge.updated",
        {
            "key": key,
            "label": LABELS[key],
            "updated_by": updated_by_name or "Admin",
            "updated_at": updated_at,
        },
    )
    if key == BASE_PRICES_KEY:
        events.publish("pitch.updated", {"proposal_id": None, "what": "base_prices.synced"})
    if notify_member:
        try:
            import notifications
            notifications.notify_team_knowledge_updated(LABELS[key], updated_by_name or "Admin")
        except Exception:
            pass


def apply(key):
    """Fetch, validate, back up, and apply the team version of key locally."""
    norm_key = normalize_key(key)
    _user, uid, role = _require_account(admin_only=False)
    dir_names = _fetch_directory_names()

    if norm_key == BASE_PRICES_KEY:
        rows = _rest_with_auth_retry("GET", "pitch_base_prices?select=item_id,price,updated_at,updated_by")
        try:
            parsed = validate_base_prices_rows(rows if isinstance(rows, list) else [])
        except TeamKnowledgeError as exc:
            _set_error(norm_key, exc.message)
            raise
        remote_at = parsed["updated_at"] or _now_iso()
        by_id = parsed["updated_by"]
        by_name = _resolve_actor_name(by_id, dir_names) if by_id else "Admin"
        validated = {"prices": parsed["prices"]}
        remote_hash = _content_hash(norm_key, validated)
        backup_local(norm_key)
        write_local(norm_key, validated, updated_by=by_name or "team", updated_at=remote_at)
        db.upsert_team_knowledge_state(
            uid,
            norm_key,
            applied_hash=remote_hash,
            applied_remote_updated_at=remote_at,
        )
        _set_remote_meta(
            norm_key,
            updated_at=parsed["updated_at"],
            updated_by_id=by_id,
            updated_by_name=by_name if parsed["updated_at"] else None,
            content_hash=remote_hash,
            valid=True,
            empty_remote=(len(parsed["prices"]) == 0),
        )
        _set_error(norm_key, None)
        _emit_updated_event(norm_key, by_name, remote_at, notify_member=(role == "member"))
        return _status_item(norm_key, uid, role)

    q_key = urllib.parse.quote(norm_key, safe="")
    rows = _rest_with_auth_retry(
        "GET",
        f"shared_knowledge?key=eq.{q_key}&select=key,data,updated_at,updated_by",
    )
    if not isinstance(rows, list) or not rows:
        raise TeamKnowledgeError(f"No team version published for {LABELS[norm_key]}.", status=404, code="not_found")
    row = rows[0]
    remote_at = str(row.get("updated_at") or _now_iso())
    by_id = str(row.get("updated_by")) if row.get("updated_by") else None
    by_name = _resolve_actor_name(by_id, dir_names)

    try:
        validated = validate_snapshot(norm_key, row.get("data"))
    except TeamKnowledgeError as exc:
        _set_error(norm_key, exc.message)
        raise

    remote_hash = _content_hash(norm_key, validated)
    backup_local(norm_key)
    write_local(norm_key, validated, updated_by=by_name or "team", updated_at=remote_at)
    db.upsert_team_knowledge_state(
        uid,
        norm_key,
        applied_hash=remote_hash,
        applied_remote_updated_at=remote_at,
    )
    _set_remote_meta(
        norm_key,
        updated_at=remote_at,
        updated_by_id=by_id,
        updated_by_name=by_name,
        content_hash=remote_hash,
        valid=True,
    )
    _set_error(norm_key, None)
    _emit_updated_event(norm_key, by_name, remote_at, notify_member=(role == "member"))
    return _status_item(norm_key, uid, role)


def _refresh_remote_metadata(user_id, role, is_signed_out=None, apply_for_member=False):
    if is_signed_out and is_signed_out():
        raise SignedOutAbort()

    sk_rows = _rest_with_auth_retry("GET", "shared_knowledge?select=key,data,updated_at,updated_by")
    bp_rows = _rest_with_auth_retry("GET", "pitch_base_prices?select=item_id,price,updated_at,updated_by")
    dir_names = _fetch_directory_names()

    if is_signed_out and is_signed_out():
        raise SignedOutAbort()

    state_map = db.get_team_knowledge_state(user_id)
    by_key = {}
    if isinstance(sk_rows, list):
        for r in sk_rows:
            if isinstance(r, dict) and r.get("key") in SHARED_KNOWLEDGE_KEYS:
                by_key[r["key"]] = r

    for key in SHARED_KNOWLEDGE_KEYS:
        row = by_key.get(key)
        if row is None:
            _set_remote_meta(
                key,
                updated_at=None,
                updated_by_id=None,
                updated_by_name=None,
                content_hash=None,
                valid=True,
                empty_remote=True,
            )
            continue

        remote_at = str(row.get("updated_at") or "") or None
        by_id = str(row.get("updated_by")) if row.get("updated_by") else None
        by_name = _resolve_actor_name(by_id, dir_names)

        try:
            validated = validate_snapshot(key, row.get("data"))
        except TeamKnowledgeError as exc:
            _set_error(key, exc.message)
            _set_remote_meta(
                key,
                updated_at=remote_at,
                updated_by_id=by_id,
                updated_by_name=by_name,
                content_hash=None,
                valid=False,
            )
            continue

        remote_hash = _content_hash(key, validated)
        _set_error(key, None)
        _set_remote_meta(
            key,
            updated_at=remote_at,
            updated_by_id=by_id,
            updated_by_name=by_name,
            content_hash=remote_hash,
            valid=True,
        )

        if apply_for_member and role == "member":
            if is_signed_out and is_signed_out():
                raise SignedOutAbort()
            st = state_map.get(key) or {}
            applied_at = st.get("applied_remote_updated_at")
            curr_hash = _safe_local_hash(key)
            if _is_newer_iso(remote_at, applied_at):
                if curr_hash != remote_hash:
                    backup_local(key)
                    write_local(key, validated, updated_by=by_name or "team", updated_at=remote_at)
                    _emit_updated_event(key, by_name, remote_at, notify_member=True)
                db.upsert_team_knowledge_state(
                    user_id,
                    key,
                    applied_hash=remote_hash,
                    applied_remote_updated_at=remote_at,
                )

    # Pitch base prices
    bp_list = bp_rows if isinstance(bp_rows, list) else []
    try:
        parsed_bp = validate_base_prices_rows(bp_list)
    except TeamKnowledgeError as exc:
        _set_error(BASE_PRICES_KEY, exc.message)
        _set_remote_meta(
            BASE_PRICES_KEY,
            updated_at=None,
            updated_by_id=None,
            updated_by_name=None,
            content_hash=None,
            valid=False,
        )
        return

    bp_data = {"prices": parsed_bp["prices"]}
    bp_hash = _content_hash(BASE_PRICES_KEY, bp_data)
    bp_at = parsed_bp["updated_at"]
    bp_by_id = parsed_bp["updated_by"]
    bp_by_name = _resolve_actor_name(bp_by_id, dir_names) if bp_by_id else None
    is_empty_bp = len(parsed_bp["prices"]) == 0
    _set_error(BASE_PRICES_KEY, None)
    _set_remote_meta(
        BASE_PRICES_KEY,
        updated_at=bp_at,
        updated_by_id=bp_by_id,
        updated_by_name=bp_by_name,
        content_hash=bp_hash,
        valid=True,
        empty_remote=is_empty_bp,
    )

    if apply_for_member and role == "member":
        if is_signed_out and is_signed_out():
            raise SignedOutAbort()
        st = state_map.get(BASE_PRICES_KEY) or {}
        applied_at = st.get("applied_remote_updated_at")
        curr_hash = _safe_local_hash(BASE_PRICES_KEY)
        should_clear = is_empty_bp and applied_at is not None and curr_hash != bp_hash
        if _is_newer_iso(bp_at, applied_at) or should_clear:
            effective_at = bp_at or _now_iso()
            if curr_hash != bp_hash:
                backup_local(BASE_PRICES_KEY)
                write_local(BASE_PRICES_KEY, bp_data, updated_by=bp_by_name or "team", updated_at=effective_at)
                _emit_updated_event(BASE_PRICES_KEY, bp_by_name or "Admin", effective_at, notify_member=True)
            db.upsert_team_knowledge_state(
                user_id,
                BASE_PRICES_KEY,
                applied_hash=bp_hash,
                applied_remote_updated_at=effective_at,
            )


def sync(user_id, role, is_signed_out=None):
    """Run one shared-knowledge sync pass: flush admin auto-publish queue, then fetch/apply."""
    global _cached_role
    if not user_id:
        return
    norm_role = str(role or "member").strip().lower()
    with _lock:
        _cached_role = (str(user_id), norm_role)

    if is_signed_out and is_signed_out():
        raise SignedOutAbort()

    if norm_role == "admin":
        for key in pending_auto_publish_keys():
            if is_signed_out and is_signed_out():
                raise SignedOutAbort()
            try:
                publish(key)
            except TeamKnowledgeError as exc:
                if exc.status in (0, 502, 503, 504) or exc.status >= 500 or exc.code == "network_error":
                    raise team_auth.TeamAuthError(exc.message, status=0 if exc.code == "network_error" else exc.status, code=exc.code) from exc
                _set_error(key, exc.message)
                with _lock:
                    _pending_auto_publish.discard(key)

    _refresh_remote_metadata(user_id, norm_role, is_signed_out=is_signed_out, apply_for_member=True)


def _status_item(key, user_id, role, state_map=None):
    norm_key = normalize_key(key)
    st = (state_map if state_map is not None else db.get_team_knowledge_state(user_id)).get(norm_key) or {}
    curr_hash = _safe_local_hash(norm_key)
    pub_hash = st.get("published_hash")
    local_changed = bool(pub_hash and curr_hash is not None and curr_hash != pub_hash)

    with _lock:
        rem = dict(_remote_meta.get(norm_key) or {})
        last_err = _last_errors.get(norm_key)

    rem_at = rem.get("updated_at")
    rem_by_id = rem.get("updated_by_id")
    rem_by_name = rem.get("updated_by_name")
    rem_hash = rem.get("hash")
    rem_valid = rem.get("valid", True)
    applied_at = st.get("applied_remote_updated_at")

    remote_newer = False
    if (
        role == "admin"
        and rem_valid
        and rem_at
        and rem_hash
        and curr_hash is not None
        and rem_hash != curr_hash
        and (not rem_by_id or str(rem_by_id).lower() != str(user_id).lower())
        and _is_newer_iso(rem_at, applied_at)
    ):
        remote_newer = True

    return {
        "key": norm_key,
        "label": LABELS[norm_key],
        "managed_by_team": bool(user_id and role == "member"),
        "role": role,
        "local_changed_since_publish": local_changed,
        "remote_updated_at": rem_at,
        "remote_updated_by": rem_by_name,
        "published_at": st.get("published_at"),
        "applied_at": applied_at,
        "remote_newer": remote_newer,
        "last_error": last_err,
    }


def status(refresh=False):
    """Return shared knowledge status across all keys for the current account (or signed-out)."""
    global _cached_role
    cfg = team_auth.get_config()
    sess = team_auth.get_session_info() if cfg.get("configured") else None
    if not cfg.get("configured") or not sess:
        return {
            "signed_in": False,
            "role": None,
            "items": [_status_item(k, None, None, state_map={}) for k in ALL_KEYS],
        }

    uid = str(sess.get("user_id") or "").strip()
    role = None
    try:
        acct = team_auth.current_account()
        if acct.get("signed_in") and not acct.get("deactivated"):
            user = acct.get("user") or {}
            uid = str(user.get("id") or uid).strip()
            role = str(user.get("role") or "member").strip().lower()
            with _lock:
                _cached_role = (uid, role)
        else:
            return {
                "signed_in": False,
                "role": None,
                "items": [_status_item(k, None, None, state_map={}) for k in ALL_KEYS],
            }
    except team_auth.TeamAuthError:
        with _lock:
            cached = _cached_role
        if cached and cached[0] == uid:
            role = cached[1]
        else:
            role = "member"

    if refresh and uid and role:
        try:
            _refresh_remote_metadata(uid, role, apply_for_member=False)
        except Exception:
            pass

    state_map = db.get_team_knowledge_state(uid)
    return {
        "signed_in": True,
        "role": role,
        "items": [_status_item(k, uid, role, state_map=state_map) for k in ALL_KEYS],
    }


def notify_signed_out():
    """Clear cached remote metadata, pending auto-publishes, and role state on sign-out."""
    global _cached_role
    with _lock:
        _remote_meta.clear()
        _last_errors.clear()
        _pending_auto_publish.clear()
        _cached_role = None
