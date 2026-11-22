"""Read-only network, request, WebSocket, and browser-context policy plus TLS-terminating GuardProxy."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
import re
import selectors
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import urllib.parse

_SOCIAL_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_SOCIAL_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from research import egress, fetch
from social import SocialError, normalize_platform
from social.recipes import DEFAULT_APP_HOSTS

MAX_PROXY_REQ_BODY = 262144
MAX_PROXY_RESP_BODY = 32 * 1024 * 1024

CDN_HOST_SUFFIXES = {
    "instagram": (".cdninstagram.com", ".fbcdn.net"),
    "x": (".twimg.com",),
    "linkedin": (".licdn.com",),
}

# Unverified guesses for read-only GraphQL / API endpoints and operation names.
READ_GRAPHQL_PATHS = {
    "instagram": (re.compile(r"^/(?:api/)?graphql(?:/query)?/?$"),),
    "x": (
        re.compile(r"^/i/api/graphql/[A-Za-z0-9_-]+/([A-Za-z0-9_]+)/?$"),
        re.compile(r"^/graphql/?$"),
    ),
    "linkedin": (re.compile(r"^/(?:voyager/api/)?graphql/?$"),),
}

READ_OPERATION_ALLOWLIST = {
    "instagram": frozenset({
        "PolarisProfilePageContentQuery",
        "PolarisProfilePostsQuery",
        "PolarisProfilePostsTabContentQuery_connection",
        "PolarisPostActionLoadPostQuery",
        "PolarisPostCommentsQuery",
        "PolarisSearchKeywordQuery",
        "PolarisExploreGridQuery",
        "PolarisUserHoverCardContentQuery",
    }),
    "x": frozenset({
        "UserByScreenName",
        "UserTweets",
        "UserTweetsAndReplies",
        "TweetDetail",
        "SearchTimeline",
        "ExplorePage",
        "TrendingTopics",
        "ProfileSpotlightsQuery",
        "Viewer",
    }),
    "linkedin": frozenset({
        "voyagerFeedDashMainFeed",
        "voyagerFeedDashProfileUpdates",
        "voyagerSearchDashClusters",
        "voyagerIdentityDashProfiles",
        "voyagerSocialDashComments",
        "voyagerFeedDashTrending",
    }),
}

LOGIN_CHALLENGE_POST_PREFIXES = {
    "instagram": (
        "/accounts/login",
        "/api/v1/web/accounts/login",
        "/api/v1/accounts/login",
        "/challenge",
        "/checkpoint",
        "/two_factor",
        "/accounts/two_factor",
        "/consent",
    ),
    "x": (
        "/i/flow/login",
        "/i/api/1.1/onboarding/task.json",
        "/1.1/onboarding/task.json",
        "/sessions",
        "/account/login_verification",
        "/account/access",
        "/i/flow/consent",
        "/checkpoint",
        "/challenge",
        "/captcha",
    ),
    "linkedin": (
        "/checkpoint",
        "/uas/login",
        "/uas/login-submit",
        "/login",
        "/challenge",
    ),
}

_RECAPTCHA_HOSTS = frozenset({"google.com", "www.google.com", "recaptcha.net", "www.recaptcha.net"})
_CAPTCHA_STATIC_HOSTS = frozenset({"www.gstatic.com", "gstatic.com", "fonts.gstatic.com", "fonts.googleapis.com"})

_BLOCKED_PATH_PREFIXES = (
    "/compose",
    "/intent",
    "/messages",
    "/messaging",
    "/direct",
    "/dm",
    "/dms",
    "/inbox",
    "/settings",
    "/preferences",
    "/notifications",
    "/i/notifications",
    "/i/compose",
    "/i/direct",
    "/i/api/1.1/statuses/update",
    "/i/api/1.1/friendships",
    "/i/api/1.1/favorites",
    "/i/api/1.1/direct_messages",
    "/i/api/2/notifications",
    "/accounts/edit",
    "/accounts/password",
    "/accounts/privacy",
    "/accounts/activity",
    "/accounts/manage_access",
    "/api/v1/direct_v2",
    "/api/v1/media",
    "/api/v1/friendships",
    "/api/v1/web/likes",
    "/api/v1/web/comments",
    "/api/v1/web/friendships",
    "/voyager/api/messaging",
    "/voyager/api/voyagerMessaging",
    "/voyager/api/feed/likes",
    "/voyager/api/socialActions",
    "/voyager/api/growth",
    "/voyager/api/relationships",
    "/share",
    "/sharer",
    "/follow",
    "/unfollow",
    "/like",
    "/unlike",
    "/repost",
    "/retweet",
    "/bookmark",
    "/reply",
    "/upload",
    "/publish",
    "/create",
    "/edit",
    "/delete",
    "/destroy",
    "/block",
    "/mute",
    "/report",
    "/tweet",
    "/post",
    "/comment",
)

_BLOCKED_SEGMENTS = frozenset({
    "compose",
    "intent",
    "messages",
    "messaging",
    "direct",
    "dm",
    "dms",
    "inbox",
    "settings",
    "preferences",
    "notifications",
    "follow",
    "unfollow",
    "like",
    "unlike",
    "repost",
    "retweet",
    "bookmark",
    "reply",
    "upload",
    "publish",
    "destroy",
    "sharer",
    "create",
    "delete",
})

_MUTATION_RE = re.compile(r"\bmutation\b", re.IGNORECASE)
_SUBRESOURCE_SAFE_SCHEMES = frozenset({"data", "blob"})
_SUBRESOURCE_SAFE_TYPES = frozenset({"image", "font", "media", "stylesheet"})


_METHOD_OVERRIDE_HEADERS = frozenset({"x-http-method-override", "x-method-override", "x-http-method"})
_METHOD_OVERRIDE_PARAMS = frozenset({"_method", "method", "_http_method", "http_method"})


def _overrides_method(headers, raw_query):
    """Return True if a request asks the server to treat it as another method."""
    if any(str(name).lower() in _METHOD_OVERRIDE_HEADERS for name in (headers or {})):
        return True
    for name, value in urllib.parse.parse_qsl(raw_query or "", keep_blank_values=True):
        if name.lower() in _METHOD_OVERRIDE_PARAMS and value.strip().lower() not in ("get", "head"):
            return True
    return False


def _is_ambiguous_path(raw_path):
    """Return True for paths servers read differently: second encodings, backslashes, matrix parameters, dot segments."""
    decoded = urllib.parse.unquote(raw_path or "/")
    if any(ch in decoded for ch in ("%", "\\", ";")) or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in decoded):
        return True
    return any(segment in (".", "..") for segment in decoded.split("/"))


def _normalize_path(raw_path):
    unquoted = urllib.parse.unquote(raw_path or "/")
    collapsed = re.sub(r"/+", "/", unquoted)
    if not collapsed.startswith("/"):
        collapsed = "/" + collapsed
    return collapsed


def _is_blocked_path(raw_path):
    norm = _normalize_path(raw_path)
    low = norm.lower()
    segments = [s for s in low.split("/") if s]
    if any(seg in (".", "..") for seg in segments):
        return True
    for prefix in _BLOCKED_PATH_PREFIXES:
        p = prefix.lower()
        if low == p or low.startswith(p + "/"):
            return True
    if segments and segments[0] not in ("p", "reel", "tv", "posts", "in"):
        if any(seg.split(".", 1)[0] in _BLOCKED_SEGMENTS for seg in segments):
            return True
    return False


class SocialPolicy:
    """Enforces host, port, scheme, path, method, GraphQL operation, captcha, and WebSocket policy."""

    def __init__(self, platform, mode="read", platform_hosts=None):
        self.platform = normalize_platform(platform)
        self._lock = threading.Lock()
        self.mode = mode  # "read", "challenge", or "login"
        self.platform_hosts = platform_hosts or {}
        self._primary_page = None

        cfg = self.platform_hosts.get(self.platform) if isinstance(self.platform_hosts, dict) else None
        self._has_custom_hosts = bool(cfg)
        if cfg:
            app_hosts = {str(h).strip().lower() for h in (cfg.get("app_hosts") or []) if str(h).strip()}
            base = str(cfg.get("base_url") or "").strip()
            if base:
                parsed_base = urllib.parse.urlsplit(base)
                if parsed_base.netloc:
                    app_hosts.add(parsed_base.netloc.lower())
            cdn_hosts = {str(h).strip().lower() for h in (cfg.get("cdn_hosts") or []) if str(h).strip()}
            captcha_hosts = {str(h).strip().lower() for h in (cfg.get("captcha_hosts") or []) if str(h).strip()}
            self._app_hosts = frozenset(app_hosts)
            self._cdn_hosts = frozenset(cdn_hosts)
            self._captcha_hosts = frozenset(captcha_hosts)
            self._allow_http = bool(cfg.get("allow_http", False))
            ca_file = cfg.get("test_ca_file")
            self._test_ca_file = str(ca_file) if ca_file else None
            extra_auth = [str(p).strip() for p in (cfg.get("auth_post_prefixes") or ()) if str(p).strip()]
            login_p = str(cfg.get("login_path") or "").strip()
            if login_p:
                extra_auth.append(login_p if login_p.startswith("/") else "/" + login_p)
            # Test servers in unit tests use /login_submit and /solve_challenge on injected hosts only.
            extra_auth.extend(["/login_submit", "/solve_challenge"])
            self._auth_post_prefixes = tuple(LOGIN_CHALLENGE_POST_PREFIXES[self.platform]) + tuple(extra_auth)
            self._test_targets = frozenset(
                self._extract_host_port(h, default_port=80 if self._allow_http else 443)
                for h in (self._app_hosts | self._cdn_hosts | self._captcha_hosts)
            )
        else:
            self._app_hosts = DEFAULT_APP_HOSTS[self.platform]
            self._cdn_hosts = frozenset()
            self._captcha_hosts = frozenset()
            self._allow_http = False
            self._test_ca_file = None
            self._auth_post_prefixes = tuple(LOGIN_CHALLENGE_POST_PREFIXES[self.platform])
            self._test_targets = frozenset()

    @staticmethod
    def _extract_host_port(netloc, default_port=443):
        parsed = urllib.parse.urlsplit(f"//{netloc}")
        return ((parsed.hostname or "").lower(), parsed.port or default_port)

    @staticmethod
    def _split_netloc(netloc, hostname):
        nl = (netloc or "").strip().lower()
        hn = (hostname or "").strip().lower()
        port = None
        if nl:
            try:
                parsed = urllib.parse.urlsplit(f"//{nl}")
                if parsed.hostname:
                    hn = parsed.hostname.lower()
                port = parsed.port
            except ValueError:
                return nl, hn, -1
        return nl, hn, port

    def set_mode(self, mode):
        if mode not in ("read", "challenge", "login"):
            raise ValueError(f"Invalid policy mode: {mode}")
        with self._lock:
            self.mode = mode

    def get_mode(self):
        with self._lock:
            return self.mode

    @property
    def test_targets(self):
        return self._test_targets

    @property
    def test_ca_file(self):
        return self._test_ca_file

    def is_app_host(self, netloc, hostname):
        nl, hn, port = self._split_netloc(netloc, hostname)
        if port == -1 or not hn:
            return False
        if self._has_custom_hosts:
            return nl in self._app_hosts or (port is None and hn in self._app_hosts)
        if port is not None and port != 443:
            return False
        return hn in self._app_hosts

    def is_cdn_host(self, netloc, hostname):
        nl, hn, port = self._split_netloc(netloc, hostname)
        if port == -1 or not hn:
            return False
        if self._has_custom_hosts:
            return nl in self._cdn_hosts or (port is None and hn in self._cdn_hosts)
        if port is not None and port != 443:
            return False
        return any(hn == s.lstrip(".") or hn.endswith(s) for s in CDN_HOST_SUFFIXES[self.platform])

    def _captcha_kind(self, netloc, hostname):
        nl, hn, port = self._split_netloc(netloc, hostname)
        if port == -1 or not hn:
            return None
        if self._has_custom_hosts and (nl in self._captcha_hosts or (port is None and hn in self._captcha_hosts)):
            return "injected"
        if port is not None and port != 443:
            return None
        if hn == "arkoselabs.com" or hn.endswith(".arkoselabs.com"):
            return "arkose"
        if hn == "funcaptcha.com" or hn.endswith(".funcaptcha.com"):
            return "arkose"
        if hn == "hcaptcha.com" or hn.endswith(".hcaptcha.com"):
            return "hcaptcha"
        if hn in _RECAPTCHA_HOSTS:
            return "recaptcha"
        if hn in _CAPTCHA_STATIC_HOSTS:
            return "captcha_static"
        return None

    def is_allowed_host(self, netloc, hostname):
        return self.is_app_host(netloc, hostname) or self.is_cdn_host(netloc, hostname)

    def is_connect_allowed(self, netloc, hostname, port):
        """Return True if a CONNECT tunnel to (netloc, hostname, port) is permitted in current mode."""
        port_int = int(port)
        nl = f"{(hostname or '').strip().lower()}:{port_int}"
        if self._has_custom_hosts:
            if (hostname.lower(), port_int) in self._test_targets:
                if self.is_app_host(nl, hostname) or self.is_cdn_host(nl, hostname):
                    return True
                if self.get_mode() in ("login", "challenge") and self._captcha_kind(nl, hostname) is not None:
                    return True
            if port_int == 443 and self.get_mode() in ("login", "challenge"):
                return self._captcha_kind(hostname.lower(), hostname) is not None
            return False

        if port_int != 443:
            return False
        if self.is_app_host(hostname.lower(), hostname) or self.is_cdn_host(hostname.lower(), hostname):
            return True
        if self.get_mode() in ("login", "challenge"):
            return self._captcha_kind(hostname.lower(), hostname) is not None
        return False

    def _check_read_graphql_get(self, norm_path, raw_query):
        url_op = None
        matched_endpoint = False
        for pat in READ_GRAPHQL_PATHS[self.platform]:
            m = pat.fullmatch(norm_path)
            if m:
                matched_endpoint = True
                if m.lastindex:
                    url_op = m.group(1)
                break
        if not matched_endpoint:
            return False, "GraphQL endpoint not in read allowlist"

        try:
            qs = urllib.parse.parse_qs(raw_query or "", keep_blank_values=True, strict_parsing=False)
        except Exception:
            return False, "unparseable GraphQL query string"

        if any(k in qs for k in ("batch", "queries", "operations")):
            return False, "batched GraphQL query denied"

        for qtxt in qs.get("query", ()):
            if _MUTATION_RE.search(qtxt):
                return False, "GraphQL mutation denied"

        qs_ops = []
        for key in ("operationName", "fb_api_req_friendly_name"):
            vals = qs.get(key)
            if vals:
                if len(vals) != 1 or not vals[0].strip():
                    return False, "ambiguous GraphQL operation name"
                qs_ops.append(vals[0].strip())
        if len(set(qs_ops)) > 1:
            return False, "conflicting GraphQL operation names"

        qs_op = qs_ops[0] if qs_ops else None
        if url_op and qs_op and url_op != qs_op:
            return False, "GraphQL URL and query operation names do not match"

        op_name = url_op or qs_op
        if not op_name or op_name not in READ_OPERATION_ALLOWLIST[self.platform]:
            return False, f"GraphQL operation '{op_name or ''}' not in read allowlist"
        return True, "ok"

    def _matches_auth_post_prefix(self, norm_path):
        low = norm_path.lower()
        for prefix in self._auth_post_prefixes:
            p = prefix.lower()
            if not p:
                continue
            if p.endswith("/"):
                p_no_slash = p.rstrip("/")
                if low == p_no_slash or low.startswith(p):
                    return True
            elif low == p or low.startswith(p + "/"):
                return True
        return False

    def check_request(
        self,
        method,
        url,
        *,
        headers=None,
        post_data=None,
        resource_type=None,
        is_navigation=False,
    ):
        """Return (allowed: bool, reason: str) for an HTTP/HTTPS request."""
        raw_url = str(url or "").strip()
        if not raw_url:
            return False, "empty URL"

        if raw_url == "about:blank":
            return True, "about:blank"

        try:
            parsed = urllib.parse.urlsplit(raw_url)
        except ValueError:
            return False, "unparseable URL"

        scheme = (parsed.scheme or "").lower()
        if scheme in _SUBRESOURCE_SAFE_SCHEMES:
            if not is_navigation and (resource_type or "").lower() in _SUBRESOURCE_SAFE_TYPES:
                return True, f"safe subresource {scheme}"
            return False, f"disallowed {scheme} request"

        allowed_schemes = ("https", "http") if self._allow_http else ("https",)
        if scheme not in allowed_schemes:
            return False, f"disallowed scheme '{scheme}'"

        if parsed.username or parsed.password:
            return False, "URL credentials disallowed"

        netloc = (parsed.netloc or "").lower()
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False, "missing hostname"

        try:
            port = parsed.port
        except ValueError:
            return False, "invalid port"

        # Block WebSocket upgrades on HTTP layer too.
        if headers:
            for k, v in headers.items():
                kl = str(k).lower()
                if kl == "upgrade":
                    return False, "HTTP upgrade blocked"
                if kl == "connection" and "upgrade" in str(v or "").lower():
                    return False, "HTTP connection upgrade blocked"

        mode = self.get_mode()
        meth = str(method or "GET").strip().upper()
        path = parsed.path or "/"
        norm_path = _normalize_path(path)
        rtype = (resource_type or "").lower()

        if _is_ambiguous_path(path):
            return False, "ambiguous path"
        if _overrides_method(headers, parsed.query):
            return False, "method override"

        captcha_kind = self._captcha_kind(netloc, hostname)
        if captcha_kind is not None:
            if mode not in ("login", "challenge"):
                return False, "captcha provider blocked in read mode"
            if captcha_kind == "recaptcha":
                low_p = norm_path.lower()
                if not (low_p == "/recaptcha" or low_p.startswith("/recaptcha/")):
                    return False, "only /recaptcha path allowed on recaptcha host"
            if captcha_kind == "captcha_static":
                if meth not in ("GET", "HEAD") or is_navigation or rtype in ("document", "iframe", "frame"):
                    return False, "captcha static hosts allow GET/HEAD subresources only"
                return True, "ok"
            if meth in ("GET", "HEAD", "POST"):
                return True, "ok"
            return False, f"blocked method '{meth}' for captcha host"

        if is_navigation or rtype in ("document", "iframe", "frame", "fencedframe"):
            if not self.is_app_host(netloc, hostname):
                return False, "off-platform navigation blocked"
        else:
            if not self.is_allowed_host(netloc, hostname):
                return False, "disallowed host"

        if _is_blocked_path(path):
            return False, "blocked path"

        if mode == "read":
            if meth not in ("GET", "HEAD"):
                return False, f"blocked HTTP method '{meth}'"
            if "graphql" in norm_path.lower():
                return self._check_read_graphql_get(norm_path, parsed.query)
            return True, "ok"

        # In login or challenge mode: GET/HEAD allowed (with GraphQL read check);
        # POST only to small per-platform sign-in/checkpoint prefix list (never GraphQL or write paths).
        if meth in ("GET", "HEAD"):
            if "graphql" in norm_path.lower():
                return self._check_read_graphql_get(norm_path, parsed.query)
            return True, "ok"
        if meth == "POST":
            if not self.is_app_host(netloc, hostname):
                return False, "POST only allowed to app host"
            if "graphql" in norm_path.lower():
                return False, "GraphQL POST denied"
            if self._matches_auth_post_prefix(norm_path):
                return True, "ok"
            return False, "POST path not in auth allowlist"
        return False, f"blocked method '{meth}' in {mode} mode"

    def check_navigation_url(self, url):
        """Validate a top-level page URL after navigation or redirect."""
        raw_url = str(url or "").strip()
        if raw_url == "about:blank":
            return
        try:
            parsed = urllib.parse.urlsplit(raw_url)
        except ValueError as exc:
            raise SocialError("Navigation blocked by policy.", status=403, code="policy_blocked") from exc
        if not parsed.hostname or not self.is_app_host(parsed.netloc, parsed.hostname):
            raise SocialError("Navigation blocked by policy.", status=403, code="policy_blocked")
        ok, _reason = self.check_request("GET", raw_url, is_navigation=True, resource_type="document")
        if not ok:
            raise SocialError("Navigation blocked by policy.", status=403, code="policy_blocked")

    def _make_route_handler(self):
        def _handle_route(route):
            req = route.request
            try:
                headers = req.headers
            except Exception:
                headers = {}
            try:
                post_data = req.post_data_buffer
                if post_data is None:
                    post_data = req.post_data
            except Exception:
                post_data = None
            allowed, _reason = self.check_request(
                req.method,
                req.url,
                headers=headers,
                post_data=post_data,
                resource_type=getattr(req, "resource_type", None),
                is_navigation=bool(req.is_navigation_request()),
            )
            if not allowed:
                try:
                    route.abort("blockedbyclient")
                except Exception:
                    pass
                return
            try:
                route.continue_()
            except Exception:
                pass

        return _handle_route

    def install_on_context(self, context):
        """Install context-level default-deny popup routing, WebSocket block, and permission clearing."""
        try:
            context.clear_permissions()
        except Exception:
            pass

        def _abort_popup_or_orphan(route):
            if self._primary_page is not None:
                try:
                    if route.request.frame.page is self._primary_page:
                        return self._make_route_handler()(route)
                except Exception:
                    pass
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass

        context.route("**/*", _abort_popup_or_orphan)
        context.route_web_socket("**/*", lambda ws: ws.close(code=1008, reason="WebSockets blocked"))

    def attach_page_guards(self, context, primary_page):
        """Bind policy routing to primary_page and block popups, downloads, dialogs, and file choosers."""
        self._primary_page = primary_page
        primary_page.route("**/*", self._make_route_handler())
        primary_page.route_web_socket("**/*", lambda ws: ws.close(code=1008, reason="WebSockets blocked"))

        def _close_extra_page(new_page):
            if new_page is not primary_page:
                try:
                    new_page.close()
                except Exception:
                    pass

        context.on("page", _close_extra_page)
        primary_page.on("popup", lambda p: _close_extra_page(p))
        primary_page.on("download", lambda d: d.cancel())
        primary_page.on("dialog", lambda d: d.dismiss())
        primary_page.on("filechooser", lambda fc: fc.set_files([]))


def _import_cryptography():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        return x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID
    except ImportError as exc:
        raise SocialError(
            "Social proxy TLS termination requires the 'cryptography' package (cryptography==50.0.2).",
            status=503,
            code="missing_cryptography",
        ) from exc


class _HttpParseError(Exception):
    def __init__(self, status_code, reason="Bad Request"):
        super().__init__(reason)
        self.status_code = int(status_code)
        self.reason = reason


class _BufferedStream:
    """Strict buffered socket reader preserving unparsed pipelined bytes across requests."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def has_buffered_or_pending(self):
        if self.buf:
            return True
        pending_fn = getattr(self.sock, "pending", None)
        if callable(pending_fn):
            try:
                return pending_fn() > 0
            except Exception:
                return False
        return False

    def read_until(self, delimiter, max_bytes):
        while True:
            idx = self.buf.find(delimiter)
            if idx != -1:
                end = idx + len(delimiter)
                out = bytes(self.buf[:end])
                del self.buf[:end]
                return out
            if len(self.buf) > max_bytes:
                raise _HttpParseError(400, "Bad Request")
            chunk = self.sock.recv(65536)
            if not chunk:
                if not self.buf:
                    return None
                raise _HttpParseError(400, "Bad Request")
            self.buf.extend(chunk)

    def read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise _HttpParseError(400, "Bad Request")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_line(self, max_bytes=8192):
        line = self.read_until(b"\r\n", max_bytes)
        if line is None:
            raise _HttpParseError(400, "Bad Request")
        return line[:-2]


_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_METHOD_RE = re.compile(r"^[A-Z]+$")
_DIGITS_RE = re.compile(r"^[0-9]+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_CTL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _read_chunked_body(stream, max_bytes, *, allow_trailers=False):
    chunks = []
    total = 0
    while True:
        size_raw = stream.read_line(max_bytes=1024)
        try:
            size_str = size_raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _HttpParseError(400, "Bad Request") from exc
        if not _HEX_RE.fullmatch(size_str):
            raise _HttpParseError(400, "Bad Request")
        chunk_size = int(size_str, 16)
        if chunk_size == 0:
            while True:
                trailer = stream.read_line(max_bytes=4096)
                if trailer == b"":
                    break
                if not allow_trailers:
                    raise _HttpParseError(400, "Bad Request")
            break
        total += chunk_size
        if total > max_bytes:
            raise _HttpParseError(413, "Payload Too Large")
        chunks.append(stream.read_exact(chunk_size))
        if stream.read_exact(2) != b"\r\n":
            raise _HttpParseError(400, "Bad Request")
    return b"".join(chunks)


def _read_http_request(stream, connect_hostname, connect_port, scheme):
    """Parse one HTTP/1.x request strictly; return dict or None on clean EOF."""
    head_bytes = stream.read_until(b"\r\n\r\n", egress.MAX_HEADER_BYTES)
    if head_bytes is None:
        return None

    head_body = head_bytes[:-4]
    raw_lines = head_body.split(b"\r\n")
    if not raw_lines:
        raise _HttpParseError(400, "Bad Request")
    for ln in raw_lines:
        if b"\n" in ln or b"\r" in ln:
            raise _HttpParseError(400, "Bad Request")

    try:
        req_line = raw_lines[0].decode("latin-1")
    except UnicodeDecodeError as exc:
        raise _HttpParseError(400, "Bad Request") from exc
    if _CTL_RE.search(req_line):
        raise _HttpParseError(400, "Bad Request")

    parts = req_line.split(" ")
    if len(parts) != 3 or any(not p for p in parts):
        raise _HttpParseError(400, "Bad Request")
    method, target, version = parts
    if not _METHOD_RE.fullmatch(method) or version not in ("HTTP/1.1", "HTTP/1.0"):
        raise _HttpParseError(400, "Bad Request")
    if "#" in target or any(ch.isspace() for ch in target):
        raise _HttpParseError(400, "Bad Request")

    if target.startswith("/"):
        path_and_query = target
    else:
        try:
            t_parsed = urllib.parse.urlsplit(target)
        except ValueError as exc:
            raise _HttpParseError(400, "Bad Request") from exc
        t_scheme = (t_parsed.scheme or "").lower()
        t_host = (t_parsed.hostname or "").lower()
        try:
            t_port = t_parsed.port or (443 if t_scheme == "https" else 80)
        except ValueError as exc:
            raise _HttpParseError(400, "Bad Request") from exc
        if t_scheme != scheme or t_host != connect_hostname or t_port != connect_port:
            raise _HttpParseError(403, "Forbidden")
        path_and_query = urllib.parse.urlunsplit(("", "", t_parsed.path or "/", t_parsed.query, ""))

    headers_list = []
    headers_map = {}
    host_vals = []
    cl_vals = []
    te_vals = []
    upgrade_vals = []
    conn_vals = []

    for raw_hdr in raw_lines[1:]:
        line = raw_hdr.decode("latin-1")
        if not line or line[0] in (" ", "\t") or _CTL_RE.search(line) or ":" not in line:
            raise _HttpParseError(400, "Bad Request")
        name, val = line.split(":", 1)
        if not _TOKEN_RE.fullmatch(name):
            raise _HttpParseError(400, "Bad Request")
        val_clean = val.strip(" \t")
        name_l = name.lower()
        headers_list.append((name, val_clean))
        headers_map[name_l] = val_clean
        if name_l == "host":
            host_vals.append(val_clean)
        elif name_l == "content-length":
            cl_vals.append(val_clean)
        elif name_l == "transfer-encoding":
            te_vals.append(val_clean)
        elif name_l == "upgrade":
            upgrade_vals.append(val_clean)
        elif name_l == "connection":
            conn_vals.append(val_clean)

    if version == "HTTP/1.1" and len(host_vals) != 1:
        raise _HttpParseError(400, "Bad Request")
    if len(host_vals) > 1:
        raise _HttpParseError(400, "Bad Request")
    if host_vals:
        hv = host_vals[0]
        if not hv or "/" in hv or "@" in hv:
            raise _HttpParseError(400, "Bad Request")
        try:
            hp = urllib.parse.urlsplit(f"//{hv}")
            h_host = (hp.hostname or "").lower()
            h_port = hp.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise _HttpParseError(400, "Bad Request") from exc
        if h_host != connect_hostname or h_port != connect_port:
            raise _HttpParseError(403, "Forbidden")

    conn_tokens = {tok.strip().lower() for v in conn_vals for tok in v.split(",") if tok.strip()}
    if upgrade_vals or "upgrade" in conn_tokens:
        raise _HttpParseError(403, "Forbidden")

    if te_vals and cl_vals:
        raise _HttpParseError(400, "Bad Request")
    if len(cl_vals) > 1:
        raise _HttpParseError(400, "Bad Request")

    body = b""
    if te_vals:
        if len(te_vals) != 1 or te_vals[0].lower() != "chunked":
            raise _HttpParseError(400, "Bad Request")
        body = _read_chunked_body(stream, MAX_PROXY_REQ_BODY, allow_trailers=False)
    elif cl_vals:
        if not _DIGITS_RE.fullmatch(cl_vals[0]):
            raise _HttpParseError(400, "Bad Request")
        cl = int(cl_vals[0])
        if cl > MAX_PROXY_REQ_BODY:
            raise _HttpParseError(413, "Payload Too Large")
        if cl > 0:
            body = stream.read_exact(cl)

    if method in ("GET", "HEAD") and (len(body) > 0 or bool(te_vals)):
        raise _HttpParseError(400, "Bad Request")

    client_close = ("close" in conn_tokens) or (version == "HTTP/1.0" and "keep-alive" not in conn_tokens)
    return {
        "method": method,
        "path_and_query": path_and_query,
        "version": version,
        "headers_list": headers_list,
        "headers_map": headers_map,
        "conn_tokens": conn_tokens,
        "body": body,
        "client_close": client_close,
    }


def _relay_http_response(upstream_stream, client_sock, req_method):
    """Read one HTTP response from upstream_stream and stream it to client_sock.

    Returns True if the connection may stay open for keep-alive, False if it must close.
    """
    while True:
        head_bytes = upstream_stream.read_until(b"\r\n\r\n", egress.MAX_HEADER_BYTES)
        if head_bytes is None:
            return False

        head_body = head_bytes[:-4]
        raw_lines = head_body.split(b"\r\n")
        status_line = raw_lines[0].decode("latin-1")
        s_parts = status_line.split(" ", 2)
        if len(s_parts) < 2 or not s_parts[0].startswith("HTTP/1."):
            raise _HttpParseError(502, "Bad Gateway")
        try:
            status_code = int(s_parts[1])
        except ValueError as exc:
            raise _HttpParseError(502, "Bad Gateway") from exc

        resp_ver = s_parts[0]
        cl_vals = []
        te_vals = []
        conn_vals = []
        for raw_hdr in raw_lines[1:]:
            line = raw_hdr.decode("latin-1")
            if ":" in line:
                name, val = line.split(":", 1)
                nl = name.strip().lower()
                vc = val.strip(" \t")
                if nl == "content-length":
                    cl_vals.append(vc)
                elif nl == "transfer-encoding":
                    te_vals.append(vc)
                elif nl == "connection":
                    conn_vals.append(vc)

        client_sock.sendall(head_bytes)
        if 100 <= status_code < 200 and status_code != 101:
            continue

        conn_tokens = {tok.strip().lower() for v in conn_vals for tok in v.split(",") if tok.strip()}
        upstream_close = ("close" in conn_tokens) or (resp_ver == "HTTP/1.0" and "keep-alive" not in conn_tokens)

        if req_method == "HEAD" or status_code in (204, 304) or (100 <= status_code < 200):
            return not upstream_close

        if te_vals and "chunked" in te_vals[-1].lower():
            total = 0
            while True:
                size_raw = upstream_stream.read_line(max_bytes=1024)
                size_hex = size_raw.split(b";", 1)[0].strip()
                if not _HEX_RE.fullmatch(size_hex.decode("ascii", errors="ignore")):
                    raise _HttpParseError(502, "Bad Gateway")
                chunk_size = int(size_hex, 16)
                client_sock.sendall(size_raw + b"\r\n")
                if chunk_size == 0:
                    while True:
                        trailer = upstream_stream.read_line(max_bytes=4096)
                        client_sock.sendall(trailer + b"\r\n")
                        if trailer == b"":
                            break
                    break
                total += chunk_size
                if total > MAX_PROXY_RESP_BODY:
                    raise _HttpParseError(502, "Bad Gateway")
                rem = chunk_size
                while rem > 0:
                    take = min(rem, 65536)
                    data = upstream_stream.read_exact(take)
                    client_sock.sendall(data)
                    rem -= take
                crlf = upstream_stream.read_exact(2)
                if crlf != b"\r\n":
                    raise _HttpParseError(502, "Bad Gateway")
                client_sock.sendall(b"\r\n")
            return not upstream_close

        if cl_vals:
            if len(set(cl_vals)) != 1 or not _DIGITS_RE.fullmatch(cl_vals[0]):
                raise _HttpParseError(502, "Bad Gateway")
            cl = int(cl_vals[0])
            if cl > MAX_PROXY_RESP_BODY:
                raise _HttpParseError(502, "Bad Gateway")
            rem = cl
            while rem > 0:
                take = min(rem, 65536)
                data = upstream_stream.read_exact(take)
                client_sock.sendall(data)
                rem -= take
            return not upstream_close

        # Close-delimited response body
        total = 0
        if upstream_stream.buf:
            total += len(upstream_stream.buf)
            client_sock.sendall(bytes(upstream_stream.buf))
            upstream_stream.buf.clear()
        while True:
            chunk = upstream_stream.sock.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PROXY_RESP_BODY:
                break
            client_sock.sendall(chunk)
        return False


def _make_social_proxy_handler(policy, proxy_owner):
    test_targets = policy.test_targets
    test_ca_file = policy.test_ca_file

    class _SocialProxyHandler(socketserver.BaseRequestHandler):
        def _refuse(self, sock, status):
            try:
                sock.sendall(
                    f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii")
                )
            except OSError:
                pass

        def handle(self):
            try:
                self.request.settimeout(egress.CONNECT_TIMEOUT_S)
                self._proxy()
            except (OSError, ValueError, ssl.SSLError):
                pass

        def _connect_upstream_tls(self, hostname, port, use_tls):
            host_l = (hostname or "").lower()
            port_i = int(port)
            if test_targets and (host_l, port_i) in test_targets:
                raw_sock = socket.create_connection((host_l, port_i), egress.CONNECT_TIMEOUT_S)
                if not use_tls:
                    return raw_sock
                cafile = test_ca_file if test_ca_file else None
                ctx = ssl.create_default_context(cafile=cafile)
            else:
                if port_i != 443 or not use_tls:
                    raise fetch.SSRFError("Blocked non-443 or plaintext upstream port")
                raw_sock = egress.create_public_connection((host_l, 443))
                ctx = ssl.create_default_context()

            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.check_hostname = True
            try:
                ctx.set_alpn_protocols(["http/1.1"])
            except Exception:
                pass
            try:
                return ctx.wrap_socket(raw_sock, server_hostname=host_l)
            except Exception:
                try:
                    raw_sock.close()
                except OSError:
                    pass
                raise

        def _proxy(self):
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = self.request.recv(65536)
                if not chunk or len(buf) > egress.MAX_HEADER_BYTES:
                    return
                buf += chunk
            head, rest = buf.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            if not lines or len(lines[0].split(" ", 2)) != 3:
                return self._refuse(self.request, "400 Bad Request")
            method, target, _version = lines[0].split(" ", 2)
            if method.upper() != "CONNECT":
                return self._refuse(self.request, "403 Forbidden")
            if rest:
                return self._refuse(self.request, "400 Bad Request")

            try:
                url = urllib.parse.urlsplit("//" + target)
            except ValueError:
                return self._refuse(self.request, "400 Bad Request")
            if not url.hostname or url.username or url.password or url.path or url.query:
                return self._refuse(self.request, "400 Bad Request")

            try:
                port = int(url.port or 443)
            except ValueError:
                return self._refuse(self.request, "400 Bad Request")

            hostname = url.hostname.lower()
            netloc = f"{hostname}:{port}"
            if not policy.is_connect_allowed(netloc, hostname, port):
                return self._refuse(self.request, "403 Forbidden")

            try:
                upstream = self._connect_upstream_tls(hostname, port, use_tls=True)
            except fetch.SSRFError:
                return self._refuse(self.request, "403 Forbidden")
            except (fetch.FetchError, ssl.SSLError, OSError):
                return self._refuse(self.request, "502 Bad Gateway")

            try:
                server_ctx = proxy_owner._get_server_ssl_context(hostname)
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    client_tls = server_ctx.wrap_socket(self.request, server_side=True)
                except (ssl.SSLError, OSError):
                    return
                try:
                    self._tunnel_http(client_tls, upstream, hostname, port)
                finally:
                    try:
                        client_tls.settimeout(0.1)
                        client_tls.unwrap()
                    except Exception:
                        pass
                    try:
                        client_tls.close()
                    except OSError:
                        pass
            finally:
                try:
                    upstream.settimeout(0.1)
                    upstream.unwrap()
                except Exception:
                    pass
                try:
                    upstream.close()
                except OSError:
                    pass

        def _tunnel_http(self, client_tls, upstream_tls, hostname, port):
            client_tls.settimeout(egress.IDLE_TIMEOUT_S)
            upstream_tls.settimeout(egress.IDLE_TIMEOUT_S)
            client_stream = _BufferedStream(client_tls)
            upstream_stream = _BufferedStream(upstream_tls)
            connect_netloc = hostname if port == 443 else f"{hostname}:{port}"
            req_num = 0

            while True:
                try:
                    req = _read_http_request(client_stream, hostname, port, "https")
                except _HttpParseError as exc:
                    status_str = f"{exc.status_code} {exc.reason}"
                    self._refuse(client_tls, status_str)
                    return
                except (OSError, ssl.SSLError):
                    return

                if req is None:
                    return
                req_num += 1

                full_url = f"https://{connect_netloc}{req['path_and_query']}"
                sec_mode = req["headers_map"].get("sec-fetch-mode", "").lower()
                sec_dest = req["headers_map"].get("sec-fetch-dest", "").lower()
                is_nav = (sec_mode == "navigate") or (sec_dest in ("document", "iframe", "frame", "fencedframe"))
                rtype = sec_dest or None

                allowed, _reason = policy.check_request(
                    req["method"],
                    full_url,
                    headers=req["headers_map"],
                    post_data=req["body"] if req["body"] else None,
                    resource_type=rtype,
                    is_navigation=is_nav,
                )
                if not allowed:
                    self._refuse(client_tls, "403 Forbidden")
                    return

                hop_headers = {
                    "proxy-connection",
                    "proxy-authorization",
                    "connection",
                    "keep-alive",
                    "transfer-encoding",
                    "content-length",
                    "te",
                    "trailer",
                    "upgrade",
                } | req["conn_tokens"]

                out_headers = [
                    f"{k}: {v}" for k, v in req["headers_list"] if k.lower() not in hop_headers
                ]
                if req["body"] or req["method"] == "POST":
                    out_headers.append(f"Content-Length: {len(req['body'])}")
                if req["client_close"]:
                    out_headers.append("Connection: close")

                wire_head = "\r\n".join(
                    [f"{req['method']} {req['path_and_query']} {req['version']}", *out_headers, "", ""]
                ).encode("latin-1")

                try:
                    upstream_tls.sendall(wire_head)
                    if req["body"]:
                        upstream_tls.sendall(req["body"])
                    keep_open = _relay_http_response(upstream_stream, client_tls, req["method"])
                except _HttpParseError as exc:
                    self._refuse(client_tls, f"{exc.status_code} {exc.reason}")
                    return
                except (OSError, ssl.SSLError):
                    return

                if not keep_open or req["client_close"]:
                    return

    return _SocialProxyHandler


class SocialGuardProxy:
    """Loopback TLS-terminating proxy enforcing public-only egress and per-request social policy."""

    def __init__(self, policy):
        self.policy = policy
        self._server = None
        self._thread = None
        self.url = None
        self.spki_hash = None
        self._priv_key = None
        self._ctx_lock = threading.Lock()
        self._ssl_contexts = {}

    def _init_ephemeral_key(self):
        _x509, _hashes, serialization, ec, _eku, _oid = _import_cryptography()
        self._priv_key = ec.generate_private_key(ec.SECP256R1())
        spki_der = self._priv_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.spki_hash = base64.b64encode(hashlib.sha256(spki_der).digest()).decode("ascii")

    def _get_server_ssl_context(self, hostname):
        hostname_l = (hostname or "").strip().lower()
        with self._ctx_lock:
            existing = self._ssl_contexts.get(hostname_l)
            if existing is not None:
                return existing

            x509, hashes, serialization, _ec, ExtendedKeyUsageOID, NameOID = _import_cryptography()
            now = datetime.now(timezone.utc)
            cn = hostname_l[:64] or "localhost"
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
            try:
                ip_obj = ipaddress.ip_address(hostname_l)
                san = x509.SubjectAlternativeName([x509.IPAddress(ip_obj)])
            except ValueError:
                san = x509.SubjectAlternativeName([x509.DNSName(hostname_l)])

            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(self._priv_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(san, critical=False)
                .sign(self._priv_key, hashes.SHA256())
            )

            pw = base64.b64encode(os.urandom(32))
            cert_pem = cert.public_bytes(serialization.Encoding.PEM)
            key_pem = self._priv_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(pw),
            )

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                ctx.set_alpn_protocols(["http/1.1"])
            except Exception:
                pass

            def _sni_cb(_sock, server_name, _initial_ctx):
                if server_name and server_name.lower() != hostname_l:
                    return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
                return None

            ctx.sni_callback = _sni_cb

            c_fd, c_path = tempfile.mkstemp(prefix=".soc_c_", suffix=".pem")
            k_fd, k_path = tempfile.mkstemp(prefix=".soc_k_", suffix=".pem")
            try:
                try:
                    os.write(c_fd, cert_pem)
                finally:
                    os.close(c_fd)
                try:
                    os.write(k_fd, key_pem)
                finally:
                    os.close(k_fd)
                ctx.load_cert_chain(certfile=c_path, keyfile=k_path, password=pw)
            finally:
                for p in (c_path, k_path):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass

            self._ssl_contexts[hostname_l] = ctx
            return ctx

    def __enter__(self):
        self._init_ephemeral_key()
        handler_cls = _make_social_proxy_handler(self.policy, self)
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler_cls, bind_and_activate=False)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self._server.server_bind()
        self._server.server_activate()
        self._thread = threading.Thread(target=self._server.serve_forever, name="magnm-social-proxy", daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self

    def __exit__(self, *exc_info):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._ctx_lock:
            self._ssl_contexts.clear()
        self._priv_key = None
