#!/usr/bin/env python3
"""Engine-side MCP tool definitions, confidential filtering, settings, and CLI snippets."""

import json
import os
import re
import shlex
import sys
from types import SimpleNamespace
import urllib.parse
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import cloud_sync
import coding_agents
import db
import events
import paths
import product_rules
import reminders
import repos
import review
from tools import content as content_tools
import weekly_summary

MCP_WRITES_META_KEY = "mcp_writes_enabled"
MCP_ACTOR = "coding_agent"
MCP_VALID_CONTENT_TYPES = ("post", "thread", "blog", "script", "card", "image", "plan")

READ_ONLY_TOOL_SPECS = (
    {
        "name": "list_drafts",
        "description": "List content drafts stored in Magnm Studio, optionally filtered by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "ready", "posted"],
                    "description": "Optional status filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of drafts to return (default 20).",
                },
            },
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "read_draft",
        "description": "Read a single content draft and its latest text by content_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The content_id of the draft to read.",
                },
            },
            "required": ["content_id"],
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "list_research_notes",
        "description": "List saved research notes newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of research notes to return (default 20).",
                },
            },
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "latest_weekly_summary",
        "description": "Return the most recent weekly performance summary.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "list_reminders",
        "description": "List reminders filtered by status ('open', 'done', or 'all').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "done", "all"],
                    "description": "Filter reminders by status (default 'open').",
                },
            },
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "product_rules",
        "description": "Return public product rules and show levels so drafted copy follows Studio rules.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "write": False,
    },
    {
        "name": "list_repos",
        "description": "List non-confidential repositories connected to Magnm Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "write": False,
    },
)

WRITE_TOOL_SPECS = (
    {
        "name": "create_draft",
        "description": "Create a new content draft in Magnm Studio; runs product rules and Reviewer checks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short title or headline for the draft.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Full draft body text.",
                },
                "platform": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Target platform (e.g. x, linkedin, instagram, youtube, blog).",
                },
                "type": {
                    "type": "string",
                    "enum": list(MCP_VALID_CONTENT_TYPES),
                    "description": "Content type: post, thread, blog, script, card, image, or plan.",
                },
            },
            "required": ["title", "body", "platform", "type"],
            "additionalProperties": False,
        },
        "write": True,
    },
    {
        "name": "add_research_note",
        "description": "Save a research note into Magnm Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short title for the research note.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Note body and key takeaways.",
                },
                "url": {
                    "type": "string",
                    "description": "Optional source http(s) URL.",
                },
            },
            "required": ["title", "body"],
            "additionalProperties": False,
        },
        "write": True,
    },
    {
        "name": "set_reminder",
        "description": "Create a reminder in Magnm Studio (due_at accepts ISO-8601 or IST phrases like 'tomorrow 10am').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short reminder title.",
                },
                "due_at": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Future timestamp (ISO-8601 or IST phrase).",
                },
                "note": {
                    "type": "string",
                    "description": "Optional extra context.",
                },
            },
            "required": ["title", "due_at"],
            "additionalProperties": False,
        },
        "write": True,
    },
)

ALL_TOOL_SPECS = READ_ONLY_TOOL_SPECS + WRITE_TOOL_SPECS
TOOLS_BY_NAME = {spec["name"]: spec for spec in ALL_TOOL_SPECS}


class McpToolError(Exception):
    """Typed error for MCP validation, permission, confidentiality, and lookup failures."""

    def __init__(self, message, status=400, error_code="mcp_error"):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.error_code = str(error_code)


def get_writes_enabled():
    """Return True if coding agents are permitted to run write MCP tools (default False)."""
    raw = str(db.get_meta(MCP_WRITES_META_KEY, "0") or "").strip().lower()
    return raw in ("1", "true", "yes")


def set_writes_enabled(enabled):
    """Persist the MCP write-tools setting in the local engine settings store."""
    if not isinstance(enabled, bool):
        raise McpToolError("writes_enabled must be a boolean", status=400, error_code="invalid_arguments")
    db.set_meta(MCP_WRITES_META_KEY, "1" if enabled else "0")
    return enabled


def list_public_tools(writes_enabled=None):
    """Return MCP tool definitions visible for the current write-enable setting."""
    allowed = get_writes_enabled() if writes_enabled is None else bool(writes_enabled)
    specs = ALL_TOOL_SPECS if allowed else READ_ONLY_TOOL_SPECS
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        for spec in specs
    ]


def validate_tool_arguments(tool_name, arguments):
    """Validate arguments against the tool's schema; raise ValueError on invalid input."""
    spec = TOOLS_BY_NAME.get(tool_name)
    if spec is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    if arguments is None:
        args = {}
    elif isinstance(arguments, dict):
        args = dict(arguments)
    else:
        raise ValueError("arguments must be an object")

    schema = spec["inputSchema"]
    props = schema.get("properties") or {}
    required = schema.get("required") or []

    if schema.get("additionalProperties") is False:
        unknown = [k for k in args if k not in props]
        if unknown:
            raise ValueError(f"Unexpected argument(s): {', '.join(sorted(unknown))}")

    for req_key in required:
        if req_key not in args or args[req_key] is None:
            raise ValueError(f"Missing required argument: {req_key}")

    cleaned = {}
    for key, val in args.items():
        if val is None and key not in required:
            continue
        prop = props.get(key) or {}
        ptype = prop.get("type")
        if ptype == "string":
            if not isinstance(val, str):
                raise ValueError(f"Argument '{key}' must be a string")
            if prop.get("minLength", 0) >= 1 and not val.strip():
                raise ValueError(f"Argument '{key}' must be a non-empty string")
            if "enum" in prop:
                norm = val.strip().lower()
                if norm not in prop["enum"]:
                    raise ValueError(
                        f"Argument '{key}' must be one of: {', '.join(prop['enum'])}"
                    )
                cleaned[key] = norm
            else:
                cleaned[key] = val
        elif ptype == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"Argument '{key}' must be an integer")
            if "minimum" in prop and val < prop["minimum"]:
                raise ValueError(f"Argument '{key}' must be >= {prop['minimum']}")
            if "maximum" in prop and val > prop["maximum"]:
                raise ValueError(f"Argument '{key}' must be <= {prop['maximum']}")
            cleaned[key] = val
        else:
            cleaned[key] = val

    if tool_name == "add_research_note" and cleaned.get("url"):
        raw_url = cleaned["url"].strip()
        if raw_url:
            parsed = urllib.parse.urlsplit(raw_url)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                raise ValueError("Argument 'url' must be a valid http or https URL")
            cleaned["url"] = raw_url
        else:
            cleaned["url"] = None

    return cleaned


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def _build_confidential_guard():
    """Return (conf_sids, conf_cids, conf_pids, checker, conf_repo_patterns); fail closed if rules unreadable."""
    try:
        conf_sids, conf_cids, checker = cloud_sync.confidential_scope()
        rules_path = _rules_path()
        conf_pids = product_rules.confidential_product_ids(rules_path)
    except Exception as exc:
        raise McpToolError(
            "Product rules could not be read; MCP access paused.",
            status=503,
            error_code="product_rules_unreadable",
        ) from exc

    repo_patterns = []
    try:
        for r in repos.list_repos():
            pid = (r.get("product_id") or "").strip().lower()
            is_conf = bool(r.get("confidential")) or (pid in conf_pids) or checker(r.get("name") or "")
            if not is_conf:
                continue
            for term in (r.get("name"), r.get("id"), r.get("product_id"), r.get("github")):
                txt = str(term or "").strip()
                if txt:
                    repo_patterns.append(product_rules._word_pattern(txt))
    except Exception as exc:
        raise McpToolError(
            "Connected repos could not be read; MCP access paused.",
            status=503,
            error_code="repos_unreadable",
        ) from exc

    return conf_sids, conf_cids, conf_pids, checker, repo_patterns


def _text_is_confidential(text, checker, repo_patterns):
    if not text:
        return False
    return bool(checker(text)) or product_rules.any_match(repo_patterns, text)


def _content_item_is_confidential(item, conf_sids, conf_cids, conf_pids, checker, repo_patterns):
    if not item:
        return True
    if item.get("id") in conf_cids:
        return True
    if item.get("session_id") in conf_sids:
        return True
    pid = (item.get("product_id") or "").strip().lower()
    if pid and pid in conf_pids:
        return True

    parts = [
        item.get("title") or "",
        item.get("product_id") or "",
        item.get("body") or "",
        item.get("notes") or "",
    ]
    for ver in item.get("versions") or []:
        if isinstance(ver, dict):
            parts.append(ver.get("body") or "")
            parts.append(ver.get("notes") or "")
    combined = "\n".join(str(p) for p in parts if p)
    return _text_is_confidential(combined, checker, repo_patterns)


def _summary_references_confidential(summary, conf_cids, checker, repo_patterns):
    if not isinstance(summary, dict):
        return True
    data = summary.get("data") if isinstance(summary.get("data"), dict) else {}
    highlights = summary.get("highlights") if isinstance(summary.get("highlights"), dict) else {}
    prev_week = data.get("previous_week") if isinstance(data.get("previous_week"), dict) else {}
    reminders_block = data.get("reminders") if isinstance(data.get("reminders"), dict) else {}

    entries = []
    for key in ("posts", "posts_posted", "drafts_created"):
        entries.extend(data.get(key) or [])
    entries.extend(prev_week.get("posts") or [])
    entries.extend(reminders_block.get("done") or [])
    entries.extend(reminders_block.get("missed") or [])
    for key in ("top_post", "bottom_post"):
        if highlights.get(key):
            entries.append(highlights.get(key))
    entries.extend(highlights.get("missing_results") or [])

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("content_id") or entry.get("id")
        if cid and cid in conf_cids:
            return True

    serialized = json.dumps(summary, ensure_ascii=False)
    return _text_is_confidential(serialized, checker, repo_patterns)


def _record_mcp_call(tool_name, detail=""):
    db.log_activity(MCP_ACTOR, "MCP", f"MCP: {tool_name} by coding agent", str(detail or "")[:160])
    try:
        events.publish("activity", {"actor": MCP_ACTOR, "target": "MCP", "tool": tool_name})
    except Exception:
        pass


def execute_tool(tool_name, arguments=None):
    """Validate and execute an MCP tool call with confidentiality and write-permission checks."""
    spec = TOOLS_BY_NAME.get(tool_name)
    if spec is None:
        raise McpToolError(f"Unknown tool: {tool_name}", status=400, error_code="unknown_tool")

    try:
        args = validate_tool_arguments(tool_name, arguments)
    except ValueError as exc:
        raise McpToolError(str(exc), status=400, error_code="invalid_arguments") from exc

    if spec.get("write") and not get_writes_enabled():
        raise McpToolError(
            "Write tools are disabled in Magnm Studio settings.",
            status=403,
            error_code="writes_disabled",
        )

    conf_sids, conf_cids, conf_pids, checker, repo_patterns = _build_confidential_guard()

    if tool_name == "list_drafts":
        status = args.get("status")
        limit = args.get("limit", 20)
        candidates = db.list_content_items(status=status, limit=500)
        drafts = []
        for entry in candidates:
            if entry.get("type") == "review":
                continue
            full_item = db.get_content_item(entry["id"], include_versions=True)
            if _content_item_is_confidential(
                full_item or entry, conf_sids, conf_cids, conf_pids, checker, repo_patterns
            ):
                continue
            drafts.append(entry)
            if len(drafts) >= limit:
                break
        _record_mcp_call(tool_name, f"count={len(drafts)}")
        return {"drafts": drafts, "count": len(drafts)}

    if tool_name == "read_draft":
        cid = args["content_id"].strip()
        item = db.get_content_item(cid, include_versions=True)
        if item is None or _content_item_is_confidential(
            item, conf_sids, conf_cids, conf_pids, checker, repo_patterns
        ):
            _record_mcp_call(tool_name, f"{cid} (unavailable)")
            raise McpToolError("Draft not found or unavailable.", status=404, error_code="not_found")
        _record_mcp_call(tool_name, cid)
        return {"draft": item}

    if tool_name == "list_research_notes":
        limit = args.get("limit", 20)
        all_notes = db.list_research_notes(limit=500)
        visible = []
        for note in all_notes:
            text = "\n".join(
                str(note.get(k) or "") for k in ("title", "body", "url", "source")
            )
            if _text_is_confidential(text, checker, repo_patterns):
                continue
            visible.append(note)
            if len(visible) >= limit:
                break
        _record_mcp_call(tool_name, f"count={len(visible)}")
        return {"notes": visible, "count": len(visible)}

    if tool_name == "latest_weekly_summary":
        summaries = weekly_summary.list_summaries(limit=1)
        if not summaries:
            _record_mcp_call(tool_name, "none")
            return {"summary": None, "message": "No weekly summary generated yet."}
        latest = summaries[0]
        if _summary_references_confidential(latest, conf_cids, checker, repo_patterns):
            _record_mcp_call(tool_name, "redacted")
            raise McpToolError(
                "Latest weekly summary references confidential records and cannot be returned.",
                status=403,
                error_code="confidential",
            )
        _record_mcp_call(tool_name, latest.get("id") or "")
        return {"summary": latest}

    if tool_name == "list_reminders":
        status = args.get("status", "open")
        try:
            items = reminders.list_reminders(status=status)
        except reminders.ReminderError as exc:
            raise McpToolError(exc.message, status=exc.status) from exc
        visible = []
        for rem in items:
            cid = rem.get("content_id")
            if cid and cid in conf_cids:
                continue
            text = f"{rem.get('title') or ''}\n{rem.get('note') or ''}"
            if _text_is_confidential(text, checker, repo_patterns):
                continue
            visible.append(rem)
        _record_mcp_call(tool_name, f"count={len(visible)}")
        return {"reminders": visible, "count": len(visible)}

    if tool_name == "product_rules":
        try:
            rules = product_rules.load_rules(_rules_path())
        except Exception as exc:
            raise McpToolError(
                "Product rules could not be read; MCP access paused.",
                status=503,
                error_code="product_rules_unreadable",
            ) from exc
        public_products = [
            prod
            for prod in (rules.get("products") or [])
            if not prod.get("confidential")
            and not checker(prod.get("name") or prod.get("id") or "")
        ]
        _record_mcp_call(tool_name, f"products={len(public_products)}")
        return {
            "version": rules.get("version", 1),
            "show_levels": rules.get("show_levels") or {},
            "products": public_products,
        }

    if tool_name == "list_repos":
        visible = []
        for r in repos.list_repos():
            pid = (r.get("product_id") or "").strip().lower()
            if r.get("confidential") or (pid and pid in conf_pids):
                continue
            text = "\n".join(
                str(r.get(k) or "") for k in ("name", "id", "product_id", "github", "marketing_angle")
            )
            if _text_is_confidential(text, checker, repo_patterns):
                continue
            visible.append(r)
        _record_mcp_call(tool_name, f"count={len(visible)}")
        return {"repos": visible, "count": len(visible)}

    if tool_name == "create_draft":
        ctx = SimpleNamespace(agent_id=MCP_ACTOR, session_id=None)
        res = content_tools.write_draft(
            ctx,
            type=args["type"],
            platform=args["platform"].strip(),
            title=args["title"].strip(),
            body=args["body"],
        )
        if isinstance(res, dict) and res.get("error"):
            raise McpToolError(res["error"], status=400)
        cid = res["content_id"]
        auto_rev = review.auto_review(cid)
        try:
            cloud_sync.request_sync()
        except Exception:
            pass
        _record_mcp_call(tool_name, cid)
        return {
            "content_id": cid,
            "version": res["version"],
            "status": "draft",
            "path": res["path"],
            "warnings": res.get("warnings") or [],
            "review": auto_rev,
            "note": "Draft created; review it in Magnm Studio before publishing.",
        }

    if tool_name == "add_research_note":
        note_id = f"note_{uuid.uuid4().hex[:12]}"
        saved = db.save_research_note(
            note_id=note_id,
            title=args["title"].strip(),
            body=args["body"],
            url=args.get("url") or None,
            source="mcp",
            agent_id=MCP_ACTOR,
        )
        try:
            cloud_sync.request_sync()
        except Exception:
            pass
        _record_mcp_call(tool_name, saved["id"])
        return {"note": saved}

    if tool_name == "set_reminder":
        try:
            rem = reminders.create_reminder(
                title=args["title"].strip(),
                due_at=args["due_at"].strip(),
                note=(args.get("note") or "").strip(),
                kind="custom",
                created_by=MCP_ACTOR,
            )
        except reminders.ReminderError as exc:
            raise McpToolError(exc.message, status=exc.status) from exc
        try:
            cloud_sync.request_sync()
        except Exception:
            pass
        _record_mcp_call(tool_name, rem["id"])
        return {"reminder": rem}

    raise McpToolError(f"Unknown tool: {tool_name}", status=400, error_code="unknown_tool")


def build_cli_snippets(clis=None):
    """Build exact MCP setup commands/snippets for each installed CLI using real paths."""
    if clis is None:
        clis = coding_agents.list_detected_agents(refresh=False)

    mcp_path = os.path.abspath(os.path.join(_ENGINE_DIR, "mcp_server.py"))
    data_dir = os.path.abspath(paths.DATA_ROOT)
    json_snippet = json.dumps(
        {
            "mcpServers": {
                "magnm": {
                    "command": "python3",
                    "args": [mcp_path, "--data-dir", data_dir],
                }
            }
        },
        indent=2,
    )
    toml_snippet = (
        "[mcp_servers.magnm]\n"
        'command = "python3"\n'
        f"args = {json.dumps([mcp_path, '--data-dir', data_dir])}"
    )

    snippets = []
    for cli in clis or []:
        if not cli.get("installed"):
            continue
        cid = cli.get("id")
        name = cli.get("name") or cid
        if cid == "claude":
            cmd = (
                f"claude mcp add magnm -- python3 "
                f"{shlex.quote(mcp_path)} --data-dir {shlex.quote(data_dir)}"
            )
            snippets.append({
                "id": cid,
                "name": name,
                "format": "cli",
                "config_hint": "Run in your terminal",
                "snippet": cmd,
            })
        elif cid == "gemini":
            snippets.append({
                "id": cid,
                "name": name,
                "format": "json",
                "config_hint": "Add to ~/.gemini/settings.json",
                "snippet": json_snippet,
            })
        elif cid == "codex":
            snippets.append({
                "id": cid,
                "name": name,
                "format": "toml",
                "config_hint": "Add to ~/.codex/config.toml",
                "snippet": toml_snippet,
            })
        elif cid == "agy":
            snippets.append({
                "id": cid,
                "name": name,
                "format": "json",
                "config_hint": "Add to ~/.gemini/antigravity/mcp_config.json",
                "snippet": json_snippet,
            })
    return snippets


def get_mcp_settings(clis=None):
    """Return MCP settings, today's call count, real paths, and per-installed-CLI snippets."""
    mcp_path = os.path.abspath(os.path.join(_ENGINE_DIR, "mcp_server.py"))
    data_dir = os.path.abspath(paths.DATA_ROOT)
    return {
        "writes_enabled": get_writes_enabled(),
        "calls_today": db.count_mcp_calls_today(),
        "mcp_server_path": mcp_path,
        "data_dir": data_dir,
        "clients": build_cli_snippets(clis=clis),
    }
