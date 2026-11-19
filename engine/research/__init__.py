#!/usr/bin/env python3
"""Research package: multi-source search, safe page fetching, browser automation, and watch topics."""

import json
import os
import sys

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_RESEARCH_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

USER_AGENT = "MagnmStudio/0.1 (research)"
DEFAULT_TOPICS = (
    "AI terminal",
    "CLI AI agent",
    "developer tools launch",
)


def load_research_config():
    """Load config/research.json from DATA_ROOT or RESOURCE_ROOT with safe defaults."""
    defaults = {
        "blocked_domains": [],
        "default_topics": list(DEFAULT_TOPICS),
    }
    for candidate in (
        paths.data("config", "research.json"),
        paths.resource("config", "research.json"),
    ):
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    blocked = [
                        str(d).strip().lower().lstrip(".")
                        for d in (data.get("blocked_domains") or [])
                        if str(d).strip()
                    ]
                    topics = []
                    for t in list(DEFAULT_TOPICS) + list(data.get("default_topics") or []):
                        ts = str(t).strip()
                        if ts and ts not in topics:
                            topics.append(ts)
                    return {
                        "blocked_domains": blocked,
                        "default_topics": topics,
                    }
            except Exception:
                pass
    return defaults


def get_setting(name):
    """Read an optional env var or key from os.environ or DATA_ROOT/.env without logging it."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_path = paths.data(".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    if k.strip() == name:
                        cleaned = v.strip()
                        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
                            cleaned = cleaned[1:-1]
                        if cleaned:
                            return cleaned
        except OSError:
            pass
    return ""
