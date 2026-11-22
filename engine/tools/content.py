#!/usr/bin/env python3
"""Content draft, review, and performance metrics tools."""

from datetime import datetime, timezone
import os
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import confidential_guard
import db
import paths
import platforms
import product_rules
import results_extract
import review
from tools import tool

VALID_CONTENT_TYPES = (
    "post",
    "thread",
    "blog",
    "script",
    "card",
    "image",
    "review",
    "plan",
)
VALID_VERDICTS = ("ok", "warn")
VALID_METRIC_SOURCES = ("screenshot", "manual")


def _rules_path():
    candidate = paths.data("config", "product_rules.json")
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource("config", "product_rules.json")
    return fallback if os.path.exists(fallback) else candidate


def _write_markdown_copy(content_id, version, content_type, platform, title, body, notes=None):
    """Write a human-readable markdown file to outputs/content/<content_id>/v<N>.md and return relative path."""
    out_dir = paths.data("outputs", "content", content_id)
    os.makedirs(out_dir, exist_ok=True)
    file_path = os.path.join(out_dir, f"v{version}.md")
    lines = [
        f"# {title}",
        "",
        f"- Content ID: {content_id}",
        f"- Type: {content_type}",
        f"- Platform: {platform or 'unspecified'}",
        f"- Version: v{version}",
    ]
    if notes:
        lines.append(f"- Notes: {notes}")
    lines.extend(["", body or "", ""])
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return os.path.relpath(file_path, paths.DATA_ROOT)


@tool(
    name="write_draft",
    description="Create a new content draft (v1) or append a new version (vN) to an existing content_id, mirror to markdown, and return product/platform warnings.",
    parameters_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": list(VALID_CONTENT_TYPES),
                "description": "Content type: post, thread, blog, script, card, image, review, or plan.",
            },
            "platform": {
                "type": "string",
                "description": "Target platform (e.g. x, linkedin, instagram, youtube, blog).",
            },
            "title": {
                "type": "string",
                "description": "Short title or headline for the content item.",
            },
            "body": {
                "type": "string",
                "description": "Full draft body text.",
            },
            "content_id": {
                "type": "string",
                "description": "Optional existing content_id to create a new version on.",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes, claims sources, or revision context.",
            },
            "product_id": {
                "type": "string",
                "description": "Optional product ID (e.g. 'my-product').",
            },
        },
        "required": ["type", "platform", "title", "body"],
    },
    side_effects=True,
)
def write_draft(ctx, type, platform, title, body, content_id=None, notes=None, product_id=None):
    ctype = (type or "").strip().lower()
    if ctype not in VALID_CONTENT_TYPES:
        return {"error": f"Invalid content type '{type}'. Expected one of: {', '.join(VALID_CONTENT_TYPES)}"}

    agent_id = ctx.agent_id if ctx else None
    session_id = ctx.session_id if ctx else None
    now = datetime.now(timezone.utc).isoformat()

    if content_id:
        existing = db.get_content_item(content_id)
        if existing is None:
            return {"error": f"Content item '{content_id}' not found"}
        item = db.add_content_version(
            content_id=content_id,
            body=body,
            notes=notes,
            created_by=agent_id,
            title=title,
            platform=platform,
            product_id=product_id,
            created_at=now,
        )
    else:
        cid = f"cnt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        item = db.create_content_item(
            content_id=cid,
            content_type=ctype,
            platform=platform,
            title=title,
            body=body,
            notes=notes,
            status="draft",
            product_id=product_id,
            created_by=agent_id,
            session_id=session_id,
            created_at=now,
        )

    cid = item["id"]
    ver = item["version"]
    rel_path = _write_markdown_copy(cid, ver, ctype, platform, title, body, notes=notes)
    db.append_content_files(cid, [{"kind": "draft", "version": ver, "path": rel_path}])

    warnings = list(product_rules.check_text(body, path=_rules_path()))
    warnings.extend(platforms.check_limits(platform, body, title=title, content_type=ctype))

    return {
        "content_id": cid,
        "version": ver,
        "path": rel_path,
        "warnings": warnings,
    }


@tool(
    name="read_content",
    description="Read a content item by content_id, optionally requesting a specific version number.",
    parameters_schema={
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "The content item ID."},
            "version": {"type": "integer", "description": "Optional specific version number."},
        },
        "required": ["content_id"],
    },
)
def read_content(ctx, content_id, version=None):
    item = db.get_content_item(content_id, version=version, include_versions=False)
    if item is None:
        return {"error": f"Content item '{content_id}' not found"}
    if not confidential_guard.is_local(ctx):
        refusal = confidential_guard.local_or_refusal(ctx, confidential_guard.scope().item(item))
        if refusal:
            return refusal
    return item


@tool(
    name="list_content",
    description="List content items, optionally filtered by status and/or type.",
    parameters_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["draft", "ready", "posted"],
                "description": "Optional status filter.",
            },
            "type": {
                "type": "string",
                "enum": list(VALID_CONTENT_TYPES),
                "description": "Optional content type filter.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of items to return (default 20).",
            },
        },
        "required": [],
    },
)
def list_content(ctx, status=None, type=None, limit=20):
    if confidential_guard.is_local(ctx):
        return {"items": db.list_content_items(status=status, content_type=type, limit=limit)}
    # Confidential items are left out of lists, even their titles and ids.
    max_items = max(1, min(int(limit or 20), 500))
    scope = confidential_guard.scope()
    items = [i for i in db.list_content_items(status=status, content_type=type, limit=500) if not scope.item(i)]
    return {"items": items[:max_items]}


@tool(
    name="write_review",
    description="Record a review verdict ('ok' or 'warn') and notes for a content item without blocking.",
    parameters_schema={
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "Target content_id being reviewed."},
            "verdict": {
                "type": "string",
                "enum": list(VALID_VERDICTS),
                "description": "Review verdict: 'ok' or 'warn'.",
            },
            "notes": {"type": "string", "description": "Numbered findings, fixes, or confirmation notes."},
        },
        "required": ["content_id", "verdict", "notes"],
    },
    side_effects=True,
)
def write_review(ctx, content_id, verdict, notes):
    norm_verdict = (verdict or "").strip().lower()
    if norm_verdict not in VALID_VERDICTS:
        return {"error": f"Invalid verdict '{verdict}'. Expected 'ok' or 'warn'."}

    target = db.get_content_item(content_id, include_versions=True)
    if target is None:
        return {"error": f"Content item '{content_id}' not found"}
    # The result carries findings drawn from the item's text, so a confidential item switches the session first.
    if not confidential_guard.is_local(ctx):
        refusal = confidential_guard.local_or_refusal(ctx, confidential_guard.scope().item(target))
        if refusal:
            return refusal

    auto = review.auto_review(target)
    auto_findings = list(auto.get("findings") or [])
    final_verdict = "warn" if (norm_verdict == "warn" or auto.get("verdict") == "warn") else "ok"

    base_notes = str(notes or "").strip()
    if auto_findings:
        auto_lines = "\n".join(
            f"- [{f['severity']}] ({f['code']}) {f['message']}" for f in auto_findings
        )
        merged_notes = (
            f"{base_notes}\n\nAuto-review findings:\n{auto_lines}"
            if base_notes
            else f"Auto-review findings:\n{auto_lines}"
        )
    else:
        merged_notes = base_notes

    now = datetime.now(timezone.utc).isoformat()
    review_id = f"rev_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    link_notes = f"target_content_id={content_id}; verdict={final_verdict}"
    agent_id = ctx.agent_id if ctx else None
    session_id = ctx.session_id if ctx else None

    db.create_content_item(
        content_id=review_id,
        content_type="review",
        platform=target.get("platform"),
        title=f"Review ({final_verdict}): {target.get('title') or content_id}",
        body=merged_notes,
        notes=link_notes,
        status="ready" if final_verdict == "ok" else "draft",
        product_id=target.get("product_id"),
        created_by=agent_id,
        session_id=session_id,
        created_at=now,
    )
    rel_path = _write_markdown_copy(
        review_id,
        1,
        "review",
        target.get("platform"),
        f"Review ({final_verdict}): {target.get('title') or content_id}",
        merged_notes,
        notes=link_notes,
    )
    db.append_content_files(review_id, [{"kind": "review_doc", "version": 1, "path": rel_path}])

    review_entry = {
        "kind": "review",
        "review_id": review_id,
        "verdict": final_verdict,
        "agent_verdict": norm_verdict,
        "auto_verdict": auto.get("verdict", "ok"),
        "findings": auto_findings,
        "notes": merged_notes,
        "path": rel_path,
        "created_at": now,
    }
    db.append_content_files(content_id, [review_entry])

    return {
        "review_id": review_id,
        "content_id": content_id,
        "verdict": final_verdict,
        "agent_verdict": norm_verdict,
        "auto_review": auto,
        "findings": auto_findings,
        "notes": merged_notes,
        "path": rel_path,
    }


def _session_saw_image(ctx):
    """Return True if the current chat has image attachments or the agent called check_image in it."""
    if not ctx:
        return False
    if getattr(ctx, "attachments", None):
        return True
    session_id = getattr(ctx, "session_id", None)
    if not session_id:
        return False
    try:
        calls = db.list_tool_calls(session_id=session_id, limit=500)
    except Exception:
        return True  # If we can't tell, be safe and ask for confirmation.
    return any(str(c.get("tool") or "") == "check_image" for c in calls or [])


@tool(
    name="record_metrics",
    description="Persist manual post performance numbers directly, or stage screenshot metrics as a pending proposal for user confirmation.",
    parameters_schema={
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "Associated content_id (or external post id)."},
            "platform": {"type": "string", "description": "Platform name (e.g. x, linkedin, instagram, youtube)."},
            "metrics": {
                "type": "object",
                "description": "Metric counts object, e.g. {views, likes, comments, reposts}.",
            },
            "source": {
                "type": "string",
                "enum": list(VALID_METRIC_SOURCES),
                "description": "Where the numbers came from: 'screenshot' (stages proposal for confirmation) or 'manual' (saves directly).",
            },
            "captured_at": {"type": "string", "description": "Optional ISO timestamp of capture."},
        },
        "required": ["content_id", "platform", "metrics", "source"],
    },
    side_effects=True,
)
def record_metrics(ctx, content_id, platform, metrics, source, captured_at=None):
    norm_source = (source or "").strip().lower()
    if norm_source not in VALID_METRIC_SOURCES:
        return {"error": f"Invalid source '{source}'. Expected 'screenshot' or 'manual'."}
    if not isinstance(metrics, dict):
        return {"error": "metrics must be an object"}

    # The model chooses `source`, so don't trust it: if this chat has an image attached or the agent looked at one,
    # the numbers may come from a screenshot and must wait for the user's confirmation.
    if norm_source == "manual" and _session_saw_image(ctx):
        norm_source = "screenshot"

    if norm_source == "screenshot":
        cleaned = results_extract.clean_metrics_dict(metrics)
        if not cleaned:
            return {"error": "At least one valid non-negative metric is required."}
        norm_platform = platforms.normalize_platform(platform) if platform else None
        img_rel = None
        if ctx and ctx.attachments:
            real_root = os.path.realpath(paths.DATA_ROOT)
            for att in ctx.attachments:
                if not isinstance(att, str) or not att.strip():
                    continue
                cand = att if os.path.isabs(att) else os.path.join(paths.DATA_ROOT, att)
                real_att = os.path.realpath(cand)
                try:
                    inside = os.path.commonpath([real_root, real_att]) == real_root
                except ValueError:
                    inside = False
                if inside and os.path.isfile(real_att):
                    img_rel = os.path.relpath(real_att, real_root).replace("\\", "/")
                    break
        proposal_id = f"mprop_{uuid.uuid4().hex[:12]}"
        proposal = db.create_metric_proposal(
            proposal_id,
            content_id=content_id,
            platform=norm_platform,
            metrics=cleaned,
            evidence={},
            image_path=img_rel,
            status="pending",
        )
        results_extract.notify_pending_proposal(proposal)
        return {
            "status": "pending",
            "message": "Waiting for your confirmation in Results",
            "proposal": proposal,
        }

    metric_id = f"met_{uuid.uuid4().hex[:12]}"
    saved = db.record_metric(
        metric_id=metric_id,
        content_id=content_id,
        platform=platform,
        metrics=metrics,
        source=norm_source,
        captured_at=captured_at,
        created_by=ctx.agent_id if ctx else None,
    )
    return saved


@tool(
    name="read_metrics",
    description="Read previously recorded performance metrics, optionally filtered by content_id.",
    parameters_schema={
        "type": "object",
        "properties": {
            "content_id": {"type": "string", "description": "Optional content_id filter."},
            "limit": {"type": "integer", "description": "Maximum number of metric records to return (default 20)."},
        },
        "required": [],
    },
)
def read_metrics(ctx, content_id=None, limit=20):
    if confidential_guard.is_local(ctx):
        return {"metrics": db.list_metrics(content_id=content_id, limit=limit)}
    scope = confidential_guard.scope()
    if content_id:
        refusal = confidential_guard.local_or_refusal(ctx, scope.content_id(content_id))
        if refusal:
            return refusal
        return {"metrics": db.list_metrics(content_id=content_id, limit=limit)}
    max_rows = max(1, min(int(limit or 20), 500))
    rows = [r for r in db.list_metrics(limit=500) if not scope.linked(r)]
    return {"metrics": rows[:max_rows]}
