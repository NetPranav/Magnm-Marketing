#!/usr/bin/env python3
"""Static file resolution and serving for the built apps/web studio frontend."""

import os
import sys
import urllib.parse

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def dist_dir():
    """Return the built web app directory when index.html exists, or None."""
    override = os.environ.get("MAGNM_WEB_DIST", "").strip()
    if override:
        candidate = os.path.abspath(os.path.expanduser(override))
    elif getattr(sys, "frozen", False):
        candidate = paths.resource("web")
    else:
        candidate = paths.resource("apps", "web", "dist")

    index_path = os.path.join(candidate, "index.html")
    if os.path.isfile(index_path):
        return os.path.realpath(candidate)
    return None


def resolve(url_path):
    """Safely map a URL path to a file inside dist_dir(), or return None."""
    root = dist_dir()
    if not root or not isinstance(url_path, str):
        return None
    if "\x00" in url_path or "\\" in url_path:
        return None

    decoded = urllib.parse.unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        return None
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None

    rel = decoded[1:]
    if rel == "":
        rel = "index.html"
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        return None

    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None

    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, *parts)
    candidate_real = os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([root_real, candidate_real]) == root_real
    except ValueError:
        return None
    if not inside or candidate_real == root_real or not os.path.isfile(candidate_real):
        return None
    return candidate_real


def mime_type(file_path):
    """Return the MIME type for a built web asset path."""
    _, ext = os.path.splitext(file_path.lower())
    return MIME_TYPES.get(ext, "application/octet-stream")


def cache_control(url_path):
    """Return Cache-Control header value for a URL path inside dist."""
    decoded = urllib.parse.unquote(url_path or "")
    rel = decoded.lstrip("/")
    if rel in ("", "index.html"):
        return "no-store"
    if rel.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "no-cache"


def serve_static(handler, url_path):
    """Serve a static file from dist_dir() onto handler; return False if missing."""
    target = resolve(url_path)
    if target is None:
        return False

    with open(target, "rb") as f:
        data = f.read()

    handler.send_response(200)
    handler.send_header("Content-Type", mime_type(target))
    handler.send_header("Cache-Control", cache_control(url_path))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True
