#!/usr/bin/env python3
"""Evidence-backed Analyst learnings proposal, approval lifecycle, and prompt injection."""

from datetime import datetime, timezone
import json
import os
import re
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import cloud_sync
import db
import notifications
import paths
import product_rules
import weekly_summary

MAX_PROPOSED_LEARNINGS = 3
MIN_EVIDENCE_POSTS = 2
MAX_INJECTED_LEARNINGS = 8
MAX_INJECTED_CHARS = 600

VALID_TARGET_AGENTS = (
    "writer",
    "cmo",
    "designer",
    "video-producer",
    "researcher",
    "reviewer",
    "analyst",
    "all",
)
VALID_TARGET_SET = set(VALID_TARGET_AGENTS)

_FENCE_OPEN_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")


class LearningError(Exception):
    """Typed error for learning validation and lookup failures."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)


def _strip_fences(raw_text):
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = _FENCE_OPEN_RE.sub("", text, count=1)
        text = _FENCE_CLOSE_RE.sub("", text, count=1)
    return text.strip()


def normalize_applies_to(raw):
    """Normalize applies_to into a list of valid agent IDs or ['all']."""
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in re.split(r"[,;\s]+", raw) if p.strip()]
    elif isinstance(raw, list):
        parts = [str(p or "").strip().lower() for p in raw if str(p or "").strip()]
    else:
        return []

    cleaned = []
    seen = set()
    for p in parts:
        if p == "all":
            return ["all"]
        if p in VALID_TARGET_SET and p not in seen:
            seen.add(p)
            cleaned.append(p)
    return cleaned


def _posts_index(data):
    """Build {content_id: post_dict} and {metric_id: content_id} from week + previous week data."""
    by_cid = {}
    by_mid = {}
    for bucket in (data.get("posts") or [], (data.get("previous_week") or {}).get("posts") or []):
        for post in bucket:
            cid = str(post.get("content_id") or "").strip()
            if not cid:
                continue
            by_cid[cid] = post
            met = post.get("latest_metric")
            if isinstance(met, dict) and met.get("id"):
                by_mid[str(met["id"])] = cid
    return by_cid, by_mid


def _validate_evidence(raw_evidence, by_cid, by_mid):
    """Validate evidence entries against posts in data and enrich with post titles and metrics."""
    if not isinstance(raw_evidence, list):
        return []

    seen_cids = set()
    validated = []
    for entry in raw_evidence:
        cid = None
        mid = None
        if isinstance(entry, str):
            tok = entry.strip()
            if tok in by_cid:
                cid = tok
            elif tok in by_mid:
                cid = by_mid[tok]
                mid = tok
        elif isinstance(entry, dict):
            raw_cid = str(entry.get("content_id") or "").strip()
            raw_mid = str(entry.get("metric_id") or "").strip()
            if raw_cid and raw_cid in by_cid:
                cid = raw_cid
                if raw_mid and by_mid.get(raw_mid) == cid:
                    mid = raw_mid
            elif raw_mid and raw_mid in by_mid:
                cid = by_mid[raw_mid]
                mid = raw_mid

        if not cid or cid in seen_cids:
            continue
        post = by_cid[cid]
        met = post.get("latest_metric") if isinstance(post.get("latest_metric"), dict) else None
        if not mid and met and met.get("id"):
            mid = str(met["id"])
        seen_cids.add(cid)
        validated.append({
            "content_id": cid,
            "metric_id": mid,
            "title": post.get("title") or cid,
            "platform": post.get("platform") or "unknown",
            "metrics": dict((met or {}).get("metrics") or {}),
        })

    return validated


def _build_learning_prompts(data, highlights):
    rules_path = paths.data("config", "product_rules.json")
    if not os.path.exists(rules_path):
        rules_path = paths.resource("config", "product_rules.json")
    rules_text = product_rules.prompt_block(rules_path) if os.path.exists(rules_path) else ""

    sys_lines = [
        "You are the Analyst for Magnm Studio.",
        "Propose 0 to 3 short, evidence-backed learnings from the post performance data below.",
        "Return JSON only (a JSON array of objects, no markdown code fences):",
        '[{"text": "...", "evidence": [{"content_id": "...", "metric_id": "..."}], "applies_to": ["writer"]}]',
        "Rules:",
        "- Each learning must cite at least 2 distinct posts from the data in `evidence` (using exact content_id and metric_id values).",
        "- Never invent numbers. Every number in `text` must appear in the input data.",
        "- `applies_to` must be ['all'] or a non-empty subset of: writer, cmo, designer, video-producer, researcher.",
        "- If there are fewer than 2 posts with clear evidence, return [].",
        "- Use zero emojis.",
    ]
    if rules_text:
        sys_lines.append(rules_text)

    posts_payload = []
    for label, bucket in (
        ("current_week", data.get("posts") or []),
        ("previous_week", (data.get("previous_week") or {}).get("posts") or []),
    ):
        for post in bucket:
            met = post.get("latest_metric") or {}
            posts_payload.append({
                "period": label,
                "content_id": post.get("content_id"),
                "title": post.get("title"),
                "platform": post.get("platform"),
                "posted_at": post.get("posted_at"),
                "metric_id": met.get("id"),
                "metrics": met.get("metrics") or {},
            })

    user_prompt = json.dumps(
        {
            "week_start": data.get("week_start"),
            "week_end": data.get("week_end"),
            "highlights": {
                "platform_rows": highlights.get("platform_rows") or [],
                "counts": highlights.get("counts") or {},
            },
            "posts": posts_payload,
        },
        ensure_ascii=False,
    )
    return "\n".join(sys_lines), user_prompt


def propose_for_summary(summary, *, data=None, highlights=None, notify=True):
    """Ask the Analyst model to propose 0-3 grounded learnings from summary data."""
    if not isinstance(summary, dict) or not summary.get("id"):
        return []
    week_data = data if data is not None else (summary.get("data") or {})
    week_highlights = highlights if highlights is not None else (summary.get("highlights") or {})

    by_cid, by_mid = _posts_index(week_data)
    if len(by_cid) < MIN_EVIDENCE_POSTS:
        return []

    sys_prompt, user_prompt = _build_learning_prompts(week_data, week_highlights)
    is_conf = weekly_summary._data_mentions_confidential(week_data)
    try:
        res = ai_gateway.generate_completion_with_meta(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            agent_role="analyst",
            confidential=is_conf,
        )
        raw_text = _strip_fences((res or {}).get("text") or "")
        if not raw_text:
            return []
        parsed = json.loads(raw_text)
    except Exception:
        return []

    if isinstance(parsed, dict) and isinstance(parsed.get("learnings"), list):
        candidates = parsed["learnings"]
    elif isinstance(parsed, list):
        candidates = parsed
    else:
        return []

    db.delete_pending_learnings_for_summary(summary["id"])

    created = []
    for item in candidates:
        if len(created) >= MAX_PROPOSED_LEARNINGS:
            break
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        if not weekly_summary.text_uses_only_allowed_numbers(text, week_data, week_highlights):
            continue
        evidence = _validate_evidence(item.get("evidence"), by_cid, by_mid)
        if len(evidence) < MIN_EVIDENCE_POSTS:
            continue
        applies_to = normalize_applies_to(item.get("applies_to"))
        if not applies_to:
            applies_to = ["all"]
        row = db.create_learning(
            text=text,
            evidence=evidence,
            applies_to=applies_to,
            status="pending",
            source_summary_id=summary["id"],
        )
        created.append(row)

    if notify and created:
        count = len(created)
        notifications.notify(
            "learning_proposed",
            f"{count} new learning{'s' if count != 1 else ''} proposed",
            body=created[0]["text"],
            route="#/today/learnings",
        )

    return created


def list_learnings(status=None, limit=200):
    """List learnings optionally filtered by status."""
    return db.list_learnings(status=status, limit=limit)


def approve_learning(learning_id, text=None):
    """Approve a learning (optionally updating its text) and return the updated row."""
    existing = db.get_learning(learning_id)
    if existing is None:
        raise LearningError("Learning not found", status=404)
    next_text = None
    if text is not None:
        clean = " ".join(str(text).split()).strip()
        if not clean:
            raise LearningError("Learning text cannot be empty", status=400)
        next_text = clean
    return db.update_learning_decision(learning_id, "approved", text=next_text)


def reject_learning(learning_id):
    """Reject a learning so it will not be injected into agent prompts."""
    existing = db.get_learning(learning_id)
    if existing is None:
        raise LearningError("Learning not found", status=404)
    return db.update_learning_decision(learning_id, "rejected")


def retire_learning(learning_id):
    """Retire an approved learning so it is removed from future agent prompts."""
    existing = db.get_learning(learning_id)
    if existing is None:
        raise LearningError("Learning not found", status=404)
    return db.update_learning_decision(learning_id, "retired")


def add_manual_learning(text, applies_to=None):
    """Create a user-authored learning, approved immediately."""
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        raise LearningError("text is required", status=400)
    targets = normalize_applies_to(applies_to if applies_to is not None else ["all"])
    if not targets:
        raise LearningError("applies_to must contain valid agent ids or 'all'", status=400)
    now = datetime.now(timezone.utc).isoformat()
    return db.create_learning(
        text=clean,
        evidence=[],
        applies_to=targets,
        status="approved",
        source_summary_id=None,
        created_at=now,
        decided_at=now,
    )


def prompt_block(agent_id, max_items=MAX_INJECTED_LEARNINGS, max_chars=MAX_INJECTED_CHARS):
    """Build the 'What has worked for us' section for agent_id (<=8 approved, <=600 chars, no confidential items)."""
    canon = str(agent_id or "").strip().lower()
    if not canon:
        return ""

    try:
        is_confidential = cloud_sync.confidential_checker()
    except Exception:
        return ""

    approved = db.list_approved_learnings(limit=200)
    if not approved:
        return ""

    header = "## What has worked for us"
    lines = [header]
    total_len = len(header)
    added = 0

    for item in approved:
        if added >= max_items:
            break
        targets = [str(t).strip().lower() for t in (item.get("applies_to") or [])]
        if "all" not in targets and canon not in targets:
            continue
        clean_text = " ".join(str(item.get("text") or "").split()).strip()
        if not clean_text or is_confidential(clean_text):
            continue

        bullet = f"- {clean_text}"
        candidate_len = total_len + 1 + len(bullet)
        if candidate_len > max_chars:
            if added == 0:
                avail = max_chars - total_len - 1
                if avail > 10:
                    lines.append(bullet[: avail - 3].rstrip() + "...")
                    added += 1
            break
        lines.append(bullet)
        total_len = candidate_len
        added += 1

    if added == 0:
        return ""
    return "\n".join(lines)
