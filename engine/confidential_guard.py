#!/usr/bin/env python3
"""Keep confidential product and repo text out of prompts that go to cloud AI.

Every check here fails closed: when the product rules or repos can't be read, text counts as confidential.
"""

import os
import re
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cloud_sync
import db
import json as _json
import paths
import product_rules
import repos
import retired_repos

_SECTION_START = re.compile(r"(?m)^(?=## )")


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fallback = paths.resource("config", "product_rules.json")
    return fallback if os.path.exists(fallback) else cand


def confidential_product_ids():
    """Return lowercase ids of confidential products. No rules file means none; an unreadable one raises."""
    path = _rules_path()
    if not os.path.exists(path):
        return set()
    return {str(pid).lower() for pid in product_rules.confidential_product_ids(path)}


def _repo_flagged(repo, conf_ids):
    pid = str(repo.get("product_id") or repo.get("id") or "").lower()
    return bool(repo.get("confidential")) or pid in conf_ids


def is_confidential_repo(repo):
    """True if the repo is flagged confidential or belongs to a confidential product. Fails closed."""
    try:
        return _repo_flagged(repo, confidential_product_ids())
    except Exception:
        return True


def product_is_confidential(product_id):
    """True if a product id or name is confidential in the rules, or names a confidential repo. Fails closed."""
    target = str(product_id or "").strip().lower()
    if not target:
        return False
    try:
        conf_ids = confidential_product_ids()
        if target in conf_ids:
            return True
        path = _rules_path()
        if os.path.exists(path):
            for prod in product_rules.load_rules(path).get("products", []):
                if prod.get("confidential") and target == str(prod.get("name") or "").strip().lower():
                    return True
        if target in {t.lower() for t in retired_repos.terms()}:
            return True
        return any(
            _repo_flagged(r, conf_ids)
            for r in repos.list_repos()
            if target in (str(r.get("id") or "").lower(), str(r.get("product_id") or "").lower())
        )
    except Exception:
        return True


def patterns():
    """Whole-word patterns for every confidential product and repo name. Raises if they can't be built."""
    terms = []
    path = _rules_path()
    if os.path.exists(path):
        for prod in product_rules.load_rules(path).get("products", []):
            if prod.get("confidential"):
                terms += [prod.get("name"), prod.get("id")]
    conf_ids = confidential_product_ids()
    for repo in repos.list_repos():
        if _repo_flagged(repo, conf_ids):
            terms += [repo.get("name"), repo.get("id"), repo.get("product_id"), repo.get("github")]
    # Removed or no longer shared confidential repos stay guarded.
    terms += retired_repos.terms()
    return [product_rules._word_pattern(str(t).strip()) for t in terms if str(t or "").strip()]


def matcher():
    """Return a function telling whether text names a confidential product or repo (True if that can't be checked).

    Build it once and reuse it when checking many texts.
    """
    try:
        pats = patterns()
    except Exception:
        return lambda text: bool(text)
    return lambda text: bool(text) and product_rules.any_match(pats, str(text))


def mentions(text):
    """True if text names a confidential product or repo, or if that can't be checked."""
    return matcher()(text)


def drop_confidential_sections(text):
    """Remove every '## ' section that names a confidential product or repo. Returns '' if that can't be checked."""
    if not text:
        return ""
    try:
        pats = patterns()
    except Exception:
        return ""
    return "".join(part for part in _SECTION_START.split(text) if not product_rules.any_match(pats, part))


class Scope:
    """Which sessions, items, reminders, metrics and notes are confidential, built once per check.

    Names come from confidential_guard (products and repos), inheritance through sessions from
    cloud_sync.confidential_scope, so there is one definition. If any part can't be built, everything
    counts as confidential.
    """

    def __init__(self):
        self.broken = False
        try:
            sessions = db.list_session_flags()
            self.session_ids, self.content_ids, self.product_check = cloud_sync.confidential_scope(
                sessions=sessions, content_items=[]
            )
            self.names = matcher()
            self.product_ids = confidential_product_ids()
        except Exception:
            self.broken = True

    def text(self, *parts):
        """True if any text part names a confidential product or repo."""
        if self.broken:
            return True
        joined = "\n".join(p if isinstance(p, str) else _json.dumps(p, ensure_ascii=False, default=str) for p in parts)
        return bool(joined) and bool(self.product_check(joined) or self.names(joined))

    def session(self, session_id):
        """True if the session is confidential itself or through an ancestor."""
        return self.broken or bool(session_id and session_id in self.session_ids)

    def item(self, item):
        """True for a content item that is confidential by session, by product, by name or by repo."""
        if self.broken:
            return True
        if not isinstance(item, dict):
            return True
        pid = str(item.get("product_id") or "").strip()
        return bool(
            item.get("id") in self.content_ids
            or self.session(item.get("session_id"))
            or (pid and (pid.lower() in self.product_ids or product_is_confidential(pid)))
            or self.text(item)
        )

    def content_id(self, content_id):
        """True if the content item with this id is confidential (or the id is a recorded confidential id)."""
        if self.broken:
            return True
        if not content_id:
            return False
        if content_id in self.content_ids:
            return True
        try:
            item = db.get_content_item(content_id)
        except Exception:
            return True
        return bool(item) and self.item(item)

    def linked(self, row, *text_keys):
        """True for a reminder, metric or memory row: its own text, or the content item it points at."""
        if self.broken:
            return True
        return bool(
            self.content_id(row.get("content_id")) or self.text(*[row.get(k) for k in text_keys if row.get(k)])
        )

    def path(self, rel_path):
        """True if a file path names a confidential product or repo, or sits under a confidential item."""
        if self.broken:
            return True
        text = str(rel_path or "")
        parts = [seg for seg in text.replace("\\", "/").split("/") if seg]
        return self.text(text) or any(self.content_id(seg) for seg in parts)


def scope():
    """Build a Scope. Cheap enough to build per tool call."""
    return Scope()


def switch_to_local(ctx):
    """Make the session local before confidential data is returned. False if there is no session that can switch."""
    if getattr(ctx, "confidential", False):
        return True
    mark = getattr(ctx, "mark_confidential", None)
    if ctx is None or not callable(mark):
        return False
    mark()
    return True


CONFIDENTIAL_REFUSAL = "That is confidential and can only be read in a session on local AI."


def local_or_refusal(ctx, confidential):
    """None when data may be returned. Otherwise an error dict: there is no session that can switch to local AI.

    When `confidential` is true the session is switched to local first, before anything is returned.
    """
    if not confidential or switch_to_local(ctx):
        return None
    return {"error": CONFIDENTIAL_REFUSAL}


def is_local(ctx):
    """True if the session already runs on local AI."""
    return bool(getattr(ctx, "confidential", False))
