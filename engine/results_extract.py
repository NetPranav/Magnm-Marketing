#!/usr/bin/env python3
"""Vision-based analytics screenshot extraction and confirmation proposals."""

from datetime import datetime, timezone
import json
import math
import mimetypes
import os
import re
import sys
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import db
import notifications
import paths
import platforms
import product_rules

MAX_IMAGE_BYTES = 10 * 1024 * 1024

KNOWN_METRICS = (
    "views",
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "reposts",
    "saves",
    "bookmarks",
    "clicks",
    "profile_visits",
    "followers_gained",
    "watch_time_seconds",
    "engagement_rate",
    "ctr",
)
KNOWN_METRICS_SET = set(KNOWN_METRICS)

_NUMBER_RE = re.compile(r"^\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*([kKmMbB%])?\s*$")
_FENCE_OPEN_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")

_EXTRACT_SYSTEM_PROMPT = (
    "You extract visible social post analytics from screenshots. "
    "Return JSON only with no markdown, no code fences, and no prose. "
    "Use this exact schema:\n"
    "{\n"
    '  "platform": string or null,\n'
    '  "metrics": {\n'
    '    "views": number or string or null,\n'
    '    "impressions": number or string or null,\n'
    '    "reach": number or string or null,\n'
    '    "likes": number or string or null,\n'
    '    "comments": number or string or null,\n'
    '    "shares": number or string or null,\n'
    '    "reposts": number or string or null,\n'
    '    "saves": number or string or null,\n'
    '    "bookmarks": number or string or null,\n'
    '    "clicks": number or string or null,\n'
    '    "profile_visits": number or string or null,\n'
    '    "followers_gained": number or string or null,\n'
    '    "watch_time_seconds": number or string or null,\n'
    '    "engagement_rate": number or string or null,\n'
    '    "ctr": number or string or null\n'
    "  },\n"
    '  "visible_text_evidence": {"metric_name": "exact text seen in screenshot"},\n'
    '  "period": string or null,\n'
    '  "notes": string or null\n'
    "}\n"
    "Rules:\n"
    "- Set any metric that is not clearly visible to null.\n"
    "- Never guess, estimate, or compute missing metrics.\n"
    "- Copy exact text strings (such as '1.2K', '3,400', '12%') into visible_text_evidence."
)


class ResultsExtractError(Exception):
    """Typed error for screenshot validation, extraction, and confirmation."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)


def detect_image_type(raw_bytes):
    """Validate image magic bytes for PNG, JPEG, or WebP and return (ext, mime)."""
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise ResultsExtractError("Image body must not be empty", status=400)
    data = bytes(raw_bytes)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise ResultsExtractError("Unsupported image format. Only PNG, JPEG, and WebP are allowed.", status=400)


def parse_metric_number(value):
    """Parse a non-negative metric value (including 1.2K, 3,400, 12%) into int/float, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if not math.isfinite(num) or num < 0:
            return None
        return int(num) if num.is_integer() else round(num, 4)

    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in ("null", "none", "n/a", "na", "-", "--"):
            return None
        match = _NUMBER_RE.match(s)
        if not match:
            return None
        num_str = match.group(1)
        if num_str.startswith("-"):
            return None
        try:
            base = float(num_str.replace(",", ""))
        except ValueError:
            return None
        suffix = (match.group(2) or "").lower()
        mult = 1.0
        if suffix == "k":
            mult = 1_000.0
        elif suffix == "m":
            mult = 1_000_000.0
        elif suffix == "b":
            mult = 1_000_000_000.0
        num = base * mult
        if not math.isfinite(num) or num < 0:
            return None
        if abs(num - round(num)) < 1e-9:
            return int(round(num))
        return round(num, 4)

    return None


def clean_metrics_dict(raw_metrics):
    """Filter raw_metrics to known metric names with non-negative numeric values."""
    if not isinstance(raw_metrics, dict):
        return {}
    cleaned = {}
    for k, v in raw_metrics.items():
        key = str(k or "").strip().lower()
        if key not in KNOWN_METRICS_SET:
            continue
        parsed = parse_metric_number(v)
        if parsed is not None:
            cleaned[key] = parsed
    return cleaned


def strip_code_fences(raw_text):
    """Strip surrounding markdown code fences from model output."""
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = _FENCE_OPEN_RE.sub("", text, count=1)
        text = _FENCE_CLOSE_RE.sub("", text, count=1)
    return text.strip()


def parse_extraction_response(raw_text):
    """Defensively parse vision model JSON output into normalized metrics and evidence."""
    cleaned = strip_code_fences(raw_text)
    if not cleaned:
        raise ResultsExtractError("Vision model returned an empty response.", status=422)
    try:
        payload = json.loads(cleaned)
    except Exception as exc:
        raise ResultsExtractError("Vision model did not return valid JSON.", status=422) from exc

    if not isinstance(payload, dict):
        raise ResultsExtractError("Vision model response must be a JSON object.", status=422)

    raw_platform = payload.get("platform")
    norm_platform = None
    if isinstance(raw_platform, str) and raw_platform.strip():
        norm_platform = platforms.normalize_platform(raw_platform)

    metrics = clean_metrics_dict(payload.get("metrics") or {})

    raw_evidence = payload.get("visible_text_evidence")
    evidence = {}
    if isinstance(raw_evidence, dict):
        for k, v in raw_evidence.items():
            key = str(k or "").strip().lower()
            if key not in KNOWN_METRICS_SET:
                continue
            if v is not None and str(v).strip():
                evidence[key] = str(v).strip()

    period = payload.get("period")
    period_str = str(period).strip() if isinstance(period, str) and period.strip() else None
    notes = payload.get("notes")
    notes_str = str(notes).strip() if isinstance(notes, str) and notes.strip() else None

    return {
        "platform": norm_platform,
        "metrics": metrics,
        "evidence": evidence,
        "period": period_str,
        "notes": notes_str,
    }


def _results_assets_dir():
    return os.path.realpath(paths.data("assets", "results"))


def save_result_image(raw_bytes):
    """Validate and store screenshot bytes under <DATA_ROOT>/assets/results/ with a safe filename."""
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise ResultsExtractError("Screenshot image is required", status=400)
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ResultsExtractError("Image exceeds maximum allowed size of 10 MB", status=413)
    ext, _ = detect_image_type(raw_bytes)

    out_dir = _results_assets_dir()
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"res_{ts}_{uuid.uuid4().hex[:8]}{ext}"
    abs_path = os.path.realpath(os.path.join(out_dir, filename))
    if os.path.commonpath([out_dir, abs_path]) != out_dir:
        raise ResultsExtractError("Invalid image path", status=400)

    with open(abs_path, "wb") as f:
        f.write(raw_bytes)

    rel_path = f"assets/results/{filename}"
    return rel_path, abs_path


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    return cand if os.path.exists(cand) else paths.resource("config", "product_rules.json")


def _is_content_confidential(item):
    if not isinstance(item, dict):
        return False
    return product_rules.mentions_confidential(
        f"{item.get('title') or ''}\n{item.get('product_id') or ''}",
        path=_rules_path(),
    )


def notify_pending_proposal(proposal):
    """Emit a single results_pending notification for a newly created proposal."""
    if not isinstance(proposal, dict):
        return None
    plat = proposal.get("platform") or "post"
    count = len(proposal.get("metrics") or {})
    body = f"{plat}: {count} metric{'s' if count != 1 else ''} ready to review"
    return notifications.notify(
        "results_pending",
        "Results ready to confirm",
        body=body,
        route="#/today",
    )


def extract_from_image(raw_bytes, *, content_id=None, platform=None):
    """Save screenshot, run vision extraction, create a pending proposal (never auto-saving metrics)."""
    clean_cid = str(content_id).strip() if content_id and str(content_id).strip() else None
    item = None
    if clean_cid:
        item = db.get_content_item(clean_cid)
        if item is None:
            raise ResultsExtractError(f"Content item '{clean_cid}' not found", status=404)

    rel_path, abs_path = save_result_image(raw_bytes)
    try:
        hint_platform = platforms.normalize_platform(platform) if platform and str(platform).strip() else None
        user_prompt = "Extract all visible analytics metrics from this screenshot as JSON."
        if hint_platform:
            user_prompt += f" Expected platform: {hint_platform}."
        if item and item.get("title"):
            user_prompt += f" Post title: {item.get('title')}."

        res = ai_gateway.generate_completion_with_meta(
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model_tier="vision",
            tier="vision",
            image_paths=[abs_path],
            agent_role="analyst",
            confidential=_is_content_confidential(item),
        )
        parsed = parse_extraction_response(res.get("text", ""))
    except Exception as exc:
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except OSError:
            pass
        if isinstance(exc, ResultsExtractError):
            raise
        raise ResultsExtractError(str(exc), status=502) from exc

    final_platform = (
        (platforms.normalize_platform(platform) if platform and str(platform).strip() else None)
        or parsed["platform"]
        or (platforms.normalize_platform(item.get("platform")) if item and item.get("platform") else None)
    )

    proposal_id = f"mprop_{uuid.uuid4().hex[:12]}"
    proposal = db.create_metric_proposal(
        proposal_id,
        content_id=clean_cid,
        platform=final_platform,
        metrics=parsed["metrics"],
        evidence=parsed["evidence"],
        image_path=rel_path,
        status="pending",
    )
    notify_pending_proposal(proposal)
    return {
        "proposal": proposal,
        "period": parsed["period"],
        "notes": parsed["notes"],
    }


def list_proposals(status="pending", limit=100):
    """List metric proposals filtered by status."""
    return db.list_metric_proposals(status=status, limit=limit)


def confirm_proposal(proposal_id, *, metrics=None, content_id=None, platform=None):
    """Confirm a pending proposal, save metrics with source='screenshot', and close matching reminders."""
    prop = db.get_metric_proposal(proposal_id)
    if prop is None:
        raise ResultsExtractError("Metric proposal not found", status=404)
    if prop["status"] != "pending":
        raise ResultsExtractError(f"Proposal is already {prop['status']}", status=400)

    if content_id is not None:
        final_cid = str(content_id).strip() or None
    else:
        final_cid = prop.get("content_id")

    item = None
    if final_cid:
        item = db.get_content_item(final_cid)
        if item is None:
            raise ResultsExtractError(f"Content item '{final_cid}' not found", status=404)

    if platform is not None and str(platform).strip():
        final_platform = platforms.normalize_platform(platform)
    elif prop.get("platform"):
        final_platform = platforms.normalize_platform(prop["platform"])
    elif item and item.get("platform"):
        final_platform = platforms.normalize_platform(item["platform"])
    else:
        raise ResultsExtractError("platform is required", status=400)

    raw_metrics = metrics if metrics is not None else prop.get("metrics")
    if not isinstance(raw_metrics, dict):
        raise ResultsExtractError("metrics must be an object", status=400)
    cleaned_metrics = clean_metrics_dict(raw_metrics)
    if not cleaned_metrics:
        raise ResultsExtractError("At least one valid metric is required", status=400)

    metric_id = f"met_{uuid.uuid4().hex[:12]}"
    saved_metric = db.record_metric(
        metric_id=metric_id,
        content_id=final_cid,
        platform=final_platform,
        metrics=cleaned_metrics,
        source="screenshot",
        created_by="user",
    )
    updated_prop = db.decide_metric_proposal(
        proposal_id,
        "saved",
        content_id=final_cid,
        platform=final_platform,
        metrics=cleaned_metrics,
    )
    if final_cid:
        db.mark_content_results_reminders_done(final_cid)

    return {
        "proposal": updated_prop,
        "metric": saved_metric,
    }


def discard_proposal(proposal_id):
    """Discard a pending metric proposal without saving any metrics."""
    prop = db.get_metric_proposal(proposal_id)
    if prop is None:
        raise ResultsExtractError("Metric proposal not found", status=404)
    if prop["status"] != "pending":
        raise ResultsExtractError(f"Proposal is already {prop['status']}", status=400)
    updated = db.decide_metric_proposal(proposal_id, "discarded")
    return {"proposal": updated}


def resolve_proposal_image(proposal_id):
    """Resolve and validate the local image path for a metric proposal."""
    prop = db.get_metric_proposal(proposal_id)
    if prop is None or not prop.get("image_path"):
        raise ResultsExtractError("Proposal image not found", status=404)

    base_dir = _results_assets_dir()
    candidate = os.path.realpath(os.path.join(paths.DATA_ROOT, prop["image_path"]))
    try:
        inside = os.path.commonpath([base_dir, candidate]) == base_dir
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(candidate):
        raise ResultsExtractError("Proposal image not found", status=404)

    mime, _ = mimetypes.guess_type(candidate)
    return candidate, mime or "image/png"
