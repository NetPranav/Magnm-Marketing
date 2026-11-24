#!/usr/bin/env python3
"""Deterministic pre-publish content review checks for Magnm Studio."""

import json
import os
import re
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths
import platforms
import posting_kit
import product_rules
import repos

_DEFAULT_HYPE_WORDS = (
    "revolutionary",
    "game-changing",
    "game changer",
    "cutting-edge",
    "seamless",
    "blazing fast",
    "unleash",
    "supercharge",
    "next-level",
    "world-class",
    "best-in-class",
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0F"
    "]+"
)
_HASHTAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_]+")
_URL_RE = re.compile(r"https?://[^\s<>()\"'\]]+", re.IGNORECASE)
_INLINE_ALT_RE = re.compile(r"(?i)\balt[_\s-]?text\s*:\s*(\S.+)")
_TLDR_RE = re.compile(r"(?i)\btl\s*;?\s*dr\b")
_H2_RE = re.compile(r"^\s*##(?!#)\s*\S")


def _resolve_readable_path(*parts):
    candidate = paths.data(*parts)
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource(*parts)
    return fallback if os.path.exists(fallback) else candidate


def load_review_config(path=None):
    """Load config/review.json (or defaults if missing)."""
    cfg_path = path or _resolve_readable_path("config", "review.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"version": 1, "hype_words": list(_DEFAULT_HYPE_WORDS)}


def _word_pattern(term):
    parts = [re.escape(part) for part in str(term or "").strip().split() if part]
    if not parts:
        return None
    body = r"\s+".join(parts)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


def _normalize_url(raw_url):
    cleaned = str(raw_url or "").strip().rstrip(".,;:!?)]}>'\"")
    if not cleaned:
        return ""
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except Exception:
        return cleaned.rstrip("/")
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def _extract_urls(text):
    if not text:
        return []
    out = []
    seen = set()
    for match in _URL_RE.findall(str(text)):
        norm = _normalize_url(match)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _approved_url_sources():
    """Collect normalized approved URLs and repo GitHub base URLs."""
    exact_urls = set()
    repo_prefixes = set()

    # 1. Approved DB facts
    try:
        for fact in db.list_product_facts(status="approved", limit=1000):
            if fact.get("source_url"):
                exact_urls.add(_normalize_url(fact["source_url"]))
            for u in _extract_urls(fact.get("text") or ""):
                exact_urls.add(u)
    except Exception:
        pass

    # 2. research/product-facts.md
    facts_path = _resolve_readable_path("research", "product-facts.md")
    if os.path.exists(facts_path):
        try:
            with open(facts_path, "r", encoding="utf-8") as f:
                for u in _extract_urls(f.read()):
                    exact_urls.add(u)
        except OSError:
            pass

    # 3. research_items and research_notes in SQLite
    try:
        db.ensure_init()
        conn = db.connect()
        for row in conn.execute("SELECT url FROM research_items").fetchall():
            if row["url"]:
                exact_urls.add(_normalize_url(row["url"]))
        for row in conn.execute("SELECT url, body FROM research_notes").fetchall():
            if row["url"]:
                exact_urls.add(_normalize_url(row["url"]))
            for u in _extract_urls(row["body"] or ""):
                exact_urls.add(u)
    except Exception:
        pass

    # 4. Repo GitHub URLs from config/repos.json
    try:
        for repo in repos.list_repos():
            gh = str(repo.get("github") or "").strip().strip("/")
            if gh:
                repo_url = _normalize_url(f"https://github.com/{gh}")
                if repo_url:
                    exact_urls.add(repo_url)
                    repo_prefixes.add(repo_url + "/")
    except Exception:
        pass

    exact_urls.discard("")
    return exact_urls, repo_prefixes


def _is_approved_url(norm_url, exact_urls, repo_prefixes):
    if norm_url in exact_urls:
        return True
    return any(norm_url.startswith(prefix) for prefix in repo_prefixes)


def _has_alt_text(item, entry):
    if posting_kit._extract_alt_text(item, entry):
        return True
    notes = str(item.get("notes") or "")
    if notes and _INLINE_ALT_RE.search(notes):
        m = _INLINE_ALT_RE.search(notes)
        if m and m.group(1).strip().upper() != "MISSING":
            return True
    return False


def auto_review(content_item):
    """Run deterministic review checks on a content item dict (or content_id)."""
    if isinstance(content_item, str):
        loaded = db.get_content_item(content_item, include_versions=True)
        if loaded is None:
            raise ValueError(f"Content item '{content_item}' not found")
        item = loaded
    elif isinstance(content_item, dict):
        item = content_item
    else:
        raise ValueError("content_item must be a dict or content_id")

    title = str(item.get("title") or "")
    body = str(item.get("body") or "")
    combined = f"{title}\n{body}".strip() if title else body
    platform = platforms.normalize_platform(item.get("platform") or "")
    content_type = str(item.get("type") or "").strip().lower()
    findings = []

    # 1. Product rules
    rules_path = _resolve_readable_path("config", "product_rules.json")
    if os.path.exists(rules_path):
        for w in product_rules.check_text(combined, path=rules_path):
            findings.append({
                "code": "product_rule",
                "severity": w.get("severity") or "warn",
                "message": w.get("message") or "Product rule violation.",
            })

    # 2. Zero-emoji policy
    if _EMOJI_RE.search(combined):
        findings.append({
            "code": "emoji_present",
            "severity": "warn",
            "message": "Text contains emoji (strict zero-emoji policy).",
        })

    # 3. Hype words
    cfg = load_review_config()
    hype_words = cfg.get("hype_words") or list(_DEFAULT_HYPE_WORDS)
    for term in hype_words:
        pat = _word_pattern(term)
        if pat and pat.search(combined):
            findings.append({
                "code": "hype_word",
                "severity": "warn",
                "message": f"Contains hype word '{term}'.",
            })

    # 4. Platform limits (never for a video script: it is production notes, not the caption)
    limits = None if content_type in platforms.NOT_POSTED_AS_TEXT else platforms.limits_for(platform)
    if limits:
        norm_plat = limits["platform"]
        max_chars = limits.get("max_chars")
        if max_chars is not None:
            if norm_plat == "x" and content_type == "thread":
                parts = [p.strip() for p in re.split(r"\n\s*---\s*\n|\n\n+", body) if p.strip()]
                if not parts and body:
                    parts = [body]
                for idx, part in enumerate(parts, start=1):
                    if len(part) > max_chars:
                        findings.append({
                            "code": "platform_max_chars",
                            "severity": "warn",
                            "message": f"X thread item {idx} exceeds {max_chars} chars ({len(part)} chars).",
                        })
            elif len(body) > max_chars:
                findings.append({
                    "code": "platform_max_chars",
                    "severity": "warn",
                    "message": f"Text exceeds {norm_plat} limit ({len(body)} > {max_chars} chars).",
                })

        hook_chars = limits.get("hook_chars")
        if hook_chars is not None:
            non_empty_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            first_line = non_empty_lines[0] if non_empty_lines else ""
            if len(first_line) > hook_chars:
                findings.append({
                    "code": "hook_too_long",
                    "severity": "warn",
                    "message": f"First line ({len(first_line)} chars) exceeds {norm_plat} visible hook ({hook_chars} chars).",
                })

        max_hashtags = limits.get("max_hashtags")
        if max_hashtags is not None:
            tags = _HASHTAG_RE.findall(body)
            if len(tags) > max_hashtags:
                findings.append({
                    "code": "too_many_hashtags",
                    "severity": "warn",
                    "message": f"Too many hashtags for {norm_plat} ({len(tags)} > {max_hashtags}).",
                })

        title_max = limits.get("title_max_chars")
        if title_max is not None and title and len(title) > title_max:
            findings.append({
                "code": "platform_title_max_chars",
                "severity": "warn",
                "message": f"Title exceeds {norm_plat} limit ({len(title)} > {title_max} chars).",
            })

        max_words = limits.get("max_words")
        if max_words is not None:
            word_count = len(body.split())
            if word_count > max_words:
                findings.append({
                    "code": "platform_max_words",
                    "severity": "warn",
                    "message": f"Word count exceeds {norm_plat} limit ({word_count} > {max_words} words).",
                })

    # 5. Links must come from approved facts, product-facts.md, research_items, research_notes, or repo GitHub URL
    urls = _extract_urls(combined)
    if urls:
        exact_urls, repo_prefixes = _approved_url_sources()
        for u in urls:
            if not _is_approved_url(u, exact_urls, repo_prefixes):
                findings.append({
                    "code": "unapproved_link",
                    "severity": "warn",
                    "message": f"link not from an approved source: {u}",
                })

    # 6. Images attached to instagram/x/linkedin items without alt text in notes
    if platform in ("instagram", "x", "linkedin"):
        image_entries = [
            f for f in (item.get("files") or [])
            if isinstance(f, dict) and f.get("kind") in ("card", "image")
        ]
        missing_paths = [
            e.get("path") or "attachment"
            for e in image_entries
            if not _has_alt_text(item, e)
        ]
        if missing_paths:
            findings.append({
                "code": "missing_alt_text",
                "severity": "warn",
                "message": f"Attached image(s) for {platform} missing alt text in notes: {', '.join(missing_paths)}.",
            })

    # 7. Blog structure: TL;DR + at least 3 '##' headings
    if content_type == "blog" or (platform == "blog" and content_type in ("", "blog")):
        if not _TLDR_RE.search(body):
            findings.append({
                "code": "blog_missing_tldr",
                "severity": "warn",
                "message": "Blog post is missing a TL;DR.",
            })
        h2_count = sum(1 for ln in body.splitlines() if _H2_RE.match(ln))
        if h2_count < 3:
            findings.append({
                "code": "blog_few_headings",
                "severity": "warn",
                "message": f"Blog post has fewer than 3 '##' headings ({h2_count} found).",
            })

    verdict = "warn" if any(f.get("severity") == "warn" for f in findings) else "ok"
    return {
        "verdict": verdict,
        "findings": findings,
    }
