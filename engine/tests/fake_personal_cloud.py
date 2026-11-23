"""A fake of the cloud side of personal keys and personal repos, for tests. It follows the SQL functions and RLS.

It stands in for team_auth.rest (patch it with `patch("team_auth.rest", cloud.rest)`), so no network is used. Keys
here are obviously fake strings. The fake keeps keys in `self.vault` only; every request is recorded in `requests`
(path and body) so tests can check what was sent where.
"""

import hashlib
import re
import urllib.parse
import uuid
from datetime import datetime, timezone

import team_auth

SLOT_RE = re.compile(r"NVIDIA_NIM_API_KEY(?:[1-9]|10)?")
KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_-]{20,200}")
FIELDS = ("id", "name", "kind", "github", "local_path", "branch", "status", "marketing_angle", "confidential")


def fp(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FakeCloud:
    """Per-user keys and repos; the current user is whoever team_auth says is signed in (or `self.uid`)."""

    def __init__(self):
        self.uid = None
        self.vault = {}  # (owner, slot) -> {"key", "version"}
        self.repos = {}  # uid -> row dict
        self.requests = []
        self.down = False
        self.inactive = set()
        self.extra_rows = []  # raw rows the server returns after the real ones, whatever they look like

    def user(self):
        sess = team_auth.get_session_info() or {}
        return self.uid or sess.get("user_id")

    def err(self, message, status=400):
        return team_auth.TeamAuthError(message, status=status)

    # -- helpers tests use to play "the other device" ----------------------------------------------------------
    def put_key(self, slot, key, owner=None):
        owner = owner or self.user()
        cur = self.vault.get((owner, slot))
        self.vault[(owner, slot)] = {"key": key, "version": (cur["version"] + 1) if cur else 1}

    def drop_key(self, slot, owner=None):
        self.vault.pop((owner or self.user(), slot), None)

    def keys_of(self, owner=None):
        owner = owner or self.user()
        return {s: v["key"] for (o, s), v in self.vault.items() if o == owner}

    def rows_of(self, owner=None):
        owner = owner or self.user()
        return [r for r in self.repos.values() if r["owner_id"] == owner]

    def add_repo_row(self, owner=None, **fields):
        owner = owner or self.user()
        row = self._save(owner, None, fields, True)
        return row

    def edit_row(self, uid, fields, checked=False):
        row = self.repos[uid]
        return self._save(row["owner_id"], uid, fields, checked)

    # -- the PostgREST surface ----------------------------------------------------------------------------------
    def rest(self, method, path, body=None, headers=None, timeout=None):
        self.requests.append({"method": method, "path": path, "body": body})
        if self.down:
            raise team_auth.TeamAuthError("Network error contacting Supabase: down", status=0, code="network_error")
        owner = self.user()
        if not owner:
            raise self.err("not signed in", 401)
        rel = path.lstrip("/")
        if rel.startswith("rpc/"):
            return self._rpc(owner, rel[4:], body or {})
        base = rel.split("?", 1)[0]
        if base == "personal_repos" and method == "GET":
            if owner in self.inactive:
                return []
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(rel).query)
            limit = int((qs.get("limit") or ["1000"])[0])
            rows = sorted(self.rows_of(owner), key=lambda r: (r["created_at"], r["id"]))
            return [dict(r) for r in rows[:limit]] + list(self.extra_rows)
        if method == "GET":
            return []
        return None

    def _active(self, owner):
        if owner in self.inactive:
            raise self.err("Active member access required", 400)

    def _rpc(self, owner, name, body):
        self._active(owner)
        if name == "personal_key_list":
            return [
                {"name": s, "last4": v["key"][-4:], "fingerprint": fp(v["key"]), "version": v["version"],
                 "updated_at": "2026-01-01T00:00:00Z"}
                for (o, s), v in sorted(self.vault.items()) if o == owner
            ]
        if name == "personal_key_get":
            names = body.get("p_names")
            if not isinstance(names, list) or len(names) > 11:
                raise self.err("Too many key slots requested")
            return [{"name": s, "api_key": v["key"]} for (o, s), v in sorted(self.vault.items())
                    if o == owner and s in names]
        if name == "personal_key_set":
            slot, key = body.get("p_name"), body.get("p_key")
            if not isinstance(slot, str) or not SLOT_RE.fullmatch(slot):
                raise self.err("Unknown key slot")
            if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                raise self.err("Invalid NVIDIA NIM API key format")
            for (o, s), v in self.vault.items():
                if o == owner and s != slot and v["key"] == key:
                    raise self.err("That key is already saved in another slot")
            self.put_key(slot, key, owner)
            v = self.vault[(owner, slot)]
            return [{"name": slot, "last4": key[-4:], "fingerprint": fp(key), "version": v["version"]}]
        if name == "personal_key_delete":
            slot = body.get("p_name")
            if not isinstance(slot, str) or not SLOT_RE.fullmatch(slot):
                raise self.err("Unknown key slot")
            return self.vault.pop((owner, slot), None) is not None
        if name == "personal_repo_save":
            row = self._save(owner, body.get("p_uid"), body.get("p_fields"), bool(body.get("p_checked")))
            return dict(row)
        if name == "personal_repo_delete":
            row = self.repos.get(body.get("p_uid"))
            if row and row["owner_id"] == owner:
                del self.repos[body["p_uid"]]
                return True
            return False
        if name == "personal_repo_path_result":
            row = self.repos.get(body.get("p_uid"))
            if not row or row["owner_id"] != owner:
                raise self.err("Repository not found")
            if row["local_path"] == body.get("p_path"):
                row["path_status"] = "ok" if body.get("p_ok") else "rejected"
                row["path_reason"] = None if body.get("p_ok") else (body.get("p_reason") or "")[:200] or None
            return None
        raise self.err("unknown function", 404)

    def _save(self, owner, uid, fields, checked):
        if not isinstance(fields, dict):
            raise self.err("Repository fields must be an object")
        for k in fields:
            if k not in FIELDS:
                raise self.err("Unknown repository field")
        now = datetime.now(timezone.utc).isoformat()
        if uid is None:
            if len(self.rows_of(owner)) >= 100:
                raise self.err("At most 100 repositories are allowed")
            for r in self.rows_of(owner):
                if r["id"] == fields.get("id"):
                    raise self.err("duplicate key", 409)
                if fields.get("github") and (r.get("github") or "").lower() == fields["github"].lower():
                    raise self.err("duplicate key", 409)
            row = {
                "uid": str(uuid.uuid4()), "owner_id": owner, "id": fields.get("id"), "name": fields.get("name"),
                "kind": fields.get("kind"), "github": fields.get("github"), "local_path": fields.get("local_path"),
                "path_status": "pending" if fields.get("local_path") and not checked else "ok", "path_reason": None,
                "branch": fields.get("branch"), "status": fields.get("status") or "in_development",
                "marketing_angle": fields.get("marketing_angle"), "confidential": bool(fields.get("confidential")),
                "rev": 1, "field_rev": {f: 1 for f in FIELDS}, "field_at": {f: now for f in FIELDS},
                "created_at": now, "updated_at": now,
            }
            self.repos[row["uid"]] = row
            return row
        row = self.repos.get(uid)
        if not row or row["owner_id"] != owner:
            raise self.err("Repository not found")
        changed = [f for f in fields if fields[f] != row[f]]
        if not changed:
            return row
        for r in self.rows_of(owner):
            if r["uid"] != uid and "id" in changed and r["id"] == fields["id"]:
                raise self.err("duplicate key", 409)
        row["rev"] += 1
        for f in changed:
            row[f] = fields[f]
            row["field_rev"][f] = row["rev"]
            row["field_at"][f] = now
            if f == "local_path":
                row["path_status"] = "ok" if checked or fields[f] is None else "pending"
                row["path_reason"] = None
        row["updated_at"] = now
        return row
