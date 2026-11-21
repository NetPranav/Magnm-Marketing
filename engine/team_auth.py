#!/usr/bin/env python3
"""Google PKCE sign-in, session storage, token refresh, and admin invites via Supabase."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
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

import paths

REQUEST_TIMEOUT_S = 15
PKCE_TTL_S = 600
REFRESH_LEEWAY_S = 60

NOT_INVITED_MESSAGE = "This Google account is not invited to Magnm Studio. Ask your admin to invite it."
ADMIN_ONLY_INVITES_MESSAGE = "Only admins can manage invites."
NOT_CONFIGURED_MESSAGE = "Team sign-in is not configured."

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_state_lock = threading.Lock()
_session_lock = threading.Lock()
_pending_login = None
_last_error = None


class TeamAuthError(Exception):
    """Error raised for team authentication, Supabase REST, or invite failures."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


def _session_path():
    return paths.data("state", "team_session.json")


def _config_path():
    # Read the bundled copy, not the data-folder copy: app updates must be able to change the project.
    return paths.resource("config", "supabase.json")


def get_config():
    """Return Supabase configuration dict with env overrides and configured flag."""
    file_cfg = {}
    cfg_path = _config_path()
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                file_cfg = loaded
        except Exception:
            file_cfg = {}

    env_url = os.environ.get("MAGNM_SUPABASE_URL")
    env_key = os.environ.get("MAGNM_SUPABASE_PUBLISHABLE_KEY")

    raw_url = env_url if env_url and env_url.strip() else file_cfg.get("url", "")
    raw_key = env_key if env_key and env_key.strip() else file_cfg.get("publishable_key", "")

    url = str(raw_url or "").strip().rstrip("/")
    publishable_key = str(raw_key or "").strip()
    return {
        "configured": bool(url and publishable_key),
        "url": url,
        "publishable_key": publishable_key,
    }


def _require_config():
    cfg = get_config()
    if not cfg["configured"]:
        raise TeamAuthError(NOT_CONFIGURED_MESSAGE, status=503, code="not_configured")
    return cfg


def pkce_challenge(verifier):
    """Compute the RFC 7636 S256 code challenge (base64url without padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _extract_error_message(raw_bytes, status):
    if not raw_bytes:
        return f"Supabase request failed (HTTP {status})"
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("message", "msg", "error_description", "error", "hint"):
                val = parsed.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:
        pass
    return text or f"Supabase request failed (HTTP {status})"


def _http_json(method, url, headers=None, body=None, timeout=REQUEST_TIMEOUT_S):
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if body is not None:
        req_headers.setdefault("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_bytes = b""
        try:
            err_bytes = exc.read()
        except Exception:
            pass
        msg = _extract_error_message(err_bytes, exc.code)
        raise TeamAuthError(msg, status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TeamAuthError(f"Network error contacting Supabase: {exc}", status=0, code="network_error") from exc
    except json.JSONDecodeError as exc:
        raise TeamAuthError("Invalid JSON response from Supabase.", status=502, code="bad_gateway") from exc


def _load_session():
    path = _session_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("access_token"):
            return data
    except Exception:
        return None
    return None


def _save_session(session):
    path = _session_path()
    state_dir = os.path.dirname(path)
    os.makedirs(state_dir, exist_ok=True)
    payload = json.dumps(session, indent=2) + "\n"
    fd, tmp_path = tempfile.mkstemp(prefix="team_session_", suffix=".tmp", dir=state_dir)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def _delete_session_file():
    path = _session_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _jwt_sub(token):
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        sub = claims.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


def _session_from_token_response(data, previous=None):
    if not isinstance(data, dict) or not data.get("access_token"):
        raise TeamAuthError("Supabase token response was incomplete.", status=502, code="invalid_token_response")
    now = int(time.time())
    expires_at = data.get("expires_at")
    if isinstance(expires_at, (int, float)):
        exp = int(expires_at)
    else:
        expires_in = int(data.get("expires_in") or 3600)
        exp = now + expires_in

    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    prev = previous or {}
    access_token = str(data["access_token"])
    user_id = user.get("id") or prev.get("user_id") or _jwt_sub(access_token)
    email = user.get("email") or prev.get("email")
    refresh_token = data.get("refresh_token") or prev.get("refresh_token") or ""
    return {
        "access_token": access_token,
        "refresh_token": str(refresh_token),
        "expires_at": exp,
        "user_id": str(user_id) if user_id else "",
        "email": str(email).lower() if email else "",
    }


def _set_last_error(message):
    global _last_error
    with _state_lock:
        _last_error = message


def get_last_error():
    """Return the last OAuth callback or login error, if any."""
    with _state_lock:
        return _last_error


def start_login(port):
    """Begin a Google PKCE login flow and return the Supabase authorize URL."""
    global _pending_login, _last_error
    cfg = _require_config()
    try:
        port_num = int(port)
    except (TypeError, ValueError) as exc:
        raise TeamAuthError("Invalid loopback port.", status=400) from exc
    if port_num <= 0 or port_num > 65535:
        raise TeamAuthError("Invalid loopback port.", status=400)

    verifier = secrets.token_urlsafe(48)
    challenge = pkce_challenge(verifier)
    state = secrets.token_urlsafe(24)

    with _state_lock:
        _pending_login = {
            "state": state,
            "verifier": verifier,
            "created_at": time.time(),
        }
        _last_error = None

    redirect_to = f"http://127.0.0.1:{port_num}/auth/callback?{urllib.parse.urlencode({'s': state})}"
    query = urllib.parse.urlencode({
        "provider": "google",
        "redirect_to": redirect_to,
        "code_challenge": challenge,
        "code_challenge_method": "s256",
    })
    return f"{cfg['url']}/auth/v1/authorize?{query}"


def cancel_login():
    """Cancel any pending Google PKCE login."""
    global _pending_login
    with _state_lock:
        _pending_login = None


def _normalize_query(query):
    if isinstance(query, str):
        parsed = urllib.parse.parse_qs(query.lstrip("?"), keep_blank_values=True)
    elif isinstance(query, dict):
        parsed = query
    else:
        parsed = {}

    out = {}
    for k, v in parsed.items():
        if isinstance(v, (list, tuple)):
            out[str(k)] = str(v[0]) if v else ""
        elif v is None:
            out[str(k)] = ""
        else:
            out[str(k)] = str(v)
    return out


def finish_login(query):
    """Validate callback state, exchange PKCE auth_code for tokens, and store session."""
    global _pending_login, _last_error
    cfg = _require_config()
    params = _normalize_query(query)
    state = params.get("s", "")
    code = params.get("code", "")
    oauth_err = params.get("error", "")
    err_desc = params.get("error_description", "")

    with _state_lock:
        pending = _pending_login
        if not pending or not state or not secrets.compare_digest(state, pending["state"]):
            raise TeamAuthError("Sign-in state is invalid or already used.", status=400, code="invalid_state")
        _pending_login = None
        expired = (time.time() - pending["created_at"]) > PKCE_TTL_S
        if expired:
            _last_error = "Sign-in session expired. Please try again."
            raise TeamAuthError(_last_error, status=400, code="expired_state")

    if oauth_err or err_desc:
        combined = f"{oauth_err} {err_desc}"
        if "Database error saving new user" in combined:
            msg = NOT_INVITED_MESSAGE
        else:
            msg = "Google sign-in could not be completed. Please try again."
        _set_last_error(msg)
        raise TeamAuthError(msg, status=400, code="oauth_error")

    if not code:
        msg = "Missing authorization code from Google sign-in."
        _set_last_error(msg)
        raise TeamAuthError(msg, status=400, code="missing_code")

    token_url = f"{cfg['url']}/auth/v1/token?grant_type=pkce"
    try:
        data = _http_json(
            "POST",
            token_url,
            headers={"apikey": cfg["publishable_key"]},
            body={"auth_code": code, "code_verifier": pending["verifier"]},
        )
    except TeamAuthError as exc:
        msg = NOT_INVITED_MESSAGE if "Database error saving new user" in exc.message else exc.message
        _set_last_error(msg)
        raise TeamAuthError(msg, status=exc.status, code=exc.code) from exc

    session = _session_from_token_response(data)
    with _session_lock:
        _save_session(session)
    _set_last_error(None)
    return {
        "user_id": session.get("user_id", ""),
        "email": session.get("email", ""),
    }


def get_access_token():
    """Return a valid bearer access token, refreshing safely if expiring within 60 seconds."""
    cfg = _require_config()
    with _session_lock:
        session = _load_session()
        if not session:
            raise TeamAuthError("Not signed in.", status=401, code="not_signed_in")

        expires_at = float(session.get("expires_at") or 0)
        if expires_at - time.time() > REFRESH_LEEWAY_S:
            return session["access_token"]

        refresh_token = session.get("refresh_token") or ""
        if not refresh_token:
            _delete_session_file()
            raise TeamAuthError("Your session expired. Please sign in again.", status=401, code="session_expired")

        refresh_url = f"{cfg['url']}/auth/v1/token?grant_type=refresh_token"
        try:
            data = _http_json(
                "POST",
                refresh_url,
                headers={"apikey": cfg["publishable_key"]},
                body={"refresh_token": refresh_token},
            )
        except TeamAuthError as exc:
            if exc.status in (400, 401):
                _delete_session_file()
                raise TeamAuthError(
                    "Your session expired. Please sign in again.",
                    status=401,
                    code="session_expired",
                ) from exc
            raise

        updated = _session_from_token_response(data, previous=session)
        _save_session(updated)
        return updated["access_token"]


def rest(method, path, body=None, headers=None):
    """Call a Supabase PostgREST endpoint under /rest/v1/ with apikey and user bearer token."""
    cfg = _require_config()
    token = get_access_token()
    rel = str(path or "").lstrip("/")
    url = f"{cfg['url']}/rest/v1/{rel}"
    req_headers = {
        "apikey": cfg["publishable_key"],
        "Authorization": f"Bearer {token}",
    }
    if headers:
        req_headers.update(headers)
    return _http_json(method, url, headers=req_headers, body=body)


def current_account():
    """Return current team account configuration, sign-in status, and profile."""
    cfg = get_config()
    last_err = get_last_error()
    if not cfg["configured"]:
        return {
            "configured": False,
            "signed_in": False,
            "user": None,
            "last_error": last_err,
        }

    try:
        get_access_token()
    except TeamAuthError as exc:
        if exc.status == 401 or exc.code in ("not_signed_in", "session_expired"):
            return {
                "configured": True,
                "signed_in": False,
                "user": None,
                "last_error": last_err or (exc.message if exc.code == "session_expired" else None),
            }
        raise

    with _session_lock:
        session = _load_session() or {}
    uid = session.get("user_id") or _jwt_sub(session.get("access_token"))
    if not uid:
        return {
            "configured": True,
            "signed_in": False,
            "user": None,
            "last_error": last_err,
        }

    q_uid = urllib.parse.quote(str(uid), safe="")
    rows = rest(
        "GET",
        f"profiles?id=eq.{q_uid}&select=id,email,display_name,avatar_url,role,active",
    )
    if not isinstance(rows, list) or len(rows) == 0:
        return {
            "configured": True,
            "signed_in": True,
            "deactivated": True,
            "user": None,
            "last_error": last_err,
        }

    row = rows[0]
    if not row.get("active", True):
        return {
            "configured": True,
            "signed_in": True,
            "deactivated": True,
            "user": None,
            "last_error": last_err,
        }

    return {
        "configured": True,
        "signed_in": True,
        "deactivated": False,
        "user": {
            "id": row.get("id"),
            "email": row.get("email"),
            "display_name": row.get("display_name"),
            "avatar_url": row.get("avatar_url"),
            "role": row.get("role"),
            "active": bool(row.get("active", True)),
        },
        "last_error": last_err,
    }


def logout():
    """Sign out on Supabase (best effort) and delete the local session file."""
    cfg = get_config()
    with _session_lock:
        session = _load_session()
        access_token = session.get("access_token") if session else None
        if cfg["configured"] and access_token:
            try:
                _http_json(
                    "POST",
                    f"{cfg['url']}/auth/v1/logout",
                    headers={
                        "apikey": cfg["publishable_key"],
                        "Authorization": f"Bearer {access_token}",
                    },
                )
            except Exception:
                pass
        _delete_session_file()
    _set_last_error(None)


def _normalize_invite_email(email):
    cleaned = str(email or "").strip().lower()
    if not _EMAIL_RE.match(cleaned):
        raise TeamAuthError("Enter a valid email address.", status=400, code="invalid_email")
    return cleaned


def _normalize_invite_role(role):
    cleaned = str(role or "member").strip().lower()
    if cleaned not in ("admin", "member"):
        raise TeamAuthError("Invite role must be 'admin' or 'member'.", status=400, code="invalid_role")
    return cleaned


def list_invites():
    """List team invites (admin only; RLS enforces access)."""
    _require_config()
    try:
        rows = rest(
            "GET",
            "invites?select=email,role,invited_by,created_at,accepted_at,revoked_at&order=created_at.desc",
        )
    except TeamAuthError as exc:
        if exc.status in (401, 403):
            raise TeamAuthError(ADMIN_ONLY_INVITES_MESSAGE, status=403, code="forbidden") from exc
        raise
    return rows if isinstance(rows, list) else []


def create_invite(email, role="member"):
    """Create a new invite for a Google account email (admin only)."""
    _require_config()
    norm_email = _normalize_invite_email(email)
    norm_role = _normalize_invite_role(role)
    try:
        rows = rest(
            "POST",
            "invites",
            body={"email": norm_email, "role": norm_role},
            headers={"Prefer": "return=representation"},
        )
    except TeamAuthError as exc:
        if exc.status in (401, 403):
            raise TeamAuthError(ADMIN_ONLY_INVITES_MESSAGE, status=403, code="forbidden") from exc
        if exc.status == 409 or "duplicate" in exc.message.lower():
            raise TeamAuthError("An invite for this email already exists.", status=409, code="duplicate_invite") from exc
        raise

    if not isinstance(rows, list) or not rows:
        raise TeamAuthError(ADMIN_ONLY_INVITES_MESSAGE, status=403, code="forbidden")
    return rows[0]


def revoke_invite(email):
    """Revoke an invite by setting revoked_at to now (admin only)."""
    _require_config()
    norm_email = _normalize_invite_email(email)
    q_email = urllib.parse.quote(norm_email, safe="")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        rows = rest(
            "PATCH",
            f"invites?email=eq.{q_email}&revoked_at=is.null",
            body={"revoked_at": now_iso},
            headers={"Prefer": "return=representation"},
        )
    except TeamAuthError as exc:
        if exc.status in (401, 403):
            raise TeamAuthError(ADMIN_ONLY_INVITES_MESSAGE, status=403, code="forbidden") from exc
        raise

    if not isinstance(rows, list) or not rows:
        raise TeamAuthError(ADMIN_ONLY_INVITES_MESSAGE, status=403, code="forbidden")
    return rows[0]
