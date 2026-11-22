#!/usr/bin/env python3
"""Designer and visual inspection tools: render_card, generate_image, check_image."""

import base64
from datetime import datetime, timezone
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import confidential_guard
import db
import key_pool
import model_registry
import paths
import private_files
from tools import tool

VALID_CARD_FORMATS = ("4x5", "1x1", "16x9", "9x16")
FLUX_BASE_URL = "https://ai.api.nvidia.com/v1/genai"
FLUX_TIMEOUT_S = 120


def _split_headline_lines(headline, max_lines=3):
    """Split a headline into 1-3 short lines for templates/card.py typography."""
    raw = (headline or "").strip()
    if not raw:
        return ["MAGNM"]
    if "\n" in raw:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return lines[:max_lines] or [raw]

    words = raw.split()
    if len(raw) <= 14 or len(words) <= 2:
        return [raw]

    target_len = max(8, (len(raw) + max_lines - 1) // min(max_lines, len(words)))
    lines = []
    cur = []
    cur_len = 0
    for w in words:
        add_len = len(w) + (1 if cur else 0)
        if cur and (cur_len + add_len > target_len) and len(lines) < max_lines - 1:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add_len
    if cur:
        lines.append(" ".join(cur))
    return lines[:max_lines]


def _load_card_module():
    """Load templates/card.py in-process without spawning a subprocess."""
    card_path = paths.resource("templates", "card.py")
    spec = importlib.util.spec_from_file_location("magnm_templates_card", card_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load card renderer from {card_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_content_item(ctx, content_id, content_type, title, body):
    """Return existing content_id (verifying it exists) or create a new draft item."""
    if content_id:
        existing = db.get_content_item(content_id)
        if existing is None:
            return None, f"Content item '{content_id}' not found"
        return content_id, None

    new_id = f"cnt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    db.create_content_item(
        content_id=new_id,
        content_type=content_type,
        platform="social",
        title=title or new_id,
        body=body or "",
        status="draft",
        created_by=ctx.agent_id if ctx else None,
        session_id=ctx.session_id if ctx else None,
    )
    return new_id, None


@tool(
    name="render_card",
    description="Render typographic social cards via templates/card.py in-process and attach them to a content item.",
    parameters_schema={
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "Short punchy card headline (1-3 lines)."},
            "subline": {"type": "string", "description": "Optional italic connector subline."},
            "formats": {
                "type": "array",
                "items": {"type": "string", "enum": list(VALID_CARD_FORMATS)},
                "description": "Aspect ratios to render (default ['4x5', '1x1']).",
            },
            "content_id": {"type": "string", "description": "Optional existing content_id to attach cards to."},
        },
        "required": ["headline"],
    },
    side_effects=True,
)
def render_card(ctx, headline, subline=None, formats=None, content_id=None):
    chosen_formats = list(formats) if formats else ["4x5", "1x1"]
    bad_formats = [f for f in chosen_formats if f not in VALID_CARD_FORMATS]
    if bad_formats:
        return {"error": f"Unsupported card format(s): {', '.join(bad_formats)}"}

    big_lines = _split_headline_lines(headline, max_lines=3)
    cid, err = _ensure_content_item(
        ctx,
        content_id,
        "card",
        headline,
        f"Headline: {headline}\nSubline: {subline or ''}\nFormats: {', '.join(chosen_formats)}",
    )
    if err:
        return {"error": err}

    out_dir = paths.data("outputs", "content", cid)
    os.makedirs(out_dir, exist_ok=True)

    card_spec = {
        "id": cid,
        "field": "sky",
        "kicker": "MAGNM",
        "big": big_lines,
        "serif": subline or "",
        "foot": "magnm-org.vercel.app",
        "sizes": chosen_formats,
    }

    try:
        card_mod = _load_card_module()
        written_paths = card_mod.render(card_spec, out_dir)
    except ImportError:
        return {"error": "Card rendering needs the Pillow library."}
    except Exception as exc:
        return {"error": f"Card rendering failed: {exc}"}

    rel_paths = [os.path.relpath(p, paths.DATA_ROOT) for p in written_paths]
    entries = [
        {"kind": "card", "format": fmt, "path": rel}
        for fmt, rel in zip(chosen_formats, rel_paths)
    ]
    db.append_content_files(cid, entries)

    return {
        "content_id": cid,
        "files": rel_paths,
    }


def _models_cfg_path():
    candidate = paths.data("config", "models.json")
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource("config", "models.json")
    return fallback if os.path.exists(fallback) else candidate


@tool(
    name="generate_image",
    description="Generate an image via NVIDIA FLUX, save it under outputs/content/<content_id>/, and attach it to the content item.",
    parameters_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Visual generation prompt."},
            "width": {"type": "integer", "description": "Image width in pixels (default 1024)."},
            "height": {"type": "integer", "description": "Image height in pixels (default 1024)."},
            "content_id": {"type": "string", "description": "Optional existing content_id to attach the image to."},
        },
        "required": ["prompt"],
    },
    side_effects=True,
)
def generate_image(ctx, prompt, width=1024, height=1024, content_id=None):
    if ctx and ctx.confidential:
        return {"error": "generate_image is disabled in confidential mode."}

    cfg = model_registry.load_models_config(_models_cfg_path())
    model = (cfg.get("image_models") or {}).get("generate") or "black-forest-labs/flux.2-klein-4b"
    url = f"{FLUX_BASE_URL}/{model}"

    payload = {
        "prompt": str(prompt),
        "width": int(width or 1024),
        "height": int(height or 1024),
        "steps": 4,
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    pool = key_pool.get_pool()
    role = ctx.agent_id if ctx else "designer"
    key, label = pool.acquire(role=role)

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=FLUX_TIMEOUT_S) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
        pool.report(label, "ok")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            pool.report(label, "rate_limited", retry_after=retry_after)
        elif exc.code in (401, 403):
            pool.report(label, "auth_failed")
        else:
            pool.report(label, "error")
        return {"error": f"Image generation failed (HTTP {exc.code})."}
    except Exception as exc:
        pool.report(label, "error")
        return {"error": f"Image generation failed: {exc}"}

    artifacts = resp_data.get("artifacts") or []
    if not artifacts or not isinstance(artifacts[0], dict) or not artifacts[0].get("base64"):
        return {"error": "Image generation returned no image artifact."}

    try:
        img_bytes = base64.b64decode(artifacts[0]["base64"])
    except Exception as exc:
        return {"error": f"Failed to decode generated image: {exc}"}

    cid, err = _ensure_content_item(
        ctx,
        content_id,
        "image",
        str(prompt)[:80],
        str(prompt),
    )
    if err:
        return {"error": err}

    out_dir = paths.data("outputs", "content", cid)
    os.makedirs(out_dir, exist_ok=True)
    filename = f"image_{uuid.uuid4().hex[:8]}.jpg"
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "wb") as f:
        f.write(img_bytes)

    rel_path = os.path.relpath(out_path, paths.DATA_ROOT)
    db.append_content_files(
        cid,
        [{"kind": "image", "path": rel_path, "prompt": str(prompt)[:200], "model": model}],
    )

    return {
        "content_id": cid,
        "path": rel_path,
        "size_bytes": len(img_bytes),
        "model": model,
    }


@tool(
    name="check_image",
    description="Inspect an image file inside DATA_ROOT using the vision tier model and answer a specific question.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Image path inside DATA_ROOT (relative or absolute)."},
            "question": {"type": "string", "description": "Question to answer about the image."},
        },
        "required": ["path", "question"],
    },
)
def check_image(ctx, path, question):
    raw_path = (path or "").strip()
    if not raw_path:
        return {"error": "path is required"}

    real_root = os.path.realpath(paths.DATA_ROOT)
    candidate = raw_path if os.path.isabs(raw_path) else os.path.join(paths.DATA_ROOT, raw_path)
    real_target = os.path.realpath(candidate)

    try:
        inside = os.path.commonpath([real_root, real_target]) == real_root
    except ValueError:
        inside = False

    if not inside:
        return {"error": "path must be inside DATA_ROOT"}

    if not os.path.isfile(real_target):
        return {"error": f"Image file not found: {raw_path}"}
    if private_files.is_private(real_target, real_root) or not private_files.is_image(real_target):
        return {"error": "check_image only reads PNG, JPEG, WebP or GIF images"}

    rel_for_check = os.path.relpath(real_target, real_root)
    use_local = confidential_guard.is_local(ctx)
    if not use_local:
        # A picture of a confidential item or product goes to local AI only. The session switches first.
        use_local = confidential_guard.scope().path(rel_for_check)
        refusal = confidential_guard.local_or_refusal(ctx, use_local)
        if refusal:
            return refusal

    res = ai_gateway.generate_completion_with_meta(
        system_prompt=(
            "You are a careful visual reviewer. Answer strictly based on what is visible in the image. "
            "Be concise and use zero emojis."
        ),
        user_prompt=question,
        model_tier="vision",
        image_paths=[real_target],
        agent_role="reviewer",
        confidential=bool(use_local),
    )

    return {
        "path": os.path.relpath(real_target, real_root),
        "answer": res.get("text", ""),
        "model_used": res.get("model_used"),
    }
