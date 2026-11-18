#!/usr/bin/env python3
"""Platform formatting norms and limit checks for Magnm Studio content."""

import re

PLATFORM_ALIASES = {
    "x": "x",
    "twitter": "x",
    "linkedin": "linkedin",
    "li": "linkedin",
    "instagram": "instagram",
    "ig": "instagram",
    "youtube": "youtube",
    "yt": "youtube",
    "blog": "blog",
}

PLATFORM_LIMITS = {
    "x": {
        "platform": "x",
        "max_chars": 280,
        "max_hashtags": 2,
        "threads_numbered": True,
    },
    "linkedin": {
        "platform": "linkedin",
        "max_chars": 3000,
        "hook_chars": 210,
        "min_hashtags": 3,
        "max_hashtags": 5,
    },
    "instagram": {
        "platform": "instagram",
        "max_chars": 2200,
        "hook_chars": 125,
        "max_hashtags": 5,
        "alt_text_required": True,
    },
    "youtube": {
        "platform": "youtube",
        "title_max_chars": 100,
        "description_hook_chars": 150,
    },
    "blog": {
        "platform": "blog",
        "min_words": 1200,
        "max_words": 2000,
        "requires_headings": True,
        "requires_tldr": True,
    },
}

_HASHTAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_]+")


def normalize_platform(platform):
    """Return canonical platform key or lowercase stripped input."""
    key = (platform or "").strip().lower()
    return PLATFORM_ALIASES.get(key, key)


def limits_for(platform):
    """Return a copy of the platform limits dict, or {} if unknown."""
    norm = normalize_platform(platform)
    spec = PLATFORM_LIMITS.get(norm)
    return dict(spec) if spec else {}


def check_limits(platform, body, title=None, content_type=None):
    """Return over-limit warning dicts for body/title against limits_for(platform)."""
    limits = limits_for(platform)
    if not limits:
        return []

    norm = limits["platform"]
    text = body or ""
    warnings = []

    max_chars = limits.get("max_chars")
    if max_chars is not None:
        if norm == "x" and (content_type or "").strip().lower() == "thread":
            parts = [p.strip() for p in re.split(r"\n\s*---\s*\n|\n\n+", text) if p.strip()]
            if not parts:
                parts = [text] if text else []
            for idx, part in enumerate(parts, start=1):
                if len(part) > max_chars:
                    warnings.append({
                        "product": "platform",
                        "severity": "warn",
                        "message": f"X thread item {idx} exceeds {max_chars} chars ({len(part)} chars).",
                    })
        elif len(text) > max_chars:
            warnings.append({
                "product": "platform",
                "severity": "warn",
                "message": f"Text exceeds {norm} limit ({len(text)} > {max_chars} chars).",
            })

    max_hashtags = limits.get("max_hashtags")
    if max_hashtags is not None:
        tags = _HASHTAG_RE.findall(text)
        if len(tags) > max_hashtags:
            warnings.append({
                "product": "platform",
                "severity": "warn",
                "message": f"Too many hashtags for {norm} ({len(tags)} > {max_hashtags}).",
            })

    title_max = limits.get("title_max_chars")
    if title_max is not None and title and len(title) > title_max:
        warnings.append({
            "product": "platform",
            "severity": "warn",
            "message": f"Title exceeds {norm} limit ({len(title)} > {title_max} chars).",
        })

    max_words = limits.get("max_words")
    if max_words is not None:
        word_count = len(text.split())
        if word_count > max_words:
            warnings.append({
                "product": "platform",
                "severity": "warn",
                "message": f"Word count exceeds {norm} limit ({word_count} > {max_words} words).",
            })

    return warnings


def prompt_block():
    """Return the platform norms block for content-producing and reviewing agents."""
    return "\n".join([
        "Platform norms:",
        "- X: 280 chars per post, threads numbered, max 2 hashtags.",
        "- LinkedIn: about 3,000 chars max, first ~210 chars show before 'see more' (put the hook there); 3-5 hashtags at the end.",
        "- Instagram caption: 2,200 chars max, first 125 chars matter, up to 5 relevant hashtags, alt text required for images.",
        "- YouTube: title <= 100 chars, description first 150 chars matter.",
        "- Blog: 1,200-2,000 words, clear headings, include a TL;DR.",
    ])
