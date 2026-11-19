#!/usr/bin/env python3
"""Configurable product visibility, confidentiality, and claim rules."""

import json
import os
import re
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

ROOT = paths.DATA_ROOT
DEFAULT_RULES_PATH = paths.data("config", "product_rules.json")

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


def validate_rules(data):
    """Validate a product_rules dict, raising ValueError on invalid fields."""
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


def load_rules(path=None):
    """Load and validate product rules from JSON, raising ValueError on invalid config."""
    rules_path = path or DEFAULT_RULES_PATH
    with open(rules_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return validate_rules(data)


def _normalize_blocked_terms(raw_terms):
    if raw_terms is None:
        return []
    if isinstance(raw_terms, str):
        parts = re.split(r"[,;\n]+", raw_terms)
    elif isinstance(raw_terms, list):
        parts = raw_terms
    else:
        raise ValueError("blocked_terms must be a list or comma-separated string")
    cleaned = []
    seen = set()
    for term in parts:
        val = str(term or "").strip()
        if val and val.lower() not in seen:
            seen.add(val.lower())
            cleaned.append(val)
    return cleaned


def save_product_rule(product_id, updates, path=None):
    """Update a single product rule entry and persist product_rules.json atomically."""
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("product_id is required")
    if not isinstance(updates, dict):
        raise ValueError("updates must be a JSON object")

    rules_path = path or DEFAULT_RULES_PATH
    if not os.path.exists(rules_path):
        fallback = paths.resource("config", "product_rules.json")
        if os.path.exists(fallback):
            data = load_rules(fallback)
        else:
            raise FileNotFoundError(f"Product rules file not found: {rules_path}")
    else:
        data = load_rules(rules_path)

    target = None
    for prod in data.get("products", []):
        if prod.get("id") == pid:
            target = prod
            break
    if target is None:
        raise ValueError(f"Product '{pid}' not found")

    if "status" in updates and updates["status"] is not None:
        target["status"] = str(updates["status"]).strip()
    if "show" in updates and updates["show"] is not None:
        target["show"] = str(updates["show"]).strip()
    if "blocked_terms" in updates:
        target["blocked_terms"] = _normalize_blocked_terms(updates["blocked_terms"])
    if "confidential" in updates and updates["confidential"] is not None:
        target["confidential"] = bool(updates["confidential"])
    if "notes" in updates and updates["notes"] is not None:
        target["notes"] = str(updates["notes"]).strip()

    validate_rules(data)
    parent = os.path.dirname(rules_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = rules_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, rules_path)
    return dict(target), data


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
