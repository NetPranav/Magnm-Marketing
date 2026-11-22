"""How the engine serves stored files (uploads, generated media, drafts) from its own origin.

A page on the engine's origin can use the whole API, so stored files must never run as a page: only inert types are
shown inline, SVG is sandboxed without scripts, and everything else is a download.
"""

import os
import re

import paths

_INLINE_TYPES = frozenset((
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp", "image/x-icon",
    "image/vnd.microsoft.icon",
    "video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "video/x-m4v", "video/ogg",
    "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/ogg", "audio/webm", "audio/aac", "audio/flac",
))
_PLAIN_TEXT_TYPES = frozenset(("text/plain", "text/markdown", "text/csv", "application/json"))


def safe_headers(file_path, mime):
    """Return response headers (including Content-Type) that keep a stored file from running as a page."""
    kind = str(mime or "").split(";", 1)[0].strip().lower()
    headers = {"X-Content-Type-Options": "nosniff"}
    if kind in _INLINE_TYPES:
        headers["Content-Type"] = kind
    elif kind in _PLAIN_TEXT_TYPES:
        headers["Content-Type"] = "text/plain; charset=utf-8"
    elif kind == "image/svg+xml":
        headers["Content-Type"] = kind
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    else:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(file_path)) or "file"
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return headers


def servable_path(candidate, *folder):
    """Return candidate's real path if it is a file inside DATA_ROOT/<folder> and not private, else None."""
    data_root = os.path.realpath(paths.DATA_ROOT)
    base = os.path.realpath(os.path.join(data_root, *folder))
    real = os.path.realpath(candidate)
    try:
        if os.path.commonpath([base, real]) != base or os.path.commonpath([data_root, real]) != data_root:
            return None
    except ValueError:  # Different drives on Windows
        return None
    parts = os.path.relpath(real, data_root).lower().split(os.sep)
    if parts[0] in ("state", ".git") or parts[-1].startswith(".env"):
        return None
    return real if os.path.isfile(real) else None
