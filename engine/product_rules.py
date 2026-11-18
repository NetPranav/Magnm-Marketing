#!/usr/bin/env python3
"""Configurable product visibility, confidentiality, and claim rules."""

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES_PATH = os.path.join(ROOT, "config", "product_rules.json")

ALLOWED_STATUSES = ("released", "in_development", "secret")
ALLOWED_SHOW_LEVELS = ("everything", "text_only", "teasers_only", "nothing")

_FEATURE_WORDS_RE = re.compile(
    r"\b(?:features?|demos?|editors?|keyframes?|animations?|timelines?|exports?|launch\s+dates?|beta)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _word_pattern(term):
    """Build a case-insensitive whole-word regex for a product name or term."""
    parts = [re.escape(part) for part in term.strip().split()]
    body = r"\s+".join(parts)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


def load_rules(path=None):
    """Load and validate product rules from JSON, raising ValueError on invalid config."""
    rules_path = path or DEFAULT_RULES_PATH
    with open(rules_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("product_rules config must be a JSON object")

    show_levels = data.get("show_levels")
    if not isinstance(show_levels, dict) or not show_levels:
        raise ValueError("show_levels must be a non-empty object")
    for level in show_levels:
        if level not in ALLOWED_SHOW_LEVELS:
            raise ValueError(f"Invalid show_levels key: {level!r}")

    products = data.get("products")
    if not isinstance(products, list):
        raise ValueError("products must be a list")

    for prod in products:
        if not isinstance(prod, dict):
            raise ValueError("Each product entry must be an object")
        pid = prod.get("id") or prod.get("name") or "<unknown>"
        status = prod.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status {status!r} for product {pid!r}")
        show = prod.get("show")
        if show not in show_levels or show not in ALLOWED_SHOW_LEVELS:
            raise ValueError(f"Invalid show level {show!r} for product {pid!r}")

    return data


def get_product(product_id, path=None):
    """Return the product dict matching product_id, or None if not found."""
    rules = load_rules(path)
    for prod in rules.get("products", []):
        if prod.get("id") == product_id:
            return prod
    return None


def prompt_block(path=None):
    """Generate a compact plain-text rules block for agent system prompts."""
    rules = load_rules(path)
    show_levels = rules.get("show_levels", {})
    lines = ["Product rules (from config/product_rules.json):"]
    for prod in rules.get("products", []):
        name = prod.get("name") or prod.get("id") or ""
        status = prod.get("status", "")
        show = prod.get("show", "")
        meaning = show_levels.get(show, "")
        line = f"- {name} (status: {status}, show: {show}): {meaning}".strip()
        notes = (prod.get("notes") or "").strip()
        if notes:
            line += f" Notes: {notes}"
        lines.append(line)
    return "\n".join(lines)


def _check_teasers_only(product, text):
    """Return a teaser or stronger feature-description warning if product is mentioned."""
    pid = product.get("id", "")
    name = product.get("name") or pid
    name_re = _word_pattern(name)
    if not name_re.search(text):
        return None

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    matching_sentences = [s for s in sentences if name_re.search(s)]
    if not matching_sentences:
        matching_sentences = [text]

    looks_like_feature = any(
        len(sentence.split()) > 20 or _FEATURE_WORDS_RE.search(sentence)
        for sentence in matching_sentences
    )
    if looks_like_feature:
        return {
            "product": pid,
            "severity": "warn",
            "message": (
                f"{name} is teasers_only, and this looks like a feature description "
                "(never name features, show visuals, or promise demos; CEO approval required per post)."
            ),
        }
    return {
        "product": pid,
        "severity": "warn",
        "message": f"{name} is teasers_only: only a short text-only teaser is allowed, with CEO approval per post.",
    }


def check_text(text, path=None):
    """Check text against configured product rules and return warning dicts (never blocks)."""
    if not text:
        return []

    rules = load_rules(path)
    warnings = []

    for prod in rules.get("products", []):
        pid = prod.get("id", "")
        name = prod.get("name") or pid
        show = prod.get("show")

        if show == "nothing" and name and _word_pattern(name).search(text):
            warnings.append({
                "product": pid,
                "severity": "warn",
                "message": f"{name} has show level 'nothing' and must not be mentioned.",
            })
        elif show == "teasers_only" and name:
            warning = _check_teasers_only(prod, text)
            if warning:
                warnings.append(warning)

        for term in prod.get("blocked_terms") or []:
            if term and _word_pattern(term).search(text):
                warnings.append({
                    "product": pid,
                    "severity": "warn",
                    "message": f"Blocked term '{term}' is not allowed for {name}.",
                })

    return warnings


def confidential_product_ids(path=None):
    """Return the set of product IDs marked confidential."""
    rules = load_rules(path)
    return {
        prod["id"]
        for prod in rules.get("products", [])
        if prod.get("confidential") and prod.get("id")
    }


def mentions_confidential(text, path=None):
    """Return True if any confidential product name appears in text."""
    if not text:
        return False
    rules = load_rules(path)
    for prod in rules.get("products", []):
        if not prod.get("confidential"):
            continue
        name = prod.get("name") or prod.get("id")
        if name and _word_pattern(name).search(text):
            return True
    return False
