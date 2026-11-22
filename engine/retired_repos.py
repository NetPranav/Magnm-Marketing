#!/usr/bin/env python3
"""Remember the names of confidential repos that were removed, so removing one never declassifies its text.

A removed (or no longer shared) confidential repo leaves snapshots, shipped items and facts behind. Its names stay
on this list so the confidential guard keeps matching them. Only names are stored, never repo content.
"""

import json
import os
import sys
import tempfile
import threading

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

MAX_ENTRIES = 200
_NAME_KEYS = ("id", "name", "product_id", "github")
_lock = threading.Lock()


def _path():
    return paths.data("state", "retired_confidential_repos.json")


def load():
    """Return the remembered entries. Missing file means none; an unreadable file raises (callers fail closed)."""
    path = _path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("retired repos file is not a list")
    return [e for e in data if isinstance(e, dict)]


def terms():
    """Return every remembered name as a flat list of strings."""
    out = []
    for entry in load():
        for key in _NAME_KEYS:
            value = str(entry.get(key) or "").strip()
            if value:
                out.append(value)
    return out


def remember(repos, strict=True):
    """Remember the names of the confidential repos in `repos`; other repos are ignored.

    With `strict` a failure to save raises, so callers can refuse to remove the repo. Otherwise it is best effort.
    """
    fresh = []
    for repo in repos or []:
        if not isinstance(repo, dict) or not repo.get("confidential"):
            continue
        entry = {k: str(repo.get(k))[:160] for k in _NAME_KEYS if repo.get(k)}
        if entry:
            fresh.append(entry)
    if not fresh:
        return
    with _lock:
        try:
            try:
                current = load()
            except Exception:
                current = []
            for entry in fresh:
                if entry not in current:
                    current.append(entry)
            current = current[-MAX_ENTRIES:]
            path = _path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="retired_repos_", suffix=".tmp", dir=os.path.dirname(path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(current, f)
            os.replace(tmp, path)
        except Exception:
            if strict:
                raise
