#!/usr/bin/env python3
"""Magnm Autonomous Research Scanner.

Delegates live search to engine/research/search.py across configured default topics,
scores opportunities using the Magnm formula:
  Score = Relevance (1-5) * AudienceInterest (1-5) * Credibility (1-5) * Timeliness (1-5)
and writes outputs/research/<YYYYMMDD>-findings.json and -summary.md.
"""

from datetime import datetime, timezone
import json
import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths
from research import load_research_config
from research import search as research_search

ROOT = paths.DATA_ROOT
OUTPUT_DIR = paths.data("outputs", "research")

_SOURCE_LABELS = {
    "hn": "Hacker News",
    "reddit": "Reddit",
    "github": "GitHub",
    "devto": "Dev.to",
    "web": "Web",
    "searxng": "SearXNG",
    "browser_search": "Browser Search",
    "tavily": "Tavily",
    "exa": "Exa",
}


def _to_finding_item(raw, topic):
    src_code = str(raw.get("source") or "web")
    source_label = _SOURCE_LABELS.get(src_code, src_code)
    points = int(raw.get("points") if raw.get("points") is not None else (raw.get("score") or 0))
    comments = int(raw.get("comments") or 0)
    if src_code == "hn":
        engagement = f"{points} points, {comments} comments"
    elif src_code == "reddit":
        engagement = f"{points} upvotes, {comments} comments"
    elif src_code == "github":
        engagement = f"{points} stars"
    elif src_code == "devto":
        engagement = f"{points} reactions"
    else:
        engagement = str(raw.get("snippet") or "")[:120]

    return {
        "source": source_label,
        "title": str(raw.get("title") or ""),
        "url": str(raw.get("url") or ""),
        "created_at": str(raw.get("published_at") or ""),
        "snippet": str(raw.get("snippet") or ""),
        "engagement": engagement,
        "points": points,
        "comments": comments,
        "query": topic,
    }


def scan_hacker_news(queries=None):
    """Compatibility wrapper: scan Hacker News via engine.research.search."""
    qs = queries or ["terminal AI", "local LLM terminal", "CLI agent", "developer command line"]
    items = []
    for q in qs:
        res = research_search.search(q, sources=["hn"], days=14, limit=5)
        for raw in res.get("results") or []:
            items.append(_to_finding_item(raw, q))
    return items


def scan_reddit(queries=None):
    """Compatibility wrapper: scan Reddit via engine.research.search."""
    qs = queries or ["commandline", "rust", "LocalLLaMA"]
    items = []
    for q in qs:
        res = research_search.search(q, sources=["reddit"], days=14, limit=5)
        for raw in res.get("results") or []:
            items.append(_to_finding_item(raw, q))
    return items


def score_item(item):
    """Scoring formula:

    Opportunity = Relevance (1-5) * AudienceInterest (1-5) * Credibility (1-5) * Timeliness (1-5)
    Max score: 625.
    """
    title = str(item.get("title") or "").lower()
    points = int(item.get("points") if item.get("points") is not None else (item.get("score") or 0))
    comments = int(item.get("comments") or 0)

    # 1. Relevance to Cero & Magnm (1-5)
    relevance = 2
    if "terminal" in title or "cli" in title or "command" in title:
        relevance += 1
    if "local" in title or "llm" in title or "agent" in title:
        relevance += 1
    if "security" in title or "safety" in title or "permission" in title or "tauri" in title:
        relevance += 1
    relevance = min(relevance, 5)

    # 2. Audience Interest (1-5)
    interest = 1
    total_eng = points + comments * 2
    if total_eng > 20:
        interest = 2
    if total_eng > 50:
        interest = 3
    if total_eng > 150:
        interest = 4
    if total_eng > 300:
        interest = 5

    # 3. Credibility (1-5)
    src = str(item.get("source") or "")
    credibility = 4 if src in ("Hacker News", "hn", "GitHub", "github") else 3
    if comments > 15:
        credibility = min(credibility + 1, 5)

    # 4. Timeliness (1-5)
    timeliness = 4
    if points > 100:
        timeliness = 5

    final_score = relevance * interest * credibility * timeliness
    return {
        "relevance": relevance,
        "audience_interest": interest,
        "credibility": credibility,
        "timeliness": timeliness,
        "score": final_score,
        "max_score": 625,
    }


def run_scan():
    """Run market scan over configured default_topics and write findings JSON and Markdown."""
    out_dir = paths.data("outputs", "research")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    cfg = load_research_config()
    topics = cfg.get("default_topics") or ["AI terminal", "CLI AI agent", "developer tools launch"]

    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting market & developer intelligence scan...")

    raw_items = []
    warnings = []
    for topic in topics:
        print(f"- Searching topic: {topic}")
        res = research_search.search(topic, days=14, limit=10)
        for item in res.get("results") or []:
            raw_items.append(_to_finding_item(item, topic))
        warnings.extend(res.get("warnings") or [])

    seen_urls = set()
    deduped = []
    for it in raw_items:
        norm = research_search.normalize_url(it.get("url"))
        if norm and norm not in seen_urls:
            seen_urls.add(norm)
            deduped.append(it)

    print(f"- Found {len(deduped)} candidate opportunities. Scoring...")

    scored_items = []
    for it in deduped:
        score_data = score_item(it)
        scored_items.append({**it, "scoring": score_data})

    scored_items.sort(key=lambda x: x["scoring"]["score"], reverse=True)

    json_path = os.path.join(out_dir, f"{today}-findings.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scored_items, f, indent=2)

    md_path = os.path.join(out_dir, f"{today}-summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Market Intelligence & Opportunity Scan ({today})\n\n")
        f.write(f"Scanned topics: {', '.join(topics)}.\n")
        f.write(
            "Scoring formula: `Relevance (1-5) x Audience Interest (1-5) "
            "x Credibility (1-5) x Timeliness (1-5)` (Max: 625)\n\n"
        )
        if warnings:
            f.write(f"Warnings: {len(warnings)}\n\n")
        f.write("## Top Ranked Opportunities for Cero / Magnm\n\n")

        for idx, item in enumerate(scored_items[:8], 1):
            sc = item["scoring"]
            f.write(f"### {idx}. {item['title']}\n")
            f.write(f"- **Source:** {item['source']} | **Engagement:** {item['engagement']}\n")
            f.write(f"- **Link:** [{item['url']}]({item['url']})\n")
            f.write(
                f"- **Opportunity Score:** `{sc['score']}/625` "
                f"(R:{sc['relevance']} x I:{sc['audience_interest']} "
                f"x C:{sc['credibility']} x T:{sc['timeliness']})\n"
            )
            f.write(
                "- **Why it matters to Cero:** High developer interest in command-line workflows "
                "and local AI execution. Safe human-approval execution angle fits directly here.\n\n"
            )

    print(f"[OK] Scan complete. Generated:\n  - {json_path}\n  - {md_path}")
    return md_path


if __name__ == "__main__":
    run_scan()
