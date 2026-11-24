#!/usr/bin/env python3
"""Magnm PR & Social Media Marketing Agency Command Center (v5)
Multi-model NVIDIA NIM inference studio, PR & crisis console, business research radar,
codebase intelligence hub, editorial & Palette v3 visual card studio, CEO approval gate,
centralized Assets Library, Global Team Room, and 13-agent capability-guarded chat.

Strict zero-emoji policy; uniform embedded SVG vector icon system.
Runs on http://127.0.0.1:5050
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import email.parser
import email.policy
import hmac
import html
import importlib.util
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import admin_dashboard
import agent_chat
import agent_runtime
import agents
import ai_gateway
import assets_manager
import bakeoff
import automation
import board
import cloud_sync
import cmo_runtime
import coding_agents
import confidential_guard
import db
import events
import file_serving
import jobs
import key_pool
import learnings
import mcp_tools
import media_previews
import phone_files
import personal_keys
import personal_repo_sync
import notifications
import paths
import phone_pairing
import phone_server
import pitch
import pitch_host
import pitch_links
import platforms
from posting import PostingError as _PostingError
from posting import service as posting_service
import product_rules
import reminders
import repo_sync
import repos
import results_extract
import request_guard
import review
import review_studio
import scheduler
import setup_check
from social import SocialError as _SocialError
from social import service as social_service
import team_auth
import team_repos
import team_knowledge
import team_room
import web_ui
import weekly_summary
import workflows

ROOT = paths.DATA_ROOT
PORT = 5050
HOST = "127.0.0.1"
ENGINE_TOKEN = os.environ.get("MAGNM_ENGINE_TOKEN", "")
MAX_POST_BYTES = 20 * 1024 * 1024
MAX_REVIEW_BODY_BYTES = 1 * 1024 * 1024
_SESSION_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="magnm-session")

_KEYCHAIN_ERROR_PAYLOAD = {
    "success": False,
    "error_code": "use_desktop_keychain",
    "error": "Keys are stored in the desktop app's secure keychain. Use Settings > Keys.",
}


def get_endpoint_path():
    """Return the path to <DATA_ROOT>/state/engine_endpoint.json."""
    return paths.data("state", "engine_endpoint.json")


def write_engine_endpoint(port, token=None, pid=None, started_at=None):
    """Atomically write engine_endpoint.json with mode 0600 for local MCP discovery."""
    endpoint_path = get_endpoint_path()
    os.makedirs(os.path.dirname(endpoint_path), exist_ok=True)
    proc_pid = os.getpid() if pid is None else int(pid)
    tok = ENGINE_TOKEN if token is None else str(token or "")
    payload = {
        "url": f"http://127.0.0.1:{int(port)}",
        "token": tok,
        "pid": proc_pid,
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = f"{endpoint_path}.{proc_pid}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, endpoint_path)
    os.chmod(endpoint_path, 0o600)
    return endpoint_path


def remove_engine_endpoint(expected_pid=None):
    """Remove engine_endpoint.json on clean shutdown if owned by expected_pid."""
    endpoint_path = get_endpoint_path()
    if not os.path.exists(endpoint_path):
        return False
    if expected_pid is not None:
        try:
            with open(endpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("pid"), int) and data["pid"] != int(expected_pid):
                return False
        except Exception:
            pass
    try:
        os.unlink(endpoint_path)
        return True
    except OSError:
        return False


def _is_keychain_mode():
    return os.environ.get("MAGNM_SECRETS_MODE", "").strip().lower() == "keychain"


def _is_secret_env_name(name):
    upper = str(name or "").strip().upper()
    return any(tok in upper for tok in ("KEY", "TOKEN", "SECRET"))


def _import_legacy_folder(raw_path):
    """Validate and copy legacy Magnm-Marketing data and SQLite state into DATA_ROOT."""
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        return 400, {"success": False, "error": "Folder path is required."}

    src_root = os.path.abspath(os.path.expanduser(cleaned))
    if not os.path.isdir(src_root):
        return 400, {"success": False, "error": f"Folder does not exist: {src_root}"}

    if os.path.realpath(src_root) == os.path.realpath(os.path.abspath(paths.DATA_ROOT)):
        return 400, {"success": False, "error": "already using this folder"}

    has_state = os.path.isdir(os.path.join(src_root, "state"))
    has_outputs = os.path.isdir(os.path.join(src_root, "outputs"))
    if not has_state and not has_outputs:
        return 400, {
            "success": False,
            "error": "Folder does not look like a Magnm-Marketing folder (missing state/ or outputs/).",
        }

    files_copied = 0
    for parts in (
        ("outputs",),
        ("research",),
        ("assets", "uploads"),
        ("agents",),
        ("playbooks",),
        ("plan",),
    ):
        files_copied += paths._copy_tree_no_overwrite(
            os.path.join(src_root, *parts),
            paths.data(*parts),
        )

    src_cfg_dir = os.path.join(src_root, "config")
    if os.path.isdir(src_cfg_dir):
        dst_cfg_dir = paths.data("config")
        os.makedirs(dst_cfg_dir, exist_ok=True)
        for name in os.listdir(src_cfg_dir):
            if name.endswith(".json"):
                src_file = os.path.join(src_cfg_dir, name)
                dst_file = os.path.join(dst_cfg_dir, name)
                if os.path.isfile(src_file) and not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)
                    files_copied += 1

    src_cfg_md = os.path.join(src_root, "config.md")
    dst_cfg_md = paths.data("config.md")
    if os.path.isfile(src_cfg_md) and not os.path.exists(dst_cfg_md):
        shutil.copy2(src_cfg_md, dst_cfg_md)
        files_copied += 1

    db_counts = db.import_legacy_state(os.path.join(src_root, "state"))
    cmo_runtime.log_activity("CEO", "ENGINE", "import_legacy", src_root)
    return 200, {
        "success": True,
        "files_copied": files_copied,
        "db": db_counts,
        "counts": {
            "files_copied": files_copied,
            **db_counts,
        },
    }


def _read_text_safe(rel_path, limit=12000):
    fp = paths.data(rel_path)
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return f.read(limit)
        except Exception:
            pass
    return ""


def _parse_channels_from_config():
    md = _read_text_safe("config.md")
    channels = []
    in_accounts = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## Accounts"):
            in_accounts = True
            continue
        if in_accounts and s.startswith("## "):
            break
        if in_accounts and s.startswith("|") and not s.startswith("|---") and not s.lower().startswith("| platform"):
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) >= 3:
                channels.append({
                    "platform": parts[0],
                    "handle_url": parts[1],
                    "status": parts[2]
                })
    return channels


def _parse_editorial_plan():
    md = _read_text_safe("plan/plan.md")
    rows = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and not s.startswith("|---") and not s.lower().startswith("| slot"):
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) >= 4:
                rows.append({
                    "slot": parts[0],
                    "platforms": parts[1],
                    "topic": parts[2],
                    "status": parts[3]
                })
    return rows


def _get_latest_research_findings():
    res_dir = paths.data("outputs", "research")
    if not os.path.exists(res_dir):
        return {"file": None, "items": []}
    candidates = sorted([f for f in os.listdir(res_dir) if f.endswith("-findings.json")], reverse=True)
    if not candidates:
        return {"file": None, "items": []}
    latest = os.path.join(res_dir, candidates[0])
    try:
        with open(latest, "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"file": os.path.relpath(latest, paths.DATA_ROOT), "items": items[:20]}
    except Exception:
        return {"file": os.path.relpath(latest, paths.DATA_ROOT), "items": []}


class CommandCenterHandler(BaseHTTPRequestHandler):
    def parse_request(self):
        """Parse the request, then refuse other websites before any do_* handler (of any method) runs."""
        if not super().parse_request():
            return False
        if request_guard.is_trusted(self.command, self.path, self.headers):
            return True
        self.close_connection = True
        self.send_json({"error": "Request refused: it did not come from Magnm Studio."}, status=403)
        return False

    def log_message(self, format, *args):
        pass

    def _is_authorized(self):
        """Return True if no ENGINE_TOKEN is set or request carries a valid token header/cookie."""
        if not ENGINE_TOKEN:
            return True
        header_token = self.headers.get("X-Magnm-Token") or self.headers.get("x-magnm-token") or ""
        if header_token and hmac.compare_digest(header_token, ENGINE_TOKEN):
            return True
        cookie_hdr = self.headers.get("Cookie") or self.headers.get("cookie") or ""
        for part in cookie_hdr.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == "magnm_token" and hmac.compare_digest(v.strip(), ENGINE_TOKEN):
                    return True
        return False

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_auth_callback_html(self, ok, message, status=200):
        heading = "Signed in to Magnm Studio" if ok else "Sign-in failed"
        safe_heading = html.escape(heading, quote=True)
        safe_message = html.escape(str(message or ""), quote=True)
        doc = (
            "<!doctype html>"
            "<html lang=\"en\">"
            "<head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{safe_heading}</title>"
            "<style>"
            "body{margin:0;background:#ffffff;color:#111111;"
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}"
            "main{max-width:460px;width:100%;border:1px solid #e5e5e5;border-radius:12px;padding:32px;}"
            "h1{margin:0 0 12px;font-family:Georgia,'Times New Roman',serif;"
            "font-weight:500;font-size:28px;line-height:1.2;}"
            "p{margin:0 0 12px;font-size:15px;line-height:1.5;color:#222222;}"
            ".hint{margin:0;font-size:13px;color:#666666;}"
            "</style>"
            "</head>"
            "<body>"
            "<main>"
            f"<h1>{safe_heading}</h1>"
            f"<p>{safe_message}</p>"
            "<p class=\"hint\">You can close this tab and go back to Magnm Studio.</p>"
            "</main>"
            "</body>"
            "</html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(doc)))
        self.end_headers()
        self.wfile.write(doc)

    def _handle_auth_callback(self, url):
        qs = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        try:
            res = team_auth.finish_login(qs)
            # Pull the team's shared repos and knowledge right away, without waiting for the next cycle.
            try:
                cloud_sync.request_sync(immediate=True)
            except Exception:
                pass
            email = res.get("email") or ""
            msg = f"Signed in as {email}." if email else "Your Google account is connected."
            self._send_auth_callback_html(True, msg, status=200)
        except team_auth.TeamAuthError as exc:
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 400
            self._send_auth_callback_html(False, exc.message, status=status)
        except Exception:
            self._send_auth_callback_html(False, "Sign-in could not be completed.", status=500)

    def _handle_account_get(self, url):
        try:
            if url.path == "/api/account":
                self.send_json(team_auth.current_account())
            elif url.path == "/api/account/invites":
                self.send_json({"invites": team_auth.list_invites()})
            elif url.path == "/api/account/sync":
                self.send_json(cloud_sync.status())
            elif url.path == "/api/account/media":
                self.send_json(media_previews.status())
            elif url.path == "/api/account/storage":
                self.send_json(media_previews.storage_usage())
            elif url.path == "/api/account/phone-files":
                self.send_json(phone_files.status())
            elif url.path == "/api/account/ai":
                self.send_json(ai_gateway.get_account_ai_status())
            elif url.path == "/api/account/keys":
                self.send_json({"keys": personal_keys.status(), "keychain": _is_keychain_mode()})
            else:
                self.send_error(404, "Not Found")
        except team_auth.TeamAuthError as exc:
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 502
            self.send_json({"error": exc.message}, status=status)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, status=500)

    def _handle_account_post(self, url, params):
        try:
            if url.path == "/api/account/login/start":
                open_browser = params.get("open_browser", True)
                port = self.server.server_address[1] if getattr(self, "server", None) else PORT
                authorize_url = team_auth.start_login(port)
                if open_browser:
                    try:
                        webbrowser.open(authorize_url)
                    except Exception:
                        pass
                self.send_json({"authorize_url": authorize_url})
            elif url.path == "/api/account/login/cancel":
                team_auth.cancel_login()
                self.send_json({"ok": True})
            elif url.path == "/api/account/logout":
                team_auth.logout()
                self.send_json({"ok": True})
            elif url.path == "/api/account/invites":
                invite = team_auth.create_invite(params.get("email"), params.get("role", "member"))
                self.send_json({"invite": invite})
            elif url.path == "/api/account/invites/revoke":
                invite = team_auth.revoke_invite(params.get("email"))
                self.send_json({"invite": invite})
            elif url.path == "/api/account/sync":
                cloud_sync.request_sync(immediate=True)
                self.send_json(cloud_sync.status())
            elif url.path == "/api/account/media/sign":
                storage_path = params.get("storage_path")
                expires_in = params.get("expires_in", 3600)
                if not isinstance(storage_path, str) or not storage_path.strip():
                    self.send_json({"error": "storage_path is required"}, status=400)
                    return
                signed = media_previews.signed_url(storage_path, expires_in=expires_in)
                self.send_json({"url": signed})
            elif url.path == "/api/account/phone-files/remove":
                self._handle_phone_files_remove(params)
            elif url.path == "/api/account/keys/sync":
                cloud_sync.request_sync(immediate=True)
                self.send_json({"ok": True})
            elif url.path == "/api/account/keys/forget":
                personal_keys.forget(params.get("name"))
                cloud_sync.request_sync(immediate=True)
                self.send_json({"ok": True})
            elif url.path == "/api/account/keys/pending":
                self.send_json({"ops": personal_keys.pending_ops()})
            elif url.path == "/api/account/keys/applied":
                names = params.get("names")
                if not isinstance(names, list) or len(names) > 11:
                    raise ValueError("names must be a list of key slots")
                self.send_json({"applied": personal_keys.acknowledge(names)})
            else:
                self.send_error(404, "Unknown endpoint")
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except team_auth.TeamAuthError as exc:
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 400
            self.send_json({"error": exc.message}, status=status)

    def _handle_phone_files_remove(self, params):
        """The owner's request to remove the cloud copies of draft files (one, or all). Local files are untouched."""
        sess = team_auth.get_session_info()
        if not sess or not sess.get("user_id"):
            self.send_json({"error": "Sign in to manage phone copies."}, status=401)
            return
        remove_all = params.get("all")
        if remove_all is not None and remove_all is not True:
            raise ValueError("all must be true when given")
        try:
            out = phone_files.remove(
                str(sess["user_id"]),
                content_id=params.get("content_id"),
                file_path=params.get("file_path"),
                remove_all=bool(remove_all),
            )
        except phone_files.SignedOutAbort:
            self.send_json({"error": "Sign in to manage phone copies."}, status=401)
            return
        except phone_files.PhoneFilesError as exc:
            self.send_json({"error": exc.message}, status=exc.status if 400 <= exc.status < 600 else 502)
            return
        self.send_json(out)

    def _handle_team_get(self, url):
        path = url.path.rstrip("/") or "/"
        try:
            if path == "/api/team/knowledge":
                self.send_json(team_knowledge.status(refresh=True))
                return
            self.send_json({"error": "Not found"}, status=404)
        except team_knowledge.TeamKnowledgeError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except team_auth.TeamAuthError as exc:
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 502
            self.send_json({"error": exc.message}, status=status)

    def _handle_team_post(self, url, params):
        path = url.path.rstrip("/") or "/"
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        try:
            if parts[:3] == ["api", "team", "repos"]:
                self._handle_team_repos_post(parts, params)
                return
            if len(parts) == 5 and parts[:3] == ["api", "team", "knowledge"]:
                key = parts[3]
                action = parts[4]
                if action == "publish":
                    item = team_knowledge.publish(key)
                    self.send_json({"ok": True, "item": item, "status": team_knowledge.status(refresh=False)})
                    return
                if action == "apply":
                    item = team_knowledge.apply(key)
                    self.send_json({"ok": True, "item": item, "status": team_knowledge.status(refresh=False)})
                    return
            self.send_json({"error": "Not found"}, status=404)
        except team_repos.TeamReposError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except team_knowledge.TeamKnowledgeError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except team_auth.TeamAuthError as exc:
            status = 502 if exc.status in (0, None) or exc.status >= 500 else (
                exc.status if isinstance(exc.status, int) and 400 <= exc.status < 500 else 502
            )
            self.send_json({"error": exc.message}, status=status)

    def _handle_team_repos_post(self, parts, params):
        """Admin actions on global repos: add, promote one of their own, remove. The engine re-checks admin."""
        if len(parts) == 3:
            repo = team_repos.add(params)
            self.send_json({"success": True, "repo": repo})
        elif parts == ["api", "team", "repos", "promote"]:
            repo = team_repos.promote(params.get("repo_id"))
            self.send_json({"success": True, "repo": repo})
        elif len(parts) == 5 and parts[4] == "remove":
            self.send_json({"success": True, "id": team_repos.remove(parts[3])})
        else:
            self.send_json({"error": "Not found"}, status=404)

    def _refuse_global_repo(self, repo_id):
        """True (after sending 403) when the id is a team repo: members cannot edit or remove those."""
        repo = repos.get_repo(repo_id)
        if repo is not None and repo.get("scope") == "global":
            self.send_json({"error": "Managed by your admin"}, status=403)
            return True
        return False

    def _handle_admin_get(self, url):
        qs = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        path = url.path.rstrip("/") or "/"
        try:
            if path == "/api/admin/team":
                self.send_json(admin_dashboard.get_team())
                return
            if path == "/api/admin/activity":
                self.send_json(
                    admin_dashboard.get_activity(
                        member=(qs.get("member") or [""])[0],
                        kind=(qs.get("kind") or [""])[0],
                        before=(qs.get("before") or [""])[0],
                        limit=(qs.get("limit") or [""])[0],
                    )
                )
                return
            if path == "/api/admin/activity/daily":
                self.send_json(
                    admin_dashboard.get_daily_activity(
                        days=(qs.get("days") or ["14"])[0],
                    )
                )
                return
            if path == "/api/admin/storage":
                self.send_json(admin_dashboard.get_storage())
                return
            if path == "/api/admin/ai":
                self.send_json(admin_dashboard.get_ai_overview())
                return
            if path == "/api/admin/audit":
                self.send_json(
                    admin_dashboard.get_audit(
                        before=(qs.get("before") or [""])[0],
                        limit=(qs.get("limit") or [""])[0],
                    )
                )
                return

            parts = path.split("/")
            # /api/admin/members/<id>/records
            if len(parts) == 6 and parts[:4] == ["", "api", "admin", "members"] and parts[5] == "records":
                member_id = urllib.parse.unquote(parts[4])
                self.send_json(
                    admin_dashboard.list_member_records(
                        member_id,
                        kind=(qs.get("kind") or [""])[0],
                        q=(qs.get("q") or [""])[0],
                        before=(qs.get("before") or [""])[0],
                        limit=(qs.get("limit") or [""])[0],
                    )
                )
                return
            # /api/admin/members/<id>/records/<kind>/<local_id>
            if len(parts) == 8 and parts[:4] == ["", "api", "admin", "members"] and parts[5] == "records":
                member_id = urllib.parse.unquote(parts[4])
                kind = urllib.parse.unquote(parts[6])
                local_id = urllib.parse.unquote(parts[7])
                self.send_json(admin_dashboard.get_member_record_detail(member_id, kind, local_id))
                return
            # /api/admin/members/<id>/previews
            if len(parts) == 6 and parts[:4] == ["", "api", "admin", "members"] and parts[5] == "previews":
                member_id = urllib.parse.unquote(parts[4])
                self.send_json(
                    admin_dashboard.get_member_previews(
                        member_id,
                        parent=(qs.get("parent") or [""])[0],
                        limit=(qs.get("limit") or [""])[0],
                    )
                )
                return
            # /api/admin/members/<id>/pitch
            if len(parts) == 6 and parts[:4] == ["", "api", "admin", "members"] and parts[5] == "pitch":
                member_id = urllib.parse.unquote(parts[4])
                self.send_json(admin_dashboard.get_member_pitch(member_id))
                return

            admin_dashboard.require_admin()
            if parts[:4] == ["", "api", "admin", "members"] and len(parts) > 6 and parts[5] == "records":
                self.send_json({"error": "Invalid record path."}, status=400)
                return
            self.send_json({"error": "Not found"}, status=404)
        except admin_dashboard.AdminDashboardError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_admin_post(self, url, params):
        path = url.path.rstrip("/") or "/"
        parts = path.split("/")
        try:
            if path == "/api/admin/ai/share-local-keys":
                self.send_json(admin_dashboard.share_local_ai_keys())
                return
            if path == "/api/admin/ai/clear-keys":
                self.send_json(admin_dashboard.clear_shared_ai_keys())
                return
            if path == "/api/admin/ai/limits":
                self.send_json(admin_dashboard.update_ai_limits(params))
                return
            if path == "/api/admin/ai/models":
                self.send_json(admin_dashboard.update_ai_model(params))
                return
            if path == "/api/admin/ai/test":
                self.send_json(admin_dashboard.test_shared_ai_keys())
                return
            if len(parts) == 6 and parts[:4] == ["", "api", "admin", "members"]:
                member_id = urllib.parse.unquote(parts[4])
                action = parts[5]
                if action == "role":
                    self.send_json(admin_dashboard.update_member_role(member_id, params.get("role")))
                    return
                if action == "active":
                    self.send_json(admin_dashboard.update_member_active(member_id, params.get("active")))
                    return
            admin_dashboard.require_admin()
            self.send_json({"error": "Unknown endpoint"}, status=404)
        except admin_dashboard.AdminDashboardError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_notifications_get(self, url):
        qs = urllib.parse.parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        if path == "/api/notifications":
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(limit, 200))
            self.send_json(notifications.list_notifications(limit=limit))
            return
        if path == "/api/notifications/settings":
            self.send_json(notifications.get_settings())
            return
        self.send_json({"error": "Not found"}, status=404)

    def _handle_notifications_post(self, url, params):
        path = url.path.rstrip("/") or "/"
        try:
            if path == "/api/notifications/read":
                self.send_json(
                    notifications.mark_read(
                        ids=params.get("ids"),
                        mark_all=bool(params.get("all") is True),
                    )
                )
                return
            if path == "/api/notifications/test":
                self.send_json({"notification": notifications.send_test()})
                return
            if path == "/api/notifications/settings":
                self.send_json(
                    notifications.update_setting(
                        params.get("kind"),
                        params.get("enabled"),
                    )
                )
                return
            self.send_json({"error": "Not found"}, status=404)
        except notifications.NotificationError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_reminders_get(self, url):
        path = url.path.rstrip("/") or "/"
        if path != "/api/reminders":
            self.send_json({"error": "Not found"}, status=404)
            return
        qs = urllib.parse.parse_qs(url.query)
        status_filter = (qs.get("status") or ["open"])[0] or "open"
        try:
            items = reminders.list_reminders(status=status_filter)
            self.send_json({"reminders": items})
        except reminders.ReminderError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_reminders_post(self, url, params):
        raw_rest = url.path[len("/api/reminders"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if not parts:
                rem = reminders.create_reminder(
                    title=params.get("title"),
                    due_at=params.get("due_at"),
                    note=params.get("note") or "",
                    kind=params.get("kind") or "custom",
                    content_id=params.get("content_id"),
                    created_by="user",
                )
                cloud_sync.request_sync()
                self.send_json({"reminder": rem})
                return
            if len(parts) == 2 and parts[1] == "done":
                rem = reminders.mark_done(parts[0])
                cloud_sync.request_sync()
                self.send_json({"reminder": rem})
                return
            if len(parts) == 2 and parts[1] == "snooze":
                rem = reminders.snooze_reminder(
                    parts[0],
                    minutes=params.get("minutes"),
                    until=params.get("until"),
                )
                cloud_sync.request_sync()
                self.send_json({"reminder": rem})
                return
            self.send_json({"error": "Not found"}, status=404)
        except reminders.ReminderError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_results_get(self, url):
        raw_rest = url.path[len("/api/results/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        qs = urllib.parse.parse_qs(url.query)
        try:
            if parts == ["proposals"]:
                status_filter = (qs.get("status") or ["pending"])[0] or "pending"
                self.send_json({"proposals": results_extract.list_proposals(status=status_filter)})
                return
            if len(parts) == 2 and parts[0] == "proposals":
                prop = db.get_metric_proposal(parts[1])
                if prop is None:
                    self.send_json({"error": "Metric proposal not found"}, status=404)
                    return
                self.send_json({"proposal": prop})
                return
            if len(parts) == 3 and parts[0] == "proposals" and parts[2] == "image":
                img_path, mime = results_extract.resolve_proposal_image(parts[1])
                self._serve_file_with_range(img_path, mime)
                return
            self.send_json({"error": "Not found"}, status=404)
        except results_extract.ResultsExtractError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _parse_results_extract_body(self, content_len):
        if content_len > MAX_POST_BYTES:
            raise results_extract.ResultsExtractError("Request body too large", status=413)
        raw_body = self.rfile.read(content_len) if content_len > 0 else b""
        content_type = self.headers.get("Content-Type") or self.headers.get("content-type") or ""
        ctype_lower = content_type.split(";")[0].strip().lower()

        if ctype_lower == "multipart/form-data":
            hdr = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", errors="replace")
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(hdr + raw_body)
            if not msg.is_multipart():
                raise results_extract.ResultsExtractError("Invalid multipart request", status=400)
            image_bytes = None
            content_id = None
            platform = None
            for part in msg.iter_parts():
                field_name = (part.get_param("name", header="content-disposition") or "").strip().lower()
                filename = part.get_filename()
                payload = part.get_payload(decode=True)
                if payload is None:
                    raw_text = part.get_content()
                    payload = raw_text.encode("utf-8") if isinstance(raw_text, str) else b""
                if filename or field_name in ("file", "image", "screenshot"):
                    if image_bytes is None and payload:
                        image_bytes = payload
                elif field_name == "content_id":
                    content_id = payload.decode("utf-8", errors="replace").strip() or None
                elif field_name == "platform":
                    platform = payload.decode("utf-8", errors="replace").strip() or None
            if image_bytes is None:
                raise results_extract.ResultsExtractError("Screenshot file is required", status=400)
            return image_bytes, content_id, platform

        try:
            params = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        except Exception as exc:
            raise results_extract.ResultsExtractError("Invalid JSON body", status=400) from exc
        if not isinstance(params, dict):
            raise results_extract.ResultsExtractError("JSON body must be an object", status=400)

        b64_raw = params.get("image_base64") or params.get("content_base64") or params.get("image") or ""
        if not isinstance(b64_raw, str) or not b64_raw.strip():
            raise results_extract.ResultsExtractError("image_base64 is required", status=400)
        b64_clean = b64_raw.strip()
        if b64_clean.startswith("data:") and "," in b64_clean:
            b64_clean = b64_clean.split(",", 1)[1].strip()
        try:
            image_bytes = base64.b64decode(b64_clean, validate=True)
        except Exception as exc:
            raise results_extract.ResultsExtractError("Invalid base64 image payload", status=400) from exc

        return image_bytes, params.get("content_id"), params.get("platform")

    def _handle_results_extract_post(self, content_len):
        try:
            image_bytes, content_id, platform = self._parse_results_extract_body(content_len)
            res = results_extract.extract_from_image(
                image_bytes,
                content_id=content_id,
                platform=platform,
            )
            self.send_json(res)
        except results_extract.ResultsExtractError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except ai_gateway.AINotConnected as exc:
            self.send_json({"error": str(exc)}, status=503)

    def _handle_results_post(self, url, params):
        raw_rest = url.path[len("/api/results/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if len(parts) == 3 and parts[0] == "proposals" and parts[2] == "confirm":
                res = results_extract.confirm_proposal(
                    parts[1],
                    metrics=params.get("metrics") if "metrics" in params else None,
                    content_id=params.get("content_id") if "content_id" in params else None,
                    platform=params.get("platform") if "platform" in params else None,
                )
                cloud_sync.request_sync()
                self.send_json(res)
                return
            if len(parts) == 3 and parts[0] == "proposals" and parts[2] == "discard":
                res = results_extract.discard_proposal(parts[1])
                self.send_json(res)
                return
            self.send_json({"error": "Not found"}, status=404)
        except results_extract.ResultsExtractError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_summaries_get(self, url):
        path = url.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(url.query)
        try:
            if path == "/api/summaries":
                try:
                    limit = int((qs.get("limit") or ["52"])[0])
                except (TypeError, ValueError):
                    limit = 52
                self.send_json({"summaries": weekly_summary.list_summaries(limit=limit)})
                return
            raw_rest = url.path[len("/api/summaries/"):].strip("/")
            parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
            if len(parts) == 1:
                self.send_json({"summary": weekly_summary.get_summary(parts[0])})
                return
            self.send_json({"error": "Not found"}, status=404)
        except weekly_summary.WeeklySummaryError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_summaries_post(self, url, params):
        path = url.path.rstrip("/") or "/"
        try:
            if path == "/api/summaries/generate":
                res = weekly_summary.generate_summary(
                    week_start=params.get("week_start"),
                    notify=True,
                )
                cloud_sync.request_sync()
                self.send_json(res)
                return
            self.send_json({"error": "Not found"}, status=404)
        except weekly_summary.WeeklySummaryError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_learnings_get(self, url):
        path = url.path.rstrip("/") or "/"
        if path != "/api/learnings":
            self.send_json({"error": "Not found"}, status=404)
            return
        qs = urllib.parse.parse_qs(url.query)
        status_filter = (qs.get("status") or [""])[0].strip() or None
        try:
            limit = int((qs.get("limit") or ["200"])[0])
        except (TypeError, ValueError):
            limit = 200
        self.send_json({"learnings": learnings.list_learnings(status=status_filter, limit=limit)})

    def _handle_learnings_post(self, url, params):
        raw_rest = url.path[len("/api/learnings"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if not parts:
                lr = learnings.add_manual_learning(
                    text=params.get("text"),
                    applies_to=params.get("applies_to"),
                )
                cloud_sync.request_sync()
                self.send_json({"learning": lr})
                return
            if len(parts) == 2 and parts[1] == "approve":
                lr = learnings.approve_learning(
                    parts[0],
                    text=params.get("text") if "text" in params else None,
                )
                cloud_sync.request_sync()
                self.send_json({"learning": lr})
                return
            if len(parts) == 2 and parts[1] == "reject":
                lr = learnings.reject_learning(parts[0])
                cloud_sync.request_sync()
                self.send_json({"learning": lr})
                return
            if len(parts) == 2 and parts[1] == "retire":
                lr = learnings.retire_learning(parts[0])
                cloud_sync.request_sync()
                self.send_json({"learning": lr})
                return
            self.send_json({"error": "Not found"}, status=404)
        except learnings.LearningError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_automation_get(self, url):
        self.send_json(automation.state_for_ui())

    def _handle_automation_post(self, url, params):
        """Settings, the pause switch, Run now and the missed list for automatic work."""
        path = url.path.rstrip("/")
        try:
            if path == "/api/automation/settings":
                settings = automation.update_settings(params)
                self.send_json({"settings": settings})
            elif path == "/api/automation/pause":
                paused = params.get("paused")
                if not isinstance(paused, bool):
                    self.send_json({"error": "paused must be true or false"}, status=400)
                    return
                automation.set_paused(paused, by="laptop")
                cloud_sync.request_sync()
                self.send_json(automation.state_for_ui())
            elif path == "/api/automation/run":
                job = automation.request_run(actor="UI")
                self.send_json({"success": True, "job": job})
            elif path == "/api/automation/missed":
                action, sched_id = params.get("action"), params.get("id")
                if action not in ("run", "dismiss") or (sched_id is not None and not isinstance(sched_id, str)):
                    self.send_json({"error": "action must be run or dismiss, with the id of a missed run"}, status=400)
                    return
                if action == "run":
                    if not sched_id:
                        self.send_json({"error": "id is required"}, status=400)
                        return
                    automation.run_missed(sched_id)
                    self.send_json({"success": True, "missed": automation.missed_runs()})
                else:
                    self.send_json({"success": True, "missed": automation.dismiss_missed(sched_id or None)})
                cloud_sync.request_sync()
            else:
                self.send_json({"error": "Not found"}, status=404)
        except automation.AlreadyRunning as exc:
            self.send_json({"error": str(exc)}, status=409)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)

    def _handle_board_get(self, url):
        qs = urllib.parse.parse_qs(url.query)

        def one(name):
            values = qs.get(name)
            return values[0] if values else None

        self.send_json(board.build(agent=one("agent"), repo=one("repo"), kind=one("kind")))

    def _handle_board_post(self, url, params):
        if url.path.rstrip("/") != "/api/board/act":
            self.send_json({"error": "Not found"}, status=404)
            return
        try:
            result = board.act(params.get("id"), params.get("action"))
        except board.BadRequest as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=409)
            return
        cloud_sync.request_sync()
        self.send_json(result)

    def _handle_phone_get(self, url):
        path = url.path.rstrip("/") or "/"
        mgr = phone_server.get_manager()
        if path == "/api/phone":
            self.send_json(mgr.status())
            return
        if path == "/api/phone/devices":
            self.send_json({"devices": phone_pairing.list_devices()})
            return
        self.send_json({"error": "Not found"}, status=404)

    def _handle_phone_post(self, url, params):
        path = url.path.rstrip("/") or "/"
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        mgr = phone_server.get_manager()
        pairing = phone_pairing.get_manager()
        try:
            if path == "/api/phone/enable":
                self.send_json(mgr.enable())
                return
            if path == "/api/phone/disable":
                self.send_json(mgr.disable())
                return
            if path == "/api/phone/pair/start":
                pair_info = mgr.start_pairing()
                self.send_json({**pair_info, "status": mgr.status()})
                return
            if path == "/api/phone/pair/cancel":
                pairing.cancel_pairing()
                self.send_json(mgr.status())
                return
            if path == "/api/phone/pair/approve":
                device = pairing.approve_request(params.get("request_id"))
                self.send_json({"device": device, "status": mgr.status()})
                return
            if path == "/api/phone/pair/deny":
                res = pairing.deny_request(params.get("request_id"))
                self.send_json({**res, "status": mgr.status()})
                return
            if path == "/api/phone/devices/revoke":
                device = phone_pairing.revoke_device(params.get("device_id"))
                self.send_json({"device": device, "status": mgr.status()})
                return
            if len(parts) == 5 and parts[:3] == ["api", "phone", "devices"] and parts[4] == "revoke":
                device = phone_pairing.revoke_device(parts[3])
                self.send_json({"device": device, "status": mgr.status()})
                return
            if path == "/api/phone/send":
                res = phone_server.send_item_to_phone(params.get("kind"), params.get("id"))
                self.send_json(res)
                return
            self.send_json({"error": "Not found"}, status=404)
        except phone_server.PhoneApiError as exc:
            self.send_json({"error": exc.message, "code": exc.code}, status=exc.status)
        except phone_server.PhoneServerError as exc:
            self.send_json(
                {
                    "error": exc.message,
                    "tailscale_missing": exc.code == "tailscale_missing",
                },
                status=exc.status,
            )
        except phone_pairing.PairingError as exc:
            self.send_json({"error": exc.message}, status=exc.status)

    def _handle_coding_get(self, url):
        raw_rest = url.path[len("/api/coding"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        qs = urllib.parse.parse_qs(url.query)

        if parts == ["agents"]:
            refresh_raw = (qs.get("refresh", ["0"])[0] or "").strip().lower()
            refresh = refresh_raw in ("1", "true", "yes")
            detected_agents = coding_agents.list_detected_agents(refresh=refresh)
            self.send_json({
                "agents": detected_agents,
                "adapters": coding_agents.list_detected_adapters(refresh=refresh),
                "agent_engines": coding_agents.get_agent_engines(),
                "timeout_s": coding_agents.get_run_timeout_s(),
                "mcp": mcp_tools.get_mcp_settings(clis=detected_agents),
            })
            return

        if parts == ["mcp"]:
            self.send_json(mcp_tools.get_mcp_settings())
            return

        if parts == ["agent_engines"]:
            self.send_json({
                "agent_engines": coding_agents.get_agent_engines(),
                "agents": coding_agents.list_detected_agents(refresh=False),
                "adapters": coding_agents.list_detected_adapters(refresh=False),
            })
            return

        if parts == ["runs"]:
            try:
                limit = int((qs.get("limit", ["50"])[0] or "50"))
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(limit, 500))
            status_filter = qs.get("status", [None])[0] or None
            self.send_json({"runs": coding_agents.list_runs(limit=limit, status=status_filter)})
            return

        if len(parts) == 2 and parts[0] == "runs":
            run_obj = coding_agents.get_run(parts[1])
            if run_obj is None:
                self.send_json({"error": "Coding run not found"}, status=404)
                return
            log_tail = coding_agents.read_log_tail(run_obj.get("log_path"), max_chars=16000)
            self.send_json({**run_obj, "log_tail": log_tail})
            return

        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "log":
            try:
                offset = int((qs.get("from", ["0"])[0] or "0"))
            except (TypeError, ValueError):
                offset = 0
            payload = coding_agents.read_log_from(parts[1], offset=offset)
            if payload is None:
                self.send_json({"error": "Coding run not found"}, status=404)
                return
            self.send_json(payload)
            return

        if len(parts) >= 3 and parts[0] == "runs" and parts[2] == "out":
            rel_file = "/".join(parts[3:]) if len(parts) >= 4 else (qs.get("filename", [""])[0] or "").strip()
            try:
                safe_name, full_path = coding_agents.resolve_out_file_for_download(parts[1], rel_file)
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)
                return
            header_name = re.sub(r'[^A-Za-z0-9._-]+', "_", safe_name).strip("._") or "output.bin"
            with open(full_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{header_name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_json({"error": "Not found"}, status=404)

    def _handle_coding_post(self, url, params):
        raw_rest = url.path[len("/api/coding"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if parts == ["runs"]:
                run_obj = coding_agents.run(
                    engine=params.get("engine"),
                    prompt=params.get("prompt"),
                    repo_ids=params.get("repo_ids") or [],
                    mode=params.get("mode") or "read_only",
                    wait=False,
                    confirm_confidential=bool(params.get("confirm_confidential")),
                )
                self.send_json({"success": True, "run": run_obj}, status=201)
                return

            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "approve":
                run_obj = coding_agents.approve_run(
                    parts[1],
                    expected_hash=params.get("approval_hash"),
                    confirm_confidential=bool(params.get("confirm_confidential")),
                    wait=False,
                )
                self.send_json({"success": True, "run": run_obj})
                return

            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                run_obj = coding_agents.cancel(parts[1])
                if run_obj is None:
                    self.send_json({"error": "Coding run not found"}, status=404)
                    return
                self.send_json({"success": True, "run": run_obj})
                return

            if len(parts) == 4 and parts[0] == "runs" and parts[2] == "out" and parts[3] == "save":
                asset = coding_agents.save_out_file_to_library(
                    parts[1],
                    params.get("filename"),
                    category=params.get("category") or "generated",
                    description=params.get("description") or "",
                )
                self.send_json({"success": True, "asset": asset})
                return

            if len(parts) == 4 and parts[0] == "runs" and parts[2] == "out" and parts[3] == "attach":
                item = coding_agents.attach_out_file_to_content(
                    parts[1],
                    params.get("filename"),
                    params.get("content_id"),
                )
                self.send_json({"success": True, "content": item})
                return

            if parts == ["agent_engines"]:
                if "timeout_s" in params:
                    coding_agents.set_run_timeout_s(params["timeout_s"])
                if "agent_id" in params:
                    res = coding_agents.set_agent_engine(params.get("agent_id"), params.get("engine"))
                    self.send_json({
                        "success": True,
                        **res,
                        "agent_engines": coding_agents.get_agent_engines(),
                        "timeout_s": coding_agents.get_run_timeout_s(),
                    })
                    return
                if isinstance(params.get("agent_engines"), dict):
                    for aid, eng in params["agent_engines"].items():
                        coding_agents.set_agent_engine(aid, eng)
                    self.send_json({
                        "success": True,
                        "agent_engines": coding_agents.get_agent_engines(),
                        "timeout_s": coding_agents.get_run_timeout_s(),
                    })
                    return
                if "timeout_s" in params:
                    self.send_json({
                        "success": True,
                        "agent_engines": coding_agents.get_agent_engines(),
                        "timeout_s": coding_agents.get_run_timeout_s(),
                    })
                    return
                self.send_json({"error": "agent_id and engine (or agent_engines) required"}, status=400)
                return

            if parts == ["mcp"]:
                writes_enabled = params.get("writes_enabled")
                if not isinstance(writes_enabled, bool):
                    self.send_json(
                        {"error": "writes_enabled must be a boolean", "error_code": "invalid_arguments"},
                        status=400,
                    )
                    return
                mcp_tools.set_writes_enabled(writes_enabled)
                self.send_json({"success": True, **mcp_tools.get_mcp_settings()})
                return

            if parts == ["mcp", "call"]:
                tool_name = params.get("name")
                arguments = params.get("arguments")
                try:
                    result = mcp_tools.execute_tool(tool_name, arguments)
                    self.send_json({"ok": True, "result": result})
                except mcp_tools.McpToolError as exc:
                    self.send_json({"error": exc.message, "error_code": exc.error_code}, status=exc.status)
                return

            self.send_json({"error": "Not found"}, status=404)
        except ValueError as exc:
            msg = str(exc)
            status = 404 if "not found" in msg.lower() else 400
            self.send_json({"error": msg}, status=status)

    def _handle_social_get(self, url):
        raw_rest = url.path[len("/api/social"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        qs = urllib.parse.parse_qs(url.query)
        try:
            if parts == ["status"]:
                self.send_json(social_service.get_status())
                return

            if parts == ["runs"]:
                try:
                    limit = int((qs.get("limit", ["50"])[0] or "50"))
                except (TypeError, ValueError):
                    limit = 50
                limit = max(1, min(limit, 500))
                plat_filter = qs.get("platform", [None])[0] or None
                status_filter = qs.get("status", [None])[0] or None
                self.send_json({
                    "runs": social_service.list_runs(
                        limit=limit,
                        platform=plat_filter,
                        status=status_filter,
                    )
                })
                return

            if len(parts) == 2 and parts[0] == "runs":
                self.send_json(social_service.get_run(parts[1]))
                return

            if parts == ["live", "frame"]:
                frame_bytes = social_service.get_live_frame()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(frame_bytes)))
                self.end_headers()
                self.wfile.write(frame_bytes)
                return

            self.send_json({"error": "Not found"}, status=404)
        except _SocialError as exc:
            self.send_json({"error": exc.message, "error_code": exc.code}, status=exc.status)

    def _handle_posting_get(self, url):
        raw_rest = url.path[len("/api/posting"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        qs = urllib.parse.parse_qs(url.query)
        try:
            if parts == ["status"]:
                self.send_json(posting_service.get_status())
                return
            if parts == ["attempts"]:
                try:
                    limit = int((qs.get("limit", ["50"])[0] or "50"))
                except (TypeError, ValueError):
                    limit = 50
                limit = max(1, min(limit, 500))
                cid_filter = qs.get("content_id", [None])[0] or None
                plat_filter = qs.get("platform", [None])[0] or None
                self.send_json({
                    "attempts": posting_service.list_attempts(
                        content_id=cid_filter,
                        platform=plat_filter,
                        limit=limit,
                    )
                })
                return
            self.send_json({"error": "Not found"}, status=404)
        except _PostingError as exc:
            self.send_json({"error": exc.message, "error_code": exc.code}, status=exc.status)

    def _handle_posting_post(self, url, params):
        raw_rest = url.path[len("/api/posting"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if parts == ["connect", "linkedin"]:
                self.send_json(posting_service.connect_linkedin(params.get("token")))
                return
            if parts == ["connect", "instagram"]:
                self.send_json(posting_service.connect_instagram(params.get("token")))
                return
            if parts == ["refresh", "instagram"]:
                self.send_json(posting_service.refresh_instagram())
                return
            if parts == ["connect", "x", "start"]:
                self.send_json(posting_service.start_x_oauth(params.get("client_id")))
                return
            if parts == ["connect", "x", "cancel"]:
                self.send_json(posting_service.cancel_x_oauth())
                return
            if parts == ["disconnect"]:
                self.send_json(posting_service.disconnect(params.get("platform")))
                return
            if parts == ["prepare"]:
                self.send_json(
                    posting_service.prepare(
                        params.get("content_id"),
                        params.get("platform"),
                        media_url=params.get("media_url"),
                        media_paths=params.get("media_paths"),
                        ai_generated=params.get("ai_generated") is True,
                        acknowledge_repost=params.get("acknowledge_repost") is True,
                        acknowledge_unknown=params.get("acknowledge_unknown") is True,
                    )
                )
                return
            if parts == ["confirm"]:
                res = posting_service.confirm(
                    params.get("confirmation_id"),
                    expected_hash=params.get("payload_hash"),
                    media_url=params.get("media_url") if "media_url" in params else None,
                    media_paths=params.get("media_paths") if "media_paths" in params else None,
                    ai_generated=params.get("ai_generated") if "ai_generated" in params else None,
                    acknowledge_repost=params.get("acknowledge_repost") is True,
                    acknowledge_unknown=params.get("acknowledge_unknown") is True,
                )
                cloud_sync.request_sync()
                self.send_json(res)
                return
            self.send_json({"error": "Not found"}, status=404)
        except _PostingError as exc:
            payload = {"error": exc.message, "error_code": exc.code}
            if exc.uncertain:
                payload["uncertain"] = True
                payload["attempt_status"] = "unknown"
            self.send_json(payload, status=exc.status)

    def _handle_social_post(self, url, params):
        raw_rest = url.path[len("/api/social"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if parts == ["enable"]:
                self.send_json(
                    social_service.enable_platform(
                        params.get("platform"),
                        acknowledged=params.get("acknowledged") is True,
                        acknowledgement_text=params.get("acknowledgement_text"),
                    )
                )
                return

            if parts == ["disable"]:
                self.send_json(social_service.disable_platform(params.get("platform")))
                return

            if parts == ["remove"]:
                self.send_json(social_service.remove_platform(params.get("platform")))
                return

            if parts == ["login", "start"]:
                self.send_json(social_service.login_start(params.get("platform")))
                return

            if parts == ["login", "done"]:
                self.send_json(social_service.login_done(platform=params.get("platform")))
                return

            if parts == ["login", "cancel"]:
                self.send_json(social_service.login_cancel())
                return

            if parts == ["runs"]:
                run_obj = social_service.create_run_request(
                    platform=params.get("platform"),
                    recipe=params.get("recipe"),
                    argument=params.get("argument"),
                    requested_by="user",
                    session_id=params.get("session_id"),
                )
                self.send_json({"success": True, "run": run_obj})
                return

            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "start":
                run_obj = social_service.start_run(
                    parts[1],
                    expected_hash=params.get("approval_hash"),
                )
                self.send_json({"success": True, "run": run_obj})
                return

            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                run_obj = social_service.cancel_run(parts[1])
                self.send_json({"success": True, "run": run_obj})
                return

            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "summarise":
                run_obj = social_service.summarise_run(parts[1])
                self.send_json({"success": True, "run": run_obj})
                return

            if parts == ["challenge", "later"]:
                self.send_json(social_service.challenge_later(run_id=params.get("run_id")))
                return

            if parts == ["challenge", "skip"]:
                self.send_json(social_service.challenge_skip(run_id=params.get("run_id")))
                return

            if parts == ["challenge", "continue"]:
                res = social_service.challenge_continue(run_id=params.get("run_id"))
                status = 200 if res.get("ok") else 409
                self.send_json(res, status=status)
                return

            if parts == ["live", "input"]:
                self.send_json(social_service.post_live_input(params))
                return

            if parts == ["live", "open_window"]:
                self.send_json(social_service.open_live_full_window())
                return

            self.send_json({"error": "Not found"}, status=404)
        except _SocialError as exc:
            self.send_json({"error": exc.message, "error_code": exc.code}, status=exc.status)

    def _serve_sse(self):
        """Stream live Server-Sent Events ('job', 'message', 'ping') to connected clients."""
        q = events.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    ev_type = ev.get("type", "message")
                    payload = json.dumps(ev.get("data"))
                    frame = f"event: {ev_type}\ndata: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    ping_data = json.dumps({"ts": datetime.now(timezone.utc).isoformat()})
                    frame = f"event: ping\ndata: {ping_data}\n\n".encode("utf-8")
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            events.unsubscribe(q)

    def _serve_file_with_range(self, file_path, mime):
        safe_headers = file_serving.safe_headers(file_path, mime)
        size = os.path.getsize(file_path)
        range_hdr = self.headers.get("Range")
        if range_hdr and range_hdr.startswith("bytes="):
            try:
                spec = range_hdr.replace("bytes=", "").split(",")[0]
                start_s, end_s = spec.split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                if start <= end < size:
                    length = end - start + 1
                    with open(file_path, "rb") as f:
                        f.seek(start)
                        chunk = f.read(length)
                    self.send_response(206)
                    for name, value in safe_headers.items():
                        self.send_header(name, value)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
            except Exception:
                pass

        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        for name, value in safe_headers.items():
            self.send_header(name, value)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_review_error(self, exc):
        status = 404 if isinstance(exc, review_studio.NotFoundError) else 400
        self.send_json({"error": str(exc)}, status=status)

    def _send_pitch_error(self, exc):
        if isinstance(exc, pitch.NotFoundError):
            self.send_json({"error": str(exc)}, status=404)
        elif isinstance(exc, pitch.ValidationError):
            self.send_json({"error": str(exc), "errors": exc.errors}, status=400)
        elif isinstance(exc, pitch_links.PitchLinkError):
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 400
            self.send_json({"error": str(exc)}, status=status)
        elif isinstance(exc, team_knowledge.TeamKnowledgeError):
            status = exc.status if isinstance(exc.status, int) and 400 <= exc.status < 600 else 400
            self.send_json({"error": exc.message}, status=status)
        else:
            self.send_json({"error": str(exc)}, status=400)

    def _handle_pitch_get(self, url):
        raw_rest = url.path[len("/api/pitch/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        qs = urllib.parse.parse_qs(url.query)
        try:
            if parts == ["catalog"]:
                self.send_json(pitch.catalog())
            elif parts == ["version"]:
                self.send_json(pitch.pitch_version())
            elif parts == ["default"]:
                self.send_json(pitch.get_default_proposal())
            elif parts == ["outbox"]:
                pending_raw = (qs.get("pending", ["0"])[0] or "").strip().lower()
                pending_only = pending_raw in ("1", "true", "yes")
                self.send_json(pitch.list_outbox(pending_only=pending_only))
            elif parts == ["requests"]:
                self.send_json(pitch_links.list_requests())
            elif parts == ["proposals"]:
                status_filter = qs.get("status", [None])[0] or None
                kind_filter = qs.get("kind", [None])[0] or None
                self.send_json({"proposals": pitch.list_proposals(status=status_filter, kind=kind_filter)})
            elif len(parts) == 2 and parts[0] == "proposals":
                self.send_json(pitch.get_proposal(parts[1]))
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "pdfs":
                self.send_json(pitch.list_proposal_pdfs(parts[1]))
            elif len(parts) == 4 and parts[0] == "proposals" and parts[2] == "pdf":
                pdf_path = pitch.resolve_proposal_pdf(parts[1], parts[3])
                self._serve_file_with_range(pdf_path, "application/pdf")
            else:
                self.send_json({"error": "Not found"}, status=404)
        except ValueError as exc:
            self._send_pitch_error(exc)

    def _handle_pitch_post_pdf(self, proposal_id, content_len):
        if content_len > pitch.MAX_PDF_BYTES:
            self.send_json({"error": "PDF exceeds maximum allowed size of 10 MB"}, status=413)
            return
        raw_bytes = self.rfile.read(content_len) if content_len > 0 else b""
        content_type = self.headers.get("Content-Type") or self.headers.get("content-type") or ""
        try:
            self.send_json(pitch.save_proposal_pdf(proposal_id, raw_bytes, content_type=content_type))
        except ValueError as exc:
            self._send_pitch_error(exc)

    def _handle_pitch_post(self, url, params):
        raw_rest = url.path[len("/api/pitch/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if parts == ["base_prices"]:
                write_role = team_knowledge.check_write_access()
                if "item_id" not in params or "price" not in params:
                    self.send_json({"error": "item_id and price are required"}, status=400)
                    return
                res = pitch.set_base_price(
                    params.get("item_id"),
                    params.get("price"),
                    actor=params.get("actor") or "admin",
                )
                if write_role == "admin":
                    team_knowledge.queue_auto_publish("pitch_base_prices")
                self.send_json(res)
            elif parts == ["base_prices", "reset"]:
                write_role = team_knowledge.check_write_access()
                if "item_id" not in params:
                    self.send_json({"error": "item_id is required"}, status=400)
                    return
                res = pitch.reset_base_price(
                    params.get("item_id"),
                    actor=params.get("actor") or "admin",
                )
                if write_role == "admin":
                    team_knowledge.queue_auto_publish("pitch_base_prices")
                self.send_json(res)
            elif parts == ["preview_payload"]:
                self.send_json(pitch.preview_payload(params))
            elif parts == ["proposals"]:
                self.send_json(
                    pitch.create_proposal(
                        params,
                        created_by=params.get("created_by") or "user",
                    )
                )
            elif len(parts) == 2 and parts[0] == "proposals":
                self.send_json(pitch.update_proposal(parts[1], params))
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "delete":
                self.send_json(
                    pitch.delete_proposal(
                        parts[1],
                        hard=bool(params.get("hard", False)),
                    )
                )
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "duplicate":
                self.send_json(
                    pitch.duplicate_proposal(
                        parts[1],
                        params.get("slug"),
                        created_by=params.get("created_by") or "user",
                        kind=params.get("kind"),
                    )
                )
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "finalize":
                self.send_json(
                    pitch.finalize_proposal(
                        parts[1],
                        overrides=params,
                        created_by=params.get("created_by") or "user",
                    )
                )
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "publish":
                self.send_json(pitch_links.publish(parts[1]))
            elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "withdraw":
                self.send_json(pitch_links.withdraw(parts[1]))
            elif len(parts) == 3 and parts[0] == "requests" and parts[2] == "read":
                self.send_json(pitch_links.mark_request_read(parts[1]))
            else:
                self.send_json({"error": "Not found"}, status=404)
        except ValueError as exc:
            self._send_pitch_error(exc)

    def _handle_review_get(self, url):
        raw_rest = url.path[len("/api/review/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if len(parts) == 1:
                self.send_json(review_studio.get_review(parts[0]))
            elif len(parts) == 3 and parts[1] == "versions":
                try:
                    ver_num = int(parts[2])
                except (TypeError, ValueError):
                    self.send_json({"error": "Invalid version number"}, status=400)
                    return
                self.send_json(review_studio.get_version(parts[0], ver_num))
            elif len(parts) == 2 and parts[1] == "diff":
                qs = urllib.parse.parse_qs(url.query)
                from_raw = qs.get("from", [None])[0]
                to_raw = qs.get("to", [None])[0]
                if from_raw is None or to_raw is None:
                    self.send_json({"error": "from and to query parameters are required"}, status=400)
                    return
                try:
                    from_ver = int(from_raw)
                    to_ver = int(to_raw)
                except (TypeError, ValueError):
                    self.send_json({"error": "from and to must be integers"}, status=400)
                    return
                self.send_json(review_studio.diff_versions(parts[0], from_ver, to_ver))
            else:
                self.send_json({"error": "Not found"}, status=404)
        except ValueError as exc:
            self._send_review_error(exc)

    def _handle_review_post(self, url, params):
        raw_rest = url.path[len("/api/review/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in raw_rest.split("/") if p]
        try:
            if len(parts) == 2 and parts[1] == "versions":
                self.send_json(
                    review_studio.save_version(
                        parts[0],
                        params.get("body"),
                        notes=params.get("notes"),
                        created_by=params.get("created_by") or "user",
                    )
                )
            elif len(parts) == 4 and parts[1] == "versions" and parts[3] == "restore":
                try:
                    ver_num = int(parts[2])
                except (TypeError, ValueError):
                    self.send_json({"error": "Invalid version number"}, status=400)
                    return
                self.send_json(review_studio.restore_version(parts[0], ver_num))
            elif len(parts) == 2 and parts[1] == "comments":
                res = review_studio.create_comment(
                    parts[0],
                    kind=params.get("kind", "general"),
                    version=params.get("version"),
                    anchor=params.get("anchor"),
                    body=params.get("body", ""),
                    author=params.get("author", "user"),
                    parent_id=params.get("parent_id"),
                )
                self.send_json(res)
            elif len(parts) == 3 and parts[1] == "comments":
                res = review_studio.update_comment(
                    parts[0],
                    parts[2],
                    body=params.get("body") if "body" in params else None,
                    status=params.get("status") if "status" in params else None,
                )
                self.send_json(res)
            elif len(parts) == 4 and parts[1] == "comments" and parts[3] == "delete":
                self.send_json(review_studio.delete_comment(parts[0], parts[2]))
            elif len(parts) == 2 and parts[1] == "apply_all":
                self.send_json(
                    review_studio.apply_all(
                        parts[0],
                        agent=params.get("agent"),
                        as_suggestions=bool(params.get("as_suggestions", False)),
                    )
                )
            elif len(parts) == 2 and parts[1] == "ai_action":
                self.send_json(
                    review_studio.ai_action(
                        parts[0],
                        params.get("action"),
                        selection=params.get("selection"),
                        params=params.get("params"),
                    )
                )
            elif len(parts) == 4 and parts[1] == "suggestions" and parts[3] == "decide":
                self.send_json(
                    review_studio.decide_suggestion(parts[0], parts[2], params.get("decision"))
                )
            elif len(parts) == 3 and parts[1] == "suggestions" and parts[2] == "decide_all":
                self.send_json(
                    review_studio.decide_all_suggestions(parts[0], params.get("decision"))
                )
            elif len(parts) == 2 and parts[1] == "checklist":
                if "checked" not in params:
                    self.send_json({"error": "checked is required"}, status=400)
                    return
                self.send_json(
                    review_studio.update_checklist(
                        parts[0],
                        item_key=params.get("item_key"),
                        label=params.get("label"),
                        checked=params.get("checked"),
                    )
                )
            elif len(parts) == 2 and parts[1] == "ready":
                self.send_json(review_studio.mark_ready(parts[0]))
            elif len(parts) == 2 and parts[1] == "posting_kit":
                self.send_json(review_studio.build_posting_kit_for_item(parts[0]))
            elif len(parts) == 2 and parts[1] == "posted":
                self.send_json(
                    review_studio.mark_posted(
                        parts[0],
                        url=params.get("url"),
                        platform=params.get("platform"),
                    )
                )
            elif len(parts) == 2 and parts[1] == "unpost":
                self.send_json(
                    review_studio.unmark_posted(
                        parts[0],
                        status=params.get("status") or "ready",
                    )
                )
            else:
                self.send_json({"error": "Not found"}, status=404)
                return
            cloud_sync.request_sync()
        except ValueError as exc:
            self._send_review_error(exc)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)

        if url.path == "/api/health":
            self.send_json({
                "ok": True,
                "service": "magnm-engine",
                "version": "0.1.0",
                "data_root": paths.DATA_ROOT,
            })
            return

        if ENGINE_TOKEN and url.path == "/":
            qs = urllib.parse.parse_qs(url.query)
            tok_list = qs.get("token")
            if tok_list and hmac.compare_digest(tok_list[0], ENGINE_TOKEN):
                # Reply 200 with a tiny page that moves on to "/" by script. A 302 would
                # keep the navigation "cross-site" (it started from the desktop app's own
                # page), and browsers then refuse to send a SameSite=Strict cookie.
                body = (b"<!doctype html><meta charset=\"utf-8\"><title>Magnm Studio</title>"
                        b"<script>location.replace('/')</script>")
                self.send_response(200)
                self.send_header(
                    "Set-Cookie",
                    f"magnm_token={ENGINE_TOKEN}; HttpOnly; SameSite=Strict; Path=/",
                )
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        if url.path == "/auth/callback":
            self._handle_auth_callback(url)
            return

        if not self._is_authorized():
            self.send_json({"error": "unauthorized"}, status=401)
            return

        if url.path == "/" or url.path == "/index.html":
            if not web_ui.serve_static(self, "/index.html"):
                self.serve_ui()
        elif url.path == "/legacy":
            self.serve_ui()
        elif url.path == "/pitch" or url.path.startswith("/pitch/"):
            if not pitch_host.serve(self, url):
                self.send_error(404, "Not Found")
        elif url.path == "/api/events":
            self._serve_sse()
        elif url.path == "/api/status":
            state = cmo_runtime.get_system_status()
            state["current_job"] = jobs.get_job_status_legacy()
            cfg = ai_gateway.get_config()
            matrix = ai_gateway.get_role_model_matrix()
            roles_ready = sum(1 for m in matrix if m["has_key"])
            team_ai_active = any(m.get("key_source") == "team" for m in matrix)

            state["env_status"] = {
                "has_env_file": os.path.exists(paths.data(".env")),
                "default_provider": cfg["DEFAULT_PROVIDER"],
                "nvidia_nim_base_url": cfg["NVIDIA_NIM_BASE_URL"],
                "nvidia_nim_global": bool(cfg["NVIDIA_NIM_API_KEY"]),
                "nvidia_nim_global_masked": ai_gateway._mask_secret(cfg["NVIDIA_NIM_API_KEY"]),
                "nvidia_nim_fallback_model": cfg["NIM_FALLBACK_MODEL"],
                "nvidia_nim_roles_configured": roles_ready,
                "team_ai": team_ai_active,
                "openai": bool(cfg["OPENAI_API_KEY"]),
                "anthropic": bool(cfg["ANTHROPIC_API_KEY"]),
                "gemini": bool(cfg["GEMINI_API_KEY"]),
                "groq": bool(cfg["GROQ_API_KEY"]),
                "ollama_url": cfg["OLLAMA_BASE_URL"],
                "chatgpt_cli": ai_gateway._is_port_open("127.0.0.1", 9222, timeout=0.1)
            }
            state["models_matrix"] = matrix
            state["nim_catalog_suggestions"] = ai_gateway.NIM_CATALOG_SUGGESTIONS
            state["research_findings"] = _get_latest_research_findings()
            state["editorial_plan"] = _parse_editorial_plan()
            state["channels"] = _parse_channels_from_config()
            state["crisis_playbook"] = _read_text_safe("playbooks/crisis.md")
            state["product_facts"] = _read_text_safe("research/product-facts.md")
            self.send_json(state)

        elif url.path == "/api/job_status":
            self.send_json(jobs.get_job_status_legacy())

        elif url.path == "/api/jobs":
            qs = urllib.parse.parse_qs(url.query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 500))
            self.send_json(jobs.list_jobs(limit=limit))

        elif url.path == "/api/setup_check":
            self.send_json(setup_check.run_setup_checks())

        elif url.path == "/api/key_pool":
            self.send_json(key_pool.status())

        elif url.path == "/api/roster" or url.path == "/api/agents":
            self.send_json(agent_chat.get_roster_status())

        elif url.path == "/api/content":
            qs = urllib.parse.parse_qs(url.query)
            status_filter = qs.get("status", [None])[0] or None
            type_filter = qs.get("type", [None])[0] or None
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 500))
            items = db.list_content_items(status=status_filter, content_type=type_filter, limit=limit)
            # Review counts run the Reviewer per item, so only the Review list asks for them.
            if qs.get("review_counts", [""])[0] == "1":
                items = review_studio.enrich_content_list(items)
            self.send_json(items)

        elif url.path.startswith("/api/content/"):
            rest = url.path[len("/api/content/"):].strip("/")
            parts = rest.split("/") if rest else []
            if len(parts) == 1:
                item = db.get_content_item(parts[0], include_versions=True)
                if item is None:
                    self.send_json({"error": "Content not found"}, status=404)
                    return
                self.send_json(item)
            elif len(parts) == 2 and parts[1] == "file":
                item = db.get_content_item(parts[0])
                if item is None:
                    self.send_json({"error": "Content not found"}, status=404)
                    return
                qs = urllib.parse.parse_qs(url.query)
                req_path = (qs.get("path", [""])[0] or "").strip()
                if not req_path:
                    self.send_json({"error": "path query parameter required"}, status=400)
                    return
                norm_req = os.path.normpath(req_path).replace("\\", "/")
                allowed_paths = set()
                for entry in item.get("files") or []:
                    p = entry.get("path") if isinstance(entry, dict) else (entry if isinstance(entry, str) else "")
                    if p:
                        allowed_paths.add(os.path.normpath(p).replace("\\", "/"))
                if norm_req not in allowed_paths:
                    self.send_json({"error": "File is not listed in content files_json"}, status=403)
                    return
                full_path = file_serving.servable_path(os.path.join(paths.DATA_ROOT, norm_req))
                if not full_path:
                    self.send_json({"error": "File not found"}, status=404)
                    return
                mime, _ = mimetypes.guess_type(full_path)
                mime = mime or "application/octet-stream"
                self._serve_file_with_range(full_path, mime)
            elif len(parts) == 2 and parts[1] == "auto_review":
                item = db.get_content_item(parts[0], include_versions=True)
                if item is None:
                    self.send_json({"error": "Content not found"}, status=404)
                    return
                self.send_json(review.auto_review(item))
            else:
                self.send_json({"error": "Not found"}, status=404)

        elif url.path == "/api/assets":
            self.send_json(assets_manager.get_assets())

        elif url.path == "/api/messages":
            self.send_json(team_room.load_messages())

        elif url.path == "/api/runs":
            qs = urllib.parse.parse_qs(url.query)
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(limit, 500))
            self.send_json(db.get_recent_runs(limit=limit))

        elif url.path == "/api/product_rules":
            try:
                self.send_json(product_rules.load_rules())
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/workflows":
            qs = urllib.parse.parse_qs(url.query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 500))
            self.send_json(workflows.list_runs(limit=limit))

        elif url.path.startswith("/api/workflows/"):
            run_id = url.path[len("/api/workflows/"):].strip("/")
            if not run_id or "/" in run_id:
                self.send_json({"error": "Not found"}, status=404)
                return
            run = workflows.get_run(run_id)
            if run is None:
                self.send_json({"error": "Workflow run not found"}, status=404)
                return
            self.send_json(run)

        elif url.path == "/api/sessions":
            qs = urllib.parse.parse_qs(url.query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 500))
            status_filter = qs.get("status", [None])[0]
            self.send_json(agent_runtime.list_sessions(limit=limit, status=status_filter))

        elif url.path.startswith("/api/sessions/"):
            session_id = url.path[len("/api/sessions/"):].strip("/")
            if not session_id or "/" in session_id:
                self.send_json({"error": "Not found"}, status=404)
                return
            sess = agent_runtime.get_session(session_id)
            if sess is None:
                self.send_json({"error": "Session not found"}, status=404)
                return
            self.send_json(sess)

        elif url.path.startswith("/api/chat_history/"):
            agent_id = url.path.split("/")[-1]
            self.send_json(agent_chat.get_agent_chat_history(agent_id))

        elif url.path.startswith("/api/asset_file/"):
            asset_id = url.path.split("/")[-1]
            all_assets = assets_manager.get_assets()
            target = next((a for a in all_assets if a["id"] == asset_id), None)
            if target:
                full_path = file_serving.servable_path(paths.data(target["relative_path"]), "assets")
                if full_path:
                    mime, _ = mimetypes.guess_type(full_path)
                    mime = mime or target.get("mime_type", "application/octet-stream")
                    self._serve_file_with_range(full_path, mime)
                    return
            self.send_error(404, "Asset file not found")

        elif url.path == "/api/repos":
            repo_list = repos.list_repos()
            enriched = []
            for r in repo_list:
                snap = db.get_latest_repo_snapshot(r["id"])
                sdata = (snap.get("data") if snap else None) or {}
                enriched.append({
                    **r,
                    "last_sync": snap.get("fetched_at") if snap else None,
                    "version": sdata.get("version") or r.get("version"),
                    "branch": sdata.get("branch") or r.get("branch"),
                    "stars": sdata.get("stars"),
                    "recent_commits": sdata.get("recent_commits") or [],
                    "rate_limited": bool(sdata.get("rate_limited")),
                    "rate_limit_message": sdata.get("rate_limit_message"),
                    "cloud": personal_repo_sync.cloud_info(r["id"]) if r.get("scope") == "personal" else None,
                })
            self.send_json(enriched)

        elif url.path == "/api/shipped":
            qs = urllib.parse.parse_qs(url.query)
            repo_filter = qs.get("repo_id", [None])[0] or None
            try:
                days = int(qs.get("days", ["7"])[0])
            except (TypeError, ValueError):
                days = 7
            days = max(1, min(days, 365))
            self.send_json(repos.shipped(repo_id=repo_filter, days=days))

        elif url.path == "/api/facts":
            qs = urllib.parse.parse_qs(url.query)
            product_filter = qs.get("product_id", [None])[0] or None
            status_filter = qs.get("status", [None])[0] or None
            self.send_json(db.list_product_facts(product_id=product_filter, status=status_filter))

        elif url.path == "/api/watch_topics":
            self.send_json(db.list_watch_topics())

        elif url.path == "/api/memory":
            qs = urllib.parse.parse_qs(url.query)
            raw_aid = (qs.get("agent_id", [None])[0] or "").strip()
            if raw_aid:
                ag = agents.get_agent(raw_aid)
                canon_aid = ag["id"] if ag else raw_aid.lower()
            else:
                canon_aid = None
            self.send_json(db.list_agent_memory(agent_id=canon_aid))

        elif url.path == "/api/bakeoff/latest":
            qs = urllib.parse.parse_qs(url.query)
            reveal_raw = (qs.get("reveal", ["0"])[0] or "").strip().lower()
            reveal = reveal_raw in ("1", "true", "yes")
            latest = bakeoff.get_latest_results(reveal=reveal)
            if latest is None:
                self.send_json({"error": "No bakeoff results found"}, status=404)
                return
            self.send_json(latest)

        elif url.path == "/api/research_items":
            qs = urllib.parse.parse_qs(url.query)
            topic_filter = qs.get("topic_id", [None])[0] or None
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(limit, 500))
            self.send_json(db.list_research_items(limit=limit, topic_id=topic_filter))

        elif url.path == "/api/browser_frame":
            qs = urllib.parse.parse_qs(url.query)
            req_path = (qs.get("path", [""])[0] or "").strip()
            if not req_path:
                self.send_json({"error": "path query parameter required"}, status=400)
                return
            shots_root = os.path.realpath(paths.data("cache", "browser-shots"))
            if os.path.isabs(req_path):
                candidate = os.path.realpath(req_path)
            else:
                norm_rel = os.path.normpath(req_path).replace("\\", "/")
                if norm_rel.startswith("cache/browser-shots/"):
                    candidate = os.path.realpath(os.path.join(paths.DATA_ROOT, norm_rel))
                else:
                    candidate = os.path.realpath(os.path.join(shots_root, norm_rel))
            try:
                inside = os.path.commonpath([shots_root, candidate]) == shots_root and candidate != shots_root
            except ValueError:
                inside = False
            if not inside:
                self.send_json({"error": "Forbidden: path outside browser-shots"}, status=403)
                return
            if not os.path.isfile(candidate):
                self.send_json({"error": "Frame not found"}, status=404)
                return
            mime, _ = mimetypes.guess_type(candidate)
            self._serve_file_with_range(candidate, mime or "image/jpeg")

        elif url.path.startswith("/api/review/"):
            self._handle_review_get(url)

        elif url.path.startswith("/api/pitch/"):
            self._handle_pitch_get(url)

        elif url.path == "/api/account" or url.path.startswith("/api/account/"):
            self._handle_account_get(url)

        elif url.path.startswith("/api/team/"):
            self._handle_team_get(url)

        elif url.path.startswith("/api/admin/"):
            self._handle_admin_get(url)

        elif url.path == "/api/notifications" or url.path.startswith("/api/notifications/"):
            self._handle_notifications_get(url)

        elif url.path == "/api/reminders" or url.path.startswith("/api/reminders/"):
            self._handle_reminders_get(url)

        elif url.path.startswith("/api/results/"):
            self._handle_results_get(url)

        elif url.path == "/api/summaries" or url.path.startswith("/api/summaries/"):
            self._handle_summaries_get(url)

        elif url.path == "/api/learnings" or url.path.startswith("/api/learnings/"):
            self._handle_learnings_get(url)

        elif url.path == "/api/automation":
            self._handle_automation_get(url)

        elif url.path == "/api/board":
            self._handle_board_get(url)

        elif url.path.startswith("/api/coding/"):
            self._handle_coding_get(url)

        elif url.path.startswith("/api/social/"):
            self._handle_social_get(url)

        elif url.path.startswith("/api/posting/"):
            self._handle_posting_get(url)

        elif url.path == "/api/phone" or url.path.startswith("/api/phone/"):
            self._handle_phone_get(url)

        elif url.path.startswith("/api/asset/"):
            parts = url.path.split("/")[3:]
            if len(parts) >= 2:
                post_id, filename = parts[0], parts[1]
                for sub in ("posts", "video", "blogs"):
                    cand = file_serving.servable_path(paths.data("outputs", sub, post_id, filename), "outputs", sub)
                    if cand:
                        mime, _ = mimetypes.guess_type(cand)
                        mime = mime or "application/octet-stream"
                        self._serve_file_with_range(cand, mime)
                        return
            self.send_error(404, "Asset not found")

        else:
            if not url.path.startswith("/api/") and web_ui.serve_static(self, url.path):
                return
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        if not self._is_authorized():
            self.send_json({"error": "unauthorized"}, status=401)
            return

        if re.match(r"^/api/repos/[^/]+$", url.path):
            repo_id = urllib.parse.unquote(url.path.split("/")[3])
            if self._refuse_global_repo(repo_id):
                return
            if not repos.remove_repo(repo_id):
                self.send_json({"error": "Repository not found"}, status=404)
                return
            self.send_json({"success": True, "id": repo_id})
        elif re.match(r"^/api/memory/[^/]+$", url.path):
            mem_id = urllib.parse.unquote(url.path.split("/")[3])
            if not db.delete_agent_memory(mem_id):
                self.send_json({"error": "Memory item not found"}, status=404)
                return
            self.send_json({"success": True, "id": mem_id})
        elif re.match(r"^/api/watch_topics/[^/]+$", url.path):
            topic_id = urllib.parse.unquote(url.path.split("/")[3])
            if not db.delete_watch_topic(topic_id):
                self.send_json({"error": "Watch topic not found"}, status=404)
                return
            self.send_json({"success": True, "id": topic_id})
        elif re.match(r"^/api/reminders/[^/]+$", url.path):
            reminder_id = urllib.parse.unquote(url.path.split("/")[3])
            try:
                res = reminders.delete_reminder(reminder_id)
                cloud_sync.request_sync()
                self.send_json(res)
            except reminders.ReminderError as exc:
                self.send_json({"error": exc.message}, status=exc.status)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._is_authorized():
            self.send_json({"error": "unauthorized"}, status=401)
            return

        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self.send_json({"error": "Invalid Content-Length"}, status=400)
            return
        if content_len < 0:
            self.send_json({"error": "Invalid Content-Length"}, status=400)
            return
        if re.match(r"^/api/pitch/proposals/[^/]+/pdf$", url.path):
            proposal_id = urllib.parse.unquote(url.path.split("/")[4])
            self._handle_pitch_post_pdf(proposal_id, content_len)
            return

        if url.path == "/api/results/extract":
            self._handle_results_extract_post(content_len)
            return

        is_strict_json = (
            url.path.startswith("/api/review/")
            or url.path.startswith("/api/pitch/")
            or url.path.startswith("/api/account/")
            or url.path.startswith("/api/team/")
            or url.path.startswith("/api/admin/")
            or url.path.startswith("/api/notifications/")
            or url.path == "/api/reminders"
            or url.path.startswith("/api/reminders/")
            or url.path.startswith("/api/results/")
            or url.path.startswith("/api/summaries/")
            or url.path == "/api/learnings"
            or url.path.startswith("/api/learnings/")
            or url.path.startswith("/api/coding/")
            or url.path.startswith("/api/social/")
            or url.path.startswith("/api/posting/")
            or url.path == "/api/phone"
            or url.path.startswith("/api/phone/")
            or url.path.startswith("/api/automation/")
            or url.path.startswith("/api/board/")
        )
        max_len = MAX_REVIEW_BODY_BYTES if url.path.startswith("/api/review/") else MAX_POST_BYTES
        if content_len > max_len:
            self.send_json({"error": "Request body too large"}, status=413)
            return

        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            params = json.loads(body)
        except Exception:
            if is_strict_json:
                self.send_json({"error": "Invalid JSON body"}, status=400)
                return
            params = {}
        if is_strict_json and not isinstance(params, dict):
            self.send_json({"error": "JSON body must be an object"}, status=400)
            return

        if url.path.startswith("/api/review/"):
            self._handle_review_post(url, params)
            return

        if url.path.startswith("/api/pitch/"):
            self._handle_pitch_post(url, params)
            return

        if url.path.startswith("/api/account/"):
            self._handle_account_post(url, params)
            return

        if url.path.startswith("/api/team/"):
            self._handle_team_post(url, params)
            return

        if url.path.startswith("/api/admin/"):
            self._handle_admin_post(url, params)
            return

        if url.path.startswith("/api/notifications/"):
            self._handle_notifications_post(url, params)
            return

        if url.path == "/api/reminders" or url.path.startswith("/api/reminders/"):
            self._handle_reminders_post(url, params)
            return

        if url.path.startswith("/api/results/"):
            self._handle_results_post(url, params)
            return

        if url.path.startswith("/api/summaries/"):
            self._handle_summaries_post(url, params)
            return

        if url.path == "/api/learnings" or url.path.startswith("/api/learnings/"):
            self._handle_learnings_post(url, params)
            return

        if url.path.startswith("/api/coding/"):
            self._handle_coding_post(url, params)
            return

        if url.path.startswith("/api/social/"):
            self._handle_social_post(url, params)
            return

        if url.path.startswith("/api/posting/"):
            self._handle_posting_post(url, params)
            return

        if url.path == "/api/phone" or url.path.startswith("/api/phone/"):
            self._handle_phone_post(url, params)
            return

        if url.path.startswith("/api/automation/"):
            self._handle_automation_post(url, params)
            return

        if url.path.startswith("/api/board/"):
            self._handle_board_post(url, params)
            return

        if url.path == "/api/shutdown":
            self.send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if url.path == "/api/run":
            job_name = params.get("job")
            if not job_name or job_name not in cmo_runtime.ALLOWED_JOBS:
                self.send_json({"error": "Invalid job"}, status=400)
                return
            actor = params.get("actor", "UI")
            job = jobs.enqueue(job_name, actor=actor)
            jobs.start_worker()
            self.send_json({"success": True, "job": job, "message": f"Queued {job_name}"})

        elif url.path == "/api/jobs/cancel":
            job_id = params.get("id")
            if not job_id:
                self.send_json({"error": "id required"}, status=400)
                return
            job = jobs.cancel(job_id)
            if job is None:
                self.send_json({"error": "Job not found"}, status=404)
                return
            self.send_json({"success": True, "job": job})

        elif url.path == "/api/messages":
            text = params.get("text", "").strip()
            sender = params.get("sender", "CEO")
            attached_assets = params.get("attached_assets", [])
            if not text:
                self.send_json({"error": "Text required"}, status=400)
                return
            replies = team_room.post_message(sender, text, attached_assets=attached_assets)
            cloud_sync.request_sync()
            self.send_json({"success": True, "replies": replies})

        elif url.path == "/api/assets/upload":
            filename = params.get("filename", "upload.dat")
            content_b64 = params.get("content_base64", "")
            category = params.get("category", "brand")
            description = params.get("description", "")
            tags = params.get("tags", [])

            if not content_b64:
                self.send_json({"error": "No file content"}, status=400)
                return
            try:
                raw_bytes = base64.b64decode(content_b64)
                entry = assets_manager.register_asset(filename, raw_bytes, category=category, description=description, tags=tags)
                self.send_json({"success": True, "asset": entry})
            except Exception as e:
                self.send_json({"error": f"Upload failed: {e}"}, status=500)

        elif url.path == "/api/assets/delete":
            asset_id = params.get("id")
            if not asset_id:
                self.send_json({"error": "id required"}, status=400)
                return
            res = assets_manager.delete_asset(asset_id)
            self.send_json({"success": res})

        elif url.path == "/api/repos":
            try:
                created = repos.add_repo(params)
                self.send_json({"success": True, "repo": created})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)

        elif re.match(r"^/api/repos/[^/]+/update$", url.path):
            repo_id = urllib.parse.unquote(url.path.split("/")[3])
            if self._refuse_global_repo(repo_id):
                return
            try:
                updated = repos.update_repo(repo_id, params)
                cloud_sync.request_sync()
                self.send_json({"success": True, "repo": updated})
            except ValueError as exc:
                msg = str(exc)
                self.send_json({"error": msg}, status=404 if msg == "Repository not found" else 400)

        elif re.match(r"^/api/repos/[^/]+/sync$", url.path):
            repo_id = urllib.parse.unquote(url.path.split("/")[3])
            try:
                snap = repos.sync_repo(repo_id)
                self.send_json({"success": bool(snap.get("synced", True)), "repo": snap})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=404)
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)

        elif re.match(r"^/api/repos/[^/]+/remove$", url.path):
            repo_id = urllib.parse.unquote(url.path.split("/")[3])
            if self._refuse_global_repo(repo_id):
                return
            if not repos.remove_repo(repo_id):
                self.send_json({"error": "Repository not found"}, status=404)
                return
            self.send_json({"success": True, "id": repo_id})

        elif url.path == "/api/facts":
            try:
                fact = db.create_product_fact(
                    product_id=params.get("product_id"),
                    text=params.get("text"),
                    source_url=params.get("source_url"),
                    source_kind=params.get("source_kind") or "manual",
                    status="approved",
                    created_by=params.get("created_by") or "CEO",
                    decided_at=datetime.now(timezone.utc).isoformat(),
                )
                self.send_json({**fact, "success": True})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)

        elif re.match(r"^/api/facts/[^/]+/decide$", url.path):
            fact_id = urllib.parse.unquote(url.path.split("/")[3])
            try:
                updated = db.decide_product_fact(
                    fact_id,
                    params.get("status"),
                    text=params.get("text"),
                )
                self.send_json({**updated, "success": True})
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)

        elif url.path == "/api/repos/add":
            remote_url = params.get("remote_url", "").strip()
            name = params.get("name", "").strip()
            product = params.get("product", "").strip() or name
            status = params.get("status", "in_development")
            marketing_angle = params.get("marketing_angle", "")

            if not remote_url or not name:
                self.send_json({"error": "Remote URL and Name are required"}, status=400)
                return

            repo_id = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
            repo_conf = {
                "id": repo_id,
                "name": name,
                "product": product,
                "status": status,
                "remote_url": remote_url,
                "local_path": params.get("local_path", "").strip() or None,
                "marketing_angle": marketing_angle
            }
            res = repo_sync.add_repository(repo_conf)
            self.send_json({"success": True, "repos": res})

        elif url.path == "/api/agent_chat":
            agent_id = params.get("agent_id")
            message = params.get("message")
            attached_assets = params.get("attached_assets", [])
            if not agent_id or not message:
                self.send_json({"error": "agent_id and message required"}, status=400)
                return
            res = agent_chat.chat_with_agent(agent_id, message, attached_assets=attached_assets)
            cloud_sync.request_sync()
            self.send_json(res)

        elif url.path == "/api/approve":
            post_id = params.get("post_id")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "approved", actor="CEO")
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/request_revision":
            post_id = params.get("post_id")
            notes = params.get("notes", "Revision requested by CEO.")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "revision_required", actor="CEO")
                cmo_runtime.log_activity("CEO", "Writer", f"revision_requested:{post_id}", notes)
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/publish":
            post_id = params.get("post_id")
            if not post_id:
                self.send_json({"error": "post_id required"}, status=400)
                return
            try:
                manifest = cmo_runtime.set_post_state(post_id, "published", actor="CEO")
                self.send_json({"success": True, "manifest": manifest})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/save_env":
            key_name = params.get("key_name", "").strip().upper()
            key_val = params.get("key_val", "").strip()
            if not key_name:
                self.send_json({"error": "Invalid key name"}, status=400)
                return
            if _is_keychain_mode() and _is_secret_env_name(key_name):
                self.send_json(_KEYCHAIN_ERROR_PAYLOAD)
                return
            try:
                saved = ai_gateway.save_env_values({key_name: key_val})
                self.send_json({"success": True, "saved_keys": saved})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/save_env_config":
            updates = params.get("updates", {})
            if not isinstance(updates, dict) or not updates:
                self.send_json({"error": "updates dictionary required"}, status=400)
                return
            if _is_keychain_mode():
                secret_keys = [k for k in updates.keys() if _is_secret_env_name(k)]
                if secret_keys:
                    non_secrets = {k: v for k, v in updates.items() if not _is_secret_env_name(k)}
                    if non_secrets:
                        try:
                            ai_gateway.save_env_values(non_secrets)
                        except Exception:
                            pass
                    self.send_json(_KEYCHAIN_ERROR_PAYLOAD)
                    return
            try:
                saved = ai_gateway.save_env_values(updates)
                cmo_runtime.log_activity("CEO", "AI_GATEWAY", "env_config_updated", f"{len(saved)} keys saved to .env")
                self.send_json({"success": True, "saved_keys": saved})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/test_ai":
            role_id = params.get("role_id", "cmo")
            res = ai_gateway.test_role_endpoint(role_id)
            self.send_json(res)

        elif url.path == "/api/cards/render":
            raw_post_id = params.get("post_id", "custom-card").strip()
            post_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_post_id).strip("-") or "custom-card"
            field = params.get("field", "sky")
            kicker = params.get("kicker", "")
            big_lines = params.get("big", ["ASKS", "FIRST."])
            if isinstance(big_lines, str):
                big_lines = [ln.strip() for ln in big_lines.splitlines() if ln.strip()]
            serif = params.get("serif", "")
            foot = params.get("foot", "magnm-org.vercel.app")
            sizes = params.get("sizes", ["4x5", "1x1", "16x9"])
            asset_id = params.get("asset_id", "")

            out_dir = paths.data("outputs", "posts", post_id)
            os.makedirs(out_dir, exist_ok=True)

            spec = {
                "id": post_id,
                "field": field,
                "kicker": kicker,
                "big": big_lines[:3],
                "serif": serif,
                "foot": foot,
                "sizes": sizes
            }

            if asset_id:
                asset_match = next((a for a in assets_manager.get_assets() if a["id"] == asset_id), None)
                if asset_match and asset_match.get("file_group") == "image":
                    img_abs = paths.data(asset_match["relative_path"])
                    if os.path.exists(img_abs):
                        spec["image"] = img_abs

            spec_path = os.path.join(out_dir, "card_spec.json")
            with open(spec_path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)

            try:
                card_path = paths.resource("templates", "card.py")
                card_spec = importlib.util.spec_from_file_location("magnm_templates_card", card_path)
                if card_spec is None or card_spec.loader is None:
                    raise RuntimeError(f"Unable to load card renderer from {card_path}")
                card_mod = importlib.util.module_from_spec(card_spec)
                card_spec.loader.exec_module(card_mod)
                written_paths = card_mod.render(spec, out_dir)
            except ImportError:
                self.send_json({"success": False, "error": "Card rendering needs the Pillow library."})
                return
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
                return

            try:
                rendered_paths = [os.path.basename(p) for p in written_paths if str(p).strip()]
                kit_path = os.path.join(out_dir, "POSTING-KIT.md")
                if not os.path.exists(kit_path):
                    with open(kit_path, "w", encoding="utf-8") as kf:
                        kf.write(
                            f"# Posting kit: {post_id}\n"
                            f"Owner: CEO approves & posts manually.\n"
                            f"Palette Field: {field}\n"
                            f"Headline: {' / '.join(big_lines[:3])}\n"
                            f"Subline: {serif}\n"
                            f"Rendered formats: {', '.join(rendered_paths)}\n"
                        )

                manifest = cmo_runtime.build_delivery_package(post_id)
                cmo_runtime.log_activity("Designer", "CEO", f"render_cards:{post_id}", f"outputs/posts/{post_id}/")
                self.send_json({
                    "success": True,
                    "post_id": post_id,
                    "rendered_files": rendered_paths,
                    "manifest": manifest
                })
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/pr/draft_reply":
            scenario = params.get("scenario", "misunderstanding")
            severity = params.get("severity", "L1")
            source_context = params.get("source_context", "").strip()
            if not source_context:
                self.send_json({"error": "source_context is required"}, status=400)
                return

            crisis_playbook = _read_text_safe("playbooks/crisis.md")
            facts = _read_text_safe("research/product-facts.md")
            repo_sync_md = _read_text_safe("research/repo-sync.md", limit=1500)
            # A reply that names a confidential product runs on local AI, with everything. Any other reply
            # goes to cloud AI and gets neither the confidential facts nor the confidential repos' commits.
            is_confidential = confidential_guard.mentions(source_context)
            if not is_confidential:
                facts = confidential_guard.drop_confidential_sections(facts)
                repo_sync_md = confidential_guard.drop_confidential_sections(
                    _read_text_safe("research/repo-sync.md", limit=1_000_000)
                )[:1500]

            system_prompt = f"""You are the Community & PR Crisis Lead (@community) working with the Gatekeeper (@reviewer) at MAGNM.
Draft a concise, high-trust external PR or client response strictly adhering to MAGNM's crisis playbook and verified ground truth.

Crisis & Reply Playbook:
{crisis_playbook}

Verified Product Facts (ONLY claim what is written here):
{facts}

Live Codebase Context:
{repo_sync_md}

Hard Rules:
1. ZERO emojis. Direct, calm, transparent tone.
2. Never argue, never use hype words, never invent features/benchmarks, never admit unverified legal liability.
3. Follow the product rules:
{product_rules.prompt_block()}
4. Output structure:
   - SEVERITY & ESCALATION CHECK: State L1/L2/L3 action and whether CEO approval is mandatory before posting.
   - DRAFT RESPONSE: Ready-to-review reply text.
   - FACTUAL VERIFICATION NOTES: Exact claims referenced from product-facts.md or repo-sync.md, plus anything the CEO must confirm first.
"""
            user_prompt = f"Scenario Type: {scenario}\nSeverity Classification: {severity}\nIncoming Comment / Client / Press Inquiry:\n{source_context}"

            try:
                res = ai_gateway.generate_completion_with_meta(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model_tier="reasoning",
                    agent_role="community",
                    confidential=is_confidential,
                )
                cmo_runtime.log_activity("Community", "CEO", f"pr_draft:{scenario}:{severity}", f"model:{res.get('model_used')}")
                self.send_json({
                    "success": True,
                    "draft": res["text"],
                    "model_used": res.get("model_used"),
                    "provider": res.get("provider"),
                    "fallback_used": res.get("fallback_used", False),
                    "fallback_reason": res.get("fallback_reason"),
                    "severity": severity,
                    "scenario": scenario
                })
            except ai_gateway.AIAuthError as e:
                self.send_json({"success": False, "error_code": "ai_auth_error", "error": str(e)})
            except ai_gateway.AINotConnected as e:
                self.send_json({"success": False, "error_code": "ai_not_connected", "error": str(e)})

        elif url.path == "/api/check_text":
            text = params.get("text", "")
            if not isinstance(text, str):
                self.send_json({"error": "text must be a string"}, status=400)
                return
            try:
                warnings = list(product_rules.check_text(text))
                platform = (params.get("platform") or "").strip()
                if platform:
                    warnings.extend(
                        platforms.check_limits(
                            platform,
                            text,
                            title=params.get("title"),
                            content_type=params.get("type"),
                        )
                    )
                self.send_json({"warnings": warnings})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/product_rules":
            try:
                write_role = team_knowledge.check_write_access()
                product_id = params.get("product_id") or params.get("id")
                updated_prod, rules_data = product_rules.save_product_rule(product_id, params)
                if write_role == "admin":
                    team_knowledge.queue_auto_publish("product_rules")
                self.send_json({
                    "success": True,
                    "product": updated_prod,
                    "rules": rules_data,
                })
            except team_knowledge.TeamKnowledgeError as exc:
                self.send_json({"error": exc.message}, status=exc.status)
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)

        elif url.path == "/api/schedule/update":
            try:
                scheduler.update_schedule(
                    params.get("id"),
                    enabled=params.get("enabled"),
                    interval_hours=params.get("interval_hours"),
                    time_of_day=params.get("time"),
                    day=params.get("day"),
                )
                self.send_json({"success": True})
            except LookupError as exc:
                self.send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)

        elif url.path == "/api/import_legacy":
            try:
                status_code, payload = _import_legacy_folder(params.get("path", ""))
                self.send_json(payload, status=status_code)
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, status=500)

        elif url.path == "/api/schedule/toggle":
            try:
                scheduler.toggle_schedule(params.get("id"))
                self.send_json({"success": True})
            except LookupError as exc:
                self.send_json({"error": str(exc)}, status=404)

        elif url.path == "/api/sessions/start":
            agent_id = (params.get("agent_id") or "").strip()
            message = params.get("message")
            system_prompt = params.get("system_prompt")
            allowed_tools = params.get("allowed_tools")
            if not agent_id or not isinstance(message, str) or not message.strip():
                self.send_json({"error": "agent_id and message are required"}, status=400)
                return
            if not isinstance(system_prompt, str) or not isinstance(allowed_tools, list):
                self.send_json(
                    {"error": "system_prompt (string) and allowed_tools (array) are required"},
                    status=400,
                )
                return
            session = agent_runtime.create_session(
                agent_id,
                message,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
                attachments=params.get("attachments"),
                parent_session_id=params.get("parent_session_id"),
                depth=int(params.get("depth") or 0),
                confidential=bool(params.get("confidential")),
                title=params.get("title"),
            )
            _SESSION_EXECUTOR.submit(agent_runtime.run_session, session["id"])
            cloud_sync.request_sync()
            self.send_json({"success": True, "session_id": session["id"]})

        elif re.match(r"^/api/sessions/[^/]+/answer$", url.path):
            session_id = url.path.split("/")[3]
            if "answer" not in params:
                self.send_json({"error": "answer is required"}, status=400)
                return
            try:
                agent_runtime.prepare_resume(session_id, params.get("answer"))
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)
                return
            _SESSION_EXECUTOR.submit(agent_runtime.run_session, session_id)
            cloud_sync.request_sync()
            self.send_json({"success": True, "session_id": session_id, "status": "running"})

        elif re.match(r"^/api/sessions/[^/]+/cancel$", url.path):
            session_id = url.path.split("/")[3]
            sess = agent_runtime.cancel_session(session_id)
            if sess is None:
                self.send_json({"error": "Session not found"}, status=404)
                return
            cloud_sync.request_sync()
            self.send_json({"success": True, "session": sess})

        elif url.path == "/api/workflows/start":
            kind = (params.get("kind") or "").strip()
            inputs = params.get("inputs")
            if inputs is None:
                inputs = {k: v for k, v in params.items() if k not in ("kind", "actor")}
            if not isinstance(inputs, dict):
                self.send_json({"error": "inputs must be an object"}, status=400)
                return
            try:
                run = workflows.start(kind, inputs, actor=params.get("actor", "UI"))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            cloud_sync.request_sync()
            self.send_json(run)

        elif re.match(r"^/api/workflows/[^/]+/cancel$", url.path):
            run_id = url.path.split("/")[3]
            run = workflows.cancel(run_id)
            if run is None:
                self.send_json({"error": "Workflow run not found"}, status=404)
                return
            self.send_json(run)

        elif re.match(r"^/api/workflows/[^/]+/(pause|resume|retry)$", url.path):
            run_id, verb = url.path.split("/")[3], url.path.split("/")[4]
            try:
                run = {"pause": workflows.pause, "resume": workflows.resume, "retry": workflows.retry}[verb](run_id)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=409)
                return
            if run is None:
                self.send_json({"error": "Workflow run not found"}, status=404)
                return
            cloud_sync.request_sync()
            self.send_json(run)

        elif url.path == "/api/jobs/retry":
            try:
                job = jobs.retry(params.get("id")) if isinstance(params.get("id"), str) else None
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=409)
                return
            if job is None:
                self.send_json({"error": "Job not found"}, status=404)
                return
            jobs.start_worker()
            self.send_json({"success": True, "job": job})

        elif re.match(r"^/api/gates/[^/]+/answer$", url.path):
            gate_id = url.path.split("/")[3]
            if "answer" not in params:
                self.send_json({"error": "answer is required"}, status=400)
                return
            try:
                res = workflows.answer_gate(
                    gate_id,
                    params.get("answer"),
                    feedback=params.get("feedback"),
                )
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)
                return
            self.send_json(res)

        elif re.match(r"^/api/content/[^/]+/status$", url.path):
            content_id = url.path.split("/")[3]
            status_val = (params.get("status") or "").strip().lower()
            if status_val not in ("draft", "ready", "posted"):
                self.send_json({"error": "status must be 'draft', 'ready', or 'posted'"}, status=400)
                return
            try:
                if status_val in ("draft", "ready"):
                    existing = db.get_content_item(content_id)
                    if existing is None:
                        raise ValueError(f"Content item '{content_id}' not found")
                    if existing.get("status") == "posted":
                        updated = db.unmark_posted(content_id, status=status_val)
                    elif status_val == "ready":
                        updated = db.mark_ready(content_id)
                    else:
                        raise ValueError("status must be 'ready' or 'posted'")
                else:
                    updated = db.mark_posted(
                        content_id,
                        url=params.get("url"),
                        platform=params.get("platform"),
                        posted_at=params.get("posted_at"),
                    )
            except ValueError as exc:
                msg = str(exc)
                status = 404 if "not found" in msg.lower() else 400
                self.send_json({"error": msg}, status=status)
                return
            self.send_json(updated)
            cloud_sync.request_sync()

        elif url.path == "/api/setup/install_browser":
            actor = params.get("actor", "UI")
            job = jobs.enqueue("install_browser", actor=actor, max_attempts=1)
            jobs.start_worker()
            self.send_json({"success": True, "job": job, "message": "Queued install_browser"})

        elif url.path == "/api/watch_topics":
            try:
                created = db.create_watch_topic(
                    query=params.get("query"),
                    sources=params.get("sources"),
                    cadence_hours=params.get("cadence_hours", 24),
                    days=params.get("days", 7),
                    enabled=params.get("enabled", True),
                )
                self.send_json({**created, "success": True})
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, status=400)

        elif re.match(r"^/api/watch_topics/[^/]+/toggle$", url.path):
            topic_id = urllib.parse.unquote(url.path.split("/")[3])
            updated = db.toggle_watch_topic(topic_id)
            if updated is None:
                self.send_json({"error": "Watch topic not found"}, status=404)
                return
            self.send_json({**updated, "success": True})

        elif url.path == "/api/feedback":
            content_id = (params.get("content_id") or "").strip()
            fb_text = (params.get("text") or "").strip()
            if not content_id:
                self.send_json({"error": "content_id is required"}, status=400)
                return
            if not fb_text:
                self.send_json({"error": "text is required"}, status=400)
                return
            item = db.get_content_item(content_id)
            if item is None:
                self.send_json({"error": "Content not found"}, status=404)
                return
            raw_agent = (params.get("agent_id") or item.get("created_by") or "").strip()
            if not raw_agent:
                self.send_json({"error": "agent_id is required when content item has no created_by"}, status=400)
                return
            ag = agents.get_agent(raw_agent)
            canon_agent = ag["id"] if ag else raw_agent.lower()
            mem = db.create_agent_memory(
                agent_id=canon_agent,
                text=fb_text,
                source="feedback",
                content_id=content_id,
            )
            db.log_activity("CEO", canon_agent, f"feedback:{content_id}", fb_text[:80])
            self.send_json({"success": True, "memory": mem})

        elif url.path == "/api/memory":
            raw_agent = (params.get("agent_id") or "").strip()
            mem_text = (params.get("text") or "").strip()
            if not raw_agent or not mem_text:
                self.send_json({"error": "agent_id and text are required"}, status=400)
                return
            ag = agents.get_agent(raw_agent)
            canon_agent = ag["id"] if ag else raw_agent.lower()
            pinned = bool(params.get("pinned", False))
            mem = db.create_agent_memory(
                agent_id=canon_agent,
                text=mem_text,
                source="manual",
                pinned=pinned,
            )
            self.send_json({**mem, "success": True})

        elif re.match(r"^/api/memory/[^/]+$", url.path):
            mem_id = urllib.parse.unquote(url.path.split("/")[3])
            pinned_val = params["pinned"] if "pinned" in params else None
            text_val = params["text"] if "text" in params else None
            try:
                updated = db.update_agent_memory(
                    mem_id,
                    pinned=bool(pinned_val) if pinned_val is not None else None,
                    text=text_val,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            if updated is None:
                self.send_json({"error": "Memory item not found"}, status=404)
                return
            self.send_json({**updated, "success": True})

        else:
            self.send_error(404, "Unknown endpoint")

    def serve_ui(self):
        new_studio_link = (
            '<a href="/" class="btn btn-sm" style="text-decoration:none;">Try the new Studio</a>'
            if web_ui.dist_dir() is not None
            else ""
        )
        html = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MAGNM // PR &amp; Social Agency Command Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-canvas: #090A0D;
      --bg-rail: #0E1015;
      --bg-panel: #13161C;
      --bg-elevated: #1A1E26;
      --border: #252A36;
      --border-strong: #32394A;
      --ink: #F2F2EE;
      --muted: #8C93A4;
      --lime: #C8F31D;
      --lime-dim: rgba(200, 243, 29, 0.12);
      --mint: #6EE7B7;
      --sky: #7DD3FC;
      --amber: #FCD34D;
      --danger: #F87171;
      --lilac: #C4B5FD;
      --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-canvas);
      color: var(--ink);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      overflow-x: hidden;
    }

    /* Uniform Geometric SVG Icons */
    .icon {
      width: 15px;
      height: 15px;
      stroke-width: 1.85;
      stroke: currentColor;
      fill: none;
      stroke-linecap: round;
      stroke-linejoin: round;
      vertical-align: middle;
      display: inline-block;
      flex-shrink: 0;
    }
    .icon-lg { width: 18px; height: 18px; }
    .icon-xl { width: 34px; height: 34px; }

    /* Left Navigation Rail */
    aside.sidebar {
      width: 264px;
      flex-shrink: 0;
      background: var(--bg-rail);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
    }
    .brand-box {
      padding: 20px 18px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .brand-top {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-mark {
      background: var(--lime);
      color: #090A0D;
      font-family: var(--font-mono);
      font-weight: 800;
      font-size: 12px;
      letter-spacing: 1px;
      padding: 4px 8px;
      border-radius: 4px;
    }
    .brand-name {
      font-size: 14px;
      font-weight: 700;
      letter-spacing: -0.2px;
    }
    .brand-sub {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }
    .nav-sections {
      padding: 14px 10px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      flex: 1;
    }
    .nav-group-label {
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.9px;
      color: #636B7D;
      padding: 0 10px 6px;
      text-transform: uppercase;
    }
    .nav-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 12px;
      border-radius: 6px;
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 500;
      transition: all 0.14s ease;
      margin-bottom: 2px;
      border: 1px solid transparent;
    }
    .nav-item:hover {
      background: var(--bg-panel);
      color: var(--ink);
    }
    .nav-item.active {
      background: var(--lime-dim);
      color: var(--lime);
      border-color: rgba(200, 243, 29, 0.28);
      font-weight: 600;
    }
    .nav-item-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .nav-pill {
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 99px;
      background: var(--bg-elevated);
      color: var(--ink);
      border: 1px solid var(--border-strong);
    }
    .rail-footer {
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      background: #0B0C10;
      font-family: var(--font-mono);
      font-size: 11px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .status-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
    }

    /* Main Workspace Shell */
    .workspace-shell {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    header.topbar {
      background: rgba(14, 16, 21, 0.9);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
      padding: 14px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 90;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .page-title {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -0.2px;
    }
    .page-crumb {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 16px;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--lime);
      display: inline-block;
      box-shadow: 0 0 8px var(--lime);
      margin-right: 4px;
    }
    .status-dot.busy {
      background: var(--amber);
      box-shadow: 0 0 8px var(--amber);
      animation: pulse 1s infinite alternate;
    }
    @keyframes pulse { from { opacity: 0.4; } to { opacity: 1; } }

    main.stage {
      padding: 24px 28px 48px;
      max-width: 1480px;
      width: 100%;
      margin: 0 auto;
    }
    .tab-view { display: none; flex-direction: column; gap: 22px; }
    .tab-view.active { display: flex; }

    /* Grids & Cards */
    .kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
    .grid-asymmetric { display: grid; grid-template-columns: 1.35fr 1fr; gap: 20px; }
    @media (max-width: 1180px) {
      .kpi-grid { grid-template-columns: repeat(2, 1fr); }
      .grid-3, .grid-4, .grid-asymmetric { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 860px) {
      body { flex-direction: column; }
      aside.sidebar { width: 100%; height: auto; position: static; }
      .grid-2, .grid-3, .grid-4, .grid-asymmetric, .kpi-grid { grid-template-columns: 1fr; }
    }

    .kpi-card {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .kpi-label {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.7px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .kpi-val {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.5px;
    }
    .kpi-foot {
      font-size: 11px;
      color: var(--muted);
    }

    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 12px;
      gap: 12px;
      flex-wrap: wrap;
    }
    .panel-title {
      font-family: var(--font-mono);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.9px;
      color: var(--lime);
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .panel-meta {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }

    /* Tables */
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .data-table th {
      text-align: left;
      font-family: var(--font-mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      color: var(--muted);
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      background: #101319;
    }
    .data-table td {
      padding: 11px 12px;
      border-bottom: 1px solid #1C2029;
      vertical-align: top;
    }
    .data-table tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    /* Badges & Tags */
    .badge {
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
    }
    .badge-draft { background: #1E222B; color: #A6ADBB; border: 1px solid #2E3442; }
    .badge-approved, .badge-ok { background: rgba(110, 231, 183, 0.12); color: var(--mint); border: 1px solid rgba(110, 231, 183, 0.35); }
    .badge-revision, .badge-warn { background: rgba(252, 211, 77, 0.12); color: var(--amber); border: 1px solid rgba(252, 211, 77, 0.35); }
    .badge-published, .badge-info { background: rgba(125, 211, 252, 0.12); color: var(--sky); border: 1px solid rgba(125, 211, 252, 0.35); }
    .badge-danger { background: rgba(248, 113, 113, 0.12); color: var(--danger); border: 1px solid rgba(248, 113, 113, 0.35); }
    .code-tag {
      font-family: var(--font-mono);
      font-weight: 700;
      font-size: 10px;
      background: var(--bg-elevated);
      color: var(--lime);
      padding: 2px 7px;
      border-radius: 3px;
      border: 1px solid var(--border-strong);
      display: inline-block;
    }

    /* Buttons & Form Inputs */
    .btn {
      background: var(--bg-elevated);
      color: var(--ink);
      border: 1px solid var(--border-strong);
      padding: 8px 14px;
      border-radius: 6px;
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      transition: all 0.14s ease;
    }
    .btn:hover { background: #242A36; border-color: #465066; }
    .btn-sm { padding: 4px 10px; font-size: 10px; }
    .btn-lime { background: var(--lime); color: #090A0D; border-color: var(--lime); font-weight: 700; }
    .btn-lime:hover { background: #D7FF3B; border-color: #D7FF3B; }
    .btn-mint { background: rgba(110, 231, 183, 0.16); color: var(--mint); border-color: rgba(110, 231, 183, 0.45); }
    .btn-mint:hover { background: rgba(110, 231, 183, 0.25); }
    .btn-warning { background: rgba(252, 211, 77, 0.14); color: var(--amber); border-color: rgba(252, 211, 77, 0.4); }
    .btn-danger { background: rgba(248, 113, 113, 0.14); color: var(--danger); border-color: rgba(248, 113, 113, 0.4); }

    input, select, textarea {
      background: #0B0D11;
      border: 1px solid var(--border-strong);
      color: var(--ink);
      padding: 9px 12px;
      border-radius: 6px;
      font-family: var(--font-mono);
      font-size: 12px;
      width: 100%;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--lime);
    }
    label.field-label {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.6px;
      display: block;
      margin-bottom: 5px;
    }

    /* Terminal & Previews */
    .terminal-window {
      background: #07080B;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 1.5;
      min-height: 140px;
      max-height: 220px;
      overflow-y: auto;
      color: #B4F8C8;
      white-space: pre-wrap;
    }

    /* Repo & Agent Cards */
    .repo-card, .agent-card, .asset-item-card, .review-card {
      background: #101319;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .agent-card { transition: border-color 0.15s, transform 0.15s; justify-content: space-between; }
    .agent-card:hover { border-color: var(--lime); transform: translateY(-2px); }
    .commit-box {
      background: #090A0E;
      border-left: 2px solid var(--lime);
      padding: 8px 12px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: #C7CCD6;
    }

    /* Review Gallery */
    .review-gallery { display: flex; gap: 14px; overflow-x: auto; padding-bottom: 6px; align-items: flex-start; }
    .review-img { max-height: 210px; border-radius: 6px; border: 1px solid var(--border-strong); background: #090A0D; }
    .review-video { max-height: 220px; max-width: 380px; border-radius: 6px; border: 1px solid var(--border-strong); background: #000; }
    .review-kit-box {
      background: #080A0D;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 1.5;
      max-height: 220px;
      overflow-y: auto;
      white-space: pre-wrap;
      color: #D2D7E0;
    }

    /* Global Room & Chat Drawer */
    .chat-modal {
      position: fixed;
      right: 0;
      top: 0;
      width: 520px;
      max-width: 100vw;
      height: 100vh;
      background: var(--bg-rail);
      border-left: 1px solid var(--border-strong);
      z-index: 200;
      display: none;
      flex-direction: column;
      box-shadow: -14px 0 40px rgba(0, 0, 0, 0.75);
    }
    .chat-modal.open { display: flex; }
    .chat-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--bg-panel);
    }
    .chat-messages {
      flex: 1;
      padding: 18px 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .chat-bubble {
      padding: 12px 15px;
      border-radius: 7px;
      font-size: 13px;
      line-height: 1.5;
      max-width: 90%;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: #202531;
      color: var(--ink);
      border: 1px solid var(--border-strong);
    }
    .chat-bubble.agent {
      align-self: flex-start;
      background: #141E19;
      color: #E5F9EC;
      border: 1px solid #264032;
    }
    .chat-bubble.cmo {
      align-self: flex-start;
      background: #14212E;
      color: #E2F1FF;
      border: 1px solid #284664;
    }
    .chat-bubble.redirect {
      align-self: flex-start;
      background: #281B18;
      color: #FFE4DE;
      border: 1px solid #59322A;
    }
    .chat-input-bar {
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
      background: var(--bg-panel);
    }

    /* Asset Preview */
    .asset-preview-box {
      height: 136px;
      background: #08090C;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      border: 1px solid var(--border);
    }
    .asset-preview-img { max-height: 100%; max-width: 100%; object-fit: contain; }

    /* Modal Backdrop */
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.78);
      z-index: 300;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal-card {
      background: var(--bg-panel);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      padding: 22px;
      width: 500px;
      max-width: 100%;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
  </style>
</head>
<body>
  <!-- Embedded Uniform SVG Vector Icon Set -->
  <svg style="display:none;">
    <defs>
      <g id="icon-grid"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></g>
      <g id="icon-radar"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle><line x1="12" y1="2" x2="12" y2="12"></line></g>
      <g id="icon-calendar"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></g>
      <g id="icon-shield"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></g>
      <g id="icon-check-circle"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></g>
      <g id="icon-globe"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></g>
      <g id="icon-users"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></g>
      <g id="icon-cpu"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></g>
      <g id="icon-folder"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></g>
      <g id="icon-clock"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></g>
      <g id="icon-zap"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></g>
      <g id="icon-search"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></g>
      <g id="icon-refresh"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></g>
      <g id="icon-check"><polyline points="20 6 9 17 4 12"></polyline></g>
      <g id="icon-edit"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></g>
      <g id="icon-send"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></g>
      <g id="icon-plus"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></g>
      <g id="icon-trash"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></g>
      <g id="icon-file"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></g>
      <g id="icon-film"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line></g>
      <g id="icon-paperclip"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></g>
      <g id="icon-x"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></g>
      <g id="icon-copy"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></g>
      <g id="icon-git"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></g>
      <g id="icon-alert"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></g>
      <g id="icon-external"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></g>
      <g id="icon-activity"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></g>
    </defs>
  </svg>

  <!-- Left Agency Rail -->
  <aside class="sidebar">
    <div>
      <div class="brand-box">
        <div class="brand-top">
          <span class="brand-mark">MAGNM</span>
          <span class="brand-name">PR &amp; Growth OS</span>
        </div>
        <div class="brand-sub">Studio + Freelance Agency</div>
      </div>

      <nav class="nav-sections">
        <div>
          <div class="nav-group-label">Agency Operations</div>
          <div class="nav-item active" data-tab="tab-ops" data-title="Agency Overview &amp; Presence" onclick="switchTab('tab-ops', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-grid"/></svg> Overview &amp; Presence</span>
          </div>
          <div class="nav-item" data-tab="tab-research" data-title="Business Research &amp; PR Radar" onclick="switchTab('tab-research', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-radar"/></svg> Research &amp; PR Radar</span>
            <span class="nav-pill" id="radarCountBadge">0</span>
          </div>
          <div class="nav-item" data-tab="tab-campaigns" data-title="Editorial Calendar &amp; Card Studio" onclick="switchTab('tab-campaigns', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-calendar"/></svg> Editorial &amp; Studio</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">PR &amp; Governance</div>
          <div class="nav-item" data-tab="tab-pr" data-title="PR &amp; Crisis Communications" onclick="switchTab('tab-pr', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-shield"/></svg> PR &amp; Crisis Console</span>
          </div>
          <div class="nav-item" data-tab="tab-review" data-title="CEO Review &amp; Approval Gate" onclick="switchTab('tab-review', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-check-circle"/></svg> CEO Review Gate</span>
            <span class="nav-pill" id="pendingReviewBadge">0</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">AI Agency Team</div>
          <div class="nav-item" data-tab="tab-room" data-title="Global Team Room &amp; Swarm" onclick="switchTab('tab-room', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-globe"/></svg> Global Team Room</span>
          </div>
          <div class="nav-item" data-tab="tab-team" data-title="Team Roster &amp; Specialist Chat" onclick="switchTab('tab-team', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-users"/></svg> Team Roster &amp; Chat</span>
            <span class="nav-pill">13</span>
          </div>
        </div>

        <div>
          <div class="nav-group-label">Infrastructure</div>
          <div class="nav-item" data-tab="tab-models" data-title="AI Models &amp; .env Studio" onclick="switchTab('tab-models', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-cpu"/></svg> AI Models &amp; .env</span>
            <span class="nav-pill" id="nimReadyPill">0/13</span>
          </div>
          <div class="nav-item" data-tab="tab-assets" data-title="Centralized Assets Library" onclick="switchTab('tab-assets', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-folder"/></svg> Assets Library</span>
            <span class="nav-pill" id="assetsCountBadge">0</span>
          </div>
          <div class="nav-item" data-tab="tab-schedule" data-title="Schedules &amp; Timers" onclick="switchTab('tab-schedule', this)">
            <span class="nav-item-left"><svg class="icon"><use href="#icon-clock"/></svg> Schedules</span>
          </div>
        </div>
      </nav>
    </div>

    <div class="rail-footer">
      <div class="status-line"><span>Gateway</span><b id="railGatewayState" style="color:var(--amber);">Standby</b></div>
      <div class="status-line"><span>ChatGPT CLI</span><b id="railChatGptState">Offline</b></div>
      <div class="status-line"><span>Crisis Level</span><b style="color:var(--mint);">L0 NOMINAL</b></div>
    </div>
  </aside>

  <!-- Main Command Workspace -->
  <div class="workspace-shell">
    <header class="topbar">
      <div class="topbar-left">
        <div>
          <div class="page-title" id="headerPageTitle">Agency Overview &amp; Presence</div>
          <div class="page-crumb">Magnm Studio</div>
        </div>
      </div>
      <div class="topbar-right">
        __NEW_STUDIO_LINK__
        <div><span class="status-dot" id="systemDot"></span> <span id="systemText">SYSTEM READY</span></div>
        <button class="btn btn-sm" onclick="switchTab('tab-models', document.querySelector('[data-tab=tab-models]'))">
          <svg class="icon"><use href="#icon-cpu"/></svg> <span id="topGatewayBadge">Configure .env</span>
        </button>
        <div id="liveClock" style="color:var(--muted);"></div>
      </div>
    </header>

    <main class="stage">
      <!-- 01. OVERVIEW & PRESENCE -->
      <div id="tab-ops" class="tab-view active">
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">Active Campaigns <svg class="icon"><use href="#icon-activity"/></svg></div>
            <div class="kpi-val" id="kpiCampaigns">0</div>
            <div class="kpi-foot">Trackable post &amp; blog bundles</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">CEO Queue <svg class="icon"><use href="#icon-check-circle"/></svg></div>
            <div class="kpi-val" style="color:var(--amber);" id="kpiPending">0</div>
            <div class="kpi-foot">Awaiting CEO sign-off</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">PR Crisis Posture <svg class="icon"><use href="#icon-shield"/></svg></div>
            <div class="kpi-val" style="color:var(--mint);">L0 NOMINAL</div>
            <div class="kpi-foot">Playbook L1-L3 armed</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Monitored Repos <svg class="icon"><use href="#icon-git"/></svg></div>
            <div class="kpi-val" id="kpiRepos">0</div>
            <div class="kpi-foot">Codebase reality Ground Truth</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">NIM Role Models <svg class="icon"><use href="#icon-cpu"/></svg></div>
            <div class="kpi-val" id="kpiNimModels">0 / 13</div>
            <div class="kpi-foot" id="kpiNimSub">Configure key in AI Studio</div>
          </div>
        </div>

        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-globe"/></svg> Managed Brand Handles &amp; Online Presence</span>
              <span class="panel-meta">config.md</span>
            </div>
            <table class="data-table">
              <thead>
                <tr><th>Platform</th><th>Handle / URL</th><th>Baseline Status</th></tr>
              </thead>
              <tbody id="channelsTableBody"></tbody>
            </table>
          </div>

          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-zap"/></svg> Operations Dispatch</span>
              <span class="panel-meta">Deterministic Runtime</span>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:10px;">
              <button class="btn btn-lime" onclick="runJob('daily_cycle')">
                <svg class="icon"><use href="#icon-zap"/></svg> Run Daily Cycle
              </button>
              <button class="btn btn-mint" onclick="runJob('market_scan')">
                <svg class="icon"><use href="#icon-search"/></svg> Market &amp; HN Scan
              </button>
              <button class="btn btn-mint" onclick="runJob('repo_sync')">
                <svg class="icon"><use href="#icon-refresh"/></svg> Sync Repositories
              </button>
            </div>
            <div>
              <label class="field-label">Execution Output Stream</label>
              <div class="terminal-window" id="terminalLog">System ready. Launch any operation above.</div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-activity"/></svg> Agency Audit &amp; Orchestration Log</span>
            <span class="panel-meta">state/activity.log</span>
          </div>
          <div class="terminal-window" id="activityLogBox" style="color:var(--sky); max-height:170px;">Loading audit stream...</div>
        </div>
      </div>

      <!-- 02. BUSINESS RESEARCH & PR RADAR -->
      <div id="tab-research" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-radar"/></svg> Market &amp; Opportunity Radar</span>
            <div style="display:flex; gap:10px; align-items:center;">
              <span class="panel-meta" id="researchSourcePath">No scan loaded</span>
              <button class="btn btn-mint btn-sm" onclick="runJob('market_scan')">
                <svg class="icon"><use href="#icon-refresh"/></svg> Scan Live Ecosystem
              </button>
            </div>
          </div>
          <p style="font-size:12px; color:var(--muted);">
            Scored using deterministic formula: Relevance (1-5) x Audience Interest (1-5) x Credibility (1-5) x Timeliness (1-5) = Max 625. Click Pitch to @cmo to initiate campaign orchestration.
          </p>
          <div style="overflow-x:auto;">
            <table class="data-table">
              <thead>
                <tr><th>Score</th><th>Opportunity / Discussion</th><th>Source &amp; Signal</th><th>Action</th></tr>
              </thead>
              <tbody id="researchTableBody"></tbody>
            </table>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-git"/></svg> Monitored Codebases</span>
            <div style="display:flex; gap:10px;">
              <button class="btn btn-sm" onclick="runJob('repo_sync')"><svg class="icon"><use href="#icon-refresh"/></svg> Sync All</button>
              <button class="btn btn-lime btn-sm" onclick="openAddRepoModal()"><svg class="icon"><use href="#icon-plus"/></svg> Add Repo</button>
            </div>
          </div>
          <div class="grid-2" id="repoContainer"></div>
          <div style="border-top:1px solid var(--border); padding-top:12px;">
            <div class="field-label">Shipped This Week (Last 7 Days)</div>
            <div id="shippedListContainer" style="display:flex; flex-direction:column; gap:6px; font-size:12px;"></div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-check-circle"/></svg> Product Facts</span>
            <button class="btn btn-sm" onclick="loadFactsPanel()"><svg class="icon"><use href="#icon-refresh"/></svg> Refresh</button>
          </div>
          <div id="pendingFactsContainer" style="display:flex; flex-direction:column; gap:10px;"></div>
          <div style="border-top:1px solid var(--border); padding-top:12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <input type="text" id="manualFactProductId" placeholder="product_id" style="width:150px;">
            <input type="text" id="manualFactText" placeholder="Add verified product fact..." style="flex:1; min-width:220px;">
            <input type="text" id="manualFactUrl" placeholder="source URL (optional)" style="width:200px;">
            <button class="btn btn-lime btn-sm" onclick="addManualFact()">Add Fact</button>
          </div>
        </div>
      </div>

      <!-- 03. EDITORIAL CALENDAR & CARD STUDIO -->
      <div id="tab-campaigns" class="tab-view">
        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-calendar"/></svg> Editorial Publishing Schedule</span>
              <span class="panel-meta">plan/plan.md</span>
            </div>
            <table class="data-table">
              <thead>
                <tr><th>Target Slot</th><th>Platforms</th><th>Topic / Package</th><th>Status</th></tr>
              </thead>
              <tbody id="editorialTableBody"></tbody>
            </table>
          </div>

          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-edit"/></svg> Palette v3 Visual Card Studio</span>
              <span class="panel-meta">templates/card.py (Zero LLM Cost)</span>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Campaign / Post Slug ID</label>
                <input type="text" id="cardPostId" value="custom-card">
              </div>
              <div>
                <label class="field-label">Palette v3 Field Color</label>
                <select id="cardField">
                  <option value="sky">Sky (#BCE3FF)</option>
                  <option value="stone">Stone (#EFEFEC)</option>
                  <option value="ink">Ink (#0A0A0A Dark)</option>
                  <option value="lime">Lime (#C8F31D)</option>
                  <option value="mint">Mint (#C7F5D9)</option>
                  <option value="butter">Butter (#FFE89E)</option>
                  <option value="coral">Coral (#FF9F80)</option>
                  <option value="lilac">Lilac (#DCCBFF)</option>
                </select>
              </div>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Top Kicker Label</label>
                <input type="text" id="cardKicker" value="">
              </div>
              <div>
                <label class="field-label">Optional Screenshot Asset</label>
                <select id="cardAssetSelect"><option value="">None (Typography Only)</option></select>
              </div>
            </div>
            <div>
              <label class="field-label">Display Headline (Up to 3 lines)</label>
              <textarea id="cardBigLines" rows="2">ASKS
FIRST.</textarea>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Italic Serif Connector</label>
                <input type="text" id="cardSerif" value="before anything changes your files.">
              </div>
              <div>
                <label class="field-label">Footer URL</label>
                <input type="text" id="cardFoot" value="magnm-org.vercel.app">
              </div>
            </div>
            <button class="btn btn-lime" onclick="renderStudioCard()">
              <svg class="icon"><use href="#icon-zap"/></svg> Render Multi-Ratio Cards &amp; Stage for Review
            </button>
            <div id="cardRenderPreview" style="display:none; flex-direction:column; gap:10px;">
              <div class="panel-meta" id="cardRenderStatus">Rendered!</div>
              <div class="review-gallery" id="cardRenderGallery"></div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-zap"/></svg> Workflows</span>
            <span class="panel-meta">Multi-step agent pipelines &amp; approval gates</span>
          </div>
          <div class="grid-2" style="gap:16px; align-items:start;">
            <div style="display:flex; flex-direction:column; gap:10px;">
              <div class="grid-2" style="gap:10px;">
                <div>
                  <label class="field-label">Workflow Kind</label>
                  <select id="wfKind">
                    <option value="post">post</option>
                    <option value="blog">blog</option>
                    <option value="repurpose">repurpose</option>
                    <option value="weekly_plan">weekly_plan</option>
                    <option value="video_script">video_script</option>
                  </select>
                </div>
                <div>
                  <label class="field-label">Product ID (optional)</label>
                  <input type="text" id="wfProductId" placeholder="product id">
                </div>
              </div>
              <div>
                <label class="field-label">Brief / Topic / Source Content ID</label>
                <textarea id="wfBriefOrTopic" rows="2" placeholder="Describe the brief or blog/video topic (or content_id for repurpose)"></textarea>
              </div>
              <div>
                <label class="field-label">Platforms</label>
                <div style="display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--ink);">
                  <label><input type="checkbox" class="wf-platform-cb" value="linkedin" checked> linkedin</label>
                  <label><input type="checkbox" class="wf-platform-cb" value="x" checked> x</label>
                  <label><input type="checkbox" class="wf-platform-cb" value="instagram"> instagram</label>
                  <label><input type="checkbox" class="wf-platform-cb" value="youtube"> youtube</label>
                </div>
              </div>
              <div>
                <label style="font-size:12px; color:var(--ink); display:flex; align-items:center; gap:6px;">
                  <input type="checkbox" id="wfWithImage"> With Image (cards / illustration)
                </label>
              </div>
              <button class="btn btn-lime" onclick="startWorkflowFromForm()">
                <svg class="icon"><use href="#icon-zap"/></svg> Start Workflow
              </button>
            </div>
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span class="field-label" style="margin:0;">Recent Workflow Runs</span>
                <button class="btn btn-sm" onclick="loadWorkflows()">Refresh</button>
              </div>
              <div id="workflowsList" style="display:flex; flex-direction:column; gap:10px; max-height:420px; overflow-y:auto;"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 04. PR & CRISIS COMMUNICATIONS -->
      <div id="tab-pr" class="tab-view">
        <div class="grid-asymmetric">
          <div class="panel">
            <div class="panel-header">
              <span class="panel-title"><svg class="icon"><use href="#icon-shield"/></svg> PR, Backlash &amp; Freelance Client Response Drafter</span>
              <span class="panel-meta">Agent: @community + @reviewer guardrail</span>
            </div>
            <div class="grid-2" style="gap:12px;">
              <div>
                <label class="field-label">Communication Scenario</label>
                <select id="prScenario">
                  <option value="freelance_inquiry">Freelance Client / Project Inquiry</option>
                  <option value="misunderstanding">Technical Misunderstanding (Clarify w/ Evidence)</option>
                  <option value="valid_bug">Valid Bug Report (Plainly Own &amp; Fix Timeline)</option>
                  <option value="fair_criticism">Fair Criticism (Acknowledge &amp; Action)</option>
                  <option value="unfair">Unfair / Bad-Faith Comment (One Calm Correction)</option>
                </select>
              </div>
              <div>
                <label class="field-label">Incident Severity Tier</label>
                <select id="prSeverity">
                  <option value="L1">L1 Minor (1-2 skeptical comments / standard question)</option>
                  <option value="L2">L2 Notable (Critical thread gaining traction - CEO approves)</option>
                  <option value="L3">L3 Serious (Security/Privacy/Legal - Pause promos, CEO decides)</option>
                </select>
              </div>
            </div>
            <div>
              <label class="field-label">Incoming Comment, Thread, Press Question or Client Brief</label>
              <textarea id="prContextInput" rows="4" placeholder="Paste the Hacker News comment, Reddit feedback, press query, or client requirement here..."></textarea>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="panel-meta">Hard gate: Never posts autonomously. All PR replies require CEO send.</span>
              <button class="btn btn-lime" id="btnDraftPr" onclick="draftPrReply()">
                <svg class="icon"><use href="#icon-send"/></svg> Generate Grounded Draft
              </button>
            </div>
            <div id="prDraftOutputBox" style="display:none; flex-direction:column; gap:8px;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="badge badge-info" id="prDraftModelTag">Model</span>
                <button class="btn btn-sm" onclick="copyPrDraft()"><svg class="icon"><use href="#icon-copy"/></svg> Copy Draft</button>
              </div>
              <div class="review-kit-box" id="prDraftText"></div>
            </div>
          </div>

          <div style="display:flex; flex-direction:column; gap:20px;">
            <div class="panel">
              <div class="panel-header">
                <span class="panel-title"><svg class="icon"><use href="#icon-alert"/></svg> Crisis Protocol (L1 - L3)</span>
                <span class="panel-meta">playbooks/crisis.md</span>
              </div>
              <div class="review-kit-box" id="crisisPlaybookBox" style="max-height:240px;"></div>
            </div>
            <div class="panel">
              <div class="panel-header">
                <span class="panel-title"><svg class="icon"><use href="#icon-check-circle"/></svg> Verified Facts Guardrail</span>
                <span class="panel-meta">research/product-facts.md</span>
              </div>
              <div class="review-kit-box" id="productFactsBox" style="max-height:240px;"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 05. CEO REVIEW & APPROVAL GATE -->
      <div id="tab-review" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-check-circle"/></svg> CEO Content Review &amp; Publishing Approval Gate</span>
            <span class="panel-meta">Explicit CEO sign-off required before publication</span>
          </div>
          <div id="ceoReviewList" style="display:flex; flex-direction:column; gap:20px;"></div>
        </div>
      </div>

      <!-- 06. GLOBAL TEAM ROOM -->
      <div id="tab-room" class="tab-view">
        <div class="panel" style="height: 700px; display:flex; flex-direction:column;">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-globe"/></svg> Global Team Room &amp; CMO Swarm Board</span>
            <span class="panel-meta">Tag any of 13 roles with @handle · Specialist tasks auto-escalate to @cmo</span>
          </div>
          <div id="globalMessagesBox" style="flex:1; overflow-y:auto; padding:10px; display:flex; flex-direction:column; gap:14px;"></div>
          <div style="border-top:1px solid var(--border); padding-top:12px; display:flex; flex-direction:column; gap:10px;">
            <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
              <span class="panel-meta" style="margin-right:4px;">Quick @mention:</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@cmo ')">@cmo</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@strategist ')">@strategist</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@advisor ')">@advisor</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@researcher ')">@researcher</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@blog-writer ')">@blog-writer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@writer ')">@writer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@designer ')">@designer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@video-producer ')">@video-producer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@reviewer ')">@reviewer</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@analyst ')">@analyst</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@listener ')">@listener</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@community ')">@community</span>
              <span class="code-tag" style="cursor:pointer;" onclick="insertTag('@growth ')">@growth</span>
            </div>
            <div style="display:flex; gap:10px; align-items:center;">
              <select id="roomAssetSelect" style="width:220px; padding:8px;">
                <option value="">Attach Asset (None)</option>
              </select>
              <input type="text" id="globalMsgInput" placeholder="Message the agency room or assign a task (e.g. '@cmo plan this month's release announcement')..." onkeydown="if(event.key==='Enter') sendGlobalMessage()">
              <button class="btn btn-lime" onclick="sendGlobalMessage()">
                <svg class="icon"><use href="#icon-send"/></svg> Send
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 07. TEAM ROSTER & SPECIALIST CHAT -->
      <div id="tab-team" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-users"/></svg> 13-Specialist Agency Roster</span>
            <span class="panel-meta">Strict Capability Contracts · Non-CMO Assignments Auto-Escalate to @cmo</span>
          </div>
          <div class="grid-3" id="rosterContainer"></div>
        </div>
      </div>

      <!-- 08. AI MODELS & .ENV STUDIO -->
      <div id="tab-models" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-cpu"/></svg> NVIDIA NIM &amp; Multi-Provider Gateway Configuration (.env)</span>
            <span class="panel-meta" id="envStudioMeta">Reading .env...</span>
          </div>
          <div class="grid-3" style="gap:14px;">
            <div>
              <label class="field-label">Default Inference Provider</label>
              <select id="envDefaultProvider">
                <option value="nvidia_nim">nvidia_nim (Primary Recommended)</option>
                <option value="auto">auto</option>
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
                <option value="gemini">gemini</option>
                <option value="ollama">ollama</option>
              </select>
            </div>
            <div>
              <label class="field-label">NVIDIA NIM Base URL</label>
              <input type="text" id="envNimBaseUrl" value="https://integrate.api.nvidia.com/v1">
            </div>
            <div>
              <label class="field-label">Availability Fallback Model</label>
              <input type="text" id="envNimFallbackModel" value="meta/llama-3.3-70b-instruct">
            </div>
          </div>

          <div class="grid-2" style="gap:14px;">
            <div>
              <label class="field-label">NVIDIA NIM API Key <span id="nimGlobalMaskBadge" class="code-tag" style="margin-left:6px;">Unset</span></label>
              <div style="display:flex; gap:8px;">
                <select id="envNimKeyName" style="width:215px; flex-shrink:0;">
                  <option value="NVIDIA_NIM_API_KEY">NVIDIA_NIM_API_KEY</option>
                  <option value="NVIDIA_NIM_API_KEY1">NVIDIA_NIM_API_KEY1</option>
                  <option value="NVIDIA_NIM_API_KEY2">NVIDIA_NIM_API_KEY2</option>
                  <option value="NVIDIA_NIM_API_KEY3">NVIDIA_NIM_API_KEY3</option>
                  <option value="NVIDIA_NIM_API_KEY4">NVIDIA_NIM_API_KEY4</option>
                  <option value="NVIDIA_NIM_API_KEY5">NVIDIA_NIM_API_KEY5</option>
                  <option value="NVIDIA_NIM_API_KEY6">NVIDIA_NIM_API_KEY6</option>
                  <option value="NVIDIA_NIM_API_KEY7">NVIDIA_NIM_API_KEY7</option>
                  <option value="NVIDIA_NIM_API_KEY8">NVIDIA_NIM_API_KEY8</option>
                  <option value="NVIDIA_NIM_API_KEY9">NVIDIA_NIM_API_KEY9</option>
                  <option value="NVIDIA_NIM_API_KEY10">NVIDIA_NIM_API_KEY10</option>
                </select>
                <input type="password" id="envNimGlobalKey" placeholder="nvapi-... (leave blank to keep existing)" style="flex:1;">
              </div>
            </div>
            <div class="grid-2" style="gap:10px;">
              <div>
                <label class="field-label">Optional OpenAI Key</label>
                <input type="password" id="envOpenAiKey" placeholder="sk-... (optional)">
              </div>
              <div>
                <label class="field-label">Optional Gemini Key</label>
                <input type="password" id="envGeminiKey" placeholder="AIza... (optional)">
              </div>
            </div>
          </div>

          <div style="display:flex; justify-content:space-between; align-items:center; gap:12px;">
            <span class="panel-meta" id="envSaveStatus">API keys are stored securely in local .env (chmod 600, git-ignored) and never exposed in client logs.</span>
            <button class="btn btn-lime" onclick="saveFullEnvConfig()">
              <svg class="icon"><use href="#icon-check"/></svg> Save Gateway &amp; Per-Role Models to .env
            </button>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">NVIDIA keys</span>
            <div style="display:flex; align-items:center; gap:10px;">
              <span class="panel-meta" id="keyPoolSummary">Loading pool...</span>
              <button class="btn btn-sm" onclick="loadKeyPool()">
                <svg class="icon"><use href="#icon-refresh"/></svg> Refresh
              </button>
            </div>
          </div>
          <div id="keyPoolList" style="display:flex; flex-direction:column; gap:6px;">
            <div class="panel-meta">Open AI Models &amp; .env to inspect the NVIDIA key pool.</div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-check"/></svg> Setup Check</span>
            <div style="display:flex; align-items:center; gap:10px;">
              <span class="panel-meta" id="setupCheckSummary">Checking environment...</span>
              <button class="btn btn-sm" onclick="loadSetupCheck()">
                <svg class="icon"><use href="#icon-refresh"/></svg> Re-check
              </button>
            </div>
          </div>
          <div id="setupCheckList" style="display:flex; flex-direction:column; gap:8px;">
            <div class="panel-meta">Open AI Models &amp; .env to run setup diagnostics.</div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Import from an existing Magnm-Marketing folder</span>
          </div>
          <div style="display:flex; gap:10px; align-items:center;">
            <input type="text" id="legacyImportPath" placeholder="/path/to/Magnm-Marketing" style="flex:1;">
            <button class="btn" id="legacyImportBtn" onclick="importLegacyFolder()">Import</button>
          </div>
          <div class="panel-meta" id="legacyImportStatus">Copies outputs, config, agents, and SQLite history without overwriting existing files.</div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-cpu"/></svg> Per-Role NVIDIA NIM Model Matrix &amp; Live Endpoint Tester</span>
            <span class="panel-meta">Assign dedicated models &amp; optional per-role keys for all 13 specialists</span>
          </div>
          <div style="overflow-x:auto;">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Assigned NVIDIA NIM Model ID</th>
                  <th>Optional Role Key Override</th>
                  <th>Key Status</th>
                  <th>Endpoint Diagnostic</th>
                </tr>
              </thead>
              <tbody id="modelsMatrixBody"></tbody>
            </table>
          </div>
          <datalist id="nimCatalogList"></datalist>
        </div>
      </div>

      <!-- 09. ASSETS LIBRARY -->
      <div id="tab-assets" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-folder"/></svg> Centralized Agency Assets Library</span>
            <button class="btn btn-lime btn-sm" onclick="openUploadModal()">
              <svg class="icon"><use href="#icon-plus"/></svg> Upload Asset
            </button>
          </div>
          <p style="font-size:12px; color:var(--muted);">
            Upload real product screenshots, screen recordings, brand logos, or press brief documents. Attach them directly in Global Team Room, 1-on-1 Specialist Chats, or Card Studio.
          </p>
          <div class="grid-4" id="assetsGrid"></div>
        </div>
      </div>

      <!-- 10. SCHEDULES -->
      <div id="tab-schedule" class="tab-view">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title"><svg class="icon"><use href="#icon-clock"/></svg> Automated Operations &amp; Systemd Timers</span>
            <span class="panel-meta">state/schedules.json</span>
          </div>
          <div id="scheduleConfigContainer" style="display:flex; flex-direction:column; gap:14px;"></div>
        </div>
      </div>
    </main>
  </div>

  <!-- Slide-Over 1-on-1 Specialist Chat Drawer -->
  <div class="chat-modal" id="chatModal">
    <div class="chat-header">
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="code-tag" id="chatAgentCode">AGT</span>
        <div>
          <b id="chatAgentName" style="font-size:14px;">Agent</b>
          <div id="chatAgentRole" style="font-family:var(--font-mono); font-size:10px; color:var(--lime);">Role</div>
          <div id="chatAgentModel" style="font-family:var(--font-mono); font-size:10px; color:var(--muted);">Model</div>
        </div>
      </div>
      <button class="btn btn-sm" onclick="closeChat()"><svg class="icon"><use href="#icon-x"/></svg></button>
    </div>
    <div class="chat-messages" id="chatMessages"></div>
    <div class="chat-input-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <svg class="icon"><use href="#icon-paperclip"/></svg>
        <span class="panel-meta">Attach Asset:</span>
        <select id="chatAssetSelect" style="padding:5px 8px; font-size:11px;">
          <option value="">None</option>
        </select>
      </div>
      <div style="display:flex; gap:8px;">
        <input type="text" id="chatInput" placeholder="Assign a task or ask a role-specific question..." onkeydown="if(event.key==='Enter') sendAgentMessage()">
        <button class="btn btn-lime" onclick="sendAgentMessage()">
          <svg class="icon"><use href="#icon-send"/></svg> Send
        </button>
      </div>
    </div>
  </div>

  <!-- Upload Asset Modal -->
  <div id="uploadAssetModal" class="modal-backdrop">
    <div class="modal-card">
      <h3 style="font-size:15px;">Upload Asset to Agency Library</h3>
      <div>
        <label class="field-label">Select File (Image, Video, Document)</label>
        <input type="file" id="assetFileInput">
      </div>
      <div>
        <label class="field-label">Category</label>
        <select id="assetCategory">
          <option value="product">Product Media</option>
          <option value="brand">Brand Logo &amp; Identity</option>
          <option value="screenshot">Product UI Screenshot</option>
          <option value="recording">Terminal / Screen Recording</option>
          <option value="document">PR &amp; Technical Document</option>
        </select>
      </div>
      <div>
        <label class="field-label">Description &amp; Usage Notes</label>
        <textarea id="assetDesc" rows="2" placeholder="e.g. Settings screen of the latest release"></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:10px;">
        <button class="btn" onclick="closeUploadModal()">Cancel</button>
        <button class="btn btn-lime" onclick="submitUploadAsset()"><svg class="icon"><use href="#icon-plus"/></svg> Upload Asset</button>
      </div>
    </div>
  </div>

  <!-- Add Repo Modal -->
  <div id="addRepoModal" class="modal-backdrop">
    <div class="modal-card">
      <h3 style="font-size:15px;">Add Repository</h3>
      <div class="grid-2" style="gap:10px;">
        <div>
          <label class="field-label">Kind</label>
          <select id="newRepoKind" onchange="toggleAddRepoKind()">
            <option value="local">Local Git Clone</option>
            <option value="github">GitHub (owner/repo)</option>
          </select>
        </div>
        <div>
          <label class="field-label">Project Name</label>
          <input type="text" id="newRepoName" placeholder="e.g. My project">
        </div>
      </div>
      <div id="newRepoLocalGroup">
        <label class="field-label">Local Folder Path</label>
        <input type="text" id="newRepoLocalPath" placeholder="/home/user/projects/repo">
      </div>
      <div id="newRepoGithubGroup" style="display:none;">
        <label class="field-label">GitHub Repo (owner/repo or URL)</label>
        <input type="text" id="newRepoUrl" placeholder="owner/repo or https://github.com/owner/repo">
      </div>
      <div class="grid-2" style="gap:10px;">
        <div>
          <label class="field-label">Product ID</label>
          <input type="text" id="newRepoProductId" placeholder="optional (defaults from name)">
        </div>
        <div>
          <label class="field-label">Lifecycle Status</label>
          <select id="newRepoStatus">
            <option value="in_development">In Development</option>
            <option value="released">Released</option>
          </select>
        </div>
      </div>
      <label style="display:flex; align-items:center; gap:8px; font-size:12px;">
        <input type="checkbox" id="newRepoConfidential" style="width:auto;"> Confidential (local Ollama only, never cloud)
      </label>
      <div>
        <label class="field-label">Marketing Angle &amp; Rules</label>
        <textarea id="newRepoAngle" rows="2" placeholder="Optional positioning notes"></textarea>
      </div>
      <div class="panel-meta" id="addRepoError" style="color:var(--danger);"></div>
      <div style="display:flex; justify-content:flex-end; gap:10px;">
        <button class="btn" onclick="closeAddRepoModal()">Cancel</button>
        <button class="btn btn-lime" onclick="submitAddRepo()"><svg class="icon"><use href="#icon-check"/></svg> Add Repo</button>
      </div>
    </div>
  </div>

  <script>
    let activeAgentId = null;
    let cachedAssets = [];
    let cachedPackages = {};
    let cachedRoster = [];
    let latestState = null;

    function escapeHtml(str) {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function updateClock() {
      const now = new Date();
      document.getElementById('liveClock').innerText = now.toLocaleTimeString() + ' Local';
    }
    setInterval(updateClock, 1000);
    updateClock();

    function switchTab(tabId, navEl) {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
      if (navEl) navEl.classList.add('active');
      else {
        const match = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
        if (match) match.classList.add('active');
      }
      const view = document.getElementById(tabId);
      if (view) view.classList.add('active');

      const title = navEl ? navEl.getAttribute('data-title') : (document.querySelector(`.nav-item[data-tab="${tabId}"]`)?.getAttribute('data-title') || 'MAGNM Command Center');
      document.getElementById('headerPageTitle').innerText = title;

      if (tabId === 'tab-assets' || tabId === 'tab-campaigns' || tabId === 'tab-room') loadAssets();
      if (tabId === 'tab-research') { loadReposAndShipped(); loadFactsPanel(); }
      if (tabId === 'tab-campaigns') loadWorkflows();
      if (tabId === 'tab-room') loadGlobalMessages();
      if (tabId === 'tab-team') loadRoster();
      if (tabId === 'tab-models') {
        if (latestState) renderModelsStudio(latestState, true);
        loadKeyPool();
        loadSetupCheck();
      }
    }

    function copyPostingKit(pkgId) {
      const p = cachedPackages[pkgId];
      if (p && p.posting_kit_content) {
        navigator.clipboard.writeText(p.posting_kit_content);
        alert('Posting Kit copied to clipboard.');
      }
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        latestState = data;
        renderDashboard(data);
      } catch(e) {}
    }

    function renderDashboard(data) {
      const dot = document.getElementById('systemDot');
      const text = document.getElementById('systemText');
      const term = document.getElementById('terminalLog');

      if (data.current_job && data.current_job.running) {
        dot.className = 'status-dot busy';
        const pct = (typeof data.current_job.progress === 'number') ? ` (${data.current_job.progress}%)` : '';
        text.innerText = 'EXECUTING ' + data.current_job.job_name + pct;
        term.innerText = data.current_job.output || data.current_job.message || 'Executing job...';
      } else {
        dot.className = 'status-dot';
        text.innerText = 'SYSTEM READY';
        if (data.current_job && data.current_job.output) term.innerText = data.current_job.output;
      }

      // Env & Gateway status telemetry
      const env = data.env_status || {};
      const readyCount = env.nvidia_nim_roles_configured || 0;
      const totalRoles = (data.models_matrix || []).length || 13;
      document.getElementById('nimReadyPill').innerText = `${readyCount}/${totalRoles}`;
      document.getElementById('kpiNimModels').innerText = `${readyCount} / ${totalRoles}`;

      const railGw = document.getElementById('railGatewayState');
      const topGwBtn = document.getElementById('topGatewayBadge');
      if (readyCount > 0 || env.openai || env.anthropic || env.gemini) {
        railGw.innerText = env.nvidia_nim_global ? 'NVIDIA NIM Active' : 'Keys Active';
        railGw.style.color = 'var(--mint)';
        topGwBtn.innerText = `NIM Studio (${readyCount}/${totalRoles} Ready)`;
        document.getElementById('kpiNimSub').innerText = `Provider: ${env.default_provider || 'nvidia_nim'}`;
      } else {
        railGw.innerText = 'No Key Set';
        railGw.style.color = 'var(--amber)';
        topGwBtn.innerText = 'Set NIM Key (.env)';
        document.getElementById('kpiNimSub').innerText = 'Paste NVIDIA_NIM_API_KEY in AI Studio';
      }
      document.getElementById('railChatGptState').innerText = env.chatgpt_cli ? 'Connected (CDP)' : 'Disconnected';
      document.getElementById('railChatGptState').style.color = env.chatgpt_cli ? 'var(--sky)' : 'var(--muted)';

      // Channels
      const chBody = document.getElementById('channelsTableBody');
      chBody.innerHTML = '';
      (data.channels || []).forEach(c => {
        chBody.innerHTML += `
          <tr>
            <td><b>${escapeHtml(c.platform)}</b></td>
            <td><a href="${escapeHtml(c.handle_url)}" target="_blank" style="color:var(--sky); text-decoration:none;">${escapeHtml(c.handle_url)}</a></td>
            <td><span class="badge badge-draft">${escapeHtml(c.status)}</span></td>
          </tr>
        `;
      });

      // Audit Log
      document.getElementById('activityLogBox').innerText = (data.activity_log || []).join('\n') || 'No activity logged yet.';

      // Research Radar
      const findings = (data.research_findings && data.research_findings.items) || [];
      document.getElementById('radarCountBadge').innerText = findings.length;
      document.getElementById('researchSourcePath').innerText = (data.research_findings && data.research_findings.file) || 'Run Market Scan';
      const rBody = document.getElementById('researchTableBody');
      rBody.innerHTML = '';
      findings.slice(0, 12).forEach(it => {
        const sc = (it.scoring && it.scoring.score) || 0;
        const badgeCls = sc >= 200 ? 'badge-approved' : (sc >= 100 ? 'badge-info' : 'badge-draft');
        const safeTitle = escapeHtml(it.title);
        rBody.innerHTML += `
          <tr>
            <td><span class="badge ${badgeCls}">${sc} / 625</span></td>
            <td>
              <div style="font-weight:600; margin-bottom:4px;">${safeTitle}</div>
              <a href="${escapeHtml(it.url)}" target="_blank" style="color:var(--sky); font-size:11px; text-decoration:none;">Open Source Link</a>
            </td>
            <td>
              <div>${escapeHtml(it.source)}</div>
              <div class="panel-meta">${escapeHtml(it.engagement)}</div>
            </td>
            <td>
              <button class="btn btn-lime btn-sm" onclick="pitchOpportunityToCmo('${encodeURIComponent(it.title)}', '${encodeURIComponent(it.url)}', ${sc})">
                <svg class="icon"><use href="#icon-send"/></svg> Pitch to @cmo
              </button>
            </td>
          </tr>
        `;
      });

      // Repos count KPI (detailed cards rendered by loadReposAndShipped)
      const reposList = (data.repos && data.repos.repositories) || [];
      document.getElementById('kpiRepos').innerText = reposList.length;
      loadReposAndShipped();

      // Editorial Plan
      const edBody = document.getElementById('editorialTableBody');
      edBody.innerHTML = '';
      (data.editorial_plan || []).forEach(row => {
        const stCls = row.status.includes('CANCEL') ? 'badge-danger' : (row.status.includes('AWAIT') ? 'badge-warn' : 'badge-draft');
        edBody.innerHTML += `
          <tr>
            <td><b>${escapeHtml(row.slot)}</b></td>
            <td>${escapeHtml(row.platforms)}</td>
            <td>${escapeHtml(row.topic)}</td>
            <td><span class="badge ${stCls}">${escapeHtml(row.status)}</span></td>
          </tr>
        `;
      });

      // PR Reference docs
      document.getElementById('crisisPlaybookBox').innerText = data.crisis_playbook || 'Playbook missing';
      document.getElementById('productFactsBox').innerText = data.product_facts || 'Product facts missing';

      // CEO Reviews
      const reviewDiv = document.getElementById('ceoReviewList');
      reviewDiv.innerHTML = '';
      let pendingCount = 0;
      const pkgs = data.packages || [];
      document.getElementById('kpiCampaigns').innerText = pkgs.length;

      pkgs.forEach(p => {
        cachedPackages[p.id] = p;
        const m = p.manifest || {};
        if (m.status !== 'published') pendingCount++;
        let badgeStyle = 'badge-draft';
        if (m.status === 'approved') badgeStyle = 'badge-approved';
        if (m.status === 'revision_required') badgeStyle = 'badge-revision';
        if (m.status === 'published') badgeStyle = 'badge-published';

        let mediaHtml = '';
        if (p.images && p.images.length > 0) {
          mediaHtml += p.images.map(img =>
            `<img src="/api/asset/${encodeURIComponent(p.id)}/${encodeURIComponent(img)}" class="review-img" title="${escapeHtml(img)}">`
          ).join('');
        }
        if (p.videos && p.videos.length > 0) {
          mediaHtml += p.videos.slice(0, 2).map(vid =>
            `<video src="/api/asset/${encodeURIComponent(p.id)}/${encodeURIComponent(vid)}" class="review-video" controls preload="metadata" title="${escapeHtml(vid)}"></video>`
          ).join('');
        }

        reviewDiv.innerHTML += `
          <div class="review-card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
              <div>
                <b style="font-size:15px;">Campaign Package: ${escapeHtml(p.id)}</b>
                <span class="badge ${badgeStyle}" style="margin-left:10px;">${escapeHtml(m.status)}</span>
              </div>
              <div style="display:flex; gap:8px;">
                ${m.status !== 'approved' && m.status !== 'published' ? `
                  <button class="btn btn-lime" onclick="approvePackage('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-check"/></svg> Approve Package
                  </button>
                  <button class="btn btn-warning" onclick="requestRevision('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-edit"/></svg> Request Revision
                  </button>
                ` : m.status === 'approved' ? `
                  <button class="btn btn-mint" onclick="publishPackage('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-send"/></svg> Mark Published
                  </button>
                ` : `<span class="badge badge-published">Published by CEO</span>`}
              </div>
            </div>
            ${mediaHtml ? `<div><div class="panel-meta" style="margin-bottom:8px;">CREATIVE ASSETS &amp; RENDERED REELS</div><div class="review-gallery">${mediaHtml}</div></div>` : ''}
            ${p.posting_kit_content ? `
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <span class="panel-meta">POSTING KIT &amp; APPROVED COPY</span>
                  <button class="btn btn-sm" onclick="copyPostingKit('${escapeHtml(p.id)}')">
                    <svg class="icon"><use href="#icon-copy"/></svg> Copy Kit
                  </button>
                </div>
                <div class="review-kit-box">${escapeHtml(p.posting_kit_content)}</div>
              </div>
            ` : ''}
          </div>
        `;
      });

      document.getElementById('pendingReviewBadge').innerText = pendingCount;
      document.getElementById('kpiPending').innerText = pendingCount;

      // Schedules
      const schedDiv = document.getElementById('scheduleConfigContainer');
      schedDiv.innerHTML = '';
      (data.schedules || []).forEach(s => {
        schedDiv.innerHTML += `
          <div style="display:flex; justify-content:space-between; align-items:center; background:#101319; padding:16px 20px; border-radius:6px; border:1px solid var(--border);">
            <div>
              <b style="font-size:14px;">${escapeHtml(s.name)}</b>
              <div class="panel-meta" style="margin-top:3px;">${escapeHtml(s.description)}</div>
              <div class="panel-meta" style="margin-top:5px;">
                Cadence: ${s.interval_hours ? 'Every ' + s.interval_hours + 'h' : escapeHtml(s.cron)} | Last run: ${escapeHtml(s.last_run || 'Never')}
              </div>
            </div>
            <button class="btn" onclick="toggleSchedule('${escapeHtml(s.id)}')">
              ${s.enabled ? '<span class="badge badge-ok">Active</span>' : '<span class="badge badge-draft">Paused</span>'}
            </button>
          </div>
        `;
      });

      // Populate AI Models studio if not currently typing in it
      if (document.getElementById('tab-models').classList.contains('active')) {
        // Only populate on explicit switch or initial load so user edits aren't overwritten during 3s poll
      } else {
        renderModelsStudio(data, false);
      }
    }

    function renderModelsStudio(data, forceInputs) {
      const env = data.env_status || {};
      document.getElementById('envStudioMeta').innerText = env.has_env_file ? '.env Loaded (chmod 600)' : '.env Missing';
      const maskBadge = document.getElementById('nimGlobalMaskBadge');
      maskBadge.innerText = env.nvidia_nim_global ? env.nvidia_nim_global_masked : 'Not Set';
      maskBadge.style.color = env.nvidia_nim_global ? 'var(--mint)' : 'var(--amber)';

      if (forceInputs || !document.getElementById('envNimBaseUrl').dataset.dirty) {
        document.getElementById('envDefaultProvider').value = env.default_provider || 'nvidia_nim';
        if (env.nvidia_nim_base_url) document.getElementById('envNimBaseUrl').value = env.nvidia_nim_base_url;
        if (env.nvidia_nim_fallback_model) document.getElementById('envNimFallbackModel').value = env.nvidia_nim_fallback_model;
      }

      const dList = document.getElementById('nimCatalogList');
      dList.innerHTML = (data.nim_catalog_suggestions || []).map(m => `<option value="${escapeHtml(m)}">`).join('');

      const tbody = document.getElementById('modelsMatrixBody');
      if (!forceInputs && tbody.children.length > 0) return;

      tbody.innerHTML = '';
      (data.models_matrix || []).forEach(r => {
        const keyBadge = r.has_key
          ? `<span class="badge badge-ok">${escapeHtml(r.key_source)} (${escapeHtml(r.masked_key)})</span>`
          : `<span class="badge badge-warn">Missing Key</span>`;

        tbody.innerHTML += `
          <tr>
            <td>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="code-tag">${escapeHtml(r.code)}</span>
                <div>
                  <b>${escapeHtml(r.name)}</b>
                  <div class="panel-meta">${escapeHtml(r.specialization)}</div>
                </div>
              </div>
            </td>
            <td>
              <input type="text" list="nimCatalogList" class="role-model-input" data-env="${escapeHtml(r.model_env)}" value="${escapeHtml(r.model)}">
            </td>
            <td>
              <input type="password" class="role-key-input" data-env="${escapeHtml(r.key_env)}" placeholder="Inherit global key">
            </td>
            <td>${keyBadge}</td>
            <td>
              <button class="btn btn-sm" onclick="testRoleModel('${escapeHtml(r.role_id)}', this)">
                <svg class="icon"><use href="#icon-activity"/></svg> Test Live
              </button>
              <div class="panel-meta" id="testRes-${escapeHtml(r.role_id)}" style="margin-top:4px; max-width:220px;"></div>
            </td>
          </tr>
        `;
      });
    }

    function _isSecretEnvName(name) {
      const upper = String(name || '').toUpperCase();
      return upper.includes('KEY') || upper.includes('TOKEN') || upper.includes('SECRET');
    }

    async function loadKeyPool() {
      const listEl = document.getElementById('keyPoolList');
      const sumEl = document.getElementById('keyPoolSummary');
      if (!listEl) return;
      try {
        const res = await fetch('/api/key_pool');
        const keys = await res.json();
        if (!Array.isArray(keys) || keys.length === 0) {
          if (sumEl) sumEl.innerText = 'No keys configured';
          listEl.innerHTML = '<div class="panel-meta">No NVIDIA keys found. Add NVIDIA_NIM_API_KEY1..10 above.</div>';
          return;
        }
        const healthyCount = keys.filter(k => k.healthy).length;
        if (sumEl) sumEl.innerText = `${healthyCount} / ${keys.length} healthy`;
        listEl.innerHTML = keys.map(k => {
          let stateLabel = 'healthy';
          let badgeCls = 'badge-ok';
          if (k.disabled_reason) {
            stateLabel = 'rejected';
            badgeCls = 'badge-danger';
          } else if (k.cooling_down_s > 0) {
            stateLabel = `cooling (${k.cooling_down_s}s)`;
            badgeCls = 'badge-warn';
          } else if (!k.healthy) {
            stateLabel = 'cooling';
            badgeCls = 'badge-warn';
          }
          return `
            <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; padding:6px 10px; background:#101319; border:1px solid var(--border); border-radius:6px;">
              <div style="display:flex; align-items:center; gap:10px; min-width:0;">
                <span class="code-tag">${escapeHtml(k.label)}</span>
                <span class="panel-meta">${escapeHtml(k.masked)}</span>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="panel-meta">${Number(k.requests_last_min || 0)} req/min</span>
                <span class="badge ${badgeCls}">${escapeHtml(stateLabel)}</span>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        listEl.innerHTML = '<div class="panel-meta" style="color:var(--danger);">Failed to load NVIDIA key pool.</div>';
      }
    }

    async function loadSetupCheck() {
      const listEl = document.getElementById('setupCheckList');
      const sumEl = document.getElementById('setupCheckSummary');
      if (!listEl) return;
      try {
        const res = await fetch('/api/setup_check');
        const data = await res.json();
        const checks = data.checks || [];
        if (sumEl) {
          sumEl.innerText = data.ok ? 'All required checks OK' : 'Action needed';
          sumEl.style.color = data.ok ? 'var(--mint)' : 'var(--amber)';
        }
        listEl.innerHTML = checks.map(c => {
          const color = c.status === 'ok' ? 'var(--mint)' : (c.status === 'warn' ? 'var(--amber)' : 'var(--danger)');
          const badgeCls = c.status === 'ok' ? 'badge-ok' : (c.status === 'warn' ? 'badge-warn' : 'badge-danger');
          const fixHtml = c.fix ? `<div class="panel-meta" style="color:var(--sky); margin-top:2px;">Fix: ${escapeHtml(c.fix)}</div>` : '';
          return `
            <div style="display:flex; align-items:flex-start; gap:10px; padding:8px 10px; background:#101319; border:1px solid var(--border); border-radius:6px;">
              <span style="width:9px; height:9px; border-radius:50%; background:${color}; margin-top:5px; flex-shrink:0;"></span>
              <div style="flex:1; min-width:0;">
                <div style="display:flex; justify-content:space-between; gap:8px;">
                  <b style="font-size:13px;">${escapeHtml(c.label)}</b>
                  <span class="badge ${badgeCls}">${escapeHtml(c.status)}</span>
                </div>
                <div class="panel-meta" style="margin-top:2px;">${escapeHtml(c.detail || '')}</div>
                ${fixHtml}
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        listEl.innerHTML = '<div class="panel-meta" style="color:var(--danger);">Failed to run setup check.</div>';
      }
    }

    async function importLegacyFolder() {
      const inp = document.getElementById('legacyImportPath');
      const statusEl = document.getElementById('legacyImportStatus');
      const btn = document.getElementById('legacyImportBtn');
      const folderPath = (inp && inp.value || '').trim();
      if (!folderPath) {
        if (statusEl) {
          statusEl.innerText = 'Enter a folder path first.';
          statusEl.style.color = 'var(--amber)';
        }
        return;
      }
      if (btn) btn.disabled = true;
      if (statusEl) {
        statusEl.innerText = 'Importing...';
        statusEl.style.color = 'var(--muted)';
      }
      try {
        const res = await fetch('/api/import_legacy', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ path: folderPath })
        });
        const data = await res.json();
        if (!data.success) {
          statusEl.innerText = data.error || 'Import failed.';
          statusEl.style.color = 'var(--danger)';
          return;
        }
        const db = data.db || {};
        statusEl.innerText = `Imported ${data.files_copied || 0} files, ${db.messages || 0} messages, ${db.chat_turns || 0} chat turns, ${db.assets || 0} assets, ${db.schedules || 0} schedules, ${db.activity || 0} activity lines.`;
        statusEl.style.color = 'var(--mint)';
        fetchStatus();
      } catch (e) {
        if (statusEl) {
          statusEl.innerText = 'Import request failed: ' + e;
          statusEl.style.color = 'var(--danger)';
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function saveFullEnvConfig() {
      const updates = {
        AI_DEFAULT_PROVIDER: document.getElementById('envDefaultProvider').value,
        NVIDIA_NIM_BASE_URL: document.getElementById('envNimBaseUrl').value.trim(),
        NIM_FALLBACK_MODEL: document.getElementById('envNimFallbackModel').value.trim()
      };
      const gk = document.getElementById('envNimGlobalKey').value.trim();
      const nimKeyName = (document.getElementById('envNimKeyName')?.value || 'NVIDIA_NIM_API_KEY').trim();
      if (gk) updates[nimKeyName] = gk;
      const ok = document.getElementById('envOpenAiKey').value.trim();
      if (ok) updates['OPENAI_API_KEY'] = ok;
      const gmk = document.getElementById('envGeminiKey').value.trim();
      if (gmk) updates['GEMINI_API_KEY'] = gmk;

      document.querySelectorAll('.role-model-input').forEach(inp => {
        const k = inp.getAttribute('data-env');
        if (k && inp.value.trim()) updates[k] = inp.value.trim();
      });
      document.querySelectorAll('.role-key-input').forEach(inp => {
        const k = inp.getAttribute('data-env');
        if (k && inp.value.trim()) updates[k] = inp.value.trim();
      });

      const statusEl = document.getElementById('envSaveStatus');
      const hasTauri = Boolean(window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === 'function');
      if (hasTauri) {
        const secretUpdates = {};
        const configUpdates = {};
        Object.entries(updates).forEach(([k, v]) => {
          if (_isSecretEnvName(k)) secretUpdates[k] = v;
          else configUpdates[k] = v;
        });

        try {
          if (Object.keys(configUpdates).length > 0) {
            const res = await fetch('/api/save_env_config', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ updates: configUpdates })
            });
            const cfgOut = await res.json();
            if (!cfgOut.success) {
              alert('Error saving settings: ' + (cfgOut.error || 'Unknown'));
              return;
            }
          }
          for (const [name, value] of Object.entries(secretUpdates)) {
            await window.__TAURI__.core.invoke('set_secret', { name, value });
          }
          document.getElementById('envNimGlobalKey').value = '';
          document.getElementById('envOpenAiKey').value = '';
          document.getElementById('envGeminiKey').value = '';
          document.querySelectorAll('.role-key-input').forEach(inp => { inp.value = ''; });
          if (statusEl) {
            statusEl.innerText = 'Saved. Restarting engine...';
            statusEl.style.color = 'var(--mint)';
          }
        } catch (e) {
          alert('Error saving keychain secrets: ' + e);
        }
        return;
      }

      const res = await fetch('/api/save_env_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ updates })
      });
      const out = await res.json();
      if (out.success) {
        alert(`Saved ${out.saved_keys.length} settings cleanly to .env.`);
        document.getElementById('envNimGlobalKey').value = '';
        await fetchStatus();
        renderModelsStudio(latestState, true);
        loadKeyPool();
      } else {
        alert('Error saving .env: ' + (out.error || 'Unknown'));
      }
    }

    async function testRoleModel(roleId, btn) {
      const outEl = document.getElementById('testRes-' + roleId);
      outEl.innerText = 'Testing endpoint...';
      outEl.style.color = 'var(--amber)';
      try {
        const res = await fetch('/api/test_ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ role_id: roleId })
        });
        const data = await res.json();
        if (data.success) {
          const fbNote = data.fallback_used ? ` [Fallback -> ${data.model_used}]` : '';
          outEl.innerText = `[OK] ${data.latency_ms}ms | ${data.model_used}${fbNote}`;
          outEl.style.color = 'var(--mint)';
        } else {
          outEl.innerText = `[FAIL] ${data.error}`;
          outEl.style.color = 'var(--danger)';
        }
      } catch(e) {
        outEl.innerText = `[ERROR] ${e}`;
        outEl.style.color = 'var(--danger)';
      }
    }

    async function pitchOpportunityToCmo(encTitle, encUrl, score) {
      const title = decodeURIComponent(encTitle);
      const url = decodeURIComponent(encUrl);
      const msg = `@cmo Opportunity Radar hit (${score}/625): "${title}" (${url}). Evaluate our product positioning and delegate response or post tasks if worth pursuing.`;
      switchTab('tab-room');
      const box = document.getElementById('globalMessagesBox');
      box.innerHTML += `<div class="panel-meta" style="color:var(--lime);">Escalating opportunity to @cmo...</div>`;
      await fetch('/api/messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: msg, sender: 'CEO' })
      });
      loadGlobalMessages();
    }

    async function renderStudioCard() {
      const payload = {
        post_id: document.getElementById('cardPostId').value.trim(),
        field: document.getElementById('cardField').value,
        kicker: document.getElementById('cardKicker').value.trim(),
        big: document.getElementById('cardBigLines').value,
        serif: document.getElementById('cardSerif').value.trim(),
        foot: document.getElementById('cardFoot').value.trim(),
        asset_id: document.getElementById('cardAssetSelect').value,
        sizes: ['4x5', '1x1', '16x9', '9x16']
      };
      const res = await fetch('/api/cards/render', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.error) { alert('Render error: ' + data.error); return; }

      const wrap = document.getElementById('cardRenderPreview');
      wrap.style.display = 'flex';
      document.getElementById('cardRenderStatus').innerText = `Rendered ${(data.rendered_files || []).length} formats into outputs/posts/${data.post_id}/ and staged for CEO Review.`;
      const gal = document.getElementById('cardRenderGallery');
      gal.innerHTML = (data.rendered_files || []).map(f =>
        `<img src="/api/asset/${encodeURIComponent(data.post_id)}/${encodeURIComponent(f)}" class="review-img" title="${escapeHtml(f)}">`
      ).join('');
      fetchStatus();
    }

    async function draftPrReply() {
      const scenario = document.getElementById('prScenario').value;
      const severity = document.getElementById('prSeverity').value;
      const ctx = document.getElementById('prContextInput').value.trim();
      if (!ctx) { alert('Enter an incoming comment, press query, or client brief first.'); return; }

      const btn = document.getElementById('btnDraftPr');
      btn.disabled = true;
      btn.innerText = 'Drafting Response...';
      try {
        const res = await fetch('/api/pr/draft_reply', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ scenario, severity, source_context: ctx })
        });
        const data = await res.json();
        document.getElementById('prDraftOutputBox').style.display = 'flex';
        document.getElementById('prDraftModelTag').innerText = `${data.provider || 'gateway'} | ${data.model_used || 'unknown'}`;
        document.getElementById('prDraftText').innerText = data.draft || data.error || 'No response';
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg class="icon"><use href="#icon-send"/></svg> Generate Grounded Draft';
      }
    }

    function copyPrDraft() {
      navigator.clipboard.writeText(document.getElementById('prDraftText').innerText);
      alert('PR draft copied to clipboard.');
    }

    // Assets Library
    async function loadAssets() {
      try {
        const res = await fetch('/api/assets');
        cachedAssets = await res.json();
        document.getElementById('assetsCountBadge').innerText = cachedAssets.length;
        const grid = document.getElementById('assetsGrid');
        grid.innerHTML = '';

        const chatSel = document.getElementById('chatAssetSelect');
        const roomSel = document.getElementById('roomAssetSelect');
        const cardSel = document.getElementById('cardAssetSelect');
        if (chatSel) chatSel.innerHTML = '<option value="">None</option>';
        if (roomSel) roomSel.innerHTML = '<option value="">Attach Asset (None)</option>';
        if (cardSel) cardSel.innerHTML = '<option value="">None (Typography Only)</option>';

        cachedAssets.forEach(a => {
          const opt = `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)} (${escapeHtml(a.file_group)})</option>`;
          if (chatSel) chatSel.innerHTML += opt;
          if (roomSel) roomSel.innerHTML += opt;
          if (cardSel && a.file_group === 'image') cardSel.innerHTML += opt;

          let previewContent = '';
          if (a.file_group === 'image') {
            previewContent = `<img src="/api/asset_file/${encodeURIComponent(a.id)}" class="asset-preview-img" alt="${escapeHtml(a.name)}">`;
          } else if (a.file_group === 'video') {
            previewContent = `<svg class="icon icon-xl" style="color:var(--muted);"><use href="#icon-film"/></svg>`;
          } else {
            previewContent = `<svg class="icon icon-xl" style="color:var(--muted);"><use href="#icon-file"/></svg>`;
          }

          grid.innerHTML += `
            <div class="asset-item-card">
              <div class="asset-preview-box">${previewContent}</div>
              <div>
                <b style="font-size:13px;" title="${escapeHtml(a.name)}">${escapeHtml(a.name.length > 24 ? a.name.slice(0, 22) + '...' : a.name)}</b>
                <div class="panel-meta">${escapeHtml(a.file_group.toUpperCase())} · ${escapeHtml(a.size)}</div>
                <div style="font-size:11px; color:var(--muted); margin-top:4px;">${escapeHtml(a.description || '')}</div>
              </div>
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="badge badge-draft">${escapeHtml(a.category)}</span>
                <button class="btn btn-danger btn-sm" onclick="deleteAsset('${escapeHtml(a.id)}')">
                  <svg class="icon"><use href="#icon-trash"/></svg> Delete
                </button>
              </div>
            </div>
          `;
        });
      } catch(e) {}
    }

    function openUploadModal() { document.getElementById('uploadAssetModal').style.display = 'flex'; }
    function closeUploadModal() { document.getElementById('uploadAssetModal').style.display = 'none'; }

    async function submitUploadAsset() {
      const fileInput = document.getElementById('assetFileInput');
      const category = document.getElementById('assetCategory').value;
      const description = document.getElementById('assetDesc').value;
      if (!fileInput.files || fileInput.files.length === 0) { alert('Select a file first.'); return; }
      const file = fileInput.files[0];
      const reader = new FileReader();
      reader.onload = async function() {
        const b64 = reader.result.split(',')[1];
        await fetch('/api/assets/upload', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ filename: file.name, content_base64: b64, category, description })
        });
        closeUploadModal();
        loadAssets();
      };
      reader.readAsDataURL(file);
    }

    async function deleteAsset(id) {
      if (!confirm('Delete this asset from repository?')) return;
      await fetch('/api/assets/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      loadAssets();
    }

    // Global Team Room
    async function loadGlobalMessages() {
      try {
        const res = await fetch('/api/messages');
        const msgs = await res.json();
        const box = document.getElementById('globalMessagesBox');
        box.innerHTML = '';

        msgs.forEach(m => {
          let bubbleCls = 'chat-bubble';
          if (m.sender === 'CEO') bubbleCls += ' user';
          else if (m.sender === 'cmo') bubbleCls += ' cmo';
          else if (m.capability_redirect) bubbleCls += ' redirect';
          else bubbleCls += ' agent';

          const modelTag = m.model_used ? `<span class="badge badge-draft">${escapeHtml(m.model_used)}</span>` : '';
          box.innerHTML += `
            <div style="display:flex; flex-direction:column; ${m.sender==='CEO'?'align-items:flex-end':'align-items:flex-start'}">
              <div style="font-family:var(--font-mono); font-size:10px; color:var(--muted); margin-bottom:4px; display:flex; align-items:center; gap:6px;">
                <span class="code-tag">${escapeHtml(m.sender_code || m.sender.toUpperCase())}</span>
                <b>${escapeHtml(m.sender_name)}</b>
                ${modelTag}
                <span>${new Date(m.timestamp).toLocaleTimeString()}</span>
              </div>
              <div class="${bubbleCls}">${escapeHtml(m.text)}</div>
            </div>
          `;
        });
        box.scrollTop = box.scrollHeight;
      } catch(e) {}
    }

    function insertTag(tag) {
      const inp = document.getElementById('globalMsgInput');
      inp.value += tag;
      inp.focus();
    }

    async function sendGlobalMessage() {
      const input = document.getElementById('globalMsgInput');
      const text = input.value.trim();
      const sel = document.getElementById('roomAssetSelect');
      const attached = sel && sel.value ? [sel.value] : [];
      if (!text) return;
      input.value = '';

      const box = document.getElementById('globalMessagesBox');
      box.innerHTML += `<div class="panel-meta" style="color:var(--lime);">Routing message and orchestration replies...</div>`;
      box.scrollTop = box.scrollHeight;

      await fetch('/api/messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text, sender: 'CEO', attached_assets: attached })
      });
      loadGlobalMessages();
    }

    // Team Roster & Specialist Chat
    async function loadRoster() {
      try {
        const res = await fetch('/api/roster');
        cachedRoster = await res.json();
        const container = document.getElementById('rosterContainer');
        container.innerHTML = '';

        cachedRoster.forEach(m => {
          const mInfo = m.model_info || {};
          const canHtml = (m.can || []).slice(0, 2).map(c => `<div class="panel-meta">+ ${escapeHtml(c)}</div>`).join('');
          const cannotHtml = (m.cannot || []).slice(0, 1).map(c => `<div class="panel-meta" style="color:var(--danger);">- Cannot: ${escapeHtml(c)}</div>`).join('');

          container.innerHTML += `
            <div class="agent-card">
              <div style="display:flex; flex-direction:column; gap:8px;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
                  <div style="display:flex; align-items:center; gap:10px;">
                    <span class="code-tag" style="font-size:12px; padding:4px 8px;">${escapeHtml(m.code)}</span>
                    <div>
                      <b style="font-size:14px;">${escapeHtml(m.name)}</b>
                      <div style="font-family:var(--font-mono); font-size:10px; color:var(--lime);">${escapeHtml(m.role)}</div>
                    </div>
                  </div>
                </div>
                <div style="font-size:12px; color:#B5BCC9; line-height:1.45;">${escapeHtml(m.description)}</div>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                  <span class="badge badge-info">${escapeHtml(mInfo.model || 'NIM Default')}</span>
                  <span class="badge ${mInfo.has_key ? 'badge-ok' : 'badge-draft'}">${mInfo.has_key ? 'Key Ready' : 'Fallback'}</span>
                </div>
                <div style="border-top:1px solid var(--border); padding-top:8px;">
                  ${canHtml}
                  ${cannotHtml}
                  <div class="panel-meta" style="margin-top:4px;">Latest file: ${escapeHtml(m.latest_output)}</div>
                </div>
              </div>
              <button class="btn btn-lime" style="width:100%; justify-content:center;" onclick="openAgentChat('${escapeHtml(m.id)}')">
                <svg class="icon"><use href="#icon-send"/></svg> Chat with @${escapeHtml(m.id)}
              </button>
            </div>
          `;
        });
      } catch(e) {}
    }

    async function openAgentChat(agentId) {
      activeAgentId = agentId;
      const m = cachedRoster.find(r => r.id === agentId) || { id: agentId, name: agentId, role: '', code: agentId.toUpperCase() };
      const mInfo = m.model_info || {};
      document.getElementById('chatAgentName').innerText = m.name;
      document.getElementById('chatAgentRole').innerText = m.role;
      document.getElementById('chatAgentCode').innerText = m.code || 'AGT';
      document.getElementById('chatAgentModel').innerText = `Model: ${mInfo.model || 'NIM Default'} (${mInfo.has_key ? 'Live NIM' : 'Fallback Mode'})`;
      document.getElementById('chatModal').classList.add('open');

      const res = await fetch('/api/chat_history/' + encodeURIComponent(agentId));
      const history = await res.json();
      renderChatMessages(history);
      loadAssets();
    }

    function closeChat() {
      document.getElementById('chatModal').classList.remove('open');
      activeAgentId = null;
    }

    function renderChatMessages(history) {
      const container = document.getElementById('chatMessages');
      container.innerHTML = '';
      if (!history || history.length === 0) {
        container.innerHTML = '<div class="panel-meta" style="text-align:center; margin-top:40px;">No conversation history yet. Direct this specialist or assign a task.</div>';
        return;
      }
      history.forEach(m => {
        const cls = m.sender === 'user' ? 'user' : (m.capability_redirect ? 'redirect' : 'agent');
        let extra = '';
        if (m.capability_redirect && m.redirect_to) {
          extra = `<div style="margin-top:8px;"><button class="btn btn-sm" onclick="openAgentChat('${escapeHtml(m.redirect_to)}')">Switch chat to @${escapeHtml(m.redirect_to)}</button></div>`;
        } else if (m.sender !== 'user' && m.model_used) {
          extra = `<div class="panel-meta" style="margin-top:6px; font-size:10px;">Engine: ${escapeHtml(m.model_used)} (${escapeHtml(m.provider || '')})</div>`;
        }
        container.innerHTML += `<div class="chat-bubble ${cls}">${escapeHtml(m.text)}${extra}</div>`;
      });
      container.scrollTop = container.scrollHeight;
    }

    async function sendAgentMessage() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      const assetSelect = document.getElementById('chatAssetSelect');
      const attachedAssets = assetSelect.value ? [assetSelect.value] : [];
      if (!text || !activeAgentId) return;
      input.value = '';

      const container = document.getElementById('chatMessages');
      container.innerHTML += `<div class="chat-bubble user">${escapeHtml(text)}${attachedAssets.length ? ' [Asset Attached]' : ''}</div>`;
      container.innerHTML += `<div class="chat-bubble agent" id="thinkingBubble">Processing via AI Gateway...</div>`;
      container.scrollTop = container.scrollHeight;

      try {
        const res = await fetch('/api/agent_chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ agent_id: activeAgentId, message: text, attached_assets: attachedAssets })
        });
        const data = await res.json();
        const tb = document.getElementById('thinkingBubble');
        if (tb) tb.remove();

        if (data.history) renderChatMessages(data.history);
        if (!data.success && data.error) {
          container.innerHTML += `<div class="chat-bubble redirect">${escapeHtml(data.error)}</div>`;
          container.scrollTop = container.scrollHeight;
        }
        if (data.escalated_to_cmo) {
          container.innerHTML += `<div class="panel-meta" style="color:var(--sky); text-align:center;">[Task escalated to @cmo in Global Team Room]</div>`;
          container.scrollTop = container.scrollHeight;
        }
      } catch(e) {
        const tb = document.getElementById('thinkingBubble');
        if (tb) tb.innerText = 'Error: ' + e;
      }
    }

    function toggleAddRepoKind() {
      const kind = document.getElementById('newRepoKind').value;
      document.getElementById('newRepoLocalGroup').style.display = kind === 'local' ? 'block' : 'none';
      document.getElementById('newRepoGithubGroup').style.display = kind === 'github' ? 'block' : 'none';
    }
    function openAddRepoModal() {
      document.getElementById('addRepoError').innerText = '';
      toggleAddRepoKind();
      document.getElementById('addRepoModal').style.display = 'flex';
    }
    function closeAddRepoModal() { document.getElementById('addRepoModal').style.display = 'none'; }

    async function submitAddRepo() {
      const kind = document.getElementById('newRepoKind').value;
      const name = document.getElementById('newRepoName').value.trim();
      const local_path = document.getElementById('newRepoLocalPath').value.trim();
      const github = document.getElementById('newRepoUrl').value.trim();
      const product_id = document.getElementById('newRepoProductId').value.trim();
      const status = document.getElementById('newRepoStatus').value;
      const confidential = document.getElementById('newRepoConfidential').checked;
      const marketing_angle = document.getElementById('newRepoAngle').value.trim();
      const errEl = document.getElementById('addRepoError');
      errEl.innerText = '';
      const payload = { kind, name, status, confidential, marketing_angle };
      if (product_id) payload.product_id = product_id;
      if (kind === 'local') payload.local_path = local_path;
      else payload.github = github;

      const res = await fetch('/api/repos', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        errEl.innerText = data.error || 'Failed to add repository.';
        return;
      }
      closeAddRepoModal();
      loadReposAndShipped();
    }

    async function syncSingleRepo(repoId) {
      await fetch(`/api/repos/${encodeURIComponent(repoId)}/sync`, { method: 'POST' });
      loadReposAndShipped();
    }

    async function removeSingleRepo(repoId) {
      if (!confirm(`Remove repository "${repoId}"?`)) return;
      await fetch(`/api/repos/${encodeURIComponent(repoId)}`, { method: 'DELETE' });
      loadReposAndShipped();
    }

    async function loadReposAndShipped() {
      try {
        const [reposRes, shippedRes] = await Promise.all([
          fetch('/api/repos'),
          fetch('/api/shipped?days=7')
        ]);
        const reposList = await reposRes.json();
        const shippedList = await shippedRes.json();
        const repoDiv = document.getElementById('repoContainer');
        if (repoDiv && Array.isArray(reposList)) {
          document.getElementById('kpiRepos').innerText = reposList.length;
          repoDiv.innerHTML = reposList.map(r => {
            const statusCls = r.status === 'released' ? 'badge-approved' : 'badge-warn';
            const confBadge = r.confidential ? '<span class="badge badge-danger">confidential</span>' : '';
            const kindBadge = `<span class="badge badge-info">${escapeHtml(r.kind || 'local')}</span>`;
            const targetText = r.kind === 'github' ? (r.github || '') : (r.local_path || '');
            const syncText = r.last_sync ? `Last sync: ${escapeHtml(r.last_sync)}` : 'Never synced';
            const rlWarn = r.rate_limited ? `<div class="panel-meta" style="color:var(--amber);">${escapeHtml(r.rate_limit_message || 'Rate limited')}</div>` : '';
            const commitsHtml = (r.recent_commits || []).slice(0, 3).map(c =>
              `<div style="margin-bottom:4px;"><b>${escapeHtml(c.hash)}</b> (${escapeHtml((c.date || '').slice(0, 10))}) - ${escapeHtml(c.message)}</div>`
            ).join('');
            return `
              <div class="repo-card">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
                  <b style="font-size:15px;">${escapeHtml(r.name)}</b>
                  <div style="display:flex; gap:6px; align-items:center;">
                    ${kindBadge}
                    ${confBadge}
                    <span class="badge ${statusCls}">${escapeHtml(r.status || '')}${r.version ? ' (v' + escapeHtml(r.version) + ')' : ''}</span>
                  </div>
                </div>
                <div class="panel-meta">${escapeHtml(targetText)} | ${syncText}</div>
                ${rlWarn}
                <div class="commit-box">${commitsHtml || 'No recent commits synced'}</div>
                <div style="display:flex; gap:8px; justify-content:flex-end;">
                  <button class="btn btn-sm" onclick="syncSingleRepo('${escapeHtml(r.id)}')">Sync</button>
                  <button class="btn btn-sm" onclick="removeSingleRepo('${escapeHtml(r.id)}')">Remove</button>
                </div>
              </div>
            `;
          }).join('');
        }

        const shipDiv = document.getElementById('shippedListContainer');
        if (shipDiv && Array.isArray(shippedList)) {
          if (shippedList.length === 0) {
            shipDiv.innerHTML = '<div class="panel-meta">Nothing shipped in the last 7 days.</div>';
          } else {
            shipDiv.innerHTML = shippedList.slice(0, 20).map(it => {
              const titleHtml = it.url
                ? `<a href="${escapeHtml(it.url)}" target="_blank" style="color:var(--sky); text-decoration:none;">${escapeHtml(it.title)}</a>`
                : escapeHtml(it.title);
              return `<div><span class="badge badge-draft">${escapeHtml(it.repo_id)} / ${escapeHtml(it.kind)}</span> ${titleHtml} <span class="panel-meta">${escapeHtml((it.date || '').slice(0, 10))}</span></div>`;
            }).join('');
          }
        }
      } catch (e) {}
    }

    async function loadFactsPanel() {
      const container = document.getElementById('pendingFactsContainer');
      if (!container) return;
      try {
        const res = await fetch('/api/facts?status=pending');
        const facts = await res.json();
        if (!Array.isArray(facts) || facts.length === 0) {
          container.innerHTML = '<div class="panel-meta">No pending product facts awaiting approval.</div>';
          return;
        }
        container.innerHTML = facts.map(f => {
          const srcHtml = f.source_url
            ? `<a href="${escapeHtml(f.source_url)}" target="_blank" style="color:var(--sky); text-decoration:none;">${escapeHtml(f.source_kind)}</a>`
            : escapeHtml(f.source_kind);
          return `
            <div style="padding:10px; background:var(--bg-elevated); border:1px solid var(--border-strong); border-radius:6px; display:flex; flex-direction:column; gap:8px;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="badge badge-warn">${escapeHtml(f.product_id)} | ${srcHtml}</span>
                <span class="panel-meta">${escapeHtml(f.created_by || '')}</span>
              </div>
              <input type="text" id="factText_${escapeHtml(f.id)}" value="${escapeHtml(f.text)}">
              <div style="display:flex; gap:8px; justify-content:flex-end;">
                <button class="btn btn-lime btn-sm" onclick="decideFact('${escapeHtml(f.id)}', 'approved')">Approve</button>
                <button class="btn btn-sm" onclick="decideFact('${escapeHtml(f.id)}', 'rejected')">Reject</button>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {}
    }

    async function decideFact(factId, status) {
      const input = document.getElementById(`factText_${factId}`);
      const text = input ? input.value.trim() : undefined;
      await fetch(`/api/facts/${encodeURIComponent(factId)}/decide`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ status, text })
      });
      loadFactsPanel();
    }

    async function addManualFact() {
      const product_id = document.getElementById('manualFactProductId').value.trim();
      const text = document.getElementById('manualFactText').value.trim();
      const source_url = document.getElementById('manualFactUrl').value.trim();
      if (!product_id || !text) { alert('product_id and fact text are required.'); return; }
      await fetch('/api/facts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ product_id, text, source_url: source_url || null, source_kind: 'manual' })
      });
      document.getElementById('manualFactText').value = '';
      document.getElementById('manualFactUrl').value = '';
      loadFactsPanel();
    }

    async function runJob(job) {
      await fetch('/api/run', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ job }) });
      fetchStatus();
    }
    async function approvePackage(post_id) {
      if (!confirm(`Confirm CEO approval for "${post_id}"?`)) return;
      await fetch('/api/approve', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id }) });
      fetchStatus();
    }
    async function requestRevision(post_id) {
      const notes = prompt('Enter revision instructions for @writer / @reviewer:', 'Verify claims and tighten tone.');
      if (notes === null) return;
      await fetch('/api/request_revision', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id, notes }) });
      fetchStatus();
    }
    async function publishPackage(post_id) {
      if (!confirm(`Confirm "${post_id}" is published to social handles?`)) return;
      await fetch('/api/publish', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ post_id }) });
      fetchStatus();
    }
    async function toggleSchedule(id) {
      await fetch('/api/schedule/toggle', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id }) });
      fetchStatus();
    }

    async function loadWorkflows() {
      const container = document.getElementById('workflowsList');
      if (!container) return;
      try {
        const res = await fetch('/api/workflows?limit=20');
        const runs = await res.json();
        if (!Array.isArray(runs) || runs.length === 0) {
          container.innerHTML = '<div class="panel-meta">No workflow runs yet.</div>';
          return;
        }
        container.innerHTML = runs.map(r => {
          const pendingGates = (r.gates || []).filter(g => g.status === 'pending');
          const gatesHtml = pendingGates.map(g => {
            const btns = (g.options || []).map(opt => {
              const needsFeedback = (opt === 'Revise' || opt === 'Change');
              return `<button class="btn btn-sm ${opt === 'Approve' || opt === 'Keep' ? 'btn-lime' : ''}" onclick="answerWorkflowGate('${escapeHtml(g.id)}', '${escapeHtml(opt)}', ${needsFeedback})">${escapeHtml(opt)}</button>`;
            }).join(' ');
            return `
              <div style="margin-top:8px; padding:8px; background:var(--bg-elevated); border:1px solid var(--border-strong); border-radius:6px;">
                <div style="font-size:12px; font-weight:600; margin-bottom:6px;">Gate: ${escapeHtml(g.question)}</div>
                <input type="text" id="gateFeedback_${escapeHtml(g.id)}" placeholder="Feedback (required for Revise / Change)" style="margin-bottom:6px;">
                <div style="display:flex; gap:6px; flex-wrap:wrap;">${btns}</div>
              </div>
            `;
          }).join('');
          const canCancel = ['queued', 'running', 'waiting_user'].includes(r.status);
          const cancelBtn = canCancel
            ? `<button class="btn btn-sm" onclick="cancelWorkflowRun('${escapeHtml(r.id)}')">Cancel</button>`
            : '';
          const cids = (r.content_ids || []).join(', ');
          return `
            <div style="padding:10px; background:var(--bg-canvas); border:1px solid var(--border); border-radius:6px;">
              <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
                <div>
                  <b style="font-size:12px;">${escapeHtml(r.kind)}</b>
                  <span class="panel-meta" style="margin-left:6px;">${escapeHtml(r.id)}</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                  <span class="badge badge-info">${escapeHtml(r.status)} (step ${r.current_step})</span>
                  ${cancelBtn}
                </div>
              </div>
              ${cids ? `<div class="panel-meta" style="margin-top:4px;">Content: ${escapeHtml(cids)}</div>` : ''}
              ${r.error ? `<div class="panel-meta" style="margin-top:4px; color:var(--danger);">Error: ${escapeHtml(r.error)}</div>` : ''}
              ${gatesHtml}
            </div>
          `;
        }).join('');
      } catch (e) {}
    }

    async function startWorkflowFromForm() {
      const kind = document.getElementById('wfKind').value;
      const text = document.getElementById('wfBriefOrTopic').value.trim();
      const productId = document.getElementById('wfProductId').value.trim();
      const withImage = document.getElementById('wfWithImage').checked;
      const platforms = Array.from(document.querySelectorAll('.wf-platform-cb:checked')).map(el => el.value);

      const inputs = {};
      if (productId) inputs.product_id = productId;
      if (kind === 'post') {
        inputs.brief = text;
        inputs.platforms = platforms.length ? platforms : ['linkedin', 'x'];
        inputs.with_image = withImage;
      } else if (kind === 'blog' || kind === 'video_script') {
        inputs.topic = text;
      } else if (kind === 'repurpose') {
        inputs.content_id = text;
        inputs.targets = platforms.map(p => p === 'x' ? 'x_thread' : (p === 'youtube' ? 'youtube_description' : p));
      } else if (kind === 'weekly_plan') {
        inputs.platforms = platforms.length ? platforms : ['linkedin', 'x'];
        inputs.posts_per_week = 2;
      }

      const res = await fetch('/api/workflows/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ kind, inputs })
      });
      const data = await res.json();
      if (!res.ok && data.error) {
        alert(data.error);
      }
      loadWorkflows();
    }

    async function answerWorkflowGate(gateId, answer, needsFeedback) {
      const fbInput = document.getElementById('gateFeedback_' + gateId);
      const feedback = fbInput ? fbInput.value.trim() : '';
      if (needsFeedback && !feedback) {
        alert('Please enter feedback before choosing ' + answer + '.');
        return;
      }
      const res = await fetch('/api/gates/' + encodeURIComponent(gateId) + '/answer', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ answer, feedback: feedback || null })
      });
      const data = await res.json();
      if (!res.ok && data.error) alert(data.error);
      loadWorkflows();
    }

    async function cancelWorkflowRun(runId) {
      await fetch('/api/workflows/' + encodeURIComponent(runId) + '/cancel', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({})
      });
      loadWorkflows();
    }

    let pollTimer = null;
    function startPollingFallback() {
      if (!pollTimer) {
        pollTimer = setInterval(fetchStatus, 3000);
      }
    }
    function stopPollingFallback() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
    function initEventStream() {
      if (typeof EventSource === 'undefined') {
        startPollingFallback();
        return;
      }
      try {
        const es = new EventSource('/api/events');
        es.onopen = () => {
          stopPollingFallback();
        };
        es.addEventListener('job', () => {
          fetchStatus();
        });
        es.addEventListener('message', () => {
          loadGlobalMessages();
        });
        ['workflow_step', 'workflow_waiting', 'workflow_done', 'workflow_failed'].forEach(evtName => {
          es.addEventListener(evtName, () => {
            loadWorkflows();
          });
        });
        es.onerror = () => {
          startPollingFallback();
        };
      } catch (e) {
        startPollingFallback();
      }
    }

    initEventStream();
    fetchStatus();
    loadReposAndShipped();
    loadFactsPanel();
    loadRoster();
    loadAssets();
    loadGlobalMessages();
    loadWorkflows();
  </script>
</body>
</html>
"""
        body = html.replace("__NEW_STUDIO_LINK__", new_studio_link).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    print("============================================================")
    print("MAGNM PR & SOCIAL AGENCY COMMAND CENTER (v5 - NVIDIA NIM)")
    print("============================================================")
    db.init_db()
    try:
        repos.remove_seeded_repos()
    except Exception as exc:
        print(f"Could not clean up old default repos: {exc}")
    agent_runtime.recover_stale_sessions()
    coding_agents.recover_stale_runs()
    social_service.recover_stale_runs()
    posting_service.recover_stale_attempts()
    jobs.start_worker()
    # Same as engine/main.py: mirror work, publish live links and pull quote requests in the background.
    import cloud_sync
    if cloud_sync.is_enabled():
        cloud_sync.start()
    import model_registry
    model_registry.start_catalog_monitor()
    import scheduler_loop
    scheduler_loop.start()
    phone_server.start_if_enabled()
    print(f"Starting multi-threaded server on http://{HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), CommandCenterHandler)
    server.daemon_threads = True
    actual_port = server.server_address[1]
    write_engine_endpoint(actual_port)

    def _handle_signal(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except ValueError:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            phone_server.stop(timeout=1.0)
        except Exception:
            pass
        try:
            model_registry.stop_catalog_monitor(timeout=1.0)
        except Exception:
            pass
        try:
            scheduler_loop.stop(timeout=1.0)
        except Exception:
            pass
        try:
            cloud_sync.stop(timeout=1.0)
        except Exception:
            pass
        try:
            jobs.stop_worker(timeout=1.0)
        except Exception:
            pass
        try:
            remove_engine_endpoint(expected_pid=os.getpid())
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
