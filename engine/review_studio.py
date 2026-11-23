#!/usr/bin/env python3
"""Review Studio engine logic: comments, anchors, suggestions, versions, AI actions, checklist, and posting."""

from datetime import datetime, timezone
import difflib
import json
import math
import os
import re
import sys
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_runtime
import agents
import ai_gateway
import confidential_guard
import db
import events
import jobs
import paths
import platforms
import posting_kit
import product_rules
import review
from tools import core as core_tools

COMMENT_KINDS = ("text", "image", "video", "general")
COMMENT_STATUSES = ("open", "resolved", "applied")
SUGGESTION_DECISIONS = ("accept", "reject")

TEXT_AI_ACTIONS = (
    "shorten",
    "expand",
    "rephrase",
    "simplify",
    "formal",
    "casual",
    "fix_grammar",
    "stronger_hook",
    "hashtags",
    "fit_platform",
)
BLOG_AI_ACTIONS = ("seo_title_meta", "tldr", "outline_check")
IMAGE_AI_ACTIONS = ("alt_text",)
ALL_AI_ACTIONS = TEXT_AI_ACTIONS + BLOG_AI_ACTIONS + IMAGE_AI_ACTIONS

_READ_ONLY_REVIEW_TOOLS = {
    "read_product_facts",
    "list_repos",
    "read_repo_changes",
    "shipped_this_week",
    "search_research",
    "read_content",
    "list_content",
    "check_text",
    "check_image",
    "recall_memory",
    "read_metrics",
}

_MAX_COMMENT_MENTIONS = 3
_MENTION_RE = re.compile(r"@([a-zA-Z0-9_-]+)")
_WORD_DIFF_RE = re.compile(r"\S+|\s+")
_SEGMENT_RE = re.compile(r"\S[^.!?\n]*(?:[.!?]+|$)|\s+")


class NotFoundError(ValueError):
    """Raised when a requested content item, version, comment, or suggestion does not exist."""


class ValidationError(ValueError):
    """Raised when input parameters or anchor state fail validation."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _resolve_readable_path(*parts):
    candidate = paths.data(*parts)
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource(*parts)
    return fallback if os.path.exists(fallback) else candidate


def _rules_path():
    return _resolve_readable_path("config", "product_rules.json")


def _publish(content_id, what, extra=None):
    payload = {"content_id": str(content_id), "what": str(what)}
    if isinstance(extra, dict):
        payload.update(extra)
    events.publish("review.updated", payload)


def _is_number(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def _is_int(val):
    return isinstance(val, int) and not isinstance(val, bool)


def _require_item(content_id, version=None, include_versions=False):
    cid = str(content_id or "").strip()
    if not cid:
        raise ValidationError("content_id is required")
    item = db.get_content_item(cid, version=version, include_versions=include_versions)
    if item is None:
        raise NotFoundError(f"Content item '{cid}' not found")
    return item


def _require_version_record(content_id, version_num):
    item = _require_item(content_id, include_versions=True)
    for ver in item.get("versions") or []:
        if int(ver["version"]) == int(version_num):
            return item, ver
    raise NotFoundError(f"Version {version_num} not found for content item '{content_id}'")


def _resolve_product_and_confidential(item):
    """Determine product metadata and whether the item must stay on local AI."""
    rules_file = _rules_path()
    has_rules = os.path.exists(rules_file)
    product = None
    is_conf = False

    pid = (item.get("product_id") or "").strip()
    if has_rules:
        if pid:
            product = product_rules.get_product(pid, path=rules_file)
            if product and product.get("confidential"):
                is_conf = True
            if pid in product_rules.confidential_product_ids(path=rules_file):
                is_conf = True

        combined = f"{item.get('title') or ''}\n{item.get('body') or ''}".strip()
        if product_rules.mentions_confidential(combined, path=rules_file):
            is_conf = True

        if product is None and combined:
            try:
                rules_data = product_rules.load_rules(rules_file)
                for prod in rules_data.get("products") or []:
                    pname = (prod.get("name") or prod.get("id") or "").strip()
                    if pname and re.search(rf"\b{re.escape(pname)}\b", combined, re.IGNORECASE):
                        product = prod
                        if prod.get("confidential"):
                            is_conf = True
                        break
            except Exception:
                pass

    # The shared definition: also repos flagged confidential on their own, and inheritance through sessions.
    if not is_conf and confidential_guard.scope().item(item):
        is_conf = True

    sess_id = item.get("session_id")
    if not is_conf and sess_id:
        try:
            sess = db.get_agent_session(sess_id)
            if sess and sess.get("confidential"):
                is_conf = True
        except Exception:
            pass

    return product, is_conf


def _build_product_context_block(item, confidential=False):
    """Assemble product rules, approved product facts, and platform norms for prompts.

    Facts about confidential products are left out unless `confidential` says the prompt goes to local AI.
    """
    parts = []
    rules_file = _rules_path()
    if os.path.exists(rules_file):
        parts.append(product_rules.prompt_block(rules_file))

    pid = item.get("product_id") or None
    try:
        facts_res = core_tools.read_product_facts(None, product_id=pid, _allow_confidential=confidential)
        facts_text = (facts_res or {}).get("product_facts") or ""
        if facts_text.strip():
            parts.append("Approved product facts:\n" + facts_text.strip())
    except Exception:
        pass

    plat_norm = platforms.normalize_platform(item.get("platform") or "")
    limits = platforms.limits_for(plat_norm)
    if limits:
        parts.append(f"Target platform ({plat_norm}) limits: {json.dumps(limits, ensure_ascii=False)}")
    parts.append(platforms.prompt_block())
    return "\n\n".join(parts)


def _platform_check_payload(item):
    plat_raw = item.get("platform") or ""
    plat_norm = platforms.normalize_platform(plat_raw)
    body = item.get("body") or ""
    title = item.get("title") or ""
    ctype = item.get("type") or ""
    limits = platforms.limits_for(plat_norm)
    warnings = platforms.check_limits(plat_norm, body, title=title, content_type=ctype)
    cutoff = posting_kit._see_more_cutoff_info(plat_norm, body) if plat_norm else None
    hashtags = re.findall(r"(?<!\w)#[A-Za-z0-9_]+", body)
    return {
        "platform": plat_norm or plat_raw,
        "limits": limits,
        "warnings": warnings,
        "ok": len(warnings) == 0,
        "char_count": len(body),
        "word_count": len(body.split()),
        "hashtag_count": len(hashtags),
        "see_more_cutoff": cutoff,
    }


def _build_comment_tree(flat_comments):
    """Nest replies under parent comments chronologically."""
    nodes = []
    by_id = {}
    for raw in flat_comments:
        node = dict(raw)
        node["replies"] = []
        by_id[node["id"]] = node
        nodes.append(node)

    roots = []
    for node in nodes:
        pid = node.get("parent_id")
        if pid and pid in by_id and pid != node["id"]:
            by_id[pid]["replies"].append(node)
        else:
            roots.append(node)
    return roots


def _platform_checklist_specs(platform_norm):
    specs = {
        "linkedin": [
            ("platform:see_more", "First line works without See more (210 chars)"),
            ("platform:hashtags_end", "3-5 hashtags placed at the end"),
        ],
        "instagram": [
            ("platform:see_more", "First line works without See more (125 chars)"),
            ("platform:visual_alt", "Caption pairs with attached visual and alt text"),
        ],
        "x": [
            ("platform:x_hook", "Opening line hooks in feed (280 chars)"),
            ("platform:x_tags", "At most 2 relevant hashtags"),
        ],
        "youtube": [
            ("platform:yt_hook", "Title <= 100 chars and first 150 chars of description hook"),
        ],
        "blog": [
            ("platform:blog_read", "1,200-2,000 words with scannable subheadings"),
        ],
    }
    return specs.get(platform_norm) or [("platform:hook", "Opening line is clear and engaging")]


def _recompute_checklist(item, auto_res=None, platform_warnings=None):
    """Recompute auto checklist items, preserve platform checks and all manual checks."""
    cid = item["id"]
    if auto_res is None:
        auto_res = review.auto_review(item)
    findings = list(auto_res.get("findings") or [])
    codes = {f.get("code") for f in findings}

    plat_norm = platforms.normalize_platform(item.get("platform") or "")
    if platform_warnings is None:
        platform_warnings = platforms.check_limits(
            plat_norm,
            item.get("body") or "",
            title=item.get("title") or "",
            content_type=item.get("type") or "",
        )

    existing = db.list_review_checklist(cid)
    existing_by_key = {row["item_key"]: row for row in existing}

    auto_items = [
        ("auto:product_rules", "Product rules and claims check", "product_rule" not in codes),
        ("auto:zero_emoji", "Zero-emoji policy", "emoji_present" not in codes),
        ("auto:no_hype", "No hype or fluff words", "hype_word" not in codes),
        (
            "auto:platform_limits",
            "Within platform character/word/hashtag limits",
            not any(
                c in codes
                for c in (
                    "platform_max_chars",
                    "too_many_hashtags",
                    "platform_title_max_chars",
                    "platform_max_words",
                )
            )
            and len(platform_warnings) == 0,
        ),
        ("auto:links_approved", "All links come from approved sources", "unapproved_link" not in codes),
    ]

    image_entries = [
        f
        for f in (item.get("files") or [])
        if isinstance(f, dict) and f.get("kind") in ("card", "image")
    ]
    if image_entries:
        all_alt = all(review._has_alt_text(item, img) for img in image_entries)
        auto_items.append(("auto:alt_text", "Alt text present for attached images", all_alt))

    ctype = str(item.get("type") or "").strip().lower()
    if ctype == "blog" or (plat_norm == "blog" and ctype in ("", "blog")):
        auto_items.append(
            (
                "auto:blog_structure",
                "Blog has TL;DR and at least 3 headings",
                "blog_missing_tldr" not in codes and "blog_few_headings" not in codes,
            )
        )

    now = _now_iso()
    db.delete_review_checklist_by_sources(cid, ["auto", "platform"])
    for key, label, checked in auto_items:
        db.upsert_review_checklist_item(cid, key, label, "auto", checked, updated_at=now)

    for key, label in _platform_checklist_specs(plat_norm):
        prev = existing_by_key.get(key)
        prev_checked = bool(prev["checked"]) if prev else False
        db.upsert_review_checklist_item(cid, key, label, "platform", prev_checked, updated_at=now)

    return db.list_review_checklist(cid)


def get_review(content_id):
    """Return full Review Studio payload for content_id."""
    item = _require_item(content_id, include_versions=True)
    versions = [
        {
            "number": v["version"],
            "version": v["version"],
            "created_by": v.get("created_by"),
            "notes": v.get("notes"),
            "created_at": v.get("created_at"),
        }
        for v in (item.get("versions") or [])
    ]
    auto_res = review.auto_review(item)
    plat_check = _platform_check_payload(item)

    existing_checklist = db.list_review_checklist(item["id"])
    if not existing_checklist or not any(c["source"] == "auto" for c in existing_checklist):
        checklist = _recompute_checklist(
            item,
            auto_res=auto_res,
            platform_warnings=plat_check["warnings"],
        )
    else:
        checklist = existing_checklist

    comments = _build_comment_tree(db.list_review_comments(item["id"]))
    pending_suggestions = db.list_review_suggestions(item["id"], status="pending")
    product, is_conf = _resolve_product_and_confidential(item)

    return {
        "item": item,
        "body": item.get("body") or "",
        "current_version": int(item.get("current_version") or 1),
        "versions": versions,
        "comments": comments,
        "suggestions": pending_suggestions,
        "checklist": checklist,
        "warnings": list(auto_res.get("findings") or []),
        "auto_review": auto_res,
        "platform_check": plat_check,
        "product": product,
        "confidential": is_conf,
    }


def get_version(content_id, version_num):
    """Return a single version record for content_id."""
    if not _is_int(version_num) or version_num < 1:
        raise ValidationError("version must be a positive integer")
    item, ver = _require_version_record(content_id, version_num)
    return {
        "content_id": item["id"],
        "version": ver["version"],
        "number": ver["version"],
        "body": ver["body"],
        "notes": ver.get("notes"),
        "created_by": ver.get("created_by"),
        "created_at": ver.get("created_at"),
    }


def diff_versions(content_id, from_ver, to_ver):
    """Compute a word-level diff between two versions of content_id."""
    if not _is_int(from_ver) or not _is_int(to_ver) or from_ver < 1 or to_ver < 1:
        raise ValidationError("from and to versions must be positive integers")
    _, v_from = _require_version_record(content_id, from_ver)
    _, v_to = _require_version_record(content_id, to_ver)

    tokens_a = _WORD_DIFF_RE.findall(v_from.get("body") or "")
    tokens_b = _WORD_DIFF_RE.findall(v_to.get("body") or "")
    sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)

    raw_ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            raw_ops.append({"op": "equal", "text": "".join(tokens_a[i1:i2])})
        elif tag == "delete":
            raw_ops.append({"op": "delete", "text": "".join(tokens_a[i1:i2])})
        elif tag == "insert":
            raw_ops.append({"op": "insert", "text": "".join(tokens_b[j1:j2])})
        elif tag == "replace":
            raw_ops.append({"op": "delete", "text": "".join(tokens_a[i1:i2])})
            raw_ops.append({"op": "insert", "text": "".join(tokens_b[j1:j2])})

    ops = []
    for entry in raw_ops:
        if not entry["text"]:
            continue
        if ops and ops[-1]["op"] == entry["op"]:
            ops[-1]["text"] += entry["text"]
        else:
            ops.append(entry)

    return {
        "content_id": str(content_id),
        "from_version": int(from_ver),
        "to_version": int(to_ver),
        "ops": ops,
    }


def _find_all_occurrences(haystack, needle):
    if not needle:
        return []
    positions = []
    pos = 0
    while True:
        idx = haystack.find(needle, pos)
        if idx < 0:
            break
        positions.append(idx)
        pos = idx + 1
    return positions


def _reanchor_text_comments(content_id, new_version, new_body):
    """Re-anchor open top-level text comments to new_body or mark them orphaned."""
    comments = db.list_review_comments(content_id, status="open")
    changed = False
    for cmt in comments:
        if cmt.get("parent_id") is not None or cmt.get("kind") != "text":
            continue
        anchor = dict(cmt.get("anchor") or {})
        quote = anchor.get("quote") or ""
        old_start = int(anchor.get("start") or 0)
        matches = _find_all_occurrences(new_body, quote)
        if matches:
            best = min(matches, key=lambda idx: abs(idx - old_start))
            new_anchor = {
                "start": best,
                "end": best + len(quote),
                "quote": quote,
            }
            db.update_review_comment(
                cmt["id"],
                anchor=new_anchor,
                version=new_version,
            )
            changed = True
        else:
            if not anchor.get("orphaned"):
                anchor["orphaned"] = True
                db.update_review_comment(cmt["id"], anchor=anchor)
                changed = True
    return changed


def _on_new_version(content_id, new_version, new_body):
    """Re-anchor comments, recompute auto checklist, and publish live update events."""
    comments_changed = _reanchor_text_comments(content_id, new_version, new_body)
    item = _require_item(content_id, include_versions=True)
    _recompute_checklist(item)
    _publish(content_id, "version")
    _publish(content_id, "checklist")
    if comments_changed:
        _publish(content_id, "comment")
    return item


def save_version(content_id, body, *, notes=None, created_by="user"):
    """Create a new version from an edited body, re-anchor comments, and recompute checklist."""
    item = _require_item(content_id, include_versions=True)
    if not isinstance(body, str):
        raise ValidationError("body must be a string")
    clean_notes = notes.strip() if isinstance(notes, str) and notes.strip() else "Manual edit"
    clean_actor = str(created_by or "user").strip() or "user"
    updated = db.add_content_version(
        item["id"],
        body=body,
        notes=clean_notes,
        created_by=clean_actor,
    )
    updated = _on_new_version(item["id"], updated["current_version"], updated["body"])
    return updated


def restore_version(content_id, version_num, created_by="user"):
    """Create a new version copying the body of version_num."""
    if not _is_int(version_num) or version_num < 1:
        raise ValidationError("version must be a positive integer")
    _, target_ver = _require_version_record(content_id, version_num)
    updated = db.add_content_version(
        content_id,
        body=target_ver["body"],
        notes=f"Restored from v{version_num}",
        created_by=created_by,
    )
    updated = _on_new_version(content_id, updated["current_version"], updated["body"])
    return updated


def _item_file_paths(item):
    paths_set = set()
    for entry in item.get("files") or []:
        if isinstance(entry, dict):
            raw = entry.get("path") or ""
        elif isinstance(entry, str):
            raw = entry
        else:
            raw = ""
        if raw:
            paths_set.add(os.path.normpath(raw).replace("\\", "/"))
    return paths_set


def _validate_comment_anchor(item, ver_body, kind, anchor, is_reply=False):
    if is_reply and not anchor:
        return {}
    if kind == "general":
        if anchor is None:
            return {}
        if not isinstance(anchor, dict):
            raise ValidationError("anchor must be an object")
        return dict(anchor)

    if not isinstance(anchor, dict):
        raise ValidationError(f"anchor object is required for '{kind}' comments")

    if kind == "text":
        start = anchor.get("start")
        end = anchor.get("end")
        quote = anchor.get("quote")
        if not _is_int(start) or not _is_int(end):
            raise ValidationError("text anchor requires integer start and end offsets")
        if start < 0 or end <= start or end > len(ver_body):
            raise ValidationError("text anchor offsets are out of bounds")
        if not isinstance(quote, str) or not quote:
            raise ValidationError("text anchor requires non-empty quote")
        if ver_body[start:end] != quote:
            raise ValidationError("text anchor quote does not match body at offsets")
        return {"start": start, "end": end, "quote": quote}

    if kind == "image":
        file_path = anchor.get("file")
        x = anchor.get("x")
        y = anchor.get("y")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValidationError("image anchor requires non-empty file")
        if not _is_number(x) or not _is_number(y) or not (0.0 <= float(x) <= 1.0) or not (0.0 <= float(y) <= 1.0):
            raise ValidationError("image anchor x and y must be numbers between 0 and 1")
        norm_req = os.path.normpath(file_path.strip()).replace("\\", "/")
        image_paths = set()
        for entry in item.get("files") or []:
            if isinstance(entry, dict) and entry.get("kind") in ("image", "card") and entry.get("path"):
                image_paths.add(os.path.normpath(entry["path"]).replace("\\", "/"))
            elif isinstance(entry, str) and entry.strip():
                image_paths.add(os.path.normpath(entry.strip()).replace("\\", "/"))
        if norm_req not in image_paths:
            raise ValidationError(f"image anchor file '{file_path}' does not belong to this content item")
        return {"file": norm_req, "x": float(x), "y": float(y)}

    if kind == "video":
        file_path = anchor.get("file")
        t = anchor.get("t")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValidationError("video anchor requires non-empty file")
        if not _is_number(t) or float(t) < 0.0:
            raise ValidationError("video anchor t must be a number >= 0")
        return {"file": file_path.strip(), "t": float(t)}

    raise ValidationError(f"Unsupported comment kind '{kind}'")


def _extract_agent_mentions(body):
    seen = []
    for raw in _MENTION_RE.findall(body or ""):
        ag = agents.get_agent(raw)
        if ag is None:
            continue
        cid = ag["id"]
        if cid not in seen:
            seen.append(cid)
        if len(seen) >= _MAX_COMMENT_MENTIONS:
            break
    return seen


def _enqueue_review_job(params, actor="review_studio"):
    job = jobs.enqueue("review_action", actor=actor, max_attempts=1, params=params)
    jobs.start_worker()
    return job


def create_comment(
    content_id,
    *,
    kind="general",
    version=None,
    anchor=None,
    body="",
    author="user",
    parent_id=None,
    comment_id=None,
):
    """Create a validated review comment (or reply) and trigger @agent jobs if mentioned.

    A caller-chosen comment_id makes the call repeatable: the same id is never inserted twice.
    """
    item = _require_item(content_id, include_versions=True)

    clean_body = body.strip() if isinstance(body, str) else ""
    if not clean_body:
        raise ValidationError("comment body is required")

    parent = None
    if parent_id:
        parent = db.get_review_comment(parent_id)
        if parent is None or parent["content_id"] != item["id"]:
            raise NotFoundError(f"Parent comment '{parent_id}' not found on content item")

    raw_kind = (kind or (parent["kind"] if parent else "general")).strip().lower()
    if raw_kind not in COMMENT_KINDS:
        raise ValidationError(f"Invalid comment kind '{kind}'. Expected one of: {', '.join(COMMENT_KINDS)}")

    if version is None:
        target_ver = parent["version"] if parent else int(item.get("current_version") or 1)
    else:
        if not _is_int(version) or version < 1:
            raise ValidationError("version must be a positive integer")
        target_ver = int(version)

    _, ver_record = _require_version_record(item["id"], target_ver)
    validated_anchor = _validate_comment_anchor(
        item,
        ver_record.get("body") or "",
        raw_kind,
        anchor,
        is_reply=parent is not None,
    )

    clean_author = str(author or "user").strip() or "user"
    mentions = _extract_agent_mentions(clean_body)

    if comment_id:
        existing = db.get_review_comment(comment_id)
        if existing is not None and existing.get("content_id") == item["id"]:
            return existing
    created = db.create_review_comment(
        content_id=item["id"],
        version=target_ver,
        kind=raw_kind,
        anchor=validated_anchor,
        body=clean_body,
        author=clean_author,
        parent_id=parent["id"] if parent else None,
        status="open",
        mentions=mentions,
        comment_id=comment_id,
    )
    _publish(item["id"], "comment")

    triggered_jobs = []
    if clean_author == "user" and mentions:
        for agent_id in mentions:
            job = _enqueue_review_job(
                {
                    "action_type": "comment_agent",
                    "content_id": item["id"],
                    "comment_id": created["id"],
                    "agent_id": agent_id,
                },
                actor=f"comment:{agent_id}",
            )
            triggered_jobs.append(job["id"])

    out = dict(created)
    if triggered_jobs:
        out["job_id"] = triggered_jobs[0]
        out["job_ids"] = triggered_jobs
    return out


def update_comment(content_id, comment_id, *, body=None, status=None):
    """Edit comment body and/or resolve/reopen status."""
    _require_item(content_id)
    existing = db.get_review_comment(comment_id)
    if existing is None or existing["content_id"] != str(content_id):
        raise NotFoundError(f"Comment '{comment_id}' not found")

    if body is None and status is None:
        raise ValidationError("At least one of body or status is required")

    update_kwargs = {}
    if body is not None:
        if not isinstance(body, str) or not body.strip():
            raise ValidationError("body must be a non-empty string")
        clean_body = body.strip()
        update_kwargs["body"] = clean_body
        update_kwargs["mentions"] = _extract_agent_mentions(clean_body)

    if status is not None:
        norm_status = str(status).strip().lower()
        if norm_status not in COMMENT_STATUSES:
            raise ValidationError(f"Invalid status '{status}'. Expected one of: {', '.join(COMMENT_STATUSES)}")
        update_kwargs["status"] = norm_status

    updated = db.update_review_comment(comment_id, **update_kwargs)
    _publish(content_id, "comment")
    return updated


def delete_comment(content_id, comment_id, *, actor="user"):
    """Delete a user's own comment (and any replies)."""
    _require_item(content_id)
    existing = db.get_review_comment(comment_id)
    if existing is None or existing["content_id"] != str(content_id):
        raise NotFoundError(f"Comment '{comment_id}' not found")
    if existing["author"] != str(actor or "user"):
        raise ValidationError("Can only delete own comments")
    deleted = db.delete_review_comment(comment_id)
    _publish(content_id, "comment")
    return {"deleted": deleted, "id": str(comment_id)}


def _strip_code_fence(text):
    raw = str(text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return raw


def _diff_to_suggestions(old_body, new_body):
    """Convert old_body -> new_body into non-overlapping span tuples (start, end, original, replacement)."""
    old_text = str(old_body or "")
    new_text = str(new_body or "")
    if old_text == new_text:
        return []

    old_matches = list(_SEGMENT_RE.finditer(old_text))
    old_segs = [m.group(0) for m in old_matches]
    old_offsets = [(m.start(), m.end()) for m in old_matches]
    new_segs = _SEGMENT_RE.findall(new_text)

    sm = difflib.SequenceMatcher(None, old_segs, new_segs, autojunk=False)
    spans = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if old_offsets:
            if i1 < len(old_offsets):
                start = old_offsets[i1][0]
            else:
                start = old_offsets[-1][1]
            end = old_offsets[i2 - 1][1] if i2 > i1 else start
        else:
            start = 0
            end = 0
        original = old_text[start:end]
        replacement = "".join(new_segs[j1:j2])
        if original != replacement:
            spans.append((start, end, original, replacement))
    return spans


def _parse_agent_comment_reply(raw_text):
    """Extract (reply_text, optional_replacement) from an agent's comment response."""
    text = str(raw_text or "").strip()
    if not text:
        return "", None

    json_candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        json_candidates.insert(0, fenced.group(1))
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        json_candidates.append(brace_match.group(0))

    for cand in json_candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and ("reply" in obj or "replacement" in obj or "rewrite" in obj):
                reply = str(obj.get("reply") or obj.get("comment") or text).strip()
                rep = obj.get("replacement")
                if rep is None:
                    rep = obj.get("rewrite")
                rep_str = str(rep).strip() if rep is not None and str(rep).strip() else None
                return reply, rep_str
        except Exception:
            pass

    rewrite_match = re.search(
        r"(?:^|\n)\s*(?:REWRITE|SUGGESTION)\s*:\s*(.+)$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if rewrite_match:
        replacement = _strip_code_fence(rewrite_match.group(1).strip())
        reply_part = text[: rewrite_match.start()].strip() or f"Proposed rewrite: {replacement}"
        return reply_part, replacement or None

    return text, None


def apply_all(content_id, *, agent=None, as_suggestions=False):
    """Enqueue a background job to address all open comments on the current version."""
    item = _require_item(content_id, include_versions=True)
    current_ver = int(item.get("current_version") or 1)
    open_comments = db.list_review_comments(item["id"], version=current_ver, status="open")
    if not open_comments:
        raise ValidationError("No open comments on current version")

    agent_key = (agent or "writer").strip() or "writer"
    agent_cfg = agents.get_agent(agent_key)
    if agent_cfg is None:
        raise ValidationError(f"Unknown agent '{agent_key}'")

    job = _enqueue_review_job(
        {
            "action_type": "apply_all",
            "content_id": item["id"],
            "agent_id": agent_cfg["id"],
            "as_suggestions": bool(as_suggestions),
        },
        actor=f"apply_all:{agent_cfg['id']}",
    )
    return {"job_id": job["id"], "job": job}


def _validate_selection(selection, body):
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValidationError("selection must be an object")
    start = selection.get("start")
    end = selection.get("end")
    if not _is_int(start) or not _is_int(end):
        raise ValidationError("selection requires integer start and end offsets")
    if start < 0 or end <= start or end > len(body):
        raise ValidationError("selection offsets are out of bounds")
    quote = selection.get("quote")
    if quote is not None and body[start:end] != quote:
        raise ValidationError("selection quote does not match body at offsets")
    return {"start": start, "end": end, "quote": body[start:end]}


def _image_entries_for_item(item):
    return [
        f
        for f in (item.get("files") or [])
        if isinstance(f, dict) and f.get("kind") in ("image", "card") and f.get("path")
    ]


def ai_action(content_id, action, *, selection=None, params=None):
    """Validate an AI action request and enqueue it as a background job."""
    item = _require_item(content_id, include_versions=True)
    act = str(action or "").strip().lower()
    if not act or act not in ALL_AI_ACTIONS:
        raise ValidationError(f"Unknown action '{action}'")

    extra = dict(params or {}) if params is not None else {}
    if params is not None and not isinstance(params, dict):
        raise ValidationError("params must be an object")

    body = item.get("body") or ""
    validated_sel = _validate_selection(selection, body)

    if act in IMAGE_AI_ACTIONS:
        images = _image_entries_for_item(item)
        if not images:
            raise ValidationError("No image or card attached to this content item")
        target_file = extra.get("file")
        if target_file:
            norm_target = os.path.normpath(str(target_file)).replace("\\", "/")
            if not any(os.path.normpath(img["path"]).replace("\\", "/") == norm_target for img in images):
                raise ValidationError(f"Image file '{target_file}' does not belong to this content item")

    job = _enqueue_review_job(
        {
            "action_type": "ai_action",
            "content_id": item["id"],
            "action": act,
            "selection": validated_sel,
            "params": extra,
        },
        actor=f"ai_action:{act}",
    )
    return {"job_id": job["id"], "job": job}


def _resolve_suggestion_span(sug, current_version, current_body):
    """Return (start, end, None) if valid against current_body, else (None, None, reject_reason)."""
    original = sug["original"]
    if int(sug["base_version"]) == int(current_version):
        start = int(sug["start"])
        end = int(sug["end"])
        if start < 0 or end < start or end > len(current_body):
            return None, None, "Original offsets are out of bounds"
        if current_body[start:end] != original:
            return None, None, "Original text no longer matches body at offsets"
        return start, end, None

    if not original:
        return None, None, "Cannot relocate empty insertion from older version"

    matches = _find_all_occurrences(current_body, original)
    if not matches:
        return None, None, "Original text not found in current version"
    best = min(matches, key=lambda idx: abs(idx - int(sug["start"])))
    return best, best + len(original), None


def _apply_suggestion_batch(item, candidates):
    """Validate, reject stale/overlapping candidates, and apply accepted ones end-to-start."""
    cid = item["id"]
    cur_ver = int(item.get("current_version") or 1)
    cur_body = item.get("body") or ""
    now = _now_iso()

    resolved = []
    rejected = []
    for sug in candidates:
        start, end, err = _resolve_suggestion_span(sug, cur_ver, cur_body)
        if err:
            rej = db.update_review_suggestion(sug["id"], status="rejected", reason=err, decided_at=now)
            rejected.append(rej)
        else:
            resolved.append((start, end, sug))

    # Detect overlaps in ascending span order; adjacent spans (start == last_end) do NOT overlap.
    resolved.sort(key=lambda entry: (entry[0], entry[1], entry[2]["created_at"]))
    accepted_items = []
    last_start = -1
    last_end = -1
    last_was_empty = False
    for start, end, sug in resolved:
        is_overlap = (start < last_end) or (start == end == last_start and last_was_empty)
        if is_overlap:
            rej = db.update_review_suggestion(
                sug["id"],
                status="rejected",
                reason="Overlaps with another accepted suggestion",
                decided_at=now,
            )
            rejected.append(rej)
        else:
            accepted_items.append((start, end, sug))
            last_start = start
            last_end = end
            last_was_empty = start == end

    if not accepted_items:
        _publish(cid, "suggestion")
        return {
            "content_id": cid,
            "version_created": False,
            "version": cur_ver,
            "accepted": [],
            "rejected": rejected,
            "item": item,
        }

    # Apply from end of text to start so earlier character offsets remain valid.
    new_body = cur_body
    for start, end, sug in sorted(accepted_items, key=lambda entry: (entry[1], entry[0]), reverse=True):
        new_body = new_body[:start] + sug["replacement"] + new_body[end:]

    updated_item = db.add_content_version(
        cid,
        body=new_body,
        notes=f"Accepted {len(accepted_items)} suggestion(s)",
        created_by="user",
    )
    accepted_rows = []
    for _, _, sug in accepted_items:
        acc = db.update_review_suggestion(sug["id"], status="accepted", decided_at=now)
        accepted_rows.append(acc)

    updated_item = _on_new_version(cid, updated_item["current_version"], updated_item["body"])
    _publish(cid, "suggestion")
    return {
        "content_id": cid,
        "version_created": True,
        "version": updated_item["current_version"],
        "accepted": accepted_rows,
        "rejected": rejected,
        "item": updated_item,
    }


def decide_suggestion(content_id, suggestion_id, decision):
    """Accept or reject a single pending suggestion."""
    item = _require_item(content_id, include_versions=True)
    dec = str(decision or "").strip().lower()
    if dec not in SUGGESTION_DECISIONS:
        raise ValidationError("decision must be 'accept' or 'reject'")

    sug = db.get_review_suggestion(suggestion_id)
    if sug is None or sug["content_id"] != item["id"]:
        raise NotFoundError(f"Suggestion '{suggestion_id}' not found")
    if sug["status"] != "pending":
        raise ValidationError(f"Suggestion '{suggestion_id}' is already {sug['status']}")

    if dec == "reject":
        rej = db.update_review_suggestion(suggestion_id, status="rejected", decided_at=_now_iso())
        _publish(item["id"], "suggestion")
        return {"content_id": item["id"], "version_created": False, "rejected": [rej], "accepted": []}

    return _apply_suggestion_batch(item, [sug])


def decide_all_suggestions(content_id, decision):
    """Accept or reject all pending suggestions for content_id."""
    item = _require_item(content_id, include_versions=True)
    dec = str(decision or "").strip().lower()
    if dec not in SUGGESTION_DECISIONS:
        raise ValidationError("decision must be 'accept' or 'reject'")

    pending = db.list_review_suggestions(item["id"], status="pending")
    if dec == "reject":
        now = _now_iso()
        rejected = [
            db.update_review_suggestion(s["id"], status="rejected", decided_at=now)
            for s in pending
        ]
        _publish(item["id"], "suggestion")
        return {"content_id": item["id"], "version_created": False, "rejected": rejected, "accepted": []}

    return _apply_suggestion_batch(item, pending)


def update_checklist(content_id, *, item_key=None, label=None, checked=True):
    """Toggle an existing checklist item or add a manual checklist item."""
    item = _require_item(content_id, include_versions=True)
    if not isinstance(checked, bool):
        raise ValidationError("checked must be a boolean")

    existing = {row["item_key"]: row for row in db.list_review_checklist(item["id"])}
    key = str(item_key).strip() if item_key is not None and str(item_key).strip() else None

    if key and key in existing:
        row = existing[key]
        new_label = str(label).strip() if isinstance(label, str) and label.strip() else row["label"]
        updated = db.upsert_review_checklist_item(
            item["id"],
            key,
            new_label,
            row["source"],
            checked,
        )
    else:
        clean_label = str(label or "").strip()
        if not clean_label:
            raise ValidationError("label is required for new checklist item")
        new_key = key or f"manual:{uuid.uuid4().hex[:10]}"
        updated = db.upsert_review_checklist_item(
            item["id"],
            new_key,
            clean_label,
            "manual",
            checked,
        )

    _publish(item["id"], "checklist")
    return {
        "content_id": item["id"],
        "item": updated,
        "checklist": db.list_review_checklist(item["id"]),
    }


def mark_ready(content_id):
    """Mark content_id ready (warn-only; returns open auto_review warnings)."""
    _require_item(content_id)
    try:
        updated = db.mark_ready(content_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise NotFoundError(msg)
        raise ValidationError(msg)
    auto_res = review.auto_review(updated)
    _publish(content_id, "status")
    return {
        "item": updated,
        "status": updated["status"],
        "warnings": list(auto_res.get("findings") or []),
        "verdict": auto_res.get("verdict", "ok"),
        "auto_review": auto_res,
    }


def build_posting_kit_for_item(content_id):
    """Generate POSTING-KIT.md via posting_kit and return its text and file paths."""
    item = _require_item(content_id, include_versions=True)
    rel_path = posting_kit.build_posting_kit([item["id"]])
    kit_abs = paths.data(rel_path)
    with open(kit_abs, "r", encoding="utf-8") as f:
        kit_text = f.read()

    refreshed = _require_item(item["id"], include_versions=True)
    file_paths = []
    for entry in refreshed.get("files") or []:
        if isinstance(entry, dict) and entry.get("path"):
            p = entry["path"]
            if p not in file_paths:
                file_paths.append(p)
    return {
        "content_id": item["id"],
        "path": rel_path,
        "text": kit_text,
        "files": file_paths,
        "attachments": refreshed.get("files") or [],
    }


def mark_posted(content_id, *, url=None, platform=None):
    """Mark content_id as posted via db.mark_posted."""
    _require_item(content_id)
    if url is not None and not isinstance(url, str):
        raise ValidationError("url must be a string")
    if platform is not None and not isinstance(platform, str):
        raise ValidationError("platform must be a string")
    clean_url = url.strip() if isinstance(url, str) and url.strip() else None
    clean_plat = platform.strip() if isinstance(platform, str) and platform.strip() else None
    updated = db.mark_posted(content_id, url=clean_url, platform=clean_plat)
    _publish(content_id, "status")
    return {
        "item": updated,
        "status": updated["status"],
        "posted_url": updated.get("posted_url"),
        "posted_at": updated.get("posted_at"),
        "platform": updated.get("platform"),
    }


def unmark_posted(content_id, *, status="ready"):
    """Revert a posted content_id back to ready/draft and clear pending results reminders."""
    _require_item(content_id)
    updated = db.unmark_posted(content_id, status=status)
    _publish(content_id, "status")
    return {
        "item": updated,
        "status": updated["status"],
    }


def _run_comment_agent_job(params, job_id=None):
    content_id = params["content_id"]
    comment_id = params["comment_id"]
    agent_id = params["agent_id"]

    item = _require_item(content_id, include_versions=True)
    comment = db.get_review_comment(comment_id)
    if comment is None:
        raise NotFoundError(f"Comment '{comment_id}' not found")

    agent_cfg = agents.get_agent(agent_id)
    if agent_cfg is None:
        raise ValidationError(f"Unknown agent '{agent_id}'")
    canon_id = agent_cfg["id"]
    _, is_conf = _resolve_product_and_confidential(item)

    if job_id:
        jobs.progress(job_id, 25, f"Running @{canon_id} on comment...")

    ver_body = item.get("body") or ""
    if comment.get("version") != item.get("current_version"):
        for v in item.get("versions") or []:
            if int(v["version"]) == int(comment["version"]):
                ver_body = v.get("body") or ""
                break

    anchor = comment.get("anchor") or {}
    anchor_desc = "General item comment"
    if comment["kind"] == "text" and anchor.get("quote"):
        anchor_desc = (
            f"Anchored text [{anchor.get('start')}:{anchor.get('end')}]:\n"
            f"\"{anchor.get('quote')}\""
        )
    elif comment["kind"] == "image":
        anchor_desc = f"Image pin on {anchor.get('file')} at ({anchor.get('x')}, {anchor.get('y')})"
    elif comment["kind"] == "video":
        anchor_desc = f"Video timestamp on {anchor.get('file')} at {anchor.get('t')}s"

    context_block = _build_product_context_block(item, confidential=is_conf)
    user_prompt = (
        f"You were mentioned in a Review Studio comment on content item '{item.get('title')}' "
        f"(id={item['id']}, type={item.get('type')}, platform={item.get('platform')}).\n\n"
        f"{context_block}\n\n"
        f"Item body (v{comment['version']}):\n{ver_body}\n\n"
        f"{anchor_desc}\n\n"
        f"User comment:\n{comment['body']}\n\n"
        "Instructions:\n"
        "Answer the comment directly with zero emojis and obey all product rules.\n"
        "If this is a text-anchored comment and you propose a concrete replacement for the anchored text, "
        "return JSON: {\"reply\": \"...\", \"replacement\": \"exact replacement for anchored text\"}. "
        "Otherwise return a plain concise reply."
    )

    allowed_tools = [t for t in (agent_cfg.get("tools") or []) if t in _READ_ONLY_REVIEW_TOOLS]
    sess = agent_runtime.start_session(
        canon_id,
        user_prompt,
        system_prompt=agents.build_system_prompt(canon_id, confidential=is_conf),
        allowed_tools=allowed_tools,
        confidential=is_conf,
        title=f"Review comment @{canon_id}: {item['id']}",
    )
    if sess.get("status") != "done":
        raise RuntimeError(sess.get("error") or f"Agent session ended with status {sess.get('status')}")

    reply_text, replacement = _parse_agent_comment_reply(sess.get("result_text") or "")
    if not reply_text:
        reply_text = "Reviewed."

    root_parent_id = comment.get("parent_id") or comment["id"]
    reply_comment = db.create_review_comment(
        content_id=item["id"],
        version=comment["version"],
        kind=comment["kind"],
        anchor={},
        body=reply_text,
        author=canon_id,
        parent_id=root_parent_id,
        status="open",
        mentions=[],
    )
    _publish(item["id"], "comment")

    suggestion = None
    auto_target = dict(item)
    if (
        replacement
        and comment["kind"] == "text"
        and _is_int(anchor.get("start"))
        and _is_int(anchor.get("end"))
        and anchor.get("quote")
    ):
        suggestion = db.create_review_suggestion(
            content_id=item["id"],
            base_version=comment["version"],
            start=anchor["start"],
            end=anchor["end"],
            original=anchor["quote"],
            replacement=replacement,
            author=canon_id,
            reason=reply_text[:200],
            status="pending",
        )
        _publish(item["id"], "suggestion")
        if int(comment["version"]) == int(item.get("current_version") or 1):
            s_off, e_off = int(anchor["start"]), int(anchor["end"])
            auto_target["body"] = ver_body[:s_off] + replacement + ver_body[e_off:]

    auto_res = review.auto_review(auto_target)
    return {
        "content_id": item["id"],
        "session_id": sess["id"],
        "reply_comment": reply_comment,
        "suggestion": suggestion,
        "warnings": list(auto_res.get("findings") or []),
        "auto_review": auto_res,
    }


def _run_apply_all_job(params, job_id=None):
    content_id = params["content_id"]
    agent_id = params.get("agent_id") or "writer"
    as_suggestions = bool(params.get("as_suggestions"))

    item = _require_item(content_id, include_versions=True)
    current_ver = int(item.get("current_version") or 1)
    old_body = item.get("body") or ""
    open_comments = db.list_review_comments(item["id"], version=current_ver, status="open")
    if not open_comments:
        raise ValidationError("No open comments on current version")

    if job_id:
        jobs.progress(job_id, 25, f"Applying {len(open_comments)} comment(s) via {agent_id}...")

    _, is_conf = _resolve_product_and_confidential(item)
    context_block = _build_product_context_block(item, confidential=is_conf)

    comment_lines = []
    for idx, cmt in enumerate(open_comments, start=1):
        anchor = cmt.get("anchor") or {}
        loc = cmt["kind"]
        if cmt["kind"] == "text" and anchor.get("quote"):
            loc = f"text [{anchor.get('start')}:{anchor.get('end')}] \"{anchor.get('quote')}\""
        elif cmt["kind"] == "image":
            loc = f"image {anchor.get('file')} ({anchor.get('x')}, {anchor.get('y')})"
        elif cmt["kind"] == "video":
            loc = f"video {anchor.get('file')} @ {anchor.get('t')}s"
        comment_lines.append(f"{idx}. ({loc}) [{cmt['author']}]: {cmt['body']}")

    user_prompt = (
        f"Revise this {item.get('type')} for {item.get('platform') or 'general'} "
        f"(title: {item.get('title')}) to address all open review comments below.\n\n"
        f"{context_block}\n\n"
        f"Current body (v{current_ver}):\n{old_body}\n\n"
        f"Open comments:\n" + "\n".join(comment_lines) + "\n\n"
        "Return ONLY the full revised body text. No preamble, no markdown code fences, and zero emojis."
    )

    res = ai_gateway.generate_completion_with_meta(
        system_prompt=agents.build_system_prompt(agent_id, confidential=is_conf),
        user_prompt=user_prompt,
        agent_role=agent_id,
        confidential=is_conf,
    )
    new_body = _strip_code_fence(res.get("text") or "")
    if not new_body:
        raise RuntimeError("Model returned an empty revision")

    auto_res = review.auto_review({**item, "body": new_body})

    if as_suggestions:
        spans = _diff_to_suggestions(old_body, new_body)
        created_suggestions = [
            db.create_review_suggestion(
                content_id=item["id"],
                base_version=current_ver,
                start=start,
                end=end,
                original=original,
                replacement=replacement,
                author=agent_id,
                reason=f"apply_all ({len(open_comments)} comments)",
                status="pending",
            )
            for start, end, original, replacement in spans
        ]
        _publish(item["id"], "suggestion")
        return {
            "content_id": item["id"],
            "as_suggestions": True,
            "suggestions": created_suggestions,
            "warnings": list(auto_res.get("findings") or []),
            "auto_review": auto_res,
        }

    updated = db.add_content_version(
        item["id"],
        body=new_body,
        notes=f"Applied {len(open_comments)} comments",
        created_by=agent_id,
    )
    for cmt in open_comments:
        db.update_review_comment(cmt["id"], status="applied")
    _on_new_version(item["id"], updated["current_version"], updated["body"])
    _publish(item["id"], "comment")
    return {
        "content_id": item["id"],
        "as_suggestions": False,
        "version": updated["current_version"],
        "applied_count": len(open_comments),
        "warnings": list(auto_res.get("findings") or []),
        "auto_review": auto_res,
    }


def _safe_data_file(rel_path):
    real_root = os.path.realpath(paths.DATA_ROOT)
    norm_rel = os.path.normpath(str(rel_path)).replace("\\", "/")
    full_path = os.path.realpath(os.path.join(paths.DATA_ROOT, norm_rel))
    try:
        inside = os.path.commonpath([real_root, full_path]) == real_root
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(full_path):
        raise ValidationError(f"Image file not found in DATA_ROOT: {rel_path}")
    return full_path


def _run_alt_text_action(item, extra, is_conf, context_block):
    files = list(item.get("files") or [])
    target_file = extra.get("file")
    norm_target = os.path.normpath(str(target_file)).replace("\\", "/") if target_file else None

    updated_images = []
    for entry in files:
        if not isinstance(entry, dict) or entry.get("kind") not in ("image", "card") or not entry.get("path"):
            continue
        norm_p = os.path.normpath(entry["path"]).replace("\\", "/")
        if norm_target and norm_p != norm_target:
            continue
        abs_path = _safe_data_file(norm_p)
        user_prompt = (
            f"Write concise, accurate, screen-reader-friendly alt text for this image attached to "
            f"'{item.get('title')}' (platform={item.get('platform') or 'social'}).\n\n"
            f"{context_block}\n\n"
            "Return ONLY the alt text sentence (no quotes, no prefix, zero emojis)."
        )
        res = ai_gateway.generate_completion_with_meta(
            system_prompt=agents.build_system_prompt("designer", confidential=is_conf),
            user_prompt=user_prompt,
            model_tier="vision",
            tier="vision",
            image_paths=[abs_path],
            agent_role="designer",
            confidential=is_conf,
        )
        alt_text = _strip_code_fence(res.get("text") or "").strip().strip('"')
        if not alt_text:
            raise RuntimeError(f"Vision model returned empty alt text for {norm_p}")
        entry["alt_text"] = alt_text
        updated_images.append({"path": norm_p, "alt_text": alt_text, "model_used": res.get("model_used")})

    if not updated_images:
        raise ValidationError("No matching image files to describe")

    refreshed = db.set_content_files(item["id"], files)
    refreshed = _require_item(item["id"], include_versions=True)
    _recompute_checklist(refreshed)
    _publish(item["id"], "checklist")
    auto_res = review.auto_review(refreshed)
    return {
        "content_id": item["id"],
        "action": "alt_text",
        "alt_texts": updated_images,
        "warnings": list(auto_res.get("findings") or []),
        "auto_review": auto_res,
    }


def _run_blog_ai_action(item, act, is_conf, context_block):
    body = item.get("body") or ""
    current_ver = int(item.get("current_version") or 1)
    if act == "seo_title_meta":
        role = "writer"
        instruction = (
            "Generate an SEO title (<= 60 chars), meta description (<= 155 chars), and URL slug "
            "for this blog post. Only describe what the article says."
        )
    elif act == "tldr":
        role = "writer"
        instruction = "Write a concise TL;DR summary (2-3 sentences) for this blog post based strictly on its content."
    else:
        role = "reviewer"
        instruction = (
            "Check the blog outline, heading hierarchy ('##' headings), TL;DR presence, and section flow. "
            "Provide actionable feedback without rewriting the body."
        )

    user_prompt = (
        f"Blog title: {item.get('title')}\n\n"
        f"{context_block}\n\n"
        f"Blog body (v{current_ver}):\n{body}\n\n"
        f"Task ({act}): {instruction}\n"
        "Zero emojis."
    )
    res = ai_gateway.generate_completion_with_meta(
        system_prompt=agents.build_system_prompt(role, confidential=is_conf),
        user_prompt=user_prompt,
        agent_role=role,
        confidential=is_conf,
    )
    comment_text = _strip_code_fence(res.get("text") or "")
    if not comment_text:
        raise RuntimeError(f"Model returned empty output for {act}")

    comment = db.create_review_comment(
        content_id=item["id"],
        version=current_ver,
        kind="general",
        anchor={"ai_action": act},
        body=comment_text,
        author=role,
        status="open",
        mentions=[],
    )
    _publish(item["id"], "comment")

    auto_res = review.auto_review(item)
    comment_warnings = product_rules.check_text(comment_text, path=_rules_path()) if os.path.exists(_rules_path()) else []
    combined_warnings = list(auto_res.get("findings") or []) + [
        {"code": "product_rule", "severity": w.get("severity", "warn"), "message": w.get("message", "")}
        for w in comment_warnings
    ]
    return {
        "content_id": item["id"],
        "action": act,
        "comment": comment,
        "warnings": combined_warnings,
        "auto_review": auto_res,
    }


def _first_non_empty_line_span(body):
    pos = 0
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.strip():
            return pos, pos + len(stripped)
        pos += len(line)
    return 0, len(body)


def _run_text_ai_action(item, act, selection, is_conf, context_block):
    body = item.get("body") or ""
    current_ver = int(item.get("current_version") or 1)
    action_guides = {
        "shorten": "Shorten the text while keeping all verified facts and key meaning.",
        "expand": "Expand the text with concrete detail using only approved facts (never invent claims).",
        "rephrase": "Rephrase for clearer flow and sharper developer voice.",
        "simplify": "Simplify into plain, direct language without jargon or fluff.",
        "formal": "Rewrite in a clear, measured, professional tone.",
        "casual": "Rewrite in a natural, conversational builder tone.",
        "fix_grammar": "Fix grammar, spelling, and punctuation only without changing meaning.",
        "stronger_hook": "Make the opening hook sharper and more compelling within the platform's visible preview.",
        "hashtags": "Add or refine relevant hashtags obeying the target platform's hashtag limits.",
        "fit_platform": "Edit the text so it strictly fits the target platform's character, word, hook, and hashtag limits.",
    }
    guide = action_guides[act]

    if selection is not None:
        start = int(selection["start"])
        end = int(selection["end"])
        original = body[start:end]
        user_prompt = (
            f"Content item: {item.get('title')} (type={item.get('type')}, platform={item.get('platform')})\n\n"
            f"{context_block}\n\n"
            f"Full body:\n{body}\n\n"
            f"Selected span [{start}:{end}]:\n{original}\n\n"
            f"Action ({act}): {guide}\n"
            "Return ONLY the replacement text for the selected span. No preamble, no quotes, zero emojis."
        )
        res = ai_gateway.generate_completion_with_meta(
            system_prompt=agents.build_system_prompt("writer", confidential=is_conf),
            user_prompt=user_prompt,
            agent_role="writer",
            confidential=is_conf,
        )
        replacement = _strip_code_fence(res.get("text") or "")
        if not replacement:
            raise RuntimeError(f"Model returned empty replacement for {act}")
        sug = db.create_review_suggestion(
            content_id=item["id"],
            base_version=current_ver,
            start=start,
            end=end,
            original=original,
            replacement=replacement,
            author="writer",
            reason=f"ai_action:{act}",
            status="pending",
        )
        _publish(item["id"], "suggestion")
        hypothetical = body[:start] + replacement + body[end:]
        auto_res = review.auto_review({**item, "body": hypothetical})
        return {
            "content_id": item["id"],
            "action": act,
            "suggestions": [sug],
            "warnings": list(auto_res.get("findings") or []),
            "auto_review": auto_res,
        }

    hook_note = ""
    if act == "stronger_hook" and body.strip():
        h_start, h_end = _first_non_empty_line_span(body)
        hook_note = f"\nFocus specifically on improving the first line: \"{body[h_start:h_end]}\"."

    user_prompt = (
        f"Content item: {item.get('title')} (type={item.get('type')}, platform={item.get('platform')})\n\n"
        f"{context_block}\n\n"
        f"Full body:\n{body}\n\n"
        f"Action ({act}): {guide}{hook_note}\n"
        "Return ONLY the full revised body text. No preamble, no markdown code fences, zero emojis."
    )
    res = ai_gateway.generate_completion_with_meta(
        system_prompt=agents.build_system_prompt("writer", confidential=is_conf),
        user_prompt=user_prompt,
        agent_role="writer",
        confidential=is_conf,
    )
    revised_body = _strip_code_fence(res.get("text") or "")
    if not revised_body:
        raise RuntimeError(f"Model returned empty text for {act}")

    spans = _diff_to_suggestions(body, revised_body)
    created_suggestions = [
        db.create_review_suggestion(
            content_id=item["id"],
            base_version=current_ver,
            start=start,
            end=end,
            original=original,
            replacement=replacement,
            author="writer",
            reason=f"ai_action:{act}",
            status="pending",
        )
        for start, end, original, replacement in spans
    ]
    _publish(item["id"], "suggestion")
    auto_res = review.auto_review({**item, "body": revised_body})
    return {
        "content_id": item["id"],
        "action": act,
        "suggestions": created_suggestions,
        "warnings": list(auto_res.get("findings") or []),
        "auto_review": auto_res,
    }


def _run_ai_action_job(params, job_id=None):
    content_id = params["content_id"]
    act = params["action"]
    selection = params.get("selection")
    extra = params.get("params") or {}

    item = _require_item(content_id, include_versions=True)
    _, is_conf = _resolve_product_and_confidential(item)
    context_block = _build_product_context_block(item, confidential=is_conf)

    if job_id:
        jobs.progress(job_id, 25, f"Running AI action '{act}'...")

    if act in IMAGE_AI_ACTIONS:
        return _run_alt_text_action(item, extra, is_conf, context_block)
    if act in BLOG_AI_ACTIONS:
        return _run_blog_ai_action(item, act, is_conf, context_block)
    if act in TEXT_AI_ACTIONS:
        return _run_text_ai_action(item, act, selection, is_conf, context_block)
    raise ValidationError(f"Unknown action '{act}'")


def enrich_content_list(items):
    """Attach open_comments_count and warnings_count to content summary dicts."""
    if not items:
        return []
    db.ensure_init()
    conn = db.connect()
    counts = {}
    for row in conn.execute(
        "SELECT content_id, COUNT(*) AS cnt FROM review_comments WHERE parent_id IS NULL AND status = 'open' GROUP BY content_id"
    ).fetchall():
        counts[row["content_id"]] = int(row["cnt"])
    out = []
    for raw in items:
        entry = dict(raw)
        cid = entry.get("id")
        entry["open_comments_count"] = counts.get(cid, 0)
        try:
            full = db.get_content_item(cid)
            auto_res = review.auto_review(full) if full is not None else {"findings": []}
            entry["warnings_count"] = len(auto_res.get("findings") or [])
        except Exception:
            entry["warnings_count"] = 0
        out.append(entry)
    return out


def execute_review_job(params, job_id=None, log_file=None):
    """Execute a queued Review Studio background job."""
    payload = dict(params or {})
    action_type = payload.get("action_type")
    content_id = payload.get("content_id")
    result = None
    try:
        if action_type == "comment_agent":
            result = _run_comment_agent_job(payload, job_id=job_id)
            return result
        if action_type == "apply_all":
            result = _run_apply_all_job(payload, job_id=job_id)
            return result
        if action_type == "ai_action":
            result = _run_ai_action_job(payload, job_id=job_id)
            return result
        raise ValidationError(f"Unknown review job action_type '{action_type}'")
    finally:
        if content_id:
            extra = None
            if isinstance(result, dict) and "warnings" in result:
                extra = {"warnings": result["warnings"]}
            _publish(content_id, "job", extra=extra)
