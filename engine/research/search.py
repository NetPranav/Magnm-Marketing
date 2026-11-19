#!/usr/bin/env python3
"""Multi-source web and developer ecosystem search with caching, timeouts, and dedupe."""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_RESEARCH_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
from research import USER_AGENT, get_setting
from research.ratelimit import limiter

DEFAULT_SOURCES = ("hn", "reddit", "github", "devto", "web")
SOURCE_TIMEOUT_S = 10.0
CACHE_TTL_S = 6 * 3600

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "what", "when", "where", "which", "who", "with",
}


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_url(url):
    """Normalize a URL for deduplication (lowercase host, strip fragment/tracking params)."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = f"{host}:{port}" if port else host
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    qs_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    kept = [(k, v) for k, v in qs_pairs if k.lower() not in _TRACKING_PARAMS]
    kept.sort()
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def _strip_html_tags(text):
    cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _http_get_json(url, headers=None, timeout=SOURCE_TIMEOUT_S):
    limiter.wait(url)
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _http_post_json(url, payload, headers=None, timeout=SOURCE_TIMEOUT_S):
    limiter.wait(url)
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def search_hn(query, limit=10):
    """Search Hacker News stories via Algolia search_by_date."""
    n = max(1, min(int(limit or 10), 50))
    url = (
        "https://hn.algolia.com/api/v1/search_by_date?"
        + urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": n})
    )
    data = _http_get_json(url)
    hits = data.get("hits") if isinstance(data, dict) else []
    results = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or hit.get("story_title") or "").strip()
        item_url = str(hit.get("url") or "").strip()
        if not item_url and hit.get("objectID"):
            item_url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        if not item_url:
            continue
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        story_text = _strip_html_tags(hit.get("story_text") or "")
        snippet = story_text[:400] if story_text else f"{points} points, {comments} comments"
        results.append(
            {
                "title": title or item_url,
                "url": item_url,
                "snippet": snippet,
                "source": "hn",
                "published_at": hit.get("created_at") or None,
                "score": points,
                "comments": comments,
            }
        )
    return results


def search_reddit(query, limit=10):
    """Search Reddit posts via public JSON endpoint with descriptive User-Agent."""
    n = max(1, min(int(limit or 10), 50))
    url = (
        "https://www.reddit.com/search.json?"
        + urllib.parse.urlencode({"q": query, "sort": "new", "t": "month", "limit": n})
    )
    try:
        data = _http_get_json(url, headers={"User-Agent": USER_AGENT})
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise RuntimeError(f"Reddit returned HTTP {exc.code} (rate limited or blocked)") from exc
        raise

    children = ((data.get("data") or {}).get("children") or []) if isinstance(data, dict) else []
    results = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if not isinstance(post, dict) or post.get("stickied"):
            continue
        title = str(post.get("title") or "").strip()
        permalink = str(post.get("permalink") or "").strip()
        item_url = f"https://www.reddit.com{permalink}" if permalink else str(post.get("url") or "").strip()
        if not item_url:
            continue
        score = int(post.get("score") or 0)
        comments = int(post.get("num_comments") or 0)
        selftext = _strip_html_tags(post.get("selftext") or "")
        snippet = selftext[:400] if selftext else f"{score} upvotes, {comments} comments"
        created_utc = post.get("created_utc")
        published_at = None
        if created_utc:
            try:
                published_at = datetime.fromtimestamp(float(created_utc), timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                published_at = None
        results.append(
            {
                "title": title or item_url,
                "url": item_url,
                "snippet": snippet,
                "source": "reddit",
                "published_at": published_at,
                "score": score,
                "comments": comments,
            }
        )
    return results


def search_github(query, limit=10):
    """Search GitHub repositories sorted by recently updated."""
    n = max(1, min(int(limit or 10), 50))
    url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode({"q": query, "sort": "updated", "per_page": n})
    )
    headers = {"Accept": "application/vnd.github+json"}
    token = get_setting("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _http_get_json(url, headers=headers)
    items = data.get("items") if isinstance(data, dict) else []
    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_url = str(item.get("html_url") or "").strip()
        if not item_url:
            continue
        title = str(item.get("full_name") or item.get("name") or item_url).strip()
        desc = str(item.get("description") or "").strip()[:400]
        pub = item.get("pushed_at") or item.get("updated_at") or item.get("created_at") or None
        results.append(
            {
                "title": title,
                "url": item_url,
                "snippet": desc,
                "source": "github",
                "published_at": pub,
                "score": int(item.get("stargazers_count") or 0),
            }
        )
    return results


def search_devto(query, limit=10):
    """Fetch recent Dev.to articles and filter by query words."""
    n = max(1, min(int(limit or 10), 50))
    tokens = re.findall(r"[a-z0-9]+", str(query or "").lower())
    meaningful = [t for t in tokens if t not in _STOPWORDS] or tokens

    per_page = max(30, n)
    url = f"https://dev.to/api/articles?{urllib.parse.urlencode({'per_page': per_page})}"
    articles = _http_get_json(url)
    if not isinstance(articles, list):
        return []

    scored_matches = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        item_url = str(art.get("url") or "").strip()
        if not item_url:
            continue
        title = str(art.get("title") or "").strip()
        desc = str(art.get("description") or "").strip()
        tags = art.get("tag_list") or []
        if isinstance(tags, str):
            tags_str = tags
        else:
            tags_str = " ".join(str(t) for t in tags)
        haystack = f"{title} {desc} {tags_str}".lower()

        if meaningful:
            matched_count = sum(1 for tok in meaningful if tok in haystack)
            needed = len(meaningful) if len(meaningful) <= 2 else min(len(meaningful), 2)
            if matched_count < needed:
                continue
        else:
            matched_count = 0

        reactions = int(art.get("public_reactions_count") or art.get("positive_reactions_count") or 0)
        pub = art.get("published_at") or art.get("published_timestamp") or None
        scored_matches.append(
            (
                matched_count,
                reactions,
                {
                    "title": title or item_url,
                    "url": item_url,
                    "snippet": desc[:400],
                    "source": "devto",
                    "published_at": pub,
                    "score": reactions,
                },
            )
        )

    scored_matches.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    return [item for _, _, item in scored_matches[:n]]


def search_web(query, limit=10):
    """Search the general web via lazily imported ddgs DDGS().text()."""
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs package is not installed; skipping web source") from exc

    n = max(1, min(int(limit or 10), 25))
    limiter.wait("duckduckgo.com")
    try:
        client = DDGS(timeout=int(SOURCE_TIMEOUT_S))
    except TypeError:
        client = DDGS()

    try:
        raw_results = client.text(query, max_results=n, backend="auto")
    except TypeError:
        raw_results = client.text(query, max_results=n)

    results = []
    for item in raw_results or []:
        if not isinstance(item, dict):
            continue
        item_url = str(item.get("href") or item.get("url") or "").strip()
        if not item_url:
            continue
        results.append(
            {
                "title": str(item.get("title") or item_url).strip(),
                "url": item_url,
                "snippet": str(item.get("body") or item.get("snippet") or "").strip()[:400],
                "source": "web",
                "published_at": item.get("published_at") or None,
            }
        )
    return results


def search_searxng(query, limit=10):
    """Search a configured SearXNG instance if SEARXNG_URL is set."""
    base = get_setting("SEARXNG_URL").rstrip("/")
    if not base:
        return []
    n = max(1, min(int(limit or 10), 25))
    url = f"{base}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    data = _http_get_json(url)
    items = data.get("results") if isinstance(data, dict) else []
    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_url = str(item.get("url") or "").strip()
        if not item_url:
            continue
        results.append(
            {
                "title": str(item.get("title") or item_url).strip(),
                "url": item_url,
                "snippet": str(item.get("content") or item.get("snippet") or "").strip()[:400],
                "source": "searxng",
                "published_at": item.get("publishedDate") or None,
                "score": item.get("score"),
            }
        )
        if len(results) >= n:
            break
    return results


def search_browser(query, limit=10):
    """Last-resort DuckDuckGo HTML search via the Playwright research browser."""
    from research import browser
    return browser.search_ddg_html(query, limit=limit)


def search_tavily(query, limit=10):
    """Search via Tavily API when TAVILY_API_KEY is configured."""
    api_key = get_setting("TAVILY_API_KEY")
    if not api_key:
        return []
    n = max(1, min(int(limit or 10), 20))
    data = _http_post_json(
        "https://api.tavily.com/search",
        {"api_key": api_key, "query": query, "max_results": n},
    )
    items = data.get("results") if isinstance(data, dict) else []
    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_url = str(item.get("url") or "").strip()
        if not item_url:
            continue
        results.append(
            {
                "title": str(item.get("title") or item_url).strip(),
                "url": item_url,
                "snippet": str(item.get("content") or "").strip()[:400],
                "source": "tavily",
                "published_at": item.get("published_date") or None,
                "score": item.get("score"),
            }
        )
    return results


def search_exa(query, limit=10):
    """Search via Exa API when EXA_API_KEY is configured."""
    api_key = get_setting("EXA_API_KEY")
    if not api_key:
        return []
    n = max(1, min(int(limit or 10), 20))
    data = _http_post_json(
        "https://api.exa.ai/search",
        {"query": query, "numResults": n, "contents": {"text": {"maxCharacters": 400}}},
        headers={"x-api-key": api_key},
    )
    items = data.get("results") if isinstance(data, dict) else []
    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_url = str(item.get("url") or "").strip()
        if not item_url:
            continue
        snippet = str(item.get("text") or "").strip()
        if not snippet and isinstance(item.get("highlights"), list):
            snippet = " ".join(str(h) for h in item["highlights"]).strip()
        results.append(
            {
                "title": str(item.get("title") or item_url).strip(),
                "url": item_url,
                "snippet": snippet[:400],
                "source": "exa",
                "published_at": item.get("publishedDate") or None,
                "score": item.get("score"),
            }
        )
    return results


SOURCE_HANDLERS = {
    "hn": search_hn,
    "reddit": search_reddit,
    "github": search_github,
    "devto": search_devto,
    "web": search_web,
    "searxng": search_searxng,
    "browser_search": search_browser,
    "tavily": search_tavily,
    "exa": search_exa,
}


def _cache_is_fresh(cached_row, now_utc, ttl_s=CACHE_TTL_S):
    if not cached_row:
        return False
    fetched = _parse_iso(cached_row.get("fetched_at"))
    if fetched is None:
        return False
    return (now_utc - fetched).total_seconds() < float(ttl_s)


def _run_source_batch(query, source_names, fetch_limit, now_utc):
    """Fetch a batch of sources using search_cache first, then parallel execution (10s timeout)."""
    collected = []
    warnings = []
    to_fetch = []

    for src in source_names:
        if src not in SOURCE_HANDLERS:
            warnings.append(f"Unknown search source '{src}'")
            continue
        cached = db.get_search_cache(src, query)
        if _cache_is_fresh(cached, now_utc):
            collected.extend(cached.get("results") or [])
        else:
            to_fetch.append(src)

    if not to_fetch:
        return collected, warnings

    pool = ThreadPoolExecutor(max_workers=len(to_fetch), thread_name_prefix="magnm-search")
    try:
        futures = {
            pool.submit(SOURCE_HANDLERS[src], query, fetch_limit): src
            for src in to_fetch
        }
        done, not_done = wait(futures.keys(), timeout=SOURCE_TIMEOUT_S)
        fetched_iso = now_utc.isoformat()

        for fut in done:
            src = futures[fut]
            try:
                items = fut.result() or []
                db.set_search_cache(src, query, items, fetched_at=fetched_iso)
                collected.extend(items)
            except Exception as exc:
                warnings.append(f"{src}: {exc}")

        for fut in not_done:
            src = futures[fut]
            fut.cancel()
            warnings.append(f"{src}: timed out after {int(SOURCE_TIMEOUT_S)}s")
    finally:
        pool.shutdown(wait=False)

    return collected, warnings


# Ad and click-tracking redirect links that search engines sometimes return as "results".
_AD_URL_MARKERS = (
    "bing.com/aclick", "bing.com/ck/a", "duckduckgo.com/y.js", "googleadservices.com",
    "doubleclick.net", "google.com/aclk", "/aclk?",
)


def is_ad_url(url):
    """True for search-engine ad / click-tracking links, which are not real sources."""
    low = str(url or "").lower()
    return any(marker in low for marker in _AD_URL_MARKERS)


def _filter_and_dedupe(items, days, now_utc):
    cutoff = (now_utc - timedelta(days=int(days))) if days is not None else None
    seen_urls = set()
    out = []

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get("url") or "").strip()
        if is_ad_url(raw_url):
            continue
        norm = normalize_url(raw_url)
        if not norm or norm in seen_urls:
            continue

        pub_raw = item.get("published_at")
        if cutoff is not None and pub_raw:
            pub_dt = _parse_iso(pub_raw)
            if pub_dt is not None and pub_dt < cutoff:
                continue

        seen_urls.add(norm)
        out.append(item)

    return out


def search(query, sources=None, days=None, limit=10):
    """Search across configured sources with caching, dedupe, freshness filter, and fallbacks.

    Returns {query, results, warnings, counts, total}.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return {"query": "", "results": [], "warnings": ["Empty search query"], "counts": {}, "total": 0}

    max_results = max(1, min(int(limit or 10), 50))
    fetch_limit = max(max_results, 15)
    now_utc = _now_utc()
    warnings = []
    raw_items = []

    if sources is not None:
        batch_items, batch_warnings = _run_source_batch(clean_query, list(sources), fetch_limit, now_utc)
        raw_items.extend(batch_items)
        warnings.extend(batch_warnings)
        filtered = _filter_and_dedupe(raw_items, days, now_utc)
    else:
        batch_items, batch_warnings = _run_source_batch(clean_query, list(DEFAULT_SOURCES), fetch_limit, now_utc)
        raw_items.extend(batch_items)
        warnings.extend(batch_warnings)
        filtered = _filter_and_dedupe(raw_items, days, now_utc)

        if len(filtered) < 3 and get_setting("SEARXNG_URL"):
            b_items, b_warn = _run_source_batch(clean_query, ["searxng"], fetch_limit, now_utc)
            raw_items.extend(b_items)
            warnings.extend(b_warn)
            filtered = _filter_and_dedupe(raw_items, days, now_utc)

        if len(filtered) < 3:
            b_items, b_warn = _run_source_batch(clean_query, ["browser_search"], fetch_limit, now_utc)
            raw_items.extend(b_items)
            warnings.extend(b_warn)
            filtered = _filter_and_dedupe(raw_items, days, now_utc)

        if len(filtered) < 3:
            paid = []
            if get_setting("TAVILY_API_KEY"):
                paid.append("tavily")
            if get_setting("EXA_API_KEY"):
                paid.append("exa")
            if paid:
                b_items, b_warn = _run_source_batch(clean_query, paid, fetch_limit, now_utc)
                raw_items.extend(b_items)
                warnings.extend(b_warn)
                filtered = _filter_and_dedupe(raw_items, days, now_utc)

    final_results = filtered[:max_results]
    counts = {}
    for item in final_results:
        src = item.get("source") or "unknown"
        counts[src] = counts.get(src, 0) + 1

    return {
        "query": clean_query,
        "results": final_results,
        "warnings": warnings,
        "counts": counts,
        "total": len(filtered),
    }
