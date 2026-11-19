#!/usr/bin/env python3
"""Watch topics radar: evaluate due topics, run search, dedupe into research_items, and emit digests."""

from datetime import datetime, timezone
import os
import sys

_RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_RESEARCH_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events
from research import search as search_mod


def _as_utc(dt=None):
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return _as_utc(dt)
    except ValueError:
        return None


def is_topic_due(topic, now=None):
    """Return True if an enabled watch topic is due to run at `now`."""
    if not isinstance(topic, dict) or not topic.get("enabled", True):
        return False
    now_utc = _as_utc(now)
    last_dt = _parse_iso(topic.get("last_run"))
    if last_dt is None:
        return True
    cadence_hours = float(topic.get("cadence_hours") or 24)
    return (now_utc - last_dt).total_seconds() >= cadence_hours * 3600.0


def run_watch_topics(now=None):
    """Scan all due watch_topics, store unique items in research_items, and publish research_digest."""
    now_utc = _as_utc(now)
    now_iso = now_utc.isoformat()
    topics = db.list_watch_topics(enabled_only=True)

    topics_due = 0
    new_items_total = 0
    per_topic = []
    warnings = []

    for topic in topics:
        if not is_topic_due(topic, now=now_utc):
            continue
        topics_due += 1
        res = search_mod.search(
            topic["query"],
            sources=topic.get("sources"),
            days=topic.get("days"),
            limit=20,
        )
        items = res.get("results") or []
        inserted = db.insert_research_items(items, topic_id=topic["id"], found_at=now_iso)
        db.update_watch_topic_last_run(topic["id"], last_run=now_iso)
        new_items_total += inserted
        warnings.extend(res.get("warnings") or [])
        per_topic.append(
            {
                "topic_id": topic["id"],
                "query": topic["query"],
                "fetched": len(items),
                "new_items": inserted,
            }
        )

    digest = {
        "topics_total": len(topics),
        "topics_due": topics_due,
        "new_items": new_items_total,
        "per_topic": per_topic,
        "warnings": warnings,
        "ran_at": now_iso,
    }
    events.publish("research_digest", digest)
    return digest
