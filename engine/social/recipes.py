"""Strict recipe validation, fixed URL templates, and content-area text extraction."""

import re
import urllib.parse

from social import SocialError, normalize_platform, normalize_recipe

MAX_ARG_CHARS = 300
MAX_QUERY_CHARS = 200
MAX_TOPIC_CHARS = 120
MAX_PAGE_TEXT_CHARS = 4500
MAX_RUN_TEXT_CHARS = 10000

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

DEFAULT_PLATFORM_ORIGINS = {
    "instagram": "https://www.instagram.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
}

DEFAULT_APP_HOSTS = {
    "instagram": frozenset({"instagram.com", "www.instagram.com", "i.instagram.com"}),
    "x": frozenset({"x.com", "www.x.com", "api.x.com", "twitter.com", "www.twitter.com", "api.twitter.com"}),
    "linkedin": frozenset({"linkedin.com", "www.linkedin.com", "api.linkedin.com"}),
}

_HANDLE_PATTERNS = {
    "instagram": re.compile(r"^[A-Za-z0-9._]{1,30}$"),
    "x": re.compile(r"^[A-Za-z0-9_]{1,30}$"),
    "linkedin": re.compile(r"^[A-Za-z0-9_-]{1,100}$"),
}

_POST_URL_PATTERNS = {
    "instagram": re.compile(r"^/(p|reel|tv)/([A-Za-z0-9_-]{3,40})/?$"),
    "x": re.compile(r"^/([A-Za-z0-9_]{1,30})/status/([0-9]{1,25})/?$"),
    "linkedin": re.compile(
        r"^/(posts/[A-Za-z0-9_.-]{3,120}|feed/update/urn:li:(?:activity|share|ugcPost):[0-9]{1,30})/?$"
    ),
}

# Unverified guesses for platform DOM content containers.
CONTENT_AREA_SELECTORS = {
    "instagram": ("main[role='main']", "main", "[role='main']", "article"),
    "x": ("[data-testid='primaryColumn']", "main[role='main']", "main", "[role='main']"),
    "linkedin": (".scaffold-layout__main", "main", "[role='main']"),
}


def _clean_argument(argument):
    if not isinstance(argument, str):
        raise SocialError("argument must be a string.", status=400, code="invalid_argument")
    raw = argument.strip()
    if not raw:
        raise SocialError("argument is required.", status=400, code="invalid_argument")
    if len(raw) > MAX_ARG_CHARS:
        raise SocialError(
            f"argument is too long (max {MAX_ARG_CHARS} chars).",
            status=400,
            code="invalid_argument",
        )
    if _CONTROL_CHAR_RE.search(raw):
        raise SocialError("argument contains invalid control characters.", status=400, code="invalid_argument")
    return raw


def _base_origin(platform, platform_hosts=None):
    if platform_hosts and platform in platform_hosts:
        cfg = platform_hosts[platform]
        base = str(cfg.get("base_url") or "").rstrip("/")
        if base:
            return base
    return DEFAULT_PLATFORM_ORIGINS[platform]


def _allowed_post_hosts(platform, platform_hosts=None):
    if platform_hosts and platform in platform_hosts:
        cfg = platform_hosts[platform]
        hosts = {str(h).strip().lower() for h in (cfg.get("app_hosts") or []) if str(h).strip()}
        base = str(cfg.get("base_url") or "").strip()
        if base:
            parsed = urllib.parse.urlsplit(base)
            if parsed.netloc:
                hosts.add(parsed.netloc.lower())
        return hosts, bool(cfg.get("allow_http", False))
    return set(DEFAULT_APP_HOSTS[platform]), False


def validate_and_build(platform, recipe, argument, platform_hosts=None):
    """Strictly validate (platform, recipe, argument) and return canonical target URL."""
    plat = normalize_platform(platform)
    rec = normalize_recipe(recipe)
    raw_arg = _clean_argument(argument)
    base = _base_origin(plat, platform_hosts=platform_hosts)

    if rec == "profile_posts":
        handle = raw_arg.lstrip("@").strip()
        if not handle or ".." in handle or not _HANDLE_PATTERNS[plat].fullmatch(handle):
            raise SocialError(
                f"Invalid {plat} handle for profile_posts.",
                status=400,
                code="invalid_argument",
            )
        if plat == "instagram":
            url = f"{base}/{handle}/"
        elif plat == "x":
            url = f"{base}/{handle}"
        else:
            url = f"{base}/in/{handle}/recent-activity/all/"
        return {"platform": plat, "recipe": rec, "argument": handle, "url": url}

    if rec == "search":
        if len(raw_arg) > MAX_QUERY_CHARS:
            raise SocialError(
                f"Search query too long (max {MAX_QUERY_CHARS} chars).",
                status=400,
                code="invalid_argument",
            )
        q = urllib.parse.quote_plus(raw_arg)
        if plat == "instagram":
            url = f"{base}/explore/search/keyword/?q={q}"
        elif plat == "x":
            url = f"{base}/search?q={q}&src=typed_query&f=live"
        else:
            url = f"{base}/search/results/content/?keywords={q}"
        return {"platform": plat, "recipe": rec, "argument": raw_arg, "url": url}

    if rec == "trending":
        if len(raw_arg) > MAX_TOPIC_CHARS:
            raise SocialError(
                f"Trending topic too long (max {MAX_TOPIC_CHARS} chars).",
                status=400,
                code="invalid_argument",
            )
        q = urllib.parse.quote_plus(raw_arg)
        if plat == "instagram":
            url = f"{base}/explore/search/keyword/?q={q}"
        elif plat == "x":
            url = f"{base}/search?q={q}&src=trend_click&f=top"
        else:
            url = f"{base}/search/results/content/?keywords={q}&sortBy=%22relevance%22"
        return {"platform": plat, "recipe": rec, "argument": raw_arg, "url": url}

    # post_replies
    try:
        parsed = urllib.parse.urlsplit(raw_arg)
    except ValueError as exc:
        raise SocialError("Invalid post URL.", status=400, code="invalid_argument") from exc

    allowed_hosts, allow_http = _allowed_post_hosts(plat, platform_hosts=platform_hosts)
    scheme = (parsed.scheme or "").lower()
    allowed_schemes = ("https", "http") if allow_http else ("https",)
    if scheme not in allowed_schemes:
        raise SocialError("post_replies requires an https URL.", status=400, code="invalid_argument")
    if parsed.username or parsed.password:
        raise SocialError("post_replies URL must not include credentials.", status=400, code="invalid_argument")

    netloc = (parsed.netloc or "").lower()
    hostname = (parsed.hostname or "").lower()
    host_match = (netloc in allowed_hosts) or (not allow_http and hostname in allowed_hosts and parsed.port is None)
    if not host_match:
        raise SocialError(
            f"post_replies URL host does not match {plat}.",
            status=400,
            code="invalid_argument",
        )

    path = parsed.path or ""
    if ".." in path.split("/"):
        raise SocialError("post_replies URL contains invalid path.", status=400, code="invalid_argument")

    m = _POST_URL_PATTERNS[plat].fullmatch(path)
    if not m:
        raise SocialError(
            f"post_replies URL does not match {plat}'s post URL pattern.",
            status=400,
            code="invalid_argument",
        )

    if plat == "instagram":
        canonical_path = f"/{m.group(1)}/{m.group(2)}/"
    elif plat == "x":
        canonical_path = f"/{m.group(1)}/status/{m.group(2)}"
    else:
        canonical_path = f"/{m.group(1)}"

    url = f"{base}{canonical_path}"
    canonical_arg = f"{scheme}://{netloc}{canonical_path}"
    return {"platform": plat, "recipe": rec, "argument": canonical_arg, "url": url}


def extract_content_text(page, platform, max_chars=MAX_PAGE_TEXT_CHARS):
    """Extract visible text from the page's main content area only, capped at max_chars."""
    plat = normalize_platform(platform)
    selectors = list(CONTENT_AREA_SELECTORS.get(plat, ("main", "[role='main']")))
    try:
        raw = page.evaluate(
            """(selectors) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (!style) return true;
                    return style.display !== 'none' && style.visibility !== 'hidden';
                };
                for (const sel of selectors) {
                    const node = document.querySelector(sel);
                    if (node && isVisible(node)) {
                        const txt = (node.innerText || '').trim();
                        if (txt) return txt;
                    }
                }
                if (!document.body) return '';
                const clone = document.body.cloneNode(true);
                for (const tag of clone.querySelectorAll('nav, header, footer, aside, script, style, noscript, [role="navigation"], [role="banner"], [role="contentinfo"]')) {
                    tag.remove();
                }
                return (clone.innerText || '').trim();
            }""",
            selectors,
        )
    except Exception as exc:
        raise SocialError("Failed to extract page content.", status=500, code="extract_failed") from exc
    cleaned = re.sub(r"\n{3,}", "\n\n", str(raw or "")).strip()
    if len(cleaned) > int(max_chars):
        cleaned = cleaned[: int(max_chars)].rstrip()
    return cleaned
