#!/usr/bin/env python3
"""Personal repositories, kept the same on the owner's laptop and phone through the cloud (owner only).

The laptop's config/repos.json stays the source for what the engine uses. The cloud table personal_repos holds a
copy the phone can list, add to (GitHub only), edit and remove. Each sync cycle compares, per repository and per
field, three things: what is here, what the cloud has, and what both agreed on last time. A field changed on one side
only goes to the other; a field changed on both sides goes to whichever edit was later. A repo keeps a stable cloud
uid, so renaming its id on either side follows (snapshots, facts and references move with it).

Rules that hold on every cycle:
- Confidential repos never go to the cloud: a repo that is flagged, belongs to a confidential product, or whose name,
  id, GitHub slug, angle or folder names one is not uploaded, and its cloud row (if the phone made it) is deleted
  after the laptop has kept a confidential copy.
- Everything from the cloud is hostile: each row is rebuilt from whitelisted, checked fields; a folder path from the
  phone is only used after repos.validate_local_path accepts it, and the answer (ok or a short reason) goes back to
  the row. GitHub repos from the phone are added without any network call.
- The laptop decides what is valid: a phone change it cannot apply is put back to the laptop's value.
- Removal: the phone deleting a repo removes it here; removing it here (through the app) removes it in the cloud and
  is remembered until the cloud was reached. A repo that merely vanished from the file, or an empty cloud list, never
  deletes anything.
The sync state (state in the local database) holds repo ids, cloud uids and field values, never a key or a token.
"""

import json
import os
import re
import sys
import threading
import urllib.parse
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import repos
import retired_repos
import team_auth
import team_repos

STATE_META = "personal_repos_sync"
FIELDS = ("id", "name", "kind", "github", "local_path", "branch", "status", "marketing_angle", "confidential")
MAX_REPOS = 100
_SELECT = "uid,owner_id,id,name,kind,github,local_path,path_status,path_reason,branch,status,marketing_angle,confidential,field_at"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ANGLE_BAD_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MISSING = object()

_lock = threading.RLock()
_applying = threading.local()


class PersonalReposError(Exception):
    """A failed cloud call or an unusable answer, with an HTTP-style status."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out or switched account while repos were being synced."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _warn(message):
    try:
        import cloud_sync
        cloud_sync._log_warning(f"personal repos: {message}")
    except Exception:
        pass


# ----------------------------------------------------------------------------------------------------- state

def _empty_state(user_id):
    return {"user_id": user_id, "repos": {}, "forget": {}, "rejected": {}}


def _read_state():
    raw = db.get_meta(STATE_META)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None
    mapping = {}
    for uid, m in (data.get("repos") or {}).items():
        if isinstance(uid, str) and _UUID_RE.fullmatch(uid) and isinstance(m, dict) and isinstance(m.get("id"), str):
            mapping[uid] = {
                "id": m["id"],
                "synced": m["synced"] if isinstance(m.get("synced"), dict) else {},
                "edits": m["edits"] if isinstance(m.get("edits"), dict) else {},
            }
    return {
        "user_id": data.get("user_id"),
        "repos": mapping,
        "forget": {u: str(i) for u, i in (data.get("forget") or {}).items()
                   if isinstance(u, str) and _UUID_RE.fullmatch(u)} if isinstance(data.get("forget"), dict) else {},
        "rejected": {u: p for u, p in (data.get("rejected") or {}).items() if isinstance(u, str) and isinstance(p, str)},
    }


def _load_state(user_id):
    state = _read_state()
    if state is None or state["user_id"] not in (None, user_id):
        return _empty_state(user_id)
    state["user_id"] = user_id
    return state


def _save_state(state):
    db.set_meta(STATE_META, json.dumps(state, sort_keys=True))


def note_edit(old_id, new_id, fields, source="local"):
    """The owner edited a repo here: remember when each field changed (for last-edit-wins) and follow an id change."""
    if source == "sync" or getattr(_applying, "on", False):
        return
    with _lock:
        state = _read_state()
        if not state:
            return
        target = str(old_id or "").lower()
        for m in state["repos"].values():
            if m["id"].lower() == target:
                for f in fields:
                    if f in FIELDS:
                        m["edits"][f] = _now_iso()
                m["id"] = new_id
                _save_state(state)
                return


def note_removed(repo_id):
    """The owner removed a repo here: remove its cloud row on the next sync (remembered until that works)."""
    if getattr(_applying, "on", False):
        return
    with _lock:
        state = _read_state()
        if not state:
            return
        target = str(repo_id or "").lower()
        for uid, m in list(state["repos"].items()):
            if m["id"].lower() == target:
                del state["repos"][uid]
                state["forget"][uid] = m["id"]
                _save_state(state)
                return


def cloud_info(repo_id):
    """For the UI: whether a repo is synced and any note about its folder path. Never raises."""
    try:
        state = _read_state() or {}
        target = str(repo_id or "").lower()
        for uid, m in (state.get("repos") or {}).items():
            if m["id"].lower() == target:
                note = state["rejected"].get(uid)
                return {"synced": True, "path_rejected": bool(note)}
    except Exception:
        pass
    return {"synced": False, "path_rejected": False}


# ------------------------------------------------------------------------------------------ field shapes

def _clean_angle(value):
    if isinstance(value, str) and value.strip() and len(value) <= 500 and not _ANGLE_BAD_RE.search(value):
        return value.strip()
    return None


def local_fields(entry):
    """The cloud-shaped fields of a local repo, or None when the repo cannot be represented in the cloud."""
    rid, name = entry.get("id"), entry.get("name")
    if not (isinstance(rid, str) and _ID_RE.fullmatch(rid)):
        return None
    if not (isinstance(name, str) and 0 < len(name) <= 80 and not _CONTROL_RE.search(name)):
        return None
    kind = entry.get("kind") if entry.get("kind") in ("github", "local") else None
    github = team_repos.normalize_github(entry.get("github") or "") if entry.get("github") else None
    path = entry.get("local_path") if kind == "local" else None
    if isinstance(path, str) and (not path or len(path) > 500 or _CONTROL_RE.search(path)
                                  or ".." in re.split(r"[\\/]+", path)):
        return None
    if kind == "github" and not github:
        return None
    if kind == "local" and not path:
        return None
    if kind is None:
        return None
    branch = entry.get("branch")
    status = entry.get("status")
    return {
        "id": rid, "name": name, "kind": kind, "github": github, "local_path": path,
        "branch": branch if isinstance(branch, str) and team_repos.valid_branch(branch) else None,
        "status": status if status in repos.STATUSES else "in_development",
        "marketing_angle": _clean_angle(entry.get("marketing_angle")),
        "confidential": bool(entry.get("confidential")),
    }


def validate_row(raw, user_id):
    """Rebuild one cloud row from whitelisted, checked fields. None if it is not a usable row of this user."""
    if not isinstance(raw, dict):
        return None
    uid, rid, name = raw.get("uid"), raw.get("id"), raw.get("name")
    if not (isinstance(uid, str) and _UUID_RE.fullmatch(uid)) or str(raw.get("owner_id") or "") != str(user_id):
        return None
    if not (isinstance(rid, str) and _ID_RE.fullmatch(rid)):
        return None
    if not (isinstance(name, str) and 0 < len(name) <= 80 and not _CONTROL_RE.search(name)):
        return None
    kind = raw.get("kind")
    if kind not in ("github", "local"):
        return None
    github = raw.get("github")
    if github is not None:
        github = team_repos.normalize_github(github)
        if github is None:
            return None
    path = raw.get("local_path")
    if path is not None and not (isinstance(path, str) and 0 < len(path) <= 500 and not _CONTROL_RE.search(path)
                                 and ".." not in re.split(r"[\\/]+", path)):
        return None
    if (kind == "github" and not github) or (kind == "local" and not path):
        return None
    branch = raw.get("branch")
    if branch in (None, ""):
        branch = None
    elif not team_repos.valid_branch(branch):
        return None
    status = raw.get("status")
    if status not in repos.STATUSES:
        return None
    angle = raw.get("marketing_angle")
    if angle in (None, ""):
        angle = None
    elif _clean_angle(angle) is None:
        return None
    field_at = raw.get("field_at") if isinstance(raw.get("field_at"), dict) else {}
    # A confidential flag that is not a real boolean counts as confidential.
    conf = raw.get("confidential") if isinstance(raw.get("confidential"), bool) else True
    path_status = raw.get("path_status") if raw.get("path_status") in ("ok", "pending", "rejected") else "pending"
    return {
        "uid": uid, "id": rid, "name": name, "kind": kind, "github": github, "local_path": path, "branch": branch,
        "status": status, "marketing_angle": angle, "confidential": conf, "path_status": path_status,
        "field_at": {k: v for k, v in field_at.items() if k in FIELDS and isinstance(v, str)},
    }


def _ts(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------------- cloud

def _check(user_id, is_signed_out):
    if is_signed_out is not None and is_signed_out():
        raise SignedOutAbort()
    sess = team_auth.get_session_info()
    if not sess or str(sess.get("user_id") or "") != str(user_id):
        raise SignedOutAbort()


def _call(method, path, body=None):
    try:
        try:
            return team_auth.rest(method, path, body=body)
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
                try:
                    team_auth.refresh_access_token()
                except team_auth.TeamAuthError as refresh_exc:
                    raise SignedOutAbort() from refresh_exc
                return team_auth.rest(method, path, body=body)
            raise
    except team_auth.TeamAuthError as exc:
        status = exc.status if isinstance(exc.status, int) else 502
        raise PersonalReposError(exc.message, status=status, code=exc.code) from exc


def _table_missing(exc):
    text = exc.message.lower()
    return exc.status == 404 or (exc.status == 400 and "personal_repos" in text and "does not exist" in text)


def _pull(user_id):
    rows = _call("GET", f"personal_repos?select={_SELECT}&order=created_at.asc,id.asc&limit={MAX_REPOS + 1}")
    if not isinstance(rows, list):
        raise PersonalReposError("The repository list was not a list.", status=502, code="bad_payload")
    out, ids = [], set()
    for raw in rows:
        row = validate_row(raw, user_id)
        if row is None or row["uid"] in ids:
            continue
        ids.add(row["uid"])
        out.append(row)
    return out[:MAX_REPOS]


def _save(uid, fields, checked=False):
    return _call("POST", "rpc/personal_repo_save", {"p_uid": uid, "p_fields": fields, "p_checked": bool(checked)})


def _path_result(uid, path, ok, reason=None):
    try:
        _call("POST", "rpc/personal_repo_path_result", {"p_uid": uid, "p_path": path, "p_ok": bool(ok), "p_reason": reason})
    except PersonalReposError as exc:
        if exc.status in (0, 401) or exc.status >= 500:
            raise
        _warn(f"a folder check could not be recorded: {exc.message}")


def _delete_row(uid):
    _call("POST", "rpc/personal_repo_delete", {"p_uid": uid})


# -------------------------------------------------------------------------------------------------- the sync

class _Ctx:
    def __init__(self, user_id, is_signed_out):
        import confidential_guard
        self.user_id = user_id
        self.is_signed_out = is_signed_out
        self.guard = confidential_guard
        self.match = confidential_guard.matcher()
        self.deleted = set()
        self.summary = {k: [] for k in (
            "uploaded", "imported", "updated_local", "updated_cloud", "removed_local", "removed_cloud",
            "purged", "path_rejected")}

    def check(self):
        _check(self.user_id, self.is_signed_out)

    def texts_confidential(self, f):
        return bool(self.match("\n".join(str(f.get(k) or "") for k in ("id", "name", "github", "marketing_angle", "local_path"))))


def _entry_confidential(entry, ctx):
    """True if the repo is confidential by flag, by product, or because its text names a confidential product."""
    if ctx.guard.is_confidential_repo(entry):
        return True
    fields = local_fields(entry) or {"id": entry.get("id"), "name": entry.get("name"), "github": entry.get("github"),
                                     "marketing_angle": entry.get("marketing_angle"), "local_path": entry.get("local_path")}
    return ctx.texts_confidential(fields)


def _find_entry(entries, rid):
    target = str(rid or "").lower()
    return next((e for e in entries if (e.get("id") or "").lower() == target), None)


def _update_local(rid, field, value):
    """Apply one field from the cloud through the same checks as an edit on the laptop. Returns the stored entry."""
    if field in ("branch", "marketing_angle") and value is None:
        value = ""
    _applying.on = True
    try:
        return repos.update_repo(rid, {field: value}, source="sync")
    finally:
        _applying.on = False


def _direction(field, l, c, s, row, m):
    if s is not _MISSING:
        if s == c:
            return "push"
        if s == l:
            return "pull"
    cloud_ts = _ts(row["field_at"].get(field))
    local_ts = _ts(m["edits"].get(field))
    if local_ts and (cloud_ts is None or local_ts > cloud_ts):
        return "push"
    return "pull"


def _purge(uid, rid, state, ctx):
    """Take a confidential repo out of the cloud for good and stop tracking it."""
    _delete_row(uid)
    ctx.deleted.add(uid)
    state["repos"].pop(uid, None)
    state["rejected"].pop(uid, None)
    ctx.summary["purged"].append(rid)


def _merge(uid, m, entry, row, state, ctx):
    """Reconcile one mapped repo. Returns the current local id, or None if the repo left the cloud (purged)."""
    cur_id = entry["id"]
    if row["confidential"] or ctx.texts_confidential(row):
        # Keep what the phone wrote on the laptop, flagged, then take the row out of the cloud.
        for f in ("marketing_angle", "name", "branch", "status"):
            if row[f] != (entry.get(f) or None):
                try:
                    _update_local(cur_id, f, row[f])
                except ValueError:
                    pass
        try:
            _update_local(cur_id, "confidential", True)
        except ValueError:
            pass
        ctx.summary["updated_local"].append(cur_id)
        _purge(uid, cur_id, state, ctx)
        return None
    if _entry_confidential(entry, ctx):
        if not entry.get("confidential"):
            try:
                _update_local(cur_id, "confidential", True)
            except ValueError:
                pass
        _purge(uid, cur_id, state, ctx)
        return None

    L = local_fields(entry)
    if L is None:
        return cur_id
    S = m["synced"]
    push, push_checked, pulled = {}, False, False
    for f in FIELDS:
        l, c, s = L[f], row[f], S.get(f, _MISSING)
        if f in ("kind", "github"):
            if l != c:
                push[f] = l
            else:
                S[f] = l
            continue
        if l == c:
            S[f] = l
            if f == "local_path" and l and row["path_status"] == "pending" and state["rejected"].get(uid) != l:
                _path_result(uid, l, True)
            continue
        if _direction(f, l, c, s, row, m) == "push":
            push[f] = l
            push_checked = push_checked or f == "local_path"
            continue
        if f == "local_path":
            if c is None or entry.get("kind") != "local":
                push[f] = l
                push_checked = True
                continue
            if state["rejected"].get(uid) == c:
                continue
            try:
                stored = _update_local(cur_id, "local_path", c)
            except ValueError as exc:
                _path_result(uid, c, False, str(exc))
                state["rejected"][uid] = c
                ctx.summary["path_rejected"].append(cur_id)
                continue
            state["rejected"].pop(uid, None)
            pulled = True
            L["local_path"] = stored["local_path"]
            if stored["local_path"] == c:
                S[f] = c
                _path_result(uid, c, True)
            else:
                push[f] = stored["local_path"]
                push_checked = True
            continue
        try:
            stored = _update_local(cur_id, f, c)
        except ValueError as exc:
            _warn(f"a phone change to {f} of {cur_id} was not applied: {exc}")
            push[f] = l
            continue
        pulled = True
        S[f] = c
        L[f] = stored.get(f)
        if f == "id":
            cur_id = stored["id"]
            m["id"] = cur_id
    if pulled:
        ctx.summary["updated_local"].append(cur_id)
        fresh = _find_entry(repos._personal_entries(), cur_id)
        if fresh is not None and _entry_confidential(fresh, ctx):
            _purge(uid, cur_id, state, ctx)
            return None
    if push:
        ctx.check()
        try:
            _save(uid, push, checked=push_checked)
        except PersonalReposError as exc:
            if exc.status in (0, 401) or exc.status >= 500:
                raise
            _warn(f"{cur_id} was not updated in the cloud: {exc.message}")
        else:
            for f, v in push.items():
                S[f] = v
            ctx.summary["updated_cloud"].append(cur_id)
    for f in list(m["edits"]):
        if S.get(f) == L.get(f) or f in push:
            m["edits"].pop(f, None)
    return cur_id


def _upload(entry, state, ctx, cloud_by_github):
    fields = local_fields(entry)
    if fields is None:
        return
    adopt = cloud_by_github.get((fields["github"] or "").lower()) if fields["github"] else None
    if adopt is not None and adopt["uid"] not in state["repos"]:
        # No shared history: the laptop's values win (the cloud's current values count as the agreed ones).
        state["repos"][adopt["uid"]] = {"id": entry["id"], "synced": {f: adopt[f] for f in FIELDS}, "edits": {}}
        cloud_by_github.pop((fields["github"] or "").lower(), None)
        return adopt
    ctx.check()
    try:
        row = _save(None, fields, checked=True)
    except PersonalReposError as exc:
        if exc.status in (0, 401) or exc.status >= 500:
            raise
        _warn(f"{entry['id']} was not added to the cloud: {exc.message}")
        return None
    uid = row.get("uid") if isinstance(row, dict) else None
    if isinstance(uid, str) and _UUID_RE.fullmatch(uid):
        state["repos"][uid] = {"id": entry["id"], "synced": dict(fields), "edits": {}}
        ctx.summary["uploaded"].append(entry["id"])
    return None


def _import(row, state, ctx):
    """A repo the phone made: keep a copy on the laptop (confidential ones as confidential), by shape only."""
    conf = row["confidential"] or ctx.texts_confidential(row)
    path = None
    if row["kind"] == "local":
        if state["rejected"].get(row["uid"]) == row["local_path"] and not conf:
            return
        try:
            path = repos.validate_local_path(row["local_path"])
        except ValueError as exc:
            if conf:
                # No usable folder, but the names must stay guarded and the text must leave the cloud.
                retired_repos.remember([{"id": row["id"], "name": row["name"], "github": row["github"],
                                         "confidential": True}], strict=True)
                _delete_row(row["uid"])
                ctx.summary["purged"].append(row["id"])
                return
            _path_result(row["uid"], row["local_path"], False, str(exc))
            state["rejected"][row["uid"]] = row["local_path"]
            ctx.summary["path_rejected"].append(row["id"])
            return
    entry = {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "status": row["status"], "confidential": conf,
    }
    for key in ("github", "branch", "marketing_angle"):
        if row[key]:
            entry[key] = row[key]
    if path:
        entry["local_path"] = path
    if len(repos._personal_entries()) >= MAX_REPOS:
        return
    stored = repos.import_personal(entry)
    ctx.summary["imported"].append(stored["id"])
    if conf:
        _delete_row(row["uid"])
        ctx.summary["purged"].append(stored["id"])
        return
    fields = local_fields(stored) or {}
    state["repos"][row["uid"]] = {"id": stored["id"], "synced": dict(fields), "edits": {}}
    if stored["id"] != row["id"]:
        _save(row["uid"], {"id": stored["id"]})
        state["repos"][row["uid"]]["synced"]["id"] = stored["id"]
    if path:
        if path == row["local_path"]:
            _path_result(row["uid"], path, True)
        else:
            _save(row["uid"], {"local_path": path}, checked=True)


def sync(user_id, is_signed_out=None):
    """One repository sync for the signed-in user. Returns the repo ids touched, by kind of change."""
    _check(user_id, is_signed_out)
    with _lock:
        ctx = _Ctx(user_id, is_signed_out)
        state = _load_state(user_id)
        try:
            rows = _pull(user_id)
        except PersonalReposError as exc:
            if _table_missing(exc):
                return ctx.summary
            raise
        by_uid = {r["uid"]: r for r in rows}
        try:
            for uid, gone_id in list(state["forget"].items()):
                ctx.check()
                if uid in by_uid:
                    _delete_row(uid)
                    by_uid.pop(uid)
                    ctx.deleted.add(uid)
                    ctx.summary["removed_cloud"].append(gone_id)
                del state["forget"][uid]

            entries = repos._personal_entries()
            mass_loss = not by_uid and len(state["repos"]) >= 2
            if mass_loss:
                # An empty list for a user who had several repos looks like a fault, not a decision: start over.
                state["repos"].clear()
                state["rejected"].clear()
            for uid, m in list(state["repos"].items()):
                ctx.check()
                entry = _find_entry(entries, m["id"])
                row = by_uid.get(uid)
                if row is None:
                    if entry is not None:
                        _applying.on = True
                        try:
                            repos.remove_repo(entry["id"])
                        finally:
                            _applying.on = False
                        ctx.summary["removed_local"].append(entry["id"])
                    del state["repos"][uid]
                    state["rejected"].pop(uid, None)
                    continue
                if entry is None:
                    del state["repos"][uid]
                    state["rejected"].pop(uid, None)
                    continue
                _merge(uid, m, entry, row, state, ctx)

            entries = repos._personal_entries()
            mapped_ids = {m["id"].lower() for m in state["repos"].values()}
            by_github = {(r["github"] or "").lower(): r for r in rows
                         if r["github"] and r["uid"] in by_uid and r["uid"] not in state["repos"]}
            for entry in entries:
                if (entry.get("id") or "").lower() in mapped_ids:
                    continue
                if _entry_confidential(entry, ctx):
                    continue
                if len(state["repos"]) >= MAX_REPOS:
                    break
                adopted = _upload(entry, state, ctx, by_github)
                if adopted is not None:
                    _merge(adopted["uid"], state["repos"][adopted["uid"]], entry, adopted, state, ctx)

            for row in rows:
                ctx.check()
                if row["uid"] in state["repos"] or row["uid"] not in by_uid or row["uid"] in ctx.deleted:
                    continue
                _import(row, state, ctx)
        finally:
            state["user_id"] = user_id
            _save_state(state)
        return ctx.summary
