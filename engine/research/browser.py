#!/usr/bin/env python3
"""Read-only Playwright Chromium research browser with SSRF route interception and live frames."""

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import os
import re
import sys
import threading
import time
import urllib.parse
import uuid

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_RESEARCH_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import events
import paths
from research import USER_AGENT, egress
from research.fetch import (
    MAX_TEXT_CHARS,
    FetchError,
    SSRFError,
    check_robots,
    validate_url,
)
from research.ratelimit import limiter

INSTALL_ERROR_MSG = (
    "Research browser not installed: run scripts/dev_setup.py (dev) "
    "or install from Settings > Setup check"
)

VIEWPORT = {"width": 1280, "height": 800}
JPEG_QUALITY = 70
NAV_TIMEOUT_MS = 15000

_browser_lock = threading.Lock()


class BrowserNotInstalledError(RuntimeError):
    """Raised when playwright or its Chromium binary is not installed."""


class BrowserCancelled(RuntimeError):
    """Raised when an active agent session is cancelled mid-browse."""


def is_available():
    """Return True if playwright.sync_api is importable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _check_cancelled(cancel_check):
    if callable(cancel_check) and cancel_check():
        raise BrowserCancelled("Session cancelled")


def _shots_dir():
    d = paths.data("cache", "browser-shots")
    os.makedirs(d, exist_ok=True)
    return d


def _profile_dir():
    d = paths.data("cache", "browser-research")
    os.makedirs(d, exist_ok=True)
    return d


def _new_shot_path():
    fname = f"shot_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.jpg"
    return os.path.join(_shots_dir(), fname)


def _publish_frame(session_id, url, screenshot_path):
    events.publish(
        "browser_frame",
        {
            "session_id": session_id,
            "url": url,
            "screenshot_path": screenshot_path,
        },
    )


def _make_route_handler():
    def _handle_route(route):
        req_url = route.request.url
        parsed = urllib.parse.urlparse(req_url)
        scheme = (parsed.scheme or "").lower()
        if scheme == "file":
            route.abort()
            return
        if scheme in ("about", "data", "blob"):
            route.continue_()
            return
        if scheme not in ("http", "https"):
            route.abort()
            return
        try:
            validate_url(req_url)
        except FetchError:
            route.abort()
            return
        route.continue_()

    return _handle_route


@contextmanager
def _open_context():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserNotInstalledError(INSTALL_ERROR_MSG) from exc

    profile = _profile_dir()
    with _browser_lock, egress.GuardProxy() as proxy:
        try:
            pw_cm = sync_playwright()
            pw = pw_cm.__enter__()
        except Exception as exc:
            raise BrowserNotInstalledError(INSTALL_ERROR_MSG) from exc

        context = None
        try:
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=profile,
                    headless=True,
                    accept_downloads=False,
                    viewport=VIEWPORT,
                    user_agent=USER_AGENT,
                    # Every connection, including redirects and WebSockets, goes through the guard.
                    # "<-loopback>" removes Chromium's built-in proxy bypass for loopback addresses.
                    proxy={"server": proxy.url, "bypass": "<-loopback>"},
                    service_workers="block",
                    args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"],
                )
            except Exception as exc:
                msg = str(exc)
                low = msg.lower()
                if (
                    "executable doesn't exist" in low
                    or "playwright install" in low
                    or "browser" in low and "not found" in low
                ):
                    raise BrowserNotInstalledError(INSTALL_ERROR_MSG) from exc
                raise BrowserNotInstalledError(f"{INSTALL_ERROR_MSG} ({msg})") from exc

            context.route("**/*", _make_route_handler())
            page = context.pages[0] if context.pages else context.new_page()
            page.on("download", lambda d: d.cancel())
            yield context, page
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            try:
                pw_cm.__exit__(None, None, None)
            except Exception:
                pass


def open_and_read(url, session_id=None, cancel_check=None, skip_robots=False):
    """Open url in headless Chromium, extract innerText, save live frame, and return page data."""
    _check_cancelled(cancel_check)
    validate_url(url)
    if not skip_robots:
        check_robots(url)
    limiter.wait(url)
    _check_cancelled(cancel_check)

    with _open_context() as (_ctx, page):
        _check_cancelled(cancel_check)
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        _check_cancelled(cancel_check)
        final_url = page.url or url
        if final_url.startswith("http://") or final_url.startswith("https://"):
            validate_url(final_url)
        else:
            raise SSRFError(f"Navigation ended on non-http(s) URL: {final_url}")

        title = (page.title() or "").strip()
        raw_text = page.inner_text("body") or ""
        text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()[:MAX_TEXT_CHARS]

        _check_cancelled(cancel_check)
        shot_path = _new_shot_path()
        page.screenshot(path=shot_path, type="jpeg", quality=JPEG_QUALITY)
        _publish_frame(session_id, final_url, shot_path)

    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "text": text,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "screenshot_path": shot_path,
        "untrusted": True,
    }


def screenshot(url, session_id=None, cancel_check=None, skip_robots=False):
    """Navigate to url at 1280x800, save a JPEG (quality 70) under cache/browser-shots, and return path."""
    _check_cancelled(cancel_check)
    validate_url(url)
    if not skip_robots:
        check_robots(url)
    limiter.wait(url)
    _check_cancelled(cancel_check)

    with _open_context() as (_ctx, page):
        _check_cancelled(cancel_check)
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        _check_cancelled(cancel_check)
        final_url = page.url or url
        if final_url.startswith("http://") or final_url.startswith("https://"):
            validate_url(final_url)
        else:
            raise SSRFError(f"Navigation ended on non-http(s) URL: {final_url}")

        _check_cancelled(cancel_check)
        shot_path = _new_shot_path()
        page.screenshot(path=shot_path, type="jpeg", quality=JPEG_QUALITY)
        _publish_frame(session_id, final_url, shot_path)

    return shot_path


def _clean_ddg_href(href):
    raw = str(href or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urllib.parse.urlparse(raw)
    if "duckduckgo.com" in (parsed.netloc or "").lower() and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg")
        if uddg and uddg[0]:
            return uddg[0]
    return raw


def search_ddg_html(query, limit=10, session_id=None, cancel_check=None):
    """Last-resort web search via DuckDuckGo HTML interface in Playwright Chromium."""
    _check_cancelled(cancel_check)
    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(str(query or ''))}"
    validate_url(search_url)
    limiter.wait(search_url)
    _check_cancelled(cancel_check)

    results = []
    with _open_context() as (_ctx, page):
        _check_cancelled(cancel_check)
        page.goto(search_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        _check_cancelled(cancel_check)
        shot_path = _new_shot_path()
        page.screenshot(path=shot_path, type="jpeg", quality=JPEG_QUALITY)
        _publish_frame(session_id, page.url or search_url, shot_path)

        items = page.evaluate(
            """() => {
                const out = [];
                const nodes = document.querySelectorAll('.result, .results_links');
                for (const el of nodes) {
                    const a = el.querySelector('a.result__a');
                    if (!a) continue;
                    const snip = el.querySelector('.result__snippet');
                    out.push({
                        title: (a.textContent || '').trim(),
                        href: a.getAttribute('href') || '',
                        snippet: snip ? (snip.textContent || '').trim() : ''
                    });
                }
                return out;
            }"""
        )

    for item in items or []:
        if not isinstance(item, dict):
            continue
        clean_url = _clean_ddg_href(item.get("href"))
        if not clean_url.startswith(("http://", "https://")):
            continue
        results.append(
            {
                "title": (item.get("title") or clean_url).strip(),
                "url": clean_url,
                "snippet": (item.get("snippet") or "").strip(),
                "source": "browser_search",
                "published_at": None,
            }
        )
        if len(results) >= int(limit):
            break

    return results
