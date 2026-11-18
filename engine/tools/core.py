#!/usr/bin/env python3
"""Core built-in tools for Magnm Studio agents."""

from datetime import datetime, timezone
import json
import os
import re
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths
import product_rules
from tools import tool

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0F"
    "]+"
)


def _resolve_readable_path(*parts):
    """Return paths.data(*parts) if it exists, else paths.resource(*parts) if it exists."""
    candidate = paths.data(*parts)
    if os.path.exists(candidate):
        return candidate
    fallback = paths.resource(*parts)
    if os.path.exists(fallback):
        return fallback
    return candidate


def _format_single_product_rule(product_id):
    """Build a compact plain-text rules block for one product_id."""
    rules_path = _resolve_readable_path("config", "product_rules.json")
    rules = product_rules.load_rules(rules_path)
    show_levels = rules.get("show_levels", {})
    target = product_id.strip().lower()
    for prod in rules.get("products", []):
        pid = (prod.get("id") or "").lower()
        pname = (prod.get("name") or "").lower()
        if target in (pid, pname):
            name = prod.get("name") or prod.get("id") or ""
            status = prod.get("status", "")
            show = prod.get("show", "")
            meaning = show_levels.get(show, "")
            lines = [f"Product rule for {name} (id: {prod.get('id')}, status: {status}, show: {show}): {meaning}".strip()]
            blocked = prod.get("blocked_terms") or []
            if blocked:
                lines.append(f"Blocked terms: {', '.join(blocked)}")
            notes = (prod.get("notes") or "").strip()
            if notes:
                lines.append(f"Notes: {notes}")
            return "\n".join(lines)
    return f"No rule found for product_id '{product_id}'.\n" + product_rules.prompt_block(rules_path)


@tool(
    name="read_product_facts",
    description="Read verified product facts (research/product-facts.md) and product visibility/claim rules.",
    parameters_schema={
        "type": "object",
        "properties": {
            "product_id": {
                "type": "string",
                "description": "Optional product ID (e.g. 'cero' or 'lazylayout') to filter the rules block.",
            }
        },
        "required": [],
    },
)
def read_product_facts(ctx, product_id=None):
    facts_path = _resolve_readable_path("research", "product-facts.md")
    facts_text = ""
    if os.path.exists(facts_path):
        with open(facts_path, "r", encoding="utf-8") as f:
            facts_text = f.read()

    rules_path = _resolve_readable_path("config", "product_rules.json")
    if product_id:
        rules_text = _format_single_product_rule(product_id)
    else:
        rules_text = product_rules.prompt_block(rules_path)

    return {
        "product_facts": facts_text,
        "product_rules": rules_text,
    }


@tool(
    name="list_repos",
    description="List tracked product repositories from config/repos.json with id, name, product_id, status, and version.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def list_repos(ctx):
    repos_path = _resolve_readable_path("config", "repos.json")
    if not os.path.exists(repos_path):
        return {"repositories": []}
    with open(repos_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    repos = []
    for r in data.get("repositories", []):
        repos.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "product_id": r.get("product_id"),
                "status": r.get("status"),
                "version": r.get("version"),
            }
        )
    return {"repositories": repos}


@tool(
    name="read_repo_changes",
    description="Read recent commits and release version from research/repo-sync.json.",
    parameters_schema={
        "type": "object",
        "properties": {
            "repo_id": {
                "type": "string",
                "description": "Optional repository ID (e.g. 'cero' or 'lazylayout').",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of recent commits to return per repo (default 10).",
            },
        },
        "required": [],
    },
)
def read_repo_changes(ctx, repo_id=None, limit=10):
    sync_path = _resolve_readable_path("research", "repo-sync.json")
    if not os.path.exists(sync_path):
        return {"synced_at": None, "repositories": []}
    with open(sync_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    max_commits = max(1, min(int(limit or 10), 50))
    repos = data.get("repositories", [])
    if repo_id:
        target = repo_id.strip().lower()
        repos = [
            r for r in repos
            if target in ((r.get("id") or "").lower(), (r.get("product") or "").lower())
        ]
        if not repos:
            return {"error": f"Repository '{repo_id}' not found in repo-sync.json"}

    out = []
    for r in repos:
        commits = (r.get("recent_commits") or [])[:max_commits]
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "status": r.get("status"),
                "version": r.get("version"),
                "branch": r.get("branch"),
                "synced_at": r.get("synced_at"),
                "recent_commits": commits,
            }
        )
    return {"synced_at": data.get("synced_at"), "repositories": out}


@tool(
    name="search_research",
    description="Case-insensitive keyword search over outputs/research/*-findings.json and saved research_notes.",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword search query.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of hits to return (default 8).",
            },
        },
        "required": ["query"],
    },
)
def search_research(ctx, query, limit=8):
    max_hits = max(1, min(int(limit or 8), 50))
    tokens = [t for t in str(query or "").lower().split() if t]
    hits = []

    # 1. Search saved research_notes in SQLite
    for note in db.search_research_notes(query, limit=max_hits):
        hits.append(
            {
                "title": note["title"],
                "url": note.get("url") or "",
                "summary": (note.get("body") or "")[:400],
                "date": note.get("created_at") or "",
                "source": note.get("source") or "research_notes",
            }
        )

    if len(hits) >= max_hits:
        return {"query": query, "results": hits[:max_hits]}

    # 2. Search outputs/research/*-findings.json files
    res_dir = paths.data("outputs", "research")
    findings_files = []
    if os.path.isdir(res_dir):
        findings_files = sorted(
            [os.path.join(res_dir, f) for f in os.listdir(res_dir) if f.endswith("-findings.json")],
            reverse=True,
        )
    if not findings_files:
        fallback_dir = paths.resource("outputs", "research")
        if os.path.isdir(fallback_dir):
            findings_files = sorted(
                [os.path.join(fallback_dir, f) for f in os.listdir(fallback_dir) if f.endswith("-findings.json")],
                reverse=True,
            )

    for fp in findings_files:
        if len(hits) >= max_hits:
            break
        try:
            with open(fp, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or item.get("description") or item.get("engagement") or "")
            source = str(item.get("source") or os.path.basename(fp))
            url = str(item.get("url") or "")
            query_tag = str(item.get("query") or "")
            haystack = " ".join((title, summary, source, url, query_tag)).lower()
            if not tokens or all(tok in haystack for tok in tokens):
                hits.append(
                    {
                        "title": title,
                        "url": url,
                        "summary": summary,
                        "date": str(item.get("created_at") or item.get("date") or ""),
                        "source": source,
                    }
                )
                if len(hits) >= max_hits:
                    break

    return {"query": query, "results": hits[:max_hits]}


@tool(
    name="save_research_note",
    description="Save a structured research note to the local research_notes database table.",
    parameters_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the note."},
            "body": {"type": "string", "description": "Detailed findings or takeaway."},
            "url": {"type": "string", "description": "Optional source URL."},
            "source": {"type": "string", "description": "Optional source label (e.g. 'Hacker News')."},
        },
        "required": ["title", "body"],
    },
    side_effects=True,
)
def save_research_note(ctx, title, body, url=None, source=None):
    note_id = f"note_{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()
    saved = db.save_research_note(
        note_id=note_id,
        title=title,
        body=body,
        url=url,
        source=source,
        agent_id=ctx.agent_id if ctx else None,
        created_at=created_at,
    )
    return {"saved": True, "id": saved["id"], "created_at": saved["created_at"]}


@tool(
    name="check_text",
    description="Check draft text against Magnm product rules and zero-emoji policy.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Draft copy to check for product rules and emojis.",
            }
        },
        "required": ["text"],
    },
)
def check_text(ctx, text):
    rules_path = _resolve_readable_path("config", "product_rules.json")
    warnings = list(product_rules.check_text(text, path=rules_path))
    if _EMOJI_RE.search(text or ""):
        warnings.append(
            {
                "product": "brand",
                "severity": "warn",
                "message": "Text contains emoji (strict zero-emoji policy).",
            }
        )
    return {
        "ok": len(warnings) == 0,
        "warnings": warnings,
    }


@tool(
    name="ask_user",
    description="Pause the session and ask the user a clarifying question before continuing.",
    parameters_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Question to display to the user.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of suggested answer choices.",
            },
        },
        "required": ["question"],
    },
)
def ask_user(ctx, question, options=None):
    return {
        "status": "waiting_user",
        "question": question,
        "options": list(options or []),
    }
