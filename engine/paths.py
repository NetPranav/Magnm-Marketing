#!/usr/bin/env python3
"""Path resolution and per-user data directory seeding for the Magnm engine.

Separates read-only shipped resources (RESOURCE_ROOT) from writable per-user
data (DATA_ROOT, overridden via MAGNM_HOME).
"""

import os
import shutil
import sys

if getattr(sys, "frozen", False):
    RESOURCE_ROOT = os.path.abspath(sys._MEIPASS)
else:
    RESOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_env_home = os.environ.get("MAGNM_HOME", "").strip()
if _env_home:
    DATA_ROOT = os.path.abspath(os.path.expanduser(_env_home))
else:
    DATA_ROOT = RESOURCE_ROOT

_EMPTY_DATA_DIRS = (
    ("state",),
    ("state", "chats"),
    ("outputs",),
    ("assets", "uploads"),
    ("logs", "jobs"),
    ("cache",),
    ("queue", "agy"),
)

_SEED_FILES = (
    ("research", "product-facts.md"),
    ("config.md",),
)


def resource(*parts):
    """Join path components under the read-only RESOURCE_ROOT."""
    return os.path.join(RESOURCE_ROOT, *parts)


def data(*parts):
    """Join path components under the writable DATA_ROOT."""
    return os.path.join(DATA_ROOT, *parts)


def _copy_tree_no_overwrite(src_dir, dst_dir):
    """Recursively copy files from src_dir to dst_dir without overwriting existing files."""
    if not os.path.isdir(src_dir):
        return 0
    copied = 0
    for root_dir, _, files in os.walk(src_dir):
        rel = os.path.relpath(root_dir, src_dir)
        target_dir = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            src_file = os.path.join(root_dir, name)
            dst_file = os.path.join(target_dir, name)
            if not os.path.exists(dst_file):
                shutil.copy2(src_file, dst_file)
                copied += 1
    return copied


def ensure_data_root():
    """Create DATA_ROOT and required folders, seeding missing defaults from RESOURCE_ROOT."""
    os.makedirs(DATA_ROOT, exist_ok=True)
    for parts in _EMPTY_DATA_DIRS:
        os.makedirs(data(*parts), exist_ok=True)

    if os.path.abspath(DATA_ROOT) == os.path.abspath(RESOURCE_ROOT):
        return DATA_ROOT

    src_config = resource("config")
    dst_config = data("config")
    if os.path.isdir(src_config):
        os.makedirs(dst_config, exist_ok=True)
        for name in os.listdir(src_config):
            if name.endswith(".json"):
                src_file = os.path.join(src_config, name)
                dst_file = os.path.join(dst_config, name)
                if os.path.isfile(src_file) and not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)

    for folder in ("agents", "playbooks"):
        _copy_tree_no_overwrite(resource(folder), data(folder))

    for parts in _SEED_FILES:
        src_file = resource(*parts)
        dst_file = data(*parts)
        if os.path.isfile(src_file) and not os.path.exists(dst_file):
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            shutil.copy2(src_file, dst_file)

    return DATA_ROOT
