#!/usr/bin/env python3
"""Magnm Autonomous Research Scanner
Pulls live discussions from Hacker News, Reddit, and GitHub developer ecosystems.
Scores opportunities using:
  Score = Relevance (1-5) * AudienceInterest (1-5) * Credibility (1-5) * Timeliness (1-5)
Saves evidence and findings to outputs/research/
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

ROOT = paths.DATA_ROOT
OUTPUT_DIR = paths.data("outputs", "research")
os.makedirs(OUTPUT_DIR, exist_ok=True)

USER_AGENT = "MagnmResearchBot/1.0 (Developer Tool Market Scanner; contact: team@magnm.org)"

# Keywords relevant to Cero & Magnm developer ecosystem
KEYWORDS = ["terminal", "command line", "CLI", "local llm", "tauri", "rust cli", "developer tools", "ollama"]

def fetch_json(url, headers=None):
    """Fetch JSON with clean headers and timeout."""
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}", file=sys.stderr)
        return None

def scan_hacker_news():
    """Scan Hacker News via Algolia API (official & public)."""
    items = []
    queries = ["terminal AI", "local LLM terminal", "CLI agent", "developer command line"]
    one_week_ago = int((datetime.now(timezone.utc) - timedelta(days=14)).timestamp())

    for q in queries:
        url = f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(q)}&tags=story&numericFilters=created_at_i>{one_week_ago}&hitsPerPage=5"
        data = fetch_json(url)
        if not data or "hits" not in data:
            continue
        for hit in data["hits"]:
            points = hit.get("points") or 0
            comments = hit.get("num_comments") or 0
            title = hit.get("title", "")
            story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            created_at = hit.get("created_at", "")

            # Quick relevance filter
            items.append({
                "source": "Hacker News",
                "title": title,
                "url": story_url,
                "created_at": created_at,
                "engagement": f"{points} points, {comments} comments",
                "points": points,
                "comments": comments,
                "query": q
            })
    return items

def scan_reddit():
    """Scan relevant developer subreddits via public JSON."""
    items = []
    subreddits = ["commandline", "rust", "LocalLLaMA"]
    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=5"
        data = fetch_json(url)
        if not data or "data" not in data or "children" not in data["data"]:
            continue
        for child in data["data"]["children"]:
            post = child.get("data", {})
            if post.get("stickied"):
                continue
            title = post.get("title", "")
            score = post.get("score", 0)
            comments = post.get("num_comments", 0)
            permalink = f"https://reddit.com{post.get('permalink', '')}"
            created_utc = post.get("created_utc", 0)
            created_at = datetime.fromtimestamp(created_utc, timezone.utc).isoformat() if created_utc else ""

            # Check if any keyword matches title
            t_lower = title.lower()
            if any(k.lower() in t_lower for k in KEYWORDS) or sub in ["commandline", "LocalLLaMA"]:
                items.append({
                    "source": f"Reddit r/{sub}",
                    "title": title,
                    "url": permalink,
                    "created_at": created_at,
                    "engagement": f"{score} upvotes, {comments} comments",
                    "points": score,
                    "comments": comments,
                    "query": sub
                })
        time.sleep(1) # respectful rate limiting
    return items

def score_item(item):
    """
    Scoring formula:
    Opportunity = Relevance (1-5) * AudienceInterest (1-5) * Credibility (1-5) * Timeliness (1-5)
    Max score: 625.
    """
    title = item["title"].lower()
    points = item.get("points", 0)
    comments = item.get("comments", 0)

    # 1. Relevance to Cero & Magnm (1-5)
    # Cero is a local terminal AI that asks first before executing state-altering commands
    relevance = 2
    if "terminal" in title or "cli" in title or "command" in title:
        relevance += 1
    if "local" in title or "llm" in title or "agent" in title:
        relevance += 1
    if "security" in title or "safety" in title or "permission" in title or "tauri" in title:
        relevance += 1
    relevance = min(relevance, 5)

    # 2. Audience Interest (1-5)
    # Based on points / upvotes and discussion volume
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
    # HN stories with discussion or technical subreddit threads have high signal
    credibility = 4 if item["source"] == "Hacker News" else 3
    if comments > 15:
        credibility = min(credibility + 1, 5)

    # 4. Timeliness (1-5)
    timeliness = 4 # scanned within last 14 days
    if points > 100:
        timeliness = 5

    final_score = relevance * interest * credibility * timeliness
    return {
        "relevance": relevance,
        "audience_interest": interest,
        "credibility": credibility,
        "timeliness": timeliness,
        "score": final_score,
        "max_score": 625
    }

def run_scan():
    out_dir = paths.data("outputs", "research")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting market & developer intelligence scan...")

    raw_items = []
    print("- Scanning Hacker News Algolia API...")
    raw_items.extend(scan_hacker_news())
    print("- Scanning Developer Subreddits...")
    raw_items.extend(scan_reddit())

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for it in raw_items:
        u = it.get("url")
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(it)

    print(f"- Found {len(deduped)} candidate opportunities. Scoring...")

    scored_items = []
    for it in deduped:
        score_data = score_item(it)
        scored_items.append({**it, "scoring": score_data})

    # Sort by score descending
    scored_items.sort(key=lambda x: x["scoring"]["score"], reverse=True)

    json_path = os.path.join(out_dir, f"{today}-findings.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scored_items, f, indent=2)

    # Generate human-readable Markdown summary
    md_path = os.path.join(out_dir, f"{today}-summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Market Intelligence & Opportunity Scan ({today})\n\n")
        f.write(f"Scanned sources: Hacker News, Reddit (r/commandline, r/rust, r/LocalLLaMA).\n")
        f.write(f"Scoring formula: `Relevance (1-5) × Audience Interest (1-5) × Credibility (1-5) × Timeliness (1-5)` (Max: 625)\n\n")
        f.write("## Top Ranked Opportunities for Cero / Magnm\n\n")

        for idx, item in enumerate(scored_items[:8], 1):
            sc = item["scoring"]
            f.write(f"### {idx}. {item['title']}\n")
            f.write(f"- **Source:** {item['source']} | **Engagement:** {item['engagement']}\n")
            f.write(f"- **Link:** [{item['url']}]({item['url']})\n")
            f.write(f"- **Opportunity Score:** `{sc['score']}/625` (R:{sc['relevance']} × I:{sc['audience_interest']} × C:{sc['credibility']} × T:{sc['timeliness']})\n")
            f.write(f"- **Why it matters to Cero:** High developer interest in command-line workflows and local AI execution. Safe human-approval execution angle fits directly here.\n\n")

    print(f"[OK] Scan complete. Generated:\n  - {json_path}\n  - {md_path}")
    return md_path

if __name__ == "__main__":
    run_scan()
