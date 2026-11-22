"""Accept requests only from the Studio's own page or from local tools, never from other websites.

The engine listens on loopback, but every page open in the user's browser can reach loopback too:
- Host must be a loopback name, so a domain re-pointed at 127.0.0.1 (DNS rebinding) is refused.
- A browser request must come from the engine's own origin. Browsers set Origin and Sec-Fetch-Site themselves;
  page scripts cannot forge them.
- Another site may only navigate the top window to the two entry pages: "/" (desktop start, bookmarks, links)
  and "/auth/callback" (return from Google sign-in, protected by its one-time state).
- Tools such as curl or urllib send neither header and keep working (the engine token still applies to them).
"""

import re
import urllib.parse

_LOOPBACK_HOST_RE = re.compile(r"(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?", re.IGNORECASE)
_ENTRY_PAGES = ("/", "/auth/callback")


def is_trusted(method, target, headers):
    """Return True if a request with this method, target and headers may reach a route handler."""
    hosts = headers.get_all("Host") or []
    origins = headers.get_all("Origin") or []
    sites = headers.get_all("Sec-Fetch-Site") or []
    if len(hosts) > 1 or len(origins) > 1 or len(sites) > 1:
        return False

    host = hosts[0].strip() if hosts else ""
    if hosts and not _LOOPBACK_HOST_RE.fullmatch(host):
        return False

    site = sites[0].strip().lower() if sites else ""
    if site == "same-origin":
        return True  # Also covers the Vite dev proxy, which rewrites Host but not Origin.
    if site in ("cross-site", "same-site"):
        return _is_entry_navigation(method, target, headers)
    if site not in ("", "none"):
        return False

    if origins:
        return bool(host) and origins[0].strip().lower() == f"http://{host.lower()}"
    return True


def _is_entry_navigation(method, target, headers):
    if method != "GET":
        return False
    if (headers.get("Sec-Fetch-Mode") or "").lower() != "navigate":
        return False
    if (headers.get("Sec-Fetch-Dest") or "").lower() != "document":
        return False
    try:
        path = urllib.parse.urlsplit(target).path
    except ValueError:
        return False
    return path in _ENTRY_PAGES
