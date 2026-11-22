"""X (Twitter) posting provider: OAuth 2.0 PKCE public client, token rotation, media upload, weighted 280 limit."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

_POSTING_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_POSTING_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from posting import PostingError
from posting import http_client

OAUTH_CALLBACK_HOST = "127.0.0.1"
OAUTH_CALLBACK_PORT = 47831
OAUTH_CALLBACK_PATH = "/callback"
OAUTH_TTL_S = 300.0
OAUTH_SCOPES = "tweet.read tweet.write users.read media.write offline.access"
OAUTH_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
OAUTH_TOKEN_URL = "https://api.x.com/2/oauth2/token"
USERS_ME_URL = "https://api.x.com/2/users/me"
TWEETS_URL = "https://api.x.com/2/tweets"
MEDIA_INIT_URL = "https://api.x.com/2/media/upload/initialize"

MAX_WEIGHTED_CHARS = 280
URL_WEIGHTED_LENGTH = 23
MAX_MEDIA_COUNT = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
REFRESH_LEEWAY_S = 60

_URL_RE = re.compile(r"https?://[^\s<>()\"'\]]+", re.IGNORECASE)
# Anything shaped like word.word may be auto-linked, whatever its ending. Err high.
_POSSIBLE_LINK_RE = re.compile(r"(?:https?://\S|\bwww\.\S|[\w-]\.[^\W\d_]{2,})", re.IGNORECASE)


def _is_single_weight_char(cp):
    return (
        cp <= 0x10FF
        or (0x2000 <= cp <= 0x200D)
        or (0x2010 <= cp <= 0x201F)
        or (0x2032 <= cp <= 0x2037)
    )


def weighted_length(text):
    """Compute X weighted character length (URLs count as 23 chars; CJK/wide chars weight 2)."""
    raw = str(text or "")
    total = 0
    pos = 0
    for match in _URL_RE.finditer(raw):
        start, end = match.span()
        token = match.group(0)
        trimmed = token.rstrip(".,;:!?)]}>'\"")
        end = start + len(trimmed)
        if start > pos:
            for ch in raw[pos:start]:
                total += 1 if _is_single_weight_char(ord(ch)) else 2
        if trimmed:
            total += URL_WEIGHTED_LENGTH
        pos = end
    if pos < len(raw):
        for ch in raw[pos:]:
            total += 1 if _is_single_weight_char(ord(ch)) else 2
    return total


def estimate_cost(text, *, has_media=False):
    """Estimate X pay-per-use cost ($0.015 or $0.20 if anything could be a link; media price unconfirmed)."""
    has_link = bool(_POSSIBLE_LINK_RE.search(str(text or "")))
    amount = 0.20 if has_link else 0.015
    label = "$0.20" if has_link else "$0.015"
    parts = [f"Estimated X API cost: {label}"]
    if has_link:
        parts.append("(link pricing applied; erring high)")
    if has_media:
        parts.append("media price unconfirmed")
    else:
        parts.append("media price unconfirmed if media attached")
    parts.append("prices may change")
    return {
        "estimated_usd": amount,
        "label": label,
        "has_possible_link": has_link,
        "media_price_unconfirmed": True,
        "prices_may_change": True,
        "summary": "; ".join(parts) + ".",
    }


def pkce_challenge(verifier):
    """Compute RFC 7636 S256 code challenge (base64url without padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(client_id, state, code_challenge, *, redirect_uri):
    """Build X OAuth 2.0 PKCE authorize URL."""
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{OAUTH_AUTHORIZE_URL}?{params}"


def exchange_code(client_id, code, code_verifier, *, redirect_uri, transport=None):
    """Exchange X OAuth authorization code for access_token + refresh_token (public client)."""
    body = urllib.parse.urlencode({
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    })
    _, _, raw_bytes = http_client.http_request(
        "x",
        "POST",
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body=body,
        transport=transport,
    )
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise PostingError("X token response was not valid JSON.", status=502, code="invalid_response") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise PostingError("X token response did not include access_token.", status=502, code="invalid_response")
    return data


def fetch_me(access_token, *, transport=None):
    """Fetch X user profile (/2/users/me)."""
    _, _, raw_bytes = http_client.http_request(
        "x",
        "GET",
        USERS_ME_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        transport=transport,
    )
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise PostingError("X user response was not valid JSON.", status=502, code="invalid_response") from exc
    data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    user_id = str((data or {}).get("id") or "").strip() if isinstance(data, dict) else ""
    if not user_id:
        raise PostingError("X user response did not include data.id.", status=502, code="invalid_response")
    username = str(data.get("username") or "").strip() or None
    name = str(data.get("name") or "").strip() or None
    return {
        "account_id": user_id,
        "username": username,
        "display_name": f"@{username}" if username else (name or user_id),
    }


def refresh_token_locked(vault, lock, *, now_fn=None, transport=None):
    """Under lock, refresh X token if expired/expiring and persist rotated refresh_token."""
    wall_now = now_fn or (lambda: datetime.now(timezone.utc))
    with lock:
        tok = vault.load_token("x")
        access_token = str(tok.get("access_token") or "").strip()
        refresh_tok = str(tok.get("refresh_token") or "").strip()
        client_id = str(tok.get("client_id") or "").strip()
        exp_raw = tok.get("expires_at")
        needs_refresh = False
        if exp_raw:
            try:
                exp_dt = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt <= wall_now() + timedelta(seconds=REFRESH_LEEWAY_S):
                    needs_refresh = True
            except ValueError:
                needs_refresh = True
        if not needs_refresh:
            return tok

        if not refresh_tok or not client_id:
            raise PostingError(
                "X access token expired and no refresh token is available; reconnect X in Settings.",
                status=401,
                code="token_expired",
            )

        body = urllib.parse.urlencode({
            "refresh_token": refresh_tok,
            "grant_type": "refresh_token",
            "client_id": client_id,
        })
        _, _, raw_bytes = http_client.http_request(
            "x",
            "POST",
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            body=body,
            transport=transport,
        )
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise PostingError("X refresh response was not valid JSON.", status=502, code="invalid_response") from exc

        new_access = str((data or {}).get("access_token") or "").strip()
        if not isinstance(data, dict) or not new_access:
            raise PostingError("X refresh response did not include access_token.", status=502, code="invalid_response")
        new_refresh = str(data.get("refresh_token") or refresh_tok).strip()
        try:
            ttl_s = int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            ttl_s = 7200
        now_dt = wall_now()
        new_exp_iso = (now_dt + timedelta(seconds=max(60, ttl_s))).isoformat()
        updated = dict(tok)
        updated["access_token"] = new_access
        updated["refresh_token"] = new_refresh
        updated["expires_at"] = new_exp_iso
        updated["refreshed_at"] = now_dt.isoformat()
        vault.save_token("x", updated)
        return updated


def _encode_multipart_append(segment_index, chunk_bytes, mime_type):
    boundary = f"----MagnmXBoundary{secrets.token_hex(12)}"
    lines = [
        f"--{boundary}\r\n".encode("ascii"),
        b'Content-Disposition: form-data; name="segment_index"\r\n\r\n',
        str(segment_index).encode("ascii"),
        b"\r\n",
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="media"; filename="image"\r\n'.encode("ascii"),
        f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
        bytes(chunk_bytes),
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ]
    return f"multipart/form-data; boundary={boundary}", b"".join(lines)


def upload_image(token, img_bytes, mime_type, *, transport=None):
    """Upload one image (<= 5 MB) via X v2 media initialize/append/finalize."""
    data_bytes = bytes(img_bytes)
    if len(data_bytes) > MAX_IMAGE_BYTES:
        raise PostingError("X image exceeds 5 MB limit.", status=400, code="media_too_large")

    init_body = json.dumps({
        "media_type": mime_type,
        "total_bytes": len(data_bytes),
        "media_category": "tweet_image",
    })
    _, _, init_raw = http_client.http_request(
        "x",
        "POST",
        MEDIA_INIT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=init_body,
        transport=transport,
    )
    try:
        init_json = json.loads(init_raw.decode("utf-8"))
    except Exception as exc:
        raise PostingError("X media initialize response was not valid JSON.", status=502, code="invalid_response", uncertain=True) from exc

    media_id = str(((init_json or {}).get("data") or {}).get("id") or "").strip()
    if not media_id:
        raise PostingError("X media initialize response did not include data.id.", status=502, code="invalid_response", uncertain=True)

    seg_idx = 0
    for offset in range(0, len(data_bytes), UPLOAD_CHUNK_BYTES):
        chunk = data_bytes[offset : offset + UPLOAD_CHUNK_BYTES]
        ct, form_body = _encode_multipart_append(seg_idx, chunk, mime_type)
        http_client.http_request(
            "x",
            "POST",
            f"https://api.x.com/2/media/upload/{urllib.parse.quote(media_id, safe='')}/append",
            headers={"Authorization": f"Bearer {token}", "Content-Type": ct},
            body=form_body,
            transport=transport,
        )
        seg_idx += 1

    http_client.http_request(
        "x",
        "POST",
        f"https://api.x.com/2/media/upload/{urllib.parse.quote(media_id, safe='')}/finalize",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body="{}",
        transport=transport,
    )
    return media_id


def publish_tweet(
    token,
    text,
    *,
    media_items=None,
    made_with_ai=False,
    username=None,
    transport=None,
):
    """Upload up to 4 images and POST /2/tweets. Never auto-retries."""
    wlen = weighted_length(text)
    if wlen > MAX_WEIGHTED_CHARS:
        raise PostingError(
            f"X post exceeds {MAX_WEIGHTED_CHARS} weighted characters ({wlen}).",
            status=400,
            code="text_too_long",
        )
    items = list(media_items or [])
    if len(items) > MAX_MEDIA_COUNT:
        raise PostingError(f"X supports at most {MAX_MEDIA_COUNT} images.", status=400, code="too_many_media")

    media_ids = []
    for m in items:
        mid = upload_image(token, m["bytes_data"], m["mime_type"], transport=transport)
        media_ids.append(mid)

    payload = {
        "text": text,
        "made_with_ai": bool(made_with_ai),
    }
    if media_ids:
        payload["media"] = {"media_ids": media_ids}

    status, _, resp_raw = http_client.http_request(
        "x",
        "POST",
        TWEETS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(payload, ensure_ascii=False),
        transport=transport,
    )
    if status not in (200, 201):
        raise PostingError(
            f"X returned unexpected status HTTP {status}.",
            status=502,
            code="unexpected_status",
            uncertain=True,
        )
    try:
        resp_json = json.loads(resp_raw.decode("utf-8"))
    except Exception as exc:
        raise PostingError(
            "X tweet response was not valid JSON.",
            status=502,
            code="invalid_response",
            uncertain=True,
        ) from exc

    tweet_id = str(((resp_json or {}).get("data") or {}).get("id") or "").strip() or None
    if tweet_id:
        post_url = f"https://x.com/{username}/status/{tweet_id}" if username else f"https://x.com/i/web/status/{tweet_id}"
    else:
        post_url = None
    return {
        "post_id": tweet_id,
        "post_url": post_url,
    }


class XOAuthManager:
    """One-shot loopback OAuth listener (127.0.0.1:47831/callback) active only while connecting X."""

    def __init__(self, *, callback_port=OAUTH_CALLBACK_PORT, clock=None, wall_clock=None, transport=None):
        self._port = int(callback_port)
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._transport = transport
        self._lock = threading.Lock()
        self._session = None

    def is_active(self):
        with self._lock:
            if self._session is None:
                return False
            if self._clock() > self._session["expires_mono"]:
                self._stop_locked()
                return False
            return True

    def listener_port(self):
        with self._lock:
            return self._session["bound_port"] if self._session else None

    def _stop_locked(self):
        sess = self._session
        self._session = None
        if sess is None:
            return
        timer = sess.get("timer")
        if timer is not None:
            timer.cancel()
        srv = sess.get("server")
        if srv is not None:
            threading.Thread(target=self._close_server, args=(srv,), daemon=True).start()

    @staticmethod
    def _close_server(srv):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def cancel(self):
        """Shut down any active X OAuth listener."""
        with self._lock:
            had = self._session is not None
            self._stop_locked()
            return {"ok": True, "cancelled": had}

    def start(self, client_id, on_complete):
        """Start one-shot listener on 127.0.0.1:47831/callback (5 min TTL) and return authorize_url."""
        clean_id = str(client_id or "").strip()
        if not clean_id:
            raise PostingError("X Client ID is required.", status=400, code="client_id_required")

        with self._lock:
            self._stop_locked()

            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = pkce_challenge(verifier)
            manager = self

            class _CallbackHandler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass

                def _send_html(self, code, heading, message):
                    doc = (
                        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                        f"<title>{html.escape(heading)}</title></head>"
                        f"<body><h1>{html.escape(heading)}</h1><p>{html.escape(message)}</p>"
                        "<p>You can close this tab and return to Magnm Studio.</p></body></html>"
                    ).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", str(len(doc)))
                    self.end_headers()
                    self.wfile.write(doc)

                def do_GET(self):
                    parsed = urllib.parse.urlsplit(self.path)
                    if parsed.path != OAUTH_CALLBACK_PATH:
                        self._send_html(404, "Not found", "Unknown callback path.")
                        return

                    host_vals = self.headers.get_all("Host") or []
                    expected_host = f"{OAUTH_CALLBACK_HOST}:{self.server.server_address[1]}"
                    if len(host_vals) != 1 or host_vals[0].strip() != expected_host:
                        self._send_html(400, "Invalid host", "Callback Host header rejected.")
                        return

                    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    got_state = (qs.get("state") or [""])[0]
                    got_code = (qs.get("code") or [""])[0]
                    got_error = (qs.get("error") or [""])[0]

                    with manager._lock:
                        sess = manager._session
                        if sess is None or sess.get("server") is not self.server:
                            self._send_html(410, "Expired", "OAuth session is no longer active.")
                            return
                        if manager._clock() > sess["expires_mono"]:
                            manager._stop_locked()
                            self._send_html(410, "Expired", "OAuth session expired after 5 minutes.")
                            return
                        if sess.get("state_checked"):
                            manager._stop_locked()
                            self._send_html(409, "Already used", "OAuth callback state was already checked.")
                            return
                        # Single-use state check: any callback hit on /callback consumes the state check.
                        sess["state_checked"] = True
                        expected_state = sess["state"]
                        if not got_state or not hmac.compare_digest(got_state, expected_state):
                            manager._stop_locked()
                            self._send_html(400, "Invalid state", "OAuth state did not match.")
                            return
                        if got_error or not got_code:
                            manager._stop_locked()
                            self._send_html(400, "Sign-in cancelled", "X sign-in was cancelled or denied.")
                            return
                        redirect_uri = sess["redirect_uri"]
                        flow_verifier = sess["verifier"]
                        flow_client_id = sess["client_id"]
                        manager._stop_locked()

                    try:
                        tok_data = exchange_code(
                            flow_client_id,
                            got_code,
                            flow_verifier,
                            redirect_uri=redirect_uri,
                            transport=manager._transport,
                        )
                        me = fetch_me(tok_data["access_token"], transport=manager._transport)
                        on_complete(flow_client_id, tok_data, me)
                        self._send_html(200, "X connected", f"Connected {me['display_name']} to Magnm Studio.")
                    except PostingError as exc:
                        self._send_html(400, "X sign-in failed", exc.message)
                    except Exception:
                        self._send_html(500, "X sign-in failed", "Could not complete X connection.")

                def do_POST(self):
                    self._send_html(405, "Method not allowed", "Only GET is supported.")

            try:
                srv = ThreadingHTTPServer((OAUTH_CALLBACK_HOST, self._port), _CallbackHandler)
            except OSError as exc:
                raise PostingError(
                    f"Could not bind X callback listener on {OAUTH_CALLBACK_HOST}:{self._port}.",
                    status=409,
                    code="callback_port_busy",
                ) from exc

            srv.daemon_threads = True
            bound_port = srv.server_address[1]
            redirect_uri = f"http://{OAUTH_CALLBACK_HOST}:{bound_port}{OAUTH_CALLBACK_PATH}"
            expires_mono = self._clock() + OAUTH_TTL_S
            expires_at = (self._wall_clock() + timedelta(seconds=OAUTH_TTL_S)).isoformat()

            def _expire():
                with self._lock:
                    if self._session is not None and self._session.get("server") is srv:
                        self._stop_locked()

            timer = threading.Timer(OAUTH_TTL_S, _expire)
            timer.daemon = True
            timer.start()

            self._session = {
                "server": srv,
                "bound_port": bound_port,
                "redirect_uri": redirect_uri,
                "state": state,
                "verifier": verifier,
                "client_id": clean_id,
                "expires_mono": expires_mono,
                "expires_at": expires_at,
                "state_checked": False,
                "timer": timer,
            }

            thread = threading.Thread(target=srv.serve_forever, name="magnm-x-oauth", daemon=True)
            thread.start()

            auth_url = build_authorize_url(clean_id, state, challenge, redirect_uri=redirect_uri)
            return {
                "authorize_url": auth_url,
                "redirect_uri": redirect_uri,
                "expires_at": expires_at,
                "callback_port": bound_port,
            }
