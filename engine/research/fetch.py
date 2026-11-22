#!/usr/bin/env python3
"""Safe web page reading with SSRF protection, domain blocklist, robots.txt, and HTML extraction."""

from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_RESEARCH_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from research import USER_AGENT, load_research_config
from research.ratelimit import limiter

MAX_BYTES = 2 * 1024 * 1024
ROBOTS_MAX_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 15
MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 12000
MIN_HTML_TEXT_CHARS = 400
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain"}
ROBOTS_CACHE_TTL_S = 6 * 3600

_ORIG_URLOPEN = urllib.request.urlopen

_robots_lock = threading.Lock()
_robots_cache = {}  # (scheme, netloc) -> (fetched_mono, RobotFileParser)


class FetchError(RuntimeError):
    """Base error for refused or failed page fetches."""


class SSRFError(FetchError):
    """Raised when a URL targets a disallowed scheme, host, or private/local IP."""


class DomainBlockedError(FetchError):
    """Raised when a URL's domain is in config/research.json blocked_domains."""


class RobotsBlockedError(FetchError):
    """Raised when robots.txt disallows fetching the target URL."""


def clear_caches():
    """Clear cached robots.txt parsers (useful in tests)."""
    with _robots_lock:
        _robots_cache.clear()


def _is_disallowed_ip(ip_obj):
    """Return True if ip_obj is private, loopback, link-local, multicast, reserved, or unspecified."""
    if (
        not ip_obj.is_global  # Also covers 100.64.0.0/10 (Tailscale, carrier NAT), which is not "private".
        or ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return True
    if ip_obj.version == 6:
        embedded = [ip_obj.ipv4_mapped, ip_obj.sixtofour]
        if ip_obj in _NAT64_NET:
            embedded.append(ipaddress.IPv4Address(ip_obj.packed[-4:]))
        if any(v4 is not None and _is_disallowed_ip(v4) for v4 in embedded):
            return True
    return False


def is_disallowed_address(ip_str):
    """Return True if an address string is unparsable or not a public address."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return _is_disallowed_ip(ip_obj)


_NAT64_NET = ipaddress.IPv6Network("64:ff9b::/96")


def _domain_in_blocklist(host, blocked_domains):
    """Return True if host matches or is a subdomain of any entry in blocked_domains."""
    for dom in blocked_domains or ():
        clean = str(dom).strip().lower().lstrip(".")
        if not clean:
            continue
        if host == clean or host.endswith("." + clean):
            return True
    return False


def validate_url(url, check_blocklist=True):
    """Validate scheme, host, domain blocklist, and resolved IPs against SSRF rules.

    Returns the normalized host string on success; raises SSRFError or DomainBlockedError.
    """
    raw = str(url or "").strip()
    if not raw:
        raise SSRFError("URL is required")

    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFError(f"Unsupported URL scheme '{scheme}': only http and https are allowed")

    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise SSRFError("URL must include a hostname")

    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise SSRFError(f"Blocked local host: {host}")

    if check_blocklist:
        cfg = load_research_config()
        if _domain_in_blocklist(host, cfg.get("blocked_domains")):
            raise DomainBlockedError(f"Domain is blocked by config/research.json: {host}")

    # Check literal IP address first
    try:
        ip_obj = ipaddress.ip_address(host)
        if _is_disallowed_ip(ip_obj):
            raise SSRFError(f"Blocked non-public IP address: {host}")
        return host
    except ValueError:
        pass

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        try:
            infos = socket.getaddrinfo(host, port)
        except OSError as exc:
            raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc

    if not infos:
        raise FetchError(f"DNS resolution returned no addresses for {host}")

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SSRFError(f"Invalid resolved IP '{ip_str}' for {host}") from exc
        if _is_disallowed_ip(ip_obj):
            raise SSRFError(f"Blocked private/local resolved IP {ip_str} for host {host}")

    return host


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler enforcing max redirects and SSRF validation at every hop."""

    def __init__(self, max_redirects=MAX_REDIRECTS, validate_fn=None):
        super().__init__()
        self.max_redirects = max_redirects
        self._validate_fn = validate_fn or validate_url
        self._hop_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._hop_count += 1
        if self._hop_count > self.max_redirects:
            raise FetchError(f"Too many redirects (max {self.max_redirects})")
        self._validate_fn(newurl)
        limiter.wait(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _response_header(resp, name):
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    if hasattr(headers, "get"):
        val = headers.get(name)
        if val is not None:
            return val
    try:
        target = name.lower()
        for k, v in headers.items():
            if str(k).lower() == target:
                return v
    except Exception:
        pass
    return None


def _open_url_with_redirects(url, timeout=FETCH_TIMEOUT_S, headers=None):
    """Open URL enforcing SSRF checks on initial URL and up to MAX_REDIRECTS redirects."""
    validate_url(url)
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    if urllib.request.urlopen is _ORIG_URLOPEN:
        limiter.wait(url)
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        from research import egress  # egress imports this module

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            egress.PublicHTTPHandler(),
            egress.PublicHTTPSHandler(),
            _SafeRedirectHandler(max_redirects=MAX_REDIRECTS),
        )
        resp = opener.open(req, timeout=timeout)
        final_url = resp.geturl() if hasattr(resp, "geturl") else url
        if final_url != url:
            validate_url(final_url)
        return resp, final_url

    # urllib.request.urlopen is patched (e.g., in unit tests): handle redirects explicitly
    current_url = url
    for hop in range(MAX_REDIRECTS + 1):
        validate_url(current_url)
        limiter.wait(current_url)
        req = urllib.request.Request(current_url, headers=req_headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        status = getattr(resp, "status", None)
        if status is None and hasattr(resp, "getcode"):
            status = resp.getcode()
        location = _response_header(resp, "Location")
        if status in (301, 302, 303, 307, 308) and location:
            if hop >= MAX_REDIRECTS:
                if hasattr(resp, "close"):
                    resp.close()
                raise FetchError(f"Too many redirects (max {MAX_REDIRECTS})")
            next_url = urllib.parse.urljoin(current_url, str(location).strip())
            if hasattr(resp, "close"):
                resp.close()
            current_url = next_url
            continue

        final_url = resp.geturl() if hasattr(resp, "geturl") else getattr(resp, "url", current_url)
        if final_url and final_url != current_url:
            validate_url(final_url)
        return resp, (final_url or current_url)

    raise FetchError(f"Too many redirects (max {MAX_REDIRECTS})")


def _read_bounded_bytes(resp, max_bytes=MAX_BYTES):
    """Read response body up to max_bytes, raising FetchError if exceeded."""
    cl = _response_header(resp, "Content-Length")
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                raise FetchError(f"Response exceeds size limit ({max_bytes} bytes)")
        except ValueError:
            pass
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FetchError(f"Response exceeds size limit ({max_bytes} bytes)")
    return data


def _fetch_robots_parser(scheme, netloc):
    robots_url = f"{scheme}://{netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp, _ = _open_url_with_redirects(robots_url, timeout=5, headers={"Accept": "text/plain,*/*"})
        with resp:
            raw = _read_bounded_bytes(resp, max_bytes=ROBOTS_MAX_BYTES)
        text = raw.decode("utf-8", errors="replace")
        rp.parse(text.splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            rp.disallow_all = True
        elif 400 <= exc.code < 500:
            rp.allow_all = True
        else:
            rp.allow_all = True
    except (SSRFError, DomainBlockedError):
        raise
    except Exception:
        # Unreachable or malformed robots.txt does not block reading public pages.
        rp.allow_all = True
    return rp


def check_robots(url):
    """Verify robots.txt permits USER_AGENT to fetch url; raise RobotsBlockedError if disallowed."""
    parsed = urllib.parse.urlparse(url)
    key = ((parsed.scheme or "https").lower(), (parsed.netloc or "").lower())
    now = time.monotonic()

    with _robots_lock:
        cached = _robots_cache.get(key)
        if cached is not None:
            fetched_mono, rp = cached
            if (now - fetched_mono) < ROBOTS_CACHE_TTL_S:
                if not rp.can_fetch(USER_AGENT, url):
                    raise RobotsBlockedError(f"Blocked by robots.txt for {USER_AGENT}: {url}")
                return True

    rp = _fetch_robots_parser(key[0], key[1])
    with _robots_lock:
        _robots_cache[key] = (time.monotonic(), rp)

    if not rp.can_fetch(USER_AGENT, url):
        raise RobotsBlockedError(f"Blocked by robots.txt for {USER_AGENT}: {url}")
    return True


class _ReadableHTMLParser(HTMLParser):
    """Stdlib HTML parser extracting title and readable text while dropping boilerplate tags."""

    _DROP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
    _BLOCK_TAGS = {
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "div", "section", "article", "main",
        "li", "ul", "ol", "blockquote", "pre", "tr", "br", "hr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._drop_depth = 0
        self._in_title = False
        self._title_parts = []
        self._text_parts = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self._DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth > 0:
            return
        if t == "title":
            self._in_title = True
        elif t in self._BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self._DROP_TAGS:
            if self._drop_depth > 0:
                self._drop_depth -= 1
            return
        if self._drop_depth > 0:
            return
        if t == "title":
            self._in_title = False
        elif t in self._BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_data(self, data):
        if not data or self._drop_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            self._text_parts.append(data)

    def get_result(self):
        title = re.sub(r"\s+", " ", "".join(self._title_parts)).strip()
        raw = "".join(self._text_parts)
        lines = []
        for line in raw.splitlines():
            cleaned = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        text = "\n".join(lines)
        return title, text


def extract_readable_html(html_text):
    """Extract (title, readable_text) from HTML while dropping script/style/nav/footer/header/aside."""
    parser = _ReadableHTMLParser()
    parser.feed(html_text or "")
    parser.close()
    return parser.get_result()


def _detect_charset(content_type_header):
    if not content_type_header:
        return "utf-8"
    match = re.search(r"charset=([^\s;]+)", str(content_type_header), flags=re.IGNORECASE)
    if match:
        return match.group(1).strip("\"'")
    return "utf-8"


def _parse_content_type(content_type_header):
    if not content_type_header:
        return ""
    return str(content_type_header).split(";", 1)[0].strip().lower()


def read_page(url, session_id=None, cancel_check=None):
    """Safely fetch and extract readable text from an http/https web page.

    Returns {url, final_url, title, text (<= 12,000 chars), fetched_at, untrusted: True}.
    """
    validate_url(url)
    check_robots(url)

    resp, final_url = _open_url_with_redirects(
        url,
        timeout=FETCH_TIMEOUT_S,
        headers={"Accept": "text/html, text/plain;q=0.9"},
    )
    with resp:
        validate_url(final_url)
        if final_url != url:
            check_robots(final_url)

        ct_header = _response_header(resp, "Content-Type")
        media_type = _parse_content_type(ct_header)
        if media_type and media_type not in ALLOWED_CONTENT_TYPES:
            raise FetchError(f"Unsupported content type '{media_type}': only text/html and text/plain are allowed")

        raw_bytes = _read_bounded_bytes(resp, max_bytes=MAX_BYTES)

    charset = _detect_charset(ct_header)
    try:
        decoded = raw_bytes.decode(charset, errors="replace")
    except LookupError:
        decoded = raw_bytes.decode("utf-8", errors="replace")

    if media_type == "text/plain":
        title = ""
        text = decoded.strip()
    else:
        title, text = extract_readable_html(decoded)
        if len(text.strip()) < MIN_HTML_TEXT_CHARS:
            try:
                from research import browser
                if browser.is_available():
                    rendered = browser.open_and_read(
                        final_url,
                        session_id=session_id,
                        cancel_check=cancel_check,
                        skip_robots=True,
                    )
                    if rendered and len((rendered.get("text") or "").strip()) > len(text.strip()):
                        text = rendered["text"]
                        if not title and rendered.get("title"):
                            title = rendered["title"]
            except (SSRFError, DomainBlockedError, RobotsBlockedError):
                raise
            except Exception as exc:
                if exc.__class__.__name__ == "BrowserCancelled":
                    raise
                # Fall back to extracted HTML text if Playwright fails or is unavailable.
                pass

    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "text": text[:MAX_TEXT_CHARS],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "untrusted": True,
    }
