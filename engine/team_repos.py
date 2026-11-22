#!/usr/bin/env python3
"""Global repositories: an admin adds GitHub repos once and every member's engine receives them.

Everything that comes back from the server is treated as hostile. Each row is rebuilt field by field from a strict
whitelist, so a slug, name or branch can never become a path, a URL host, a git option or a prompt instruction.
The list is cached apart from the user's own config/repos.json (state/team_repos.json) and never written into it.
"""

import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths
import retired_repos
import team_auth

MAX_REPOS = 100
STATUSES = ("released", "in_development", "secret")

_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_REPO_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}")
_NAME_RE = re.compile(r"\w[\w .,()&'+/-]{0,79}")
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_SELECT = "id,github,name,product_id,branch,confidential,status"

_lock = threading.RLock()


class TeamReposError(Exception):
    """A refused or failed global repo action, with an HTTP-style status for the API."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


class SignedOutAbort(Exception):
    """The user signed out (or switched account) while a pull was running."""


def _cache_path():
    return paths.data("state", "team_repos.json")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_github(value):
    """Return a strict 'owner/repo' string, or None. Accepts only that shape, never a URL."""
    if not isinstance(value, str):
        return None
    parts = value.split("/")
    if len(parts) != 2:
        return None
    owner, repo = parts
    if not _OWNER_RE.fullmatch(owner) or not _REPO_RE.fullmatch(repo) or repo in (".", ".."):
        return None
    return f"{owner}/{repo}"


def valid_branch(value):
    """True for a branch name that is safe to show, store and pass on (no path tricks, never an option)."""
    return (
        isinstance(value, str)
        and bool(_BRANCH_RE.fullmatch(value))
        and ".." not in value
        and "//" not in value
        and not value.endswith(("/", ".lock"))
    )


def validate_row(raw):
    """Rebuild one server row from whitelisted, checked fields. None if it is not a valid global repo."""
    if not isinstance(raw, dict):
        return None
    repo_id, product_id = raw.get("id"), raw.get("product_id")
    name, status = raw.get("name"), raw.get("status", "in_development")
    github = normalize_github(raw.get("github"))
    if not (isinstance(repo_id, str) and _ID_RE.fullmatch(repo_id)) or github is None:
        return None
    if not (isinstance(product_id, str) and _ID_RE.fullmatch(product_id)):
        return None
    if not (isinstance(name, str) and _NAME_RE.fullmatch(name)) or status not in STATUSES:
        return None
    branch = raw.get("branch")
    if branch in (None, ""):
        branch = None
    elif not valid_branch(branch):
        return None
    # A confidential flag that is not a real boolean counts as confidential.
    confidential = raw.get("confidential") if isinstance(raw.get("confidential"), bool) else True
    row = {
        "id": repo_id,
        "name": name,
        "product_id": product_id,
        "kind": "github",
        "github": github,
        "confidential": confidential,
        "status": status,
        "scope": "global",
    }
    if branch:
        row["branch"] = branch
    return row


def validate_rows(rows):
    """Validate a list of server rows: drops bad ones and duplicates, keeps at most MAX_REPOS."""
    if not isinstance(rows, list):
        raise TeamReposError("Team repository list was not a list.", status=502, code="bad_payload")
    out, ids, slugs = [], set(), set()
    for raw in rows:
        row = validate_row(raw)
        if row is None or row["id"] in ids or row["github"].lower() in slugs:
            continue
        ids.add(row["id"])
        slugs.add(row["github"].lower())
        out.append(row)
        if len(out) >= MAX_REPOS:
            break
    return out


def _session_user_id():
    sess = team_auth.get_session_info()
    uid = str((sess or {}).get("user_id") or "").strip()
    return uid or None


def _read_cache_file():
    """Return the parsed cache dict, None if there is no file. Raises if the file is unreadable."""
    path = _cache_path()
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("team repo cache is not an object")
    return data


def cached():
    """The global repos for the signed-in user, from the cache. Empty when signed out or nothing was pulled yet.

    The cache belongs to one account: it is ignored for any other. An unreadable cache raises, so callers fail
    closed instead of treating confidential global repos as absent.
    """
    uid = _session_user_id()
    if not uid:
        return []
    with _lock:
        data = _read_cache_file()
        if data and str(data.get("user_id") or "") != uid:
            # Another account signed in on this machine: the old list goes, its confidential names stay guarded.
            clear()
            return []
    if not data:
        return []
    return validate_rows(data.get("repos") or [])


def _write_cache(user_id, repos_list):
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="team_repos_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"user_id": user_id, "fetched_at": _now_iso(), "repos": repos_list}, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _known_entries():
    """Whatever the cache holds now, validated, without checking the session. Best effort."""
    try:
        data = _read_cache_file()
        return (data or {}).get("user_id"), validate_rows((data or {}).get("repos") or [])
    except Exception:
        return None, []


def clear():
    """Forget the global repos (sign-out, account change, deactivation). Confidential names stay guarded."""
    with _lock:
        _, entries = _known_entries()
        retired_repos.remember(entries, strict=False)
        try:
            os.remove(_cache_path())
        except FileNotFoundError:
            pass
        except OSError:
            # Cannot delete: empty it, so nothing stale stays readable.
            try:
                _write_cache("", [])
            except Exception:
                pass


def _rest(method, path, body=None, headers=None):
    try:
        try:
            return team_auth.rest(method, path, body=body, headers=headers)
        except team_auth.TeamAuthError as exc:
            if exc.status == 401 and exc.code not in ("not_signed_in", "session_expired", "not_configured"):
                team_auth.refresh_access_token()
                return team_auth.rest(method, path, body=body, headers=headers)
            raise
    except team_auth.TeamAuthError as exc:
        raise TeamReposError(exc.message, status=_api_status(exc.status), code=exc.code) from exc


def _api_status(status):
    if isinstance(status, int) and 400 <= status < 500:
        return status
    return 502


def _table_missing(exc):
    """True when an older server has no team_repos table."""
    text = exc.message.lower()
    return exc.status == 404 or (exc.status == 400 and "team_repos" in text and "does not exist" in text)


def pull(user_id, is_signed_out=None):
    """Fetch the global list and cache it for `user_id`. Returns the list, or None if nothing was changed.

    A failed pull keeps the last good list. An older server without the table is not an error.
    """
    if is_signed_out and is_signed_out():
        raise SignedOutAbort()
    try:
        rows = _rest("GET", f"team_repos?select={_SELECT}&order=created_at.asc,id.asc&limit={MAX_REPOS + 1}")
    except TeamReposError as exc:
        if _table_missing(exc):
            return None
        raise
    fresh = validate_rows(rows)
    with _lock:
        if (is_signed_out and is_signed_out()) or _session_user_id() != str(user_id):
            raise SignedOutAbort()
        old_user, old = _known_entries()
        keep = {r["id"] for r in fresh}
        gone = [r for r in old if r["id"] not in keep] if old_user == str(user_id) else old
        retired_repos.remember(gone, strict=False)
        _write_cache(str(user_id), fresh)
    return fresh


def sync(user_id, role=None, is_signed_out=None):
    """One sync-cycle step: pull the list so every member has it without any click."""
    return pull(user_id, is_signed_out=is_signed_out)


def notify_signed_out():
    """Sign-out hook: drop the cached list."""
    clear()


def _require_admin():
    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        status = 403 if exc.status in (401, 403) else _api_status(exc.status)
        raise TeamReposError(exc.message if status != 403 else "Sign in as an admin to manage team repositories.",
                             status=status, code=exc.code) from exc
    user = acct.get("user") or {}
    if (
        not acct.get("configured") or not acct.get("signed_in") or acct.get("deactivated")
        or not user.get("active", True) or str(user.get("role") or "").lower() != "admin"
    ):
        raise TeamReposError("Only admins can manage team repositories.", status=403, code="forbidden")
    uid = str(user.get("id") or "").strip()
    if not uid:
        raise TeamReposError("Only admins can manage team repositories.", status=403, code="forbidden")
    return uid


def _unique_id(base, taken):
    base = (base or "repo")[:62].strip("-") or "repo"
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = f"{base[:63 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def _row_from_spec(spec, taken_ids):
    """Build and check the row an admin wants to add. GitHub only."""
    import repos

    if not isinstance(spec, dict):
        raise TeamReposError("Repository details must be an object.")
    github = normalize_github(repos.parse_github_slug(spec.get("github")))
    if github is None:
        raise TeamReposError("A GitHub repository ('owner/repo' or its URL) is required. Local paths cannot be shared.")
    name = " ".join(str(spec.get("name") or "").split()) or github.split("/")[-1]
    if not _NAME_RE.fullmatch(name):
        raise TeamReposError("Repository name must be 1-80 letters, numbers, spaces or . , ( ) & ' + / -")
    repo_id = _unique_id(repos._slugify(name), taken_ids)
    product_raw = str(spec.get("product_id") or "").strip()
    product_id = repos._slugify(product_raw)[:63].strip("-") if product_raw else repo_id
    branch = str(spec.get("branch") or "").strip() or None
    if branch is not None and not valid_branch(branch):
        raise TeamReposError("That branch name is not allowed.")
    status = str(spec.get("status") or "in_development").strip()
    if status not in STATUSES:
        raise TeamReposError("Status must be released, in_development or secret.")
    confidential = spec.get("confidential", False)
    if not isinstance(confidential, bool):
        raise TeamReposError("confidential must be true or false.")
    row = {"id": repo_id, "github": github, "name": name, "product_id": product_id,
           "confidential": confidential, "status": status}
    if branch:
        row["branch"] = branch
    if validate_row(row) is None:
        raise TeamReposError("Those repository details are not valid.")
    return row


def add(spec):
    """Admin: share a GitHub repo with the whole team. Returns the new global repo."""
    uid = _require_admin()
    current = pull_for_admin(uid)
    row = _row_from_spec(spec, {r["id"] for r in current})
    if any(r["github"].lower() == row["github"].lower() for r in current):
        raise TeamReposError("That GitHub repository is already shared with the team.", status=409, code="duplicate")
    try:
        _rest("POST", "team_repos", body=row, headers={"Prefer": "return=representation"})
    except TeamReposError as exc:
        if exc.status == 409:
            raise TeamReposError("That GitHub repository is already shared with the team.", status=409,
                                 code="duplicate") from exc
        if exc.status == 404:
            raise TeamReposError(
                "The team server does not support shared repositories yet. Apply the latest database update.",
                status=502, code="not_supported",
            ) from exc
        raise
    pull_for_admin(uid)
    return validate_row({**row, "scope": "global"}) or row


def promote(repo_id):
    """Admin: turn one of their own GitHub repos into a global repo."""
    import repos

    _require_admin()
    target = str(repo_id or "").strip().lower()
    mine = next((r for r in repos.list_personal_repos() if (r.get("id") or "").lower() == target), None)
    if mine is None:
        raise TeamReposError("Repository not found.", status=404)
    if not mine.get("github"):
        raise TeamReposError("Only GitHub repositories can be shared with the team.")
    spec = {k: mine.get(k) for k in ("github", "name", "product_id", "branch", "status")}
    spec["confidential"] = bool(mine.get("confidential"))
    if mine.get("branch") and not valid_branch(mine["branch"]):
        spec["branch"] = None
    if mine.get("status") not in STATUSES:
        spec["status"] = "in_development"
    return add(spec)


def remove(repo_id):
    """Admin: stop sharing a global repo with the team. Its confidential names stay guarded locally."""
    uid = _require_admin()
    rid = str(repo_id or "").strip()
    if not _ID_RE.fullmatch(rid):
        raise TeamReposError("Team repository not found.", status=404)
    current = pull_for_admin(uid)
    if not any(r["id"] == rid for r in current):
        raise TeamReposError("Team repository not found.", status=404)
    retired_repos.remember([r for r in current if r["id"] == rid], strict=True)
    _rest("DELETE", f"team_repos?id=eq.{urllib.parse.quote(rid, safe='')}")
    pull_for_admin(uid)
    return rid


def pull_for_admin(uid):
    """Refresh the cache for the acting admin and return the list. Failures surface to the caller."""
    fresh = pull(uid)
    return fresh if fresh is not None else cached()
