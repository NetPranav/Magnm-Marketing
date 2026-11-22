#!/usr/bin/env python3
"""Separate Tailscale-only HTTP listener and /phone/v1 API for Magnm Mobile."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import ipaddress
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_chat
import agents
import ai_gateway
import cloud_sync
import db
import events
import file_serving
import notifications
import paths
import phone_pairing
import platforms
import product_rules
import reminders
import repos
import results_extract
import review
import review_studio
from tools import content as content_tools
from tools import core as core_tools
import weekly_summary

DEFAULT_PORT = 8788
TAILSCALE_NET = ipaddress.IPv4Network("100.64.0.0/10")
NO_TAILSCALE_MESSAGE = "Install and connect Tailscale on this laptop first."
PHONE_ENABLED_META_KEY = "phone_connections_enabled"

SOCKET_TIMEOUT_S = 15.0
MAX_CONCURRENT_CONNECTIONS = 16
MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_RESULTS_EXTRACT_BODY_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
MAX_INBOX_ITEMS = 50
MAX_AGENT_MESSAGE_CHARS = 4000
AGENT_RATE_LIMIT_COUNT = 20
AGENT_RATE_LIMIT_WINDOW_S = 600.0
ENGINE_VERSION = "0.1.0"


class PhoneServerError(Exception):
    """Typed error for phone listener configuration, Tailscale resolution, or binding."""

    def __init__(self, message, status=400, code="phone_server_error"):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = str(code)


class PhoneApiError(Exception):
    """Typed error for /phone/v1 API validation, authorization, or confidentiality failures."""

    def __init__(self, message, status=400, code="phone_api_error"):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = str(code)


def validate_bind_ip(raw_ip, *, allow_loopback=False):
    """Validate that raw_ip is an IPv4 address in 100.64.0.0/10 (or 127.0.0.1 in tests)."""
    text = str(raw_ip or "").strip()
    if not text:
        raise PhoneServerError(NO_TAILSCALE_MESSAGE, status=400, code="tailscale_missing")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise PhoneServerError(
            f"Refused invalid bind address {text!r}.",
            status=400,
            code="invalid_bind_address",
        ) from exc
    if addr.version != 4:
        raise PhoneServerError(
            f"Refused non-IPv4 bind address {text!r}: only Tailscale IPv4 is allowed.",
            status=400,
            code="invalid_bind_address",
        )
    if addr in TAILSCALE_NET:
        return str(addr)
    if allow_loopback and str(addr) == "127.0.0.1":
        return "127.0.0.1"
    raise PhoneServerError(
        f"Refused bind address {text!r}: only Tailscale 100.64.0.0/10 is allowed.",
        status=400,
        code="invalid_bind_address",
    )


def resolve_tailscale_ipv4():
    """Return the local Tailscale IPv4 address via `tailscale ip -4`, or raise PhoneServerError."""
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise PhoneServerError(NO_TAILSCALE_MESSAGE, status=400, code="tailscale_missing") from exc

    if proc.returncode != 0:
        raise PhoneServerError(NO_TAILSCALE_MESSAGE, status=400, code="tailscale_missing")

    for line in (proc.stdout or "").splitlines():
        cand = line.strip()
        if not cand:
            continue
        return validate_bind_ip(cand, allow_loopback=False)

    raise PhoneServerError(NO_TAILSCALE_MESSAGE, status=400, code="tailscale_missing")


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def _build_confidential_guard():
    """Return (conf_sids, conf_cids, conf_pids, checker, repo_patterns); fail closed if rules unreadable."""
    try:
        conf_sids, conf_cids, checker = cloud_sync.confidential_scope()
        conf_pids = product_rules.confidential_product_ids(_rules_path())
    except Exception as exc:
        raise PhoneApiError(
            "Product rules could not be read; phone access paused.",
            status=503,
            code="product_rules_unreadable",
        ) from exc

    repo_patterns = []
    try:
        for r in repos.list_repos():
            pid = (r.get("product_id") or "").strip().lower()
            is_conf = bool(r.get("confidential")) or (pid in conf_pids) or checker(r.get("name") or "")
            if not is_conf:
                continue
            for term in (r.get("name"), r.get("id"), r.get("product_id"), r.get("github")):
                txt = str(term or "").strip()
                if txt:
                    repo_patterns.append(product_rules._word_pattern(txt))
    except Exception as exc:
        raise PhoneApiError(
            "Connected repos could not be read; phone access paused.",
            status=503,
            code="repos_unreadable",
        ) from exc

    return conf_sids, conf_cids, conf_pids, checker, repo_patterns


def _text_is_confidential(text, checker, repo_patterns):
    if not text:
        return False
    return bool(checker(text)) or product_rules.any_match(repo_patterns, text)


def _content_item_is_confidential(item, conf_sids, conf_cids, conf_pids, checker, repo_patterns):
    if not item:
        return True
    if item.get("id") in conf_cids:
        return True
    if item.get("session_id") in conf_sids:
        return True
    pid = (item.get("product_id") or "").strip().lower()
    if pid and pid in conf_pids:
        return True

    parts = [
        item.get("title") or "",
        item.get("product_id") or "",
        item.get("body") or "",
        item.get("notes") or "",
        item.get("posted_url") or "",
        json.dumps(item.get("files") or [], ensure_ascii=False, default=str),
    ]
    for ver in item.get("versions") or []:
        if isinstance(ver, dict):
            parts.append(ver.get("body") or "")
            parts.append(ver.get("notes") or "")
    combined = "\n".join(str(p) for p in parts if p)
    return _text_is_confidential(combined, checker, repo_patterns)


def _stub_content_item(item, *, detailed=False):
    stub = {
        "id": item["id"],
        "type": item.get("type"),
        "platform": item.get("platform"),
        "status": item.get("status"),
        "title": "Confidential item",
        "confidential": True,
        "current_version": item.get("current_version"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "files": [],
    }
    if detailed:
        stub["versions"] = []
        stub["comments"] = []
        stub["warnings"] = []
    return stub


def _summary_is_confidential(summary, conf_cids, checker, repo_patterns):
    if not isinstance(summary, dict):
        return True
    data = summary.get("data") if isinstance(summary.get("data"), dict) else {}
    highlights = summary.get("highlights") if isinstance(summary.get("highlights"), dict) else {}
    prev_week = data.get("previous_week") if isinstance(data.get("previous_week"), dict) else {}
    reminders_block = data.get("reminders") if isinstance(data.get("reminders"), dict) else {}

    entries = []
    for key in ("posts", "posts_posted", "drafts_created"):
        entries.extend(data.get(key) or [])
    entries.extend(prev_week.get("posts") or [])
    entries.extend(reminders_block.get("done") or [])
    entries.extend(reminders_block.get("missed") or [])
    for key in ("top_post", "bottom_post"):
        if highlights.get(key):
            entries.append(highlights.get(key))
    entries.extend(highlights.get("missing_results") or [])

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("content_id") or entry.get("id")
        if cid and cid in conf_cids:
            return True

    serialized = json.dumps(summary, ensure_ascii=False)
    return _text_is_confidential(serialized, checker, repo_patterns)


def _stub_summary(summary):
    return {
        "id": summary["id"],
        "week_start": summary.get("week_start"),
        "status": summary.get("status"),
        "confidential": True,
        "created_at": summary.get("created_at"),
    }


def _reminder_is_confidential(rem, conf_cids, checker, repo_patterns):
    cid = rem.get("content_id")
    if cid and cid in conf_cids:
        return True
    text = f"{rem.get('title') or ''}\n{rem.get('note') or ''}"
    return _text_is_confidential(text, checker, repo_patterns)


def _stub_reminder(rem):
    return {
        "id": rem["id"],
        "kind": rem.get("kind"),
        "title": "Confidential reminder",
        "note": "",
        "confidential": True,
        "due_at": rem.get("due_at"),
        "done_at": rem.get("done_at"),
        "snoozed_until": rem.get("snoozed_until"),
        "created_at": rem.get("created_at"),
    }


def _notification_is_confidential(notif, conf_cids, checker, repo_patterns):
    route = str(notif.get("route") or "")
    if route.startswith("#/review/"):
        cid = urllib.parse.unquote(route[len("#/review/"):].split("/", 1)[0].split("?", 1)[0])
        if cid and cid in conf_cids:
            return True
    text = f"{notif.get('title') or ''}\n{notif.get('body') or ''}"
    return _text_is_confidential(text, checker, repo_patterns)


def _stub_notification(notif):
    return {
        "id": notif["id"],
        "kind": notif.get("kind"),
        "title": "Confidential notification",
        "body": "",
        "confidential": True,
        "created_at": notif.get("created_at"),
        "read_at": notif.get("read_at"),
    }


def _proposal_is_confidential(prop, conf_cids, conf_sids, conf_pids, checker, repo_patterns):
    cid = prop.get("content_id")
    if cid:
        if cid in conf_cids:
            return True
        item = db.get_content_item(cid, include_versions=True)
        if item is not None and _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            return True
    text = json.dumps(prop, ensure_ascii=False, default=str)
    return _text_is_confidential(text, checker, repo_patterns)


def _safe_comment(comment, checker, repo_patterns):
    """Return a comment and all its nested replies, with confidential ones replaced by stubs."""
    replies = [_safe_comment(reply, checker, repo_patterns) for reply in comment.get("replies") or []]
    own = {key: value for key, value in comment.items() if key != "replies"}
    if _text_is_confidential(json.dumps(own, ensure_ascii=False, default=str), checker, repo_patterns):
        own = {
            "id": own.get("id"),
            "created_at": own.get("created_at"),
            "body": "Confidential comment",
            "confidential": True,
        }
    return {**own, "replies": replies}


def _stub_proposal(prop):
    return {
        "id": prop["id"],
        "status": prop.get("status"),
        "confidential": True,
        "created_at": prop.get("created_at"),
        "decided_at": prop.get("decided_at"),
    }


# Tools whose results hold only names and rules, which agents see in every chat anyway.
_NAMES_ONLY_TOOLS = frozenset(("read_product_facts", "list_repos", "list_pitch_catalog", "check_text"))
_MAX_SCANNED_TEXT_FILE_BYTES = 5 * 1024 * 1024


def _chat_read_confidential(session, checker, repo_patterns):
    """Return True if any tool result in the chat carried confidential text: a reply can repeat it without naming it."""
    if not session:
        return False
    messages = [m for m in session.get("messages") or [] if isinstance(m, dict)]
    names = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict):
                names[call.get("id")] = (call.get("function") or {}).get("name") or call.get("name")
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        if (names.get(msg.get("tool_call_id")) or msg.get("name")) in _NAMES_ONLY_TOOLS:
            continue
        content = msg.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if _text_is_confidential(text, checker, repo_patterns):
            return True
    return False


def _file_is_confidential(full_path, checker, repo_patterns):
    """Return True if a file's name, or the text of a text file, is confidential."""
    if _text_is_confidential(os.path.basename(full_path), checker, repo_patterns):
        return True
    with open(full_path, "rb") as f:
        head = f.read(8192)
        if b"\x00" in head:
            return False  # Binary (media): judged by the content item it belongs to.
        data = head + f.read(_MAX_SCANNED_TEXT_FILE_BYTES)
    if len(data) > _MAX_SCANNED_TEXT_FILE_BYTES:
        return True  # Too big to check.
    return _text_is_confidential(data.decode("utf-8", errors="replace"), checker, repo_patterns)


def _log_device_activity(device, target, task, detail=""):
    """Record a phone write in the activity table naming the device (never message text)."""
    dev_name = str((device or {}).get("name") or "phone").strip() or "phone"
    db.log_activity(f"phone:{dev_name}", str(target), str(task), str(detail or "")[:160])


def _is_valid_http_url(raw_url):
    try:
        parsed = urllib.parse.urlsplit(str(raw_url or "").strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


# A retry can arrive while the first attempt is still saving, so check-then-create runs one request at a time.
_INBOX_LOCK = threading.Lock()


class PhoneHTTPServer(ThreadingHTTPServer):
    """Dedicated multi-threaded HTTP server bound strictly to Tailscale IPv4 (or test loopback)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        RequestHandlerClass=None,
        *,
        allow_loopback=False,
        pairing_manager=None,
        now_fn=None,
    ):
        bound_ip = validate_bind_ip(server_address[0], allow_loopback=allow_loopback)
        self._conn_sem = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)
        self.pairing_manager = pairing_manager or phone_pairing.get_manager()
        self.now_fn = now_fn or time.time
        self._agent_rate_lock = threading.Lock()
        self._agent_rate_by_device = {}
        handler_cls = RequestHandlerClass or PhoneHandler
        super().__init__((bound_ip, int(server_address[1])), handler_cls)
        self.bound_ip = self.server_address[0]
        self.bound_port = int(self.server_address[1])

    def check_agent_rate_limit(self, device_id):
        """Enforce 20 agent messages per 10 minutes per paired device."""
        now_ts = float(self.now_fn())
        cutoff = now_ts - AGENT_RATE_LIMIT_WINDOW_S
        with self._agent_rate_lock:
            history = [t for t in self._agent_rate_by_device.get(device_id, ()) if t > cutoff]
            if len(history) >= AGENT_RATE_LIMIT_COUNT:
                self._agent_rate_by_device[device_id] = history
                return False
            history.append(now_ts)
            self._agent_rate_by_device[device_id] = history
            return True

    def process_request(self, request, client_address):
        """Reject connections above MAX_CONCURRENT_CONNECTIONS immediately."""
        if not self._conn_sem.acquire(blocking=False):
            try:
                if hasattr(request, "settimeout"):
                    request.settimeout(2.0)
                body = b'{"error":"Too many concurrent connections"}\n'
                resp = (
                    b"HTTP/1.0 503 Service Unavailable\r\n"
                    b"Content-Type: application/json; charset=utf-8\r\n"
                    b"Cache-Control: no-store\r\n"
                    b"X-Content-Type-Options: nosniff\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
                request.sendall(resp)
            except Exception:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._conn_sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._conn_sem.release()


class PhoneHandler(BaseHTTPRequestHandler):
    """Strict native-HTTP handler for /phone/v1 (not a subclass of CommandCenterHandler)."""

    def setup(self):
        if hasattr(self.request, "settimeout"):
            self.request.settimeout(SOCKET_TIMEOUT_S)
        super().setup()

    def log_message(self, format, *args):
        # Never log requests, codes, tokens, poll secrets, or Authorization headers.
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        """Ensure every error response uses JSON with no-store and nosniff (no HTML)."""
        try:
            self._send_json({"error": str(message or "Request error")}, status=int(code))
        except Exception:
            pass

    def _client_ip(self):
        if not self.client_address:
            return ""
        return str(self.client_address[0] or "").strip()

    def _is_allowed_source_ip(self, raw_ip):
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False
        if addr.version != 4:
            return False
        return (addr in TAILSCALE_NET) or (str(addr) == self.server.bound_ip)

    def _has_forbidden_browser_headers(self):
        for key in self.headers.keys():
            low = str(key).lower()
            if low == "origin" or low.startswith("sec-fetch-"):
                return True
        return False

    def _has_valid_host_header(self):
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return False
        expected = f"{self.server.bound_ip}:{self.server.bound_port}"
        return hosts[0].strip() == expected

    def parse_request(self):
        """Validate source IP, Host header, and browser-header exclusion before any route runs."""
        if not super().parse_request():
            return False
        if not self._is_allowed_source_ip(self._client_ip()):
            self.close_connection = True
            self._send_json({"error": "Forbidden source address"}, status=403)
            return False
        if self._has_forbidden_browser_headers():
            self.close_connection = True
            self._send_json({"error": "Browser requests are not allowed"}, status=403)
            return False
        if not self._has_valid_host_header():
            self.close_connection = True
            self._send_json({"error": "Invalid Host header"}, status=403)
            return False
        return True

    def _read_json_body(self, max_bytes):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise PhoneApiError("Chunked transfer encoding is not supported", status=400)
        ct_headers = self.headers.get_all("Content-Type") or []
        if len(ct_headers) != 1:
            self.close_connection = True
            raise PhoneApiError("Content-Type: application/json is required", status=415)
        media_type = ct_headers[0].split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self.close_connection = True
            raise PhoneApiError("Content-Type must be application/json", status=415)

        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            self.close_connection = True
            raise PhoneApiError("Content-Length is required", status=411)
        try:
            content_len = int(raw_len)
        except (TypeError, ValueError) as exc:
            self.close_connection = True
            raise PhoneApiError("Invalid Content-Length", status=400) from exc
        if content_len < 0:
            self.close_connection = True
            raise PhoneApiError("Invalid Content-Length", status=400)
        if content_len > max_bytes:
            self.close_connection = True
            raise PhoneApiError("Request body too large", status=413)

        raw_bytes = self.rfile.read(content_len) if content_len > 0 else b""
        if len(raw_bytes) > max_bytes:
            raise PhoneApiError("Request body too large", status=413)
        try:
            parsed = json.loads(raw_bytes.decode("utf-8") if raw_bytes else "{}")
        except Exception as exc:
            raise PhoneApiError("Invalid JSON body", status=400) from exc
        if not isinstance(parsed, dict):
            raise PhoneApiError("JSON body must be an object", status=400)
        return parsed

    def _require_device(self):
        auth_headers = self.headers.get_all("Authorization") or []
        if len(auth_headers) != 1:
            return None
        return phone_pairing.authenticate_bearer(auth_headers[0], source_ip=self._client_ip())

    def _reject_unsupported(self):
        self._send_json({"error": "Not found"}, status=404)

    def do_DELETE(self):
        self._reject_unsupported()

    def do_PUT(self):
        self._reject_unsupported()

    def do_PATCH(self):
        self._reject_unsupported()

    def do_OPTIONS(self):
        self._reject_unsupported()

    def do_HEAD(self):
        self._reject_unsupported()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        raw_path = url.path

        if not raw_path.startswith("/phone/v1/"):
            self._send_json({"error": "Not found"}, status=404)
            return

        device = self._require_device()
        if device is None:
            self._send_json({"error": "Unauthorized"}, status=401)
            return

        rel = raw_path[len("/phone/v1/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in rel.split("/") if p]
        qs = urllib.parse.parse_qs(url.query, keep_blank_values=True)

        try:
            if parts == ["status"]:
                self._handle_get_status(device)
            elif parts == ["drafts"]:
                self._handle_get_drafts(qs)
            elif len(parts) == 2 and parts[0] == "drafts":
                self._handle_get_draft_detail(parts[1])
            elif parts == ["files"]:
                self._handle_get_file(qs)
            elif parts == ["agents"]:
                self._handle_get_agents()
            elif parts == ["summary"]:
                self._handle_get_summary()
            elif parts == ["reminders"]:
                self._handle_get_reminders(qs)
            elif parts == ["notifications"]:
                self._handle_get_notifications(qs)
            elif parts == ["results", "pending"]:
                self._handle_get_results_pending()
            else:
                self._send_json({"error": "Not found"}, status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except PhoneApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except (reminders.ReminderError, results_extract.ResultsExtractError) as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except Exception:
            self._send_json({"error": "Internal server error"}, status=500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        raw_path = url.path

        if not raw_path.startswith("/phone/v1/"):
            self._send_json({"error": "Not found"}, status=404)
            return

        rel = raw_path[len("/phone/v1/"):].strip("/")
        parts = [urllib.parse.unquote(p) for p in rel.split("/") if p]

        # Pairing routes are explicitly listed and use pairing secrets + rate limits instead of Bearer token.
        if parts == ["pair"]:
            try:
                params = self._read_json_body(MAX_BODY_BYTES)
                res = self.server.pairing_manager.submit_pair_request(
                    params.get("code"),
                    params.get("device_name"),
                    source_ip=self._client_ip(),
                )
                self._send_json(res, status=200)
            except PhoneApiError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            except phone_pairing.PairingError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            return

        if parts == ["pair", "poll"]:
            try:
                params = self._read_json_body(MAX_BODY_BYTES)
                res = self.server.pairing_manager.poll_request(
                    params.get("request_id"),
                    params.get("poll_secret"),
                )
                self._send_json(res, status=200)
            except PhoneApiError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            except phone_pairing.PairingError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            return

        device = self._require_device()
        if device is None:
            self._send_json({"error": "Unauthorized"}, status=401)
            return

        max_body = (
            MAX_RESULTS_EXTRACT_BODY_BYTES
            if parts == ["results", "extract"]
            else MAX_BODY_BYTES
        )

        try:
            if parts == ["inbox"]:
                params = self._read_json_body(max_body)
                with _INBOX_LOCK:
                    self._handle_post_inbox(device, params)
            elif len(parts) == 3 and parts[0] == "drafts" and parts[2] == "comments":
                params = self._read_json_body(max_body)
                self._handle_post_draft_comment(device, parts[1], params)
            elif len(parts) == 3 and parts[0] == "drafts" and parts[2] == "posted":
                params = self._read_json_body(max_body)
                self._handle_post_draft_posted(device, parts[1], params)
            elif len(parts) == 3 and parts[0] == "agents" and parts[2] == "message":
                params = self._read_json_body(max_body)
                self._handle_post_agent_message(device, parts[1], params)
            elif len(parts) == 3 and parts[0] == "reminders" and parts[2] == "done":
                self._read_json_body(max_body)
                self._handle_post_reminder_done(device, parts[1])
            elif parts == ["notifications", "read"]:
                params = self._read_json_body(max_body)
                self._handle_post_notifications_read(device, params)
            elif parts == ["results", "extract"]:
                params = self._read_json_body(max_body)
                self._handle_post_results_extract(device, params)
            elif len(parts) == 3 and parts[0] == "results" and parts[2] == "confirm":
                params = self._read_json_body(max_body)
                self._handle_post_results_confirm(device, parts[1], params)
            else:
                self._send_json({"error": "Not found"}, status=404)
        except PhoneApiError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except (review_studio.NotFoundError,) as exc:
            self._send_json({"error": str(exc)}, status=404)
        except (review_studio.ValidationError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except (reminders.ReminderError, results_extract.ResultsExtractError, notifications.NotificationError) as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except ai_gateway.AINotConnected as exc:
            self._send_json({"error": str(exc)}, status=503)
        except Exception:
            self._send_json({"error": "Internal server error"}, status=500)

    # --- GET handlers ---

    def _handle_get_status(self, device):
        running_sessions = db.list_agent_sessions(limit=50, status="running")
        running_agents = []
        seen_agents = set()
        for sess in running_sessions:
            aid = str(sess.get("agent_id") or "").strip()
            if not aid or aid in seen_agents:
                continue
            seen_agents.add(aid)
            ag = agents.get_agent(aid)
            running_agents.append(ag["name"] if ag else aid)

        _, _, _, checker, repo_patterns = _build_confidential_guard()
        running_jobs = [
            "Confidential job" if _text_is_confidential(j["name"], checker, repo_patterns) else j["name"]
            for j in db.list_jobs(limit=100)
            if j.get("status") == "running" and j.get("name")
        ]
        unread = db.count_unread_notifications()
        self._send_json({
            "ok": True,
            "version": ENGINE_VERSION,
            "running_agents": running_agents,
            "running_jobs": running_jobs,
            "unread_notifications": unread,
            "device": {"id": device["id"], "name": device["name"]},
        })

    def _handle_get_drafts(self, qs):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        try:
            limit = int((qs.get("limit") or ["20"])[0])
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 100))

        items = db.list_content_items(limit=limit)
        out = []
        for entry in items:
            full = db.get_content_item(entry["id"], include_versions=True) or entry
            if _content_item_is_confidential(
                full, conf_sids, conf_cids, conf_pids, checker, repo_patterns
            ):
                out.append(_stub_content_item(full, detailed=False))
            else:
                out.append(entry)
        self._send_json({"drafts": out})

    def _handle_get_draft_detail(self, content_id):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        item = db.get_content_item(content_id, include_versions=True)
        if item is None:
            self._send_json({"error": "Draft not found"}, status=404)
            return
        if _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            self._send_json(_stub_content_item(item, detailed=True))
            return

        payload = review_studio.get_review(content_id)
        payload["comments"] = [
            _safe_comment(cmt, checker, repo_patterns) for cmt in payload.get("comments") or []
        ]
        # Suggestions, findings and notes can name what the item itself doesn't.
        others = {k: v for k, v in payload.items() if k not in ("comments", "product")}
        if payload.get("confidential") or _text_is_confidential(
            json.dumps(others, ensure_ascii=False, default=str), checker, repo_patterns
        ):
            self._send_json(_stub_content_item(item, detailed=True))
            return
        self._send_json(payload)

    def _handle_get_file(self, qs):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        content_id = str((qs.get("content_id") or [""])[0] or "").strip()
        req_path = str((qs.get("path") or [""])[0] or "").strip()
        if not content_id or not req_path:
            raise PhoneApiError("content_id and path query parameters are required", status=400)

        item = db.get_content_item(content_id, include_versions=True)
        if item is None:
            raise PhoneApiError("Content item not found", status=404)
        if _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            raise PhoneApiError("Confidential files are not available on phone", status=403)

        if (
            os.path.isabs(req_path)
            or req_path.startswith(("/", "\\"))
            or (len(req_path) >= 2 and req_path[1] == ":" and req_path[0].isalpha())
        ):
            raise PhoneApiError("Invalid file path", status=403)
        raw_parts = [p for p in re.split(r"[\\/]+", req_path) if p]
        if not raw_parts or any(p in (".", "..") for p in raw_parts):
            raise PhoneApiError("Invalid file path", status=403)

        norm_req = os.path.normpath(req_path).replace("\\", "/")
        allowed_paths = set()
        for entry in item.get("files") or []:
            p = entry.get("path") if isinstance(entry, dict) else (entry if isinstance(entry, str) else "")
            if p:
                allowed_paths.add(os.path.normpath(p).replace("\\", "/"))
        if norm_req not in allowed_paths:
            raise PhoneApiError("File is not listed on this content item", status=403)

        full_path = file_serving.servable_path(os.path.join(paths.DATA_ROOT, norm_req))
        if not full_path:
            raise PhoneApiError("File not found", status=404)

        if _file_is_confidential(full_path, checker, repo_patterns):
            raise PhoneApiError("Confidential files are not available on phone", status=403)
        size = os.path.getsize(full_path)
        if size > MAX_FILE_BYTES:
            raise PhoneApiError("File exceeds 200 MB limit", status=413)

        mime, _ = mimetypes.guess_type(full_path)
        mime = mime or "application/octet-stream"
        headers = file_serving.safe_headers(full_path, mime)
        headers["Cache-Control"] = "no-store"
        headers["X-Content-Type-Options"] = "nosniff"

        self.send_response(200)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(size))
        self.end_headers()

        with open(full_path, "rb") as f:
            while True:
                chunk = f.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _handle_get_agents(self):
        roster = agent_chat.get_roster_status()
        safe_agents = [
            {
                "id": ag.get("id"),
                "name": ag.get("name"),
                "code": ag.get("code"),
                "role": ag.get("role"),
                "description": ag.get("description"),
                "status": ag.get("status"),
                "message_count": ag.get("message_count", 0),
            }
            for ag in roster
        ]
        self._send_json({"agents": safe_agents})

    def _handle_get_summary(self):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        del conf_sids, conf_pids
        summaries = weekly_summary.list_summaries(limit=1)
        if not summaries:
            self._send_json({"summary": None})
            return
        latest = summaries[0]
        if _summary_is_confidential(latest, conf_cids, checker, repo_patterns):
            self._send_json({"summary": _stub_summary(latest)})
            return
        self._send_json({"summary": latest})

    def _handle_get_reminders(self, qs):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        del conf_sids, conf_pids
        status_filter = (qs.get("status") or ["open"])[0] or "open"
        items = reminders.list_reminders(status=status_filter)
        safe = [
            _stub_reminder(r) if _reminder_is_confidential(r, conf_cids, checker, repo_patterns) else r
            for r in items
        ]
        self._send_json({"reminders": safe})

    def _handle_get_notifications(self, qs):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        del conf_sids, conf_pids
        try:
            limit = int((qs.get("limit") or ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        res = notifications.list_notifications(limit=limit)
        safe_notifs = [
            _stub_notification(n) if _notification_is_confidential(n, conf_cids, checker, repo_patterns) else n
            for n in res.get("notifications") or []
        ]
        self._send_json({
            "notifications": safe_notifs,
            "unread_count": int(res.get("unread_count") or 0),
        })

    def _handle_get_results_pending(self):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        proposals = results_extract.list_proposals(status="pending", limit=100)
        safe = [
            _stub_proposal(p)
            if _proposal_is_confidential(p, conf_cids, conf_sids, conf_pids, checker, repo_patterns)
            else p
            for p in proposals
        ]
        self._send_json({"proposals": safe})

    # --- POST handlers ---

    def _handle_post_draft_comment(self, device, content_id, params):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        item = db.get_content_item(content_id, include_versions=True)
        if item is None:
            raise PhoneApiError("Draft not found", status=404)
        if _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            raise PhoneApiError("Confidential items cannot be modified from phone", status=403)

        body = params.get("body")
        version = params.get("version")
        created = review_studio.create_comment(
            content_id,
            kind="general",
            version=version,
            body=body if isinstance(body, str) else "",
            author=f"phone:{device['name']}",
        )
        _log_device_activity(device, "review", f"phone_comment:{content_id}", content_id)
        cloud_sync.request_sync()
        self._send_json({"comment": created})

    def _handle_post_draft_posted(self, device, content_id, params):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        item = db.get_content_item(content_id, include_versions=True)
        if item is None:
            raise PhoneApiError("Draft not found", status=404)
        if _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            raise PhoneApiError("Confidential items cannot be modified from phone", status=403)

        raw_url = params.get("url")
        if raw_url is not None and str(raw_url).strip() and not _is_valid_http_url(raw_url):
            raise PhoneApiError("url must be a valid http or https URL", status=400)

        updated = review_studio.mark_posted(
            content_id,
            url=str(raw_url).strip() if raw_url is not None and str(raw_url).strip() else None,
        )
        _log_device_activity(device, "content", f"phone_posted:{content_id}", content_id)
        cloud_sync.request_sync()
        self._send_json({"item": updated})

    def _handle_post_inbox(self, device, params):
        _build_confidential_guard()
        raw_items = params.get("items")
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            raise PhoneApiError("items must be a non-empty array", status=400)
        if len(raw_items) > MAX_INBOX_ITEMS:
            raise PhoneApiError(f"At most {MAX_INBOX_ITEMS} inbox items allowed per request", status=400)

        validated = []
        for idx, entry in enumerate(raw_items):
            if not isinstance(entry, dict):
                raise PhoneApiError(f"items[{idx}] must be an object", status=400)
            client_id = str(entry.get("client_id") or "").strip()
            if not client_id or len(client_id) > 128:
                raise PhoneApiError(f"items[{idx}].client_id is required (max 128 chars)", status=400)
            kind = str(entry.get("kind") or "").strip().lower()
            if kind not in ("draft", "note", "idea"):
                raise PhoneApiError(f"items[{idx}].kind must be draft, note, or idea", status=400)
            title = str(entry.get("title") or "").strip()
            body = entry.get("body")
            if not title:
                raise PhoneApiError(f"items[{idx}].title is required", status=400)
            if not isinstance(body, str):
                raise PhoneApiError(f"items[{idx}].body must be a string", status=400)
            raw_url = entry.get("url")
            clean_url = None
            if raw_url is not None and str(raw_url).strip():
                if not _is_valid_http_url(raw_url):
                    raise PhoneApiError(f"items[{idx}].url must be a valid http or https URL", status=400)
                clean_url = str(raw_url).strip()
            platform = str(entry.get("platform") or "").strip() or None
            validated.append({
                "client_id": client_id,
                "kind": kind,
                "title": title,
                "body": body,
                "platform": platform,
                "url": clean_url,
                "created_at": entry.get("created_at"),
            })

        ctx = SimpleNamespace(agent_id=f"phone:{device['name']}", session_id=None)
        results = []
        created_any = False

        for item in validated:
            existing = db.get_phone_inbox_receipt(device["id"], item["client_id"])
            if existing is not None:
                results.append({
                    "client_id": item["client_id"],
                    "kind": existing["kind"],
                    "id": existing["target_id"],
                    "deduplicated": True,
                })
                continue

            if item["kind"] == "draft":
                target_platform = platforms.normalize_platform(item["platform"] or "linkedin") or "linkedin"
                res = content_tools.write_draft(
                    ctx,
                    type="post",
                    platform=target_platform,
                    title=item["title"],
                    body=item["body"],
                    notes=f"Created from phone ({device['name']})",
                )
                if isinstance(res, dict) and res.get("error"):
                    raise PhoneApiError(res["error"], status=400)
                cid = res["content_id"]
                auto_rev = review.auto_review(cid)
                db.save_phone_inbox_receipt(device["id"], item["client_id"], "draft", cid)
                _log_device_activity(device, "inbox", "phone_inbox:draft", cid)
                created_any = True
                results.append({
                    "client_id": item["client_id"],
                    "kind": "draft",
                    "id": cid,
                    "deduplicated": False,
                    "warnings": res.get("warnings") or [],
                    "review": auto_rev,
                })
            else:
                body_text = item["body"]
                if item["url"] and item["url"] not in body_text:
                    body_text = f"{body_text}\n\nSource: {item['url']}".strip()
                saved = core_tools.save_research_note(
                    ctx,
                    title=item["title"],
                    body=body_text,
                    url=item["url"],
                    source=f"phone:{item['kind']}",
                )
                note_id = saved["id"]
                rule_warnings = list(
                    product_rules.check_text(f"{item['title']}\n{body_text}", path=_rules_path())
                )
                db.save_phone_inbox_receipt(device["id"], item["client_id"], item["kind"], note_id)
                _log_device_activity(device, "inbox", f"phone_inbox:{item['kind']}", note_id)
                created_any = True
                results.append({
                    "client_id": item["client_id"],
                    "kind": item["kind"],
                    "id": note_id,
                    "deduplicated": False,
                    "warnings": rule_warnings,
                })

        if created_any:
            cloud_sync.request_sync()

        self._send_json({"items": results})

    def _handle_post_agent_message(self, device, agent_id, params):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        del conf_cids, conf_pids

        ag = agents.get_agent(agent_id)
        if ag is None:
            raise PhoneApiError(f"Agent '{agent_id}' not found", status=404)

        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PhoneApiError("text is required", status=400)
        if len(text) > MAX_AGENT_MESSAGE_CHARS:
            raise PhoneApiError(
                f"text must be at most {MAX_AGENT_MESSAGE_CHARS} characters",
                status=400,
            )

        if not self.server.check_agent_rate_limit(device["id"]):
            raise PhoneApiError(
                "Rate limit exceeded: at most 20 messages per 10 minutes",
                status=429,
                code="rate_limited",
            )

        canon_id = ag["id"]
        waiting_sess = db.get_latest_waiting_session(canon_id, top_level_only=True)
        pre_confidential = (
            _text_is_confidential(text, checker, repo_patterns)
            or (waiting_sess is not None and (bool(waiting_sess.get("confidential")) or waiting_sess["id"] in conf_sids))
        )

        res = agent_chat.chat_with_agent(canon_id, text)
        _log_device_activity(
            device,
            canon_id,
            f"phone_agent_message:{canon_id}",
            res.get("session_id") or "",
        )
        cloud_sync.request_sync()

        if not res.get("success"):
            self._send_json(
                {
                    "success": False,
                    "error": res.get("error") or "Agent request failed",
                    "error_code": res.get("error_code"),
                },
                status=502 if res.get("error_code") == "ai_not_connected" else 400,
            )
            return

        sess_id = res.get("session_id")
        sess_obj = db.get_agent_session(sess_id) if sess_id else None
        reply_text = str(res.get("response") or "")
        is_conf = (
            pre_confidential
            or bool(sess_obj and sess_obj.get("confidential"))
            or _chat_read_confidential(sess_obj, checker, repo_patterns)
            or _text_is_confidential(reply_text, checker, repo_patterns)
            or _text_is_confidential(
                json.dumps([res.get("question"), res.get("options")], ensure_ascii=False, default=str),
                checker,
                repo_patterns,
            )
        )

        if is_conf:
            self._send_json({
                "success": True,
                "confidential": True,
                "waiting_user": bool(res.get("waiting_user")),
                "session_id": sess_id,
                "agent": {"id": ag["id"], "name": ag["name"]},
                "response": "Confidential chat: open Magnm Studio on your laptop to read this reply.",
            })
            return

        out = {
            "success": True,
            "confidential": False,
            "waiting_user": bool(res.get("waiting_user")),
            "session_id": sess_id,
            "agent": {"id": ag["id"], "name": ag["name"]},
            "response": reply_text,
        }
        if res.get("waiting_user"):
            out["question"] = res.get("question") or ""
            out["options"] = res.get("options") or []
        self._send_json(out)

    def _handle_post_reminder_done(self, device, reminder_id):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        del conf_sids, conf_pids
        existing = db.get_reminder(reminder_id)
        if existing is None:
            raise PhoneApiError("Reminder not found", status=404)
        updated = reminders.mark_done(reminder_id)
        _log_device_activity(device, "reminders", f"phone_reminder_done:{reminder_id}", reminder_id)
        cloud_sync.request_sync()
        if _reminder_is_confidential(updated, conf_cids, checker, repo_patterns):
            self._send_json({"reminder": _stub_reminder(updated)})
            return
        self._send_json({"reminder": updated})

    def _handle_post_notifications_read(self, device, params):
        _build_confidential_guard()
        mark_all = bool(params.get("all") is True)
        ids = params.get("ids")
        res = notifications.mark_read(ids=ids, mark_all=mark_all)
        detail = "all" if mark_all else f"count={len(ids) if isinstance(ids, list) else 0}"
        _log_device_activity(device, "notifications", "phone_notifications_read", detail)
        self._send_json(res)

    def _handle_post_results_extract(self, device, params):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        raw_cid = params.get("content_id")
        clean_cid = str(raw_cid).strip() if raw_cid and str(raw_cid).strip() else None
        if clean_cid:
            item = db.get_content_item(clean_cid, include_versions=True)
            if item is None:
                raise PhoneApiError(f"Content item '{clean_cid}' not found", status=404)
            if _content_item_is_confidential(
                item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
            ):
                raise PhoneApiError("Confidential items cannot be modified from phone", status=403)

        b64_raw = params.get("image_base64") or params.get("content_base64") or params.get("image") or ""
        if not isinstance(b64_raw, str) or not b64_raw.strip():
            raise PhoneApiError("image_base64 is required", status=400)
        b64_clean = b64_raw.strip()
        if b64_clean.startswith("data:") and "," in b64_clean:
            b64_clean = b64_clean.split(",", 1)[1].strip()
        try:
            image_bytes = base64.b64decode(b64_clean, validate=True)
        except Exception as exc:
            raise PhoneApiError("Invalid base64 image payload", status=400) from exc

        platform = params.get("platform")
        res = results_extract.extract_from_image(
            image_bytes,
            content_id=clean_cid,
            platform=platform,
        )
        prop = res.get("proposal") or {}
        _log_device_activity(
            device,
            "analyst",
            f"phone_results_extract:{prop.get('id') or ''}",
            clean_cid or prop.get("id") or "",
        )
        if prop and (
            _proposal_is_confidential(prop, conf_cids, conf_sids, conf_pids, checker, repo_patterns)
            or _text_is_confidential(json.dumps(res, ensure_ascii=False, default=str), checker, repo_patterns)
        ):
            self._send_json({"proposal": _stub_proposal(prop)})
            return
        self._send_json(res)

    def _handle_post_results_confirm(self, device, proposal_id, params):
        conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()
        prop = db.get_metric_proposal(proposal_id)
        if prop is None:
            raise PhoneApiError("Metric proposal not found", status=404)
        if _proposal_is_confidential(prop, conf_cids, conf_sids, conf_pids, checker, repo_patterns):
            raise PhoneApiError("Confidential results cannot be confirmed from phone", status=403)
        if "content_id" in params and params.get("content_id"):
            target_cid = str(params["content_id"]).strip()
            target_item = db.get_content_item(target_cid, include_versions=True)
            if target_item is not None and _content_item_is_confidential(
                target_item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
            ):
                raise PhoneApiError("Confidential items cannot be modified from phone", status=403)

        res = results_extract.confirm_proposal(
            proposal_id,
            metrics=params.get("metrics") if "metrics" in params else None,
            content_id=params.get("content_id") if "content_id" in params else None,
            platform=params.get("platform") if "platform" in params else None,
        )
        _log_device_activity(device, "analyst", f"phone_results_confirm:{proposal_id}", proposal_id)
        cloud_sync.request_sync()
        self._send_json(res)


class PhoneServerManager:
    """Manages lifecycle, Tailscale IP binding, and status for the phone HTTP listener."""

    def __init__(
        self,
        *,
        port=DEFAULT_PORT,
        address_resolver=None,
        allow_loopback=False,
        pairing_manager=None,
    ):
        self._lock = threading.RLock()
        self._port = int(port)
        self._address_resolver = address_resolver or resolve_tailscale_ipv4
        self._allow_loopback = bool(allow_loopback)
        self._pairing = pairing_manager or phone_pairing.get_manager()
        self._server = None
        self._thread = None
        self._last_error = None
        self._tailscale_missing = False

    def is_enabled(self):
        """Return True when the user enabled 'Allow phone connections' (off by default)."""
        raw = str(db.get_meta(PHONE_ENABLED_META_KEY, "0") or "").strip().lower()
        return raw in ("1", "true", "yes")

    def is_running(self):
        with self._lock:
            return self._server is not None

    def bound_address(self):
        with self._lock:
            if self._server is None:
                return None, None
            return self._server.bound_ip, self._server.bound_port

    def _start_locked(self):
        if self._server is not None:
            return self._server.bound_ip, self._server.bound_port

        self._last_error = None
        self._tailscale_missing = False
        try:
            raw_ip = self._address_resolver()
            bind_ip = validate_bind_ip(raw_ip, allow_loopback=self._allow_loopback)
        except PhoneServerError as exc:
            self._last_error = exc.message
            self._tailscale_missing = exc.code == "tailscale_missing"
            raise

        try:
            httpd = PhoneHTTPServer(
                (bind_ip, self._port),
                PhoneHandler,
                allow_loopback=self._allow_loopback,
                pairing_manager=self._pairing,
            )
        except OSError as exc:
            msg = f"Could not bind phone listener on {bind_ip}:{self._port}: {exc}"
            self._last_error = msg
            raise PhoneServerError(msg, status=500, code="bind_failed") from exc

        thread = threading.Thread(
            target=httpd.serve_forever,
            name="magnm-phone-server",
            daemon=True,
        )
        thread.start()
        self._server = httpd
        self._thread = thread
        return httpd.bound_ip, httpd.bound_port

    def _stop_locked(self, timeout=2.0):
        httpd = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def start_if_enabled(self):
        """Start the phone listener only if enabled in settings; never fails engine startup."""
        if not self.is_enabled():
            return False
        with self._lock:
            try:
                self._start_locked()
                return True
            except PhoneServerError:
                return False

    def enable(self):
        """Turn on phone connections after verifying and binding Tailscale IPv4."""
        with self._lock:
            host, port = self._start_locked()
            db.set_meta(PHONE_ENABLED_META_KEY, "1")
        try:
            events.publish("phone.updated", {"what": "enabled", "host": host, "port": port})
        except Exception:
            pass
        return self.status()

    def disable(self):
        """Turn off phone connections, stop listener at once, and cancel pairing."""
        with self._lock:
            db.set_meta(PHONE_ENABLED_META_KEY, "0")
            self._last_error = None
            self._tailscale_missing = False
            self._pairing.cancel_pairing()
            self._stop_locked()
        try:
            events.publish("phone.updated", {"what": "disabled"})
        except Exception:
            pass
        return self.status()

    def stop(self, timeout=2.0):
        """Stop the phone listener without modifying the persisted enabled setting."""
        with self._lock:
            self._stop_locked(timeout=timeout)

    def start_pairing(self):
        """Start a 5-minute pairing session on the active phone listener."""
        with self._lock:
            if self._server is None:
                if not self.is_enabled():
                    raise PhoneServerError(
                        "Turn on Allow phone connections first.",
                        status=400,
                        code="phone_disabled",
                    )
                self._start_locked()
            host, port = self._server.bound_ip, self._server.bound_port
        return self._pairing.start_pairing(host, port)

    def status(self):
        """Return safe phone listener, pairing, and paired devices status."""
        with self._lock:
            enabled = self.is_enabled()
            running = self._server is not None
            host = self._server.bound_ip if self._server is not None else None
            port = self._server.bound_port if self._server is not None else self._port
            last_error = self._last_error
            tailscale_missing = self._tailscale_missing
        return {
            "enabled": enabled,
            "running": running,
            "host": host,
            "port": port,
            "tailscale_missing": tailscale_missing,
            "error": last_error,
            "pairing": self._pairing.get_status(),
            "devices": phone_pairing.list_devices(),
        }


_DEFAULT_MANAGER = PhoneServerManager()


def get_manager():
    """Return the process-wide PhoneServerManager singleton."""
    return _DEFAULT_MANAGER


def start_if_enabled():
    return _DEFAULT_MANAGER.start_if_enabled()


def stop(timeout=2.0):
    _DEFAULT_MANAGER.stop(timeout=timeout)


def status():
    return _DEFAULT_MANAGER.status()


def enable():
    return _DEFAULT_MANAGER.enable()


def disable():
    return _DEFAULT_MANAGER.disable()
