#!/usr/bin/env python3
"""Personal NVIDIA NIM keys, kept the same on the owner's laptop and phone through the cloud (Supabase Vault).

The cloud copy is owner only (security-definer functions, never an admin). On the laptop the OS keychain stays the
local store: the desktop shell reads it into the engine's environment at start. Each sync cycle this module compares
three things per key slot (NVIDIA_NIM_API_KEY, NVIDIA_NIM_API_KEY1 .. 10) by fingerprint (SHA-256), never by value:
what is here, what the cloud has, and what both agreed on last time. From that it uploads what was added or changed
here, downloads what was added or changed elsewhere, and removes here what was removed elsewhere. A key removed on
this laptop is sent to the cloud through forget(), so a locked or broken keychain can never delete a cloud copy.

Two keys in one slot with no shared history are both kept: the cloud's takes the slot and this laptop's key moves to a
free slot (and is uploaded there), so nothing is lost.

Where the engine runs inside the desktop shell (MAGNM_SECRETS_MODE=keychain) it cannot write the keychain itself.
Downloads and removals wait in memory as pending operations; the shell reads them (pending_ops), writes the
keychain, and calls acknowledge(). Only then does the engine use the key and record it as agreed. Without the shell
(a plain engine) the key file next to the engine (.env) is the local store and changes apply at once.

A key value is only ever handled in memory and sent to its owner's own cloud account. It is never logged, never
written into the sync state (names and fingerprints only), the mirror, activity or any audit text.
"""

import hashlib
import json
import os
import re
import sys
import threading

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import db
import team_auth

STATE_META = "personal_keys_sync"
SLOTS = ("NVIDIA_NIM_API_KEY",) + tuple(f"NVIDIA_NIM_API_KEY{i}" for i in range(1, 11))
_SLOT_SET = frozenset(SLOTS)
_KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_-]{20,200}")
_FP_RE = re.compile(r"[0-9a-f]{64}")

_lock = threading.RLock()
_pending = {}


class PersonalKeysError(Exception):
    """A failed cloud call or an unusable answer, with an HTTP-style status. The message never holds a key."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out or switched account while keys were being synced."""


def valid_slot(name):
    """True for one of the eleven key slot names."""
    return isinstance(name, str) and name in _SLOT_SET


def valid_key(value):
    """True for a well-formed NVIDIA NIM key."""
    return isinstance(value, str) and bool(_KEY_RE.fullmatch(value))


def fingerprint(value):
    """SHA-256 hex of a key; what devices compare instead of the key."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _keychain_mode():
    return os.environ.get("MAGNM_SECRETS_MODE", "").strip().lower() == "keychain"


def _warn(message):
    try:
        import cloud_sync
        cloud_sync._log_warning(f"personal keys: {message}")
    except Exception:
        pass


def reset_for_tests():
    """Forget pending operations (as a restart does)."""
    with _lock:
        _pending.clear()


# ----------------------------------------------------------------------------------------------------- state

def _empty_state(user_id):
    return {"user_id": user_id, "synced": {}, "forget": [], "foreign": {}}


def _clean_map(raw):
    out = {}
    if isinstance(raw, dict):
        for name, fp in raw.items():
            if valid_slot(name) and isinstance(fp, str) and _FP_RE.fullmatch(fp):
                out[name] = fp
    return out


def _load_state(user_id):
    """The sync memory for this account. Another account's memory becomes `foreign`: those keys belong to it."""
    raw = db.get_meta(STATE_META)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _empty_state(user_id)
    owner = data.get("user_id")
    state = {
        "user_id": owner,
        "synced": _clean_map(data.get("synced")),
        "forget": [n for n in (data.get("forget") or []) if valid_slot(n)],
        "foreign": _clean_map(data.get("foreign")),
    }
    if owner in (None, user_id):
        state["user_id"] = user_id
        return state
    foreign = dict(state["foreign"])
    foreign.update(state["synced"])
    with _lock:
        _pending.clear()
    return {"user_id": user_id, "synced": {}, "forget": [], "foreign": foreign}


def _save_state(state):
    payload = {
        "user_id": state["user_id"],
        "synced": _clean_map(state["synced"]),
        "forget": sorted({n for n in state["forget"] if valid_slot(n)}),
        "foreign": _clean_map(state["foreign"]),
    }
    db.set_meta(STATE_META, json.dumps(payload, sort_keys=True))


# ------------------------------------------------------------------------------------------ local key store

def _local_values():
    """Valid keys held here, by slot. The key file wins over the environment, as in the key pool."""
    file_env = ai_gateway.load_env()
    out = {}
    for slot in SLOTS:
        value = file_env.get(slot)
        if not (isinstance(value, str) and value.strip()):
            value = os.environ.get(slot)
        if valid_key(value):
            out[slot] = value
    return out


def _apply_local_set(name, value):
    """Store a key here. Returns True when it is in use now, False when it waits for the desktop shell."""
    if _keychain_mode():
        with _lock:
            _pending[name] = {"op": "set", "value": value}
        return False
    ai_gateway.save_env_values({name: value})
    os.environ[name] = value
    return True


def _apply_local_delete(name):
    if _keychain_mode():
        with _lock:
            _pending[name] = {"op": "delete"}
        return False
    if name in ai_gateway.load_env():
        ai_gateway.save_env_values({name: ""})
    os.environ.pop(name, None)
    return True


def pending_ops():
    """Keychain writes the desktop shell still has to make: [{op, name, value?}]. Values only ever go to the shell."""
    with _lock:
        out = []
        for name in SLOTS:
            item = _pending.get(name)
            if not item:
                continue
            if item["op"] == "set":
                out.append({"op": "set", "name": name, "value": item["value"]})
            else:
                out.append({"op": "delete", "name": name})
        return out


def acknowledge(names):
    """The shell wrote these slots to the keychain: use them now and record them as agreed. Others are ignored."""
    with _lock:
        done = [n for n in (names if isinstance(names, list) else []) if valid_slot(n) and n in _pending]
        if not done:
            return []
        state = _load_state(_state_user())
        for name in done:
            item = _pending.pop(name)
            if item["op"] == "set":
                os.environ[name] = item["value"]
                state["synced"][name] = fingerprint(item["value"])
            else:
                os.environ.pop(name, None)
                state["synced"].pop(name, None)
        _save_state(state)
        return done


def _state_user():
    raw = db.get_meta(STATE_META)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    return (data or {}).get("user_id") if isinstance(data, dict) else None


def forget(name):
    """The owner removed this key on this laptop: remove it from the cloud on the next sync."""
    if not valid_slot(name):
        raise ValueError("Unknown key slot")
    with _lock:
        sess = team_auth.get_session_info() or {}
        state = _load_state(str(sess.get("user_id") or "") or _state_user())
        if name not in state["forget"]:
            state["forget"].append(name)
        state["synced"].pop(name, None)
        _pending.pop(name, None)
        _save_state(state)


def status():
    """Key slots known here, with the last four characters and whether the cloud agrees. Never a value."""
    with _lock:
        state = _load_state(_state_user())
        local = _local_values()
        rows = []
        for slot in SLOTS:
            if slot not in local and slot not in state["synced"]:
                continue
            value = local.get(slot)
            rows.append({
                "name": slot,
                "label": f"...{value[-4:]}" if value else None,
                "present": value is not None,
                "synced": value is not None and state["synced"].get(slot) == fingerprint(value),
            })
        return rows


# ------------------------------------------------------------------------------------------- the decision

def reconcile(local, cloud, synced):
    """Decide what to do per slot. All three arguments map slot -> fingerprint. A pure function.

    Returns {upload, download, delete_local, copy_local, keep}: slot lists, (from_slot, to_slot) moves of a local key
    that must make room for a different cloud key, and the fingerprints both sides agree on right now.
    """
    plan = {"upload": [], "download": [], "delete_local": [], "copy_local": [], "keep": {}}
    taken = set(local) | set(cloud)
    cloud_fps = set(cloud.values())
    for name in SLOTS:
        lf, cf, mf = local.get(name), cloud.get(name), synced.get(name)
        if lf and cf:
            if lf == cf:
                plan["keep"][name] = lf
            elif mf == cf:
                plan["upload"].append(name)
            elif mf == lf:
                plan["download"].append(name)
            else:
                free = next((s for s in SLOTS if s not in taken), None)
                if free is None or lf in cloud_fps:
                    # No room to keep the local key, or the cloud already holds it: never drop a key without a copy.
                    if lf in cloud_fps:
                        plan["download"].append(name)
                    continue
                taken.add(free)
                plan["download"].append(name)
                plan["copy_local"].append((name, free))
                plan["upload"].append(free)
        elif lf:
            if lf in cloud_fps:
                continue
            if mf == lf:
                plan["delete_local"].append(name)
            else:
                plan["upload"].append(name)
        elif cf:
            plan["download"].append(name)
    return plan


# ------------------------------------------------------------------------------------------------- cloud

def _check(user_id, is_signed_out):
    if is_signed_out is not None and is_signed_out():
        raise SignedOutAbort()
    sess = team_auth.get_session_info()
    if not sess or str(sess.get("user_id") or "") != str(user_id):
        raise SignedOutAbort()


def _scrub(text, secrets):
    out = str(text or "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[key]")
    return out


def _rpc(name, body, secrets=()):
    try:
        try:
            return team_auth.rest("POST", f"rpc/{name}", body=body)
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
                try:
                    team_auth.refresh_access_token()
                except team_auth.TeamAuthError as refresh_exc:
                    raise SignedOutAbort() from refresh_exc
                return team_auth.rest("POST", f"rpc/{name}", body=body)
            raise
    except team_auth.TeamAuthError as exc:
        status = exc.status if isinstance(exc.status, int) else 502
        raise PersonalKeysError(_scrub(exc.message, secrets), status=status, code=exc.code) from exc


def _is_missing(exc):
    """An older server without the key functions is not an error."""
    text = exc.message.lower()
    return exc.status == 404 or (exc.status == 400 and "personal_key" in text and "function" in text)


def _cloud_list():
    rows = _rpc("personal_key_list", {})
    if not isinstance(rows, list):
        raise PersonalKeysError("The key list was not a list.", status=502, code="bad_payload")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, fp = row.get("name"), row.get("fingerprint")
        if valid_slot(name) and isinstance(fp, str) and _FP_RE.fullmatch(fp):
            out[name] = fp
    return out


def _cloud_get(names, cloud):
    """Fetch the values for `names`, keeping only slots asked for whose value matches the listed fingerprint."""
    rows = _rpc("personal_key_get", {"p_names": list(names)})
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, value = row.get("name"), row.get("api_key")
        if name in names and valid_key(value) and fingerprint(value) == cloud.get(name):
            out[name] = value
    return out


def sync(user_id, is_signed_out=None):
    """One key sync for the signed-in user. Returns the slot names touched; never a value."""
    _check(user_id, is_signed_out)
    summary = {"uploaded": [], "downloaded": [], "removed_local": [], "removed_cloud": []}
    with _lock:
        state = _load_state(user_id)
        try:
            cloud = _cloud_list()
        except PersonalKeysError as exc:
            if _is_missing(exc):
                return summary
            raise

        for name in list(state["forget"]):
            _check(user_id, is_signed_out)
            if name in cloud:
                _rpc("personal_key_delete", {"p_name": name})
                summary["removed_cloud"].append(name)
                cloud.pop(name, None)
            state["synced"].pop(name, None)
            state["forget"].remove(name)
        _save_state(state)

        values = _local_values()
        # Keys that belong to a previous account on this machine are neither uploaded nor treated as removed.
        for name in list(state["foreign"]):
            if name in values and fingerprint(values[name]) == state["foreign"][name]:
                values.pop(name)
            else:
                state["foreign"].pop(name)
        local = {n: fingerprint(v) for n, v in values.items()}

        plan = reconcile(local, cloud, state["synced"])
        synced = dict(state["synced"])
        synced.update(plan["keep"])

        # A key that moves to a free slot is copied first, so the cloud's key can then take its old slot.
        for src, dst in plan["copy_local"]:
            values[dst] = values[src]
            local[dst] = local[src]
            if _apply_local_set(dst, values[dst]):
                synced[dst] = local[dst]

        for name in plan["upload"]:
            _check(user_id, is_signed_out)
            value = values[name]
            try:
                _rpc("personal_key_set", {"p_name": name, "p_key": value}, secrets=(value,))
            except PersonalKeysError as exc:
                if exc.status in (0, 401) or exc.status >= 500:
                    raise
                _warn(f"{name} was not saved in the cloud: {exc.message}")
                continue
            synced[name] = local[name]
            summary["uploaded"].append(name)

        wanted = [n for n in plan["download"] if not _already_pending(n, cloud)]
        if wanted:
            _check(user_id, is_signed_out)
            fetched = _cloud_get(wanted, cloud)
            for name in plan["download"]:
                if name in fetched and _apply_local_set(name, fetched[name]):
                    synced[name] = cloud[name]
        for name in plan["download"]:
            if name in cloud and (name in _pending or synced.get(name) == cloud[name]):
                summary["downloaded"].append(name)

        for name in plan["delete_local"]:
            if _apply_local_delete(name):
                synced.pop(name, None)
            summary["removed_local"].append(name)

        keep_names = set(local) | set(cloud) | set(_pending)
        for name in list(synced):
            if name not in keep_names:
                synced.pop(name)
        state["synced"] = synced
        _save_state(state)
    return summary


def _already_pending(name, cloud):
    item = _pending.get(name)
    return bool(item and item["op"] == "set" and fingerprint(item["value"]) == cloud.get(name))
