#!/usr/bin/env python3
"""Serve the pinned Magnm-Pitch bundle at /pitch/ with offline fonts and Studio host payload."""

import json
import os
import re
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}

ALLOWED_PITCH_FILES = frozenset({
    "style.css",
    "app.js",
    "presets.js",
    "logo.png",
    "vendor/jspdf.umd.min.js",
})

VALID_MODES = ("preview", "present", "form")

_GOOGLE_FONTS_RE = re.compile(
    r'<link\s+rel="preconnect"\s+href="https://fonts\.googleapis\.com"\s*>\s*'
    r'<link\s+rel="preconnect"\s+href="https://fonts\.gstatic\.com"\s+crossorigin\s*>\s*'
    r'<link\s+rel="stylesheet"\s+href="https://fonts\.googleapis\.com/[^"]+"\s*>',
    re.IGNORECASE,
)

_APP_SCRIPT_RE = re.compile(r'<script\s+src="app\.js"\s*>\s*</script>', re.IGNORECASE)


def vendor_dir():
    """Return the directory containing the vendored Magnm-Pitch files."""
    return paths.resource("vendor", "magnm-pitch")


def host_js_path():
    """Return the path to our Studio host adapter script."""
    return paths.resource("vendor-host", "magnm-host.js")


def font_paths():
    """Return (fonts_css_path, fonts_dir) for offline font serving."""
    if getattr(sys, "frozen", False):
        return (
            paths.resource("pitch-fonts", "fonts.css"),
            paths.resource("pitch-fonts", "fonts"),
        )
    return (
        paths.resource("packages", "magnm-design", "fonts.css"),
        paths.resource("packages", "magnm-design", "fonts"),
    )


def mime_type(file_path):
    """Return the MIME type for a served Pitch asset path."""
    _, ext = os.path.splitext(file_path.lower())
    return MIME_TYPES.get(ext, "application/octet-stream")


def load_catalog():
    """Load and parse catalog.json from the vendored Pitch directory."""
    catalog_path = os.path.join(vendor_dir(), "catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_price_overrides(prices, overrides):
    """Apply valid non-negative integer price overrides onto prices in place."""
    if not isinstance(overrides, dict):
        return
    for item_id, raw_price in overrides.items():
        if item_id not in prices:
            continue
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            continue
        if raw_price < 0:
            continue
        prices[item_id] = int(raw_price)


def build_payload(mode="preview", proposal=None, price_overrides=None):
    """Build the host payload injected into the composed Pitch page."""
    norm_mode = mode if mode in VALID_MODES else "preview"
    catalog = load_catalog()
    items = catalog.get("items") or {}
    prices = {item_id: int(item["p"]) for item_id, item in items.items()}
    _apply_price_overrides(prices, price_overrides)

    proposal_id = None
    preset = None
    custom_items = []
    if isinstance(proposal, dict):
        proposal_id = proposal.get("id") or proposal.get("proposal_id")
        raw_preset = proposal.get("preset")
        if isinstance(raw_preset, dict):
            preset = dict(raw_preset)
            if proposal.get("slug") and not preset.get("slug"):
                preset["slug"] = proposal["slug"]
        else:
            preset = raw_preset
        custom_items = list(proposal.get("custom_items") or [])
        _apply_price_overrides(prices, proposal.get("price_overrides"))

    return {
        "mode": norm_mode,
        "prices": prices,
        "custom_items": custom_items,
        "preset": preset,
        "proposal_id": proposal_id,
    }


def safe_json_for_script(payload):
    """Serialize payload as JSON safe for embedding inside an HTML <script> tag."""
    raw = json.dumps(payload, ensure_ascii=True)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def compose_index_html(payload):
    """Build the hosted Pitch HTML page from vendored index.html with two exact substitutions."""
    index_path = os.path.join(vendor_dir(), "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        raw_html = f.read()

    html, font_subs = _GOOGLE_FONTS_RE.subn(
        '<link rel="stylesheet" href="fonts/fonts.css">',
        raw_html,
    )
    if font_subs != 1:
        raise RuntimeError(f"Expected 1 Google Fonts block in Pitch index.html, matched {font_subs}")

    safe_json = safe_json_for_script(payload)
    replacement = (
        '<script src="app.js"></script>'
        f'<script type="application/json" id="magnm-pitch-data">{safe_json}</script>'
        '<script src="magnm-host.js"></script>'
    )
    html, app_subs = _APP_SCRIPT_RE.subn(lambda _match: replacement, html)
    if app_subs != 1:
        raise RuntimeError(f"Expected 1 app.js script tag in Pitch index.html, matched {app_subs}")

    return html


def _safe_join_inside(root_dir, *parts):
    """Resolve parts under root_dir, returning realpath only if strictly inside and a file."""
    root_real = os.path.realpath(root_dir)
    candidate_real = os.path.realpath(os.path.join(root_real, *parts))
    try:
        inside = os.path.commonpath([root_real, candidate_real]) == root_real
    except ValueError:
        return None
    if not inside or candidate_real == root_real or not os.path.isfile(candidate_real):
        return None
    return candidate_real


def _extract_rel_path(url_path):
    """Validate URL path under /pitch/ and return relative path segments, or None."""
    if not isinstance(url_path, str) or "\x00" in url_path or "\\" in url_path:
        return None
    decoded = urllib.parse.unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        return None
    if not decoded.startswith("/pitch/") or decoded.startswith("//"):
        return None

    rel = decoded[len("/pitch/"):]
    if rel in ("", "index.html"):
        return rel
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return rel


def resolve_asset(url_path):
    """Resolve an allowlisted static asset under /pitch/, or return None."""
    rel = _extract_rel_path(url_path)
    if rel is None or rel in ("", "index.html"):
        return None

    if rel == "magnm-host.js":
        target = os.path.realpath(host_js_path())
        return target if os.path.isfile(target) else None

    fonts_css, fonts_dir = font_paths()
    if rel == "fonts/fonts.css":
        target = os.path.realpath(fonts_css)
        return target if os.path.isfile(target) else None

    parts = rel.split("/")
    if len(parts) == 3 and parts[0] == "fonts" and parts[1] == "fonts" and parts[2].lower().endswith(".woff2"):
        return _safe_join_inside(fonts_dir, parts[2])
    if len(parts) == 2 and parts[0] == "fonts" and parts[1].lower().endswith(".woff2"):
        return _safe_join_inside(fonts_dir, parts[1])

    if rel in ALLOWED_PITCH_FILES:
        return _safe_join_inside(vendor_dir(), *parts)

    return None


def _send_bytes(handler, data, content_type, cache_ctrl):
    """Write a 200 response with security headers."""
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", cache_ctrl)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def serve(handler, url):
    """Serve /pitch/ composed page or allowlisted Pitch assets; return False if not found."""
    if url.path == "/pitch":
        target = "/pitch/" + (f"?{url.query}" if url.query else "")
        handler.send_response(302)
        handler.send_header("Location", target)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    rel = _extract_rel_path(url.path)
    if rel is None:
        return False

    if rel in ("", "index.html"):
        import db

        qs = urllib.parse.parse_qs(url.query)
        mode = (qs.get("mode", ["preview"])[0] or "preview").strip().lower()
        proposal_id = (qs.get("proposal", [""])[0] or "").strip()

        admin_overrides = {}
        if os.path.isfile(db.get_db_path()):
            admin_overrides = {
                iid: int(entry["price"])
                for iid, entry in db.list_pitch_base_prices().items()
            }

        proposal_payload = None
        if proposal_id:
            prop = db.get_pitch_proposal(proposal_id)
            if prop is None:
                return False
            preset_copy = dict(prop.get("preset") or {})
            preset_copy["slug"] = prop["slug"]
            proposal_payload = {
                "id": prop["id"],
                "slug": prop["slug"],
                "kind": prop["kind"],
                "preset": preset_copy,
                "price_overrides": prop.get("price_overrides") or {},
                "custom_items": prop.get("custom_items") or [],
            }
            if prop.get("kind") == "quote_form":
                mode = "form"

        payload = build_payload(
            mode=mode,
            proposal=proposal_payload,
            price_overrides=admin_overrides,
        )
        html = compose_index_html(payload).encode("utf-8")
        _send_bytes(handler, html, "text/html; charset=utf-8", "no-store")
        return True

    asset_path = resolve_asset(url.path)
    if asset_path is None:
        return False

    with open(asset_path, "rb") as f:
        data = f.read()
    _send_bytes(handler, data, mime_type(asset_path), "no-cache")
    return True
