#!/usr/bin/env python3
"""Magnm Centralized Assets Management Service
Handles asset storage, metadata indexing, deduplication, and contextual retrieval for agents.
"""

import hashlib
import json
import mimetypes
import os
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "assets", "uploads")
ASSETS_REGISTRY = os.path.join(ROOT, "state", "assets.json")
os.makedirs(ASSETS_DIR, exist_ok=True)

def load_registry():
    if not os.path.exists(ASSETS_REGISTRY):
        return []
    try:
        with open(ASSETS_REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f).get("assets", [])
    except Exception:
        return []

def save_registry(assets):
    with open(ASSETS_REGISTRY, "w", encoding="utf-8") as f:
        json.dump({"assets": assets, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)

def register_asset(filename, content_bytes, category="brand", description="", tags=None):
    """Save asset file to disk and record metadata in registry."""
    if tags is None:
        tags = []

    # Calculate SHA256 hash for deduplication
    sha = hashlib.sha256(content_bytes).hexdigest()[:12]
    ext = os.path.splitext(filename)[1].lower()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    asset_id = f"asset_{ts}_{sha}"
    saved_filename = f"{asset_id}{ext}"
    saved_path = os.path.join(ASSETS_DIR, saved_filename)

    with open(saved_path, "wb") as f:
        f.write(content_bytes)

    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "application/octet-stream"

    # Infer file type group
    file_group = "document"
    if mime.startswith("image/"):
        file_group = "image"
    elif mime.startswith("video/"):
        file_group = "video"
    elif mime.startswith("audio/"):
        file_group = "audio"

    size_kb = round(len(content_bytes) / 1024, 1)
    size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 2)} MB"

    asset_entry = {
        "id": asset_id,
        "name": filename,
        "saved_filename": saved_filename,
        "relative_path": os.path.relpath(saved_path, ROOT),
        "mime_type": mime,
        "file_group": file_group,
        "size": size_str,
        "size_bytes": len(content_bytes),
        "category": category,
        "description": description or f"Uploaded {filename}",
        "tags": tags,
        "uploaded_at": datetime.now(timezone.utc).isoformat()
    }

    assets = load_registry()
    assets.insert(0, asset_entry)
    save_registry(assets)
    return asset_entry

def get_assets():
    return load_registry()

def delete_asset(asset_id):
    assets = load_registry()
    to_delete = next((a for a in assets if a["id"] == asset_id), None)
    if not to_delete:
        return False

    saved_path = os.path.join(ROOT, to_delete["relative_path"])
    if os.path.exists(saved_path):
        os.remove(saved_path)

    updated = [a for a in assets if a["id"] != asset_id]
    save_registry(updated)
    return True

if __name__ == "__main__":
    print(f"Registered assets: {len(get_assets())}")
