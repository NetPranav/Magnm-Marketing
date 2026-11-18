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
import repos
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
    description="Read verified product facts (approved DB facts + research/product-facts.md) and product visibility/claim rules.",
    parameters_schema={
        "type": "object",
        "properties": {
            "product_id": {
                "type": "string",
                "description": "Optional product ID (e.g. 'cero') to filter approved facts and rules.",
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

    approved = db.list_product_facts(product_id=product_id, status="approved", limit=200)
    pending = db.list_product_facts(product_id=product_id, status="pending", limit=200)

    return {
        "product_facts": facts_text,
        "approved_facts": [
            {
                "id": f["id"],
                "product_id": f["product_id"],
                "text": f["text"],
                "source_url": f["source_url"],
                "source_kind": f["source_kind"],
                "decided_at": f["decided_at"],
            }
            for f in approved
        ],
        "pending_count": len(pending),
        "note": (
            "Only approved_facts and research/product-facts.md are verified for use. "
            "Pending facts are awaiting CEO approval and are not usable yet."
        ),
        "product_rules": rules_text,
    }


@tool(
    name="list_repos",
    description="List tracked product repositories from config/repos.json with id, name, product_id, kind, confidential, status, and version.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def list_repos(ctx):
    out = []
    for r in repos.list_repos():
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "product_id": r.get("product_id"),
                "kind": r.get("kind"),
                "confidential": bool(r.get("confidential")),
                "status": r.get("status"),
                "version": r.get("version"),
            }
        )
    return {"repositories": out}


def _taint_if_confidential(ctx, repo_obj):
    if not ctx or not repo_obj:
        return
    is_conf = bool(repo_obj.get("confidential"))
    if not is_conf:
        pid = (repo_obj.get("product_id") or repo_obj.get("id") or "").lower()
        try:
            rules_path = _resolve_readable_path("config", "product_rules.json")
            if os.path.exists(rules_path) and pid in product_rules.confidential_product_ids(rules_path):
                is_conf = True
        except Exception:
            pass
    if is_conf and callable(getattr(ctx, "mark_confidential", None)):
        ctx.mark_confidential()


@tool(
    name="read_repo_changes",
    description="Read recent commits, tags, releases, and version from synced repository snapshots.",
    parameters_schema={
        "type": "object",
        "properties": {
            "repo_id": {
                "type": "string",
                "description": "Optional repository ID (e.g. 'cero').",
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
    max_commits = max(1, min(int(limit or 10), 50))
    configured = repos.list_repos()

    if repo_id:
        target = repo_id.strip().lower()
        matched = [
            r for r in configured
            if target in ((r.get("id") or "").lower(), (r.get("product_id") or "").lower())
        ]
        if not matched:
            return {"error": f"Repository '{repo_id}' not found"}
    else:
        matched = configured

    sync_path = _resolve_readable_path("research", "repo-sync.json")
    file_data = {}
    if os.path.exists(sync_path):
        try:
            with open(sync_path, "r", encoding="utf-8") as f:
                file_data = json.load(f)
        except Exception:
            file_data = {}
    file_by_id = {
        (r.get("id") or "").lower(): r
        for r in (file_data.get("repositories") or [])
        if isinstance(r, dict)
    }

    out = []
    synced_at = file_data.get("synced_at")
    for cfg in matched:
        _taint_if_confidential(ctx, cfg)
        snap = db.get_latest_repo_snapshot(cfg["id"])
        src = (snap.get("data") if snap else None) or file_by_id.get(cfg["id"].lower()) or cfg
        if snap and snap.get("fetched_at"):
            synced_at = snap["fetched_at"]
        commits = (src.get("recent_commits") or [])[:max_commits]
        out.append(
            {
                "id": cfg.get("id"),
                "name": cfg.get("name"),
                "product_id": cfg.get("product_id"),
                "kind": cfg.get("kind"),
                "confidential": bool(cfg.get("confidential")),
                "status": cfg.get("status"),
                "version": src.get("version") or cfg.get("version"),
                "branch": src.get("branch") or cfg.get("branch"),
                "synced_at": src.get("synced_at") or synced_at,
                "recent_commits": commits,
                "tags": (src.get("tags") or [])[:10],
                "releases": (src.get("releases") or [])[:5],
            }
        )
    return {"synced_at": synced_at, "repositories": out}


@tool(
    name="read_repo_file",
    description="Read a UTF-8 text file (<= 40 KB) from a local repository without escaping the repository root.",
    parameters_schema={
        "type": "object",
        "properties": {
            "repo_id": {"type": "string", "description": "Local repository ID."},
            "path": {"type": "string", "description": "Relative file path inside the repository."},
        },
        "required": ["repo_id", "path"],
    },
)
def read_repo_file(ctx, repo_id, path):
    repo = repos.get_repo(repo_id)
    if repo is None:
        return {"error": f"Repository '{repo_id}' not found"}
    if repo.get("kind") != "local" or not repo.get("local_path"):
        return {"error": "read_repo_file is only available for local repositories"}

    raw_path = str(path or "").strip()
    if not raw_path or os.path.isabs(raw_path) or raw_path.startswith(("/", "\\")):
        return {"error": "Invalid path: relative path inside repository required"}
    if len(raw_path) >= 2 and raw_path[1] == ":" and raw_path[0].isalpha():
        return {"error": "Invalid path: drive-prefixed absolute paths are not allowed"}
    parts = [p for p in re.split(r"[\\/]+", raw_path) if p]
    if not parts or any(p == ".." for p in parts):
        return {"error": "Invalid path: parent directory traversal is not allowed"}

    repo_root = os.path.realpath(repo["local_path"])
    candidate = os.path.realpath(os.path.join(repo_root, *parts))
    try:
        inside = os.path.commonpath([repo_root, candidate]) == repo_root
    except ValueError:
        inside = False
    if not inside or candidate == repo_root:
        return {"error": "Invalid path: target escapes repository root"}
    if not os.path.isfile(candidate):
        return {"error": f"File not found: {raw_path}"}

    max_bytes = 40 * 1024
    try:
        size = os.path.getsize(candidate)
    except OSError as exc:
        return {"error": f"Cannot stat file: {exc}"}
    if size > max_bytes:
        return {"error": f"File exceeds 40 KB limit ({size} bytes)"}

    try:
        with open(candidate, "rb") as f:
            raw_bytes = f.read(max_bytes + 1)
    except OSError as exc:
        return {"error": f"Cannot read file: {exc}"}
    if len(raw_bytes) > max_bytes:
        return {"error": "File exceeds 40 KB limit"}
    if b"\x00" in raw_bytes:
        return {"error": "Binary files are not supported"}
    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "Binary or non-UTF-8 files are not supported"}

    _taint_if_confidential(ctx, repo)
    rel = os.path.relpath(candidate, repo_root).replace("\\", "/")
    return {
        "repo_id": repo["id"],
        "path": rel,
        "size": len(raw_bytes),
        "content": content,
    }


@tool(
    name="list_repo_media",
    description="List demo media files (docs/**, assets/**) discovered for a repository.",
    parameters_schema={
        "type": "object",
        "properties": {
            "repo_id": {"type": "string", "description": "Repository ID."},
        },
        "required": ["repo_id"],
    },
)
def list_repo_media(ctx, repo_id):
    repo = repos.get_repo(repo_id)
    if repo is None:
        return {"error": f"Repository '{repo_id}' not found"}
    _taint_if_confidential(ctx, repo)

    snap = db.get_latest_repo_snapshot(repo["id"])
    media = None
    if snap and isinstance(snap.get("data"), dict):
        media = snap["data"].get("media")
    if media is None and repo.get("kind") == "local" and repo.get("local_path") and os.path.isdir(repo["local_path"]):
        media = repos.scan_local_media(repo["local_path"])
    return {"repo_id": repo["id"], "media": list(media or [])}


@tool(
    name="shipped_this_week",
    description="Return recent releases, merged PRs, commits, and tags across monitored repositories, newest first.",
    parameters_schema={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Lookback window in days (default 7).",
            }
        },
        "required": [],
    },
)
def shipped_this_week(ctx, days=7):
    days_int = max(1, min(int(days or 7), 365))
    items = repos.shipped(days=days_int)
    by_id = {r["id"]: r for r in repos.list_repos()}
    for item in items:
        r = by_id.get(item.get("repo_id"))
        if r:
            _taint_if_confidential(ctx, r)
    return {"days": days_int, "items": items}


@tool(
    name="draft_repo_facts",
    description="Load README excerpt and recent shipped items for a repo so the researcher can propose product facts.",
    parameters_schema={
        "type": "object",
        "properties": {
            "repo_id": {"type": "string", "description": "Repository ID."},
        },
        "required": ["repo_id"],
    },
)
def draft_repo_facts(ctx, repo_id):
    repo = repos.get_repo(repo_id)
    if repo is None:
        return {"error": f"Repository '{repo_id}' not found"}
    _taint_if_confidential(ctx, repo)

    snap = db.get_latest_repo_snapshot(repo["id"])
    data = snap.get("data") if snap else None
    if not isinstance(data, dict):
        data = repos.sync_repo(repo["id"])

    shipped_items = repos.shipped(repo_id=repo["id"], days=14)
    return {
        "repo_id": repo["id"],
        "product_id": repo.get("product_id") or repo["id"],
        "name": repo.get("name"),
        "version": data.get("version") or repo.get("version"),
        "readme_excerpt": (data.get("readme") or "")[:6000],
        "shipped_items": shipped_items[:30],
        "limit_per_session": 15,
    }


@tool(
    name="propose_fact",
    description="Propose a single verifiable product fact in pending status for CEO approval (max 15 per session).",
    parameters_schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Product ID (e.g. 'cero')."},
            "text": {"type": "string", "description": "Concise factual statement grounded in repo sources."},
            "source_url": {"type": ["string", "null"], "description": "Source URL or file/commit reference."},
            "source_kind": {
                "type": "string",
                "enum": ["readme", "release", "pr", "commit", "manual"],
                "description": "Source type: readme, release, pr, commit, or manual.",
            },
        },
        "required": ["product_id", "text", "source_kind"],
    },
    side_effects=True,
)
def propose_fact(ctx, product_id, text, source_url=None, source_kind="readme"):
    prior_db = db.count_session_tool_calls(ctx.session_id, "propose_fact") if ctx and ctx.session_id else 0
    prior_ctx = int(getattr(ctx, "_proposed_facts_count", 0) or 0) if ctx else 0
    if max(prior_db, prior_ctx) >= 15:
        return {"error": "Limit of 15 proposed facts per session reached."}

    fact = db.create_product_fact(
        product_id=product_id,
        text=text,
        source_url=source_url,
        source_kind=source_kind,
        status="pending",
        created_by=ctx.agent_id if ctx else "agent",
    )
    if ctx is not None:
        ctx._proposed_facts_count = prior_ctx + 1
    return {"proposed": True, "fact": fact}


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
