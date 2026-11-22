#!/usr/bin/env python3
"""Stdlib-only Model Context Protocol (MCP) stdio server for Magnm Studio."""

import argparse
import json
import os
import re
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "magnm", "version": "0.1.0"}
MAX_OUTPUT_CHARS = 20000
TRUNCATE_SUFFIX = "\n... [truncated]"
NOT_RUNNING_MESSAGE = "Magnm Studio is not running. Start it, then try again."

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_CONTENT_TYPES = ("post", "thread", "blog", "script", "card", "image", "plan")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

READ_ONLY_TOOLS = (
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
    },
    {
        "name": "latest_weekly_summary",
        "description": "Return the most recent weekly performance summary.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
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
    },
    {
        "name": "product_rules",
        "description": "Return public product rules and show levels so drafted copy follows Studio rules.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_repos",
        "description": "List non-confidential repositories connected to Magnm Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
)

WRITE_TOOLS = (
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
                    "enum": list(_VALID_CONTENT_TYPES),
                    "description": "Content type: post, thread, blog, script, card, image, or plan.",
                },
            },
            "required": ["title", "body", "platform", "type"],
            "additionalProperties": False,
        },
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
    },
)

ALL_TOOLS = READ_ONLY_TOOLS + WRITE_TOOLS
TOOLS_BY_NAME = {tool["name"]: tool for tool in ALL_TOOLS}
WRITE_TOOL_NAMES = frozenset(tool["name"] for tool in WRITE_TOOLS)


class _EngineNotRunning(Exception):
    """Raised when engine_endpoint.json is missing/invalid or the engine is unreachable."""


class _HttpStatusError(Exception):
    """Raised when the engine HTTP API returns a 4xx/5xx JSON error."""

    def __init__(self, status, message, error_code=None):
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)
        self.error_code = error_code


def _log(msg):
    try:
        sys.stderr.write(f"[magnm-mcp] {msg}\n")
        sys.stderr.flush()
    except OSError:
        pass


def _resolve_data_root(data_dir=None):
    if data_dir:
        os.environ["MAGNM_HOME"] = os.path.abspath(os.path.expanduser(data_dir))
    import paths
    return paths.DATA_ROOT


def _read_endpoint(data_root):
    endpoint_file = os.path.join(data_root, "state", "engine_endpoint.json")
    if not os.path.isfile(endpoint_file):
        raise _EngineNotRunning()
    try:
        with open(endpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise _EngineNotRunning() from exc
    if not isinstance(data, dict):
        raise _EngineNotRunning()

    raw_url = str(data.get("url") or "").strip().rstrip("/")
    if not raw_url:
        raise _EngineNotRunning()
    parsed = urllib.parse.urlsplit(raw_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http" or host not in _LOOPBACK_HOSTS or not parsed.port:
        raise _EngineNotRunning()

    token = str(data.get("token") or "")
    return raw_url, token


def _engine_request(data_root, method, path, payload=None, timeout=15.0):
    base_url, token = _read_endpoint(data_root)
    url = base_url + path
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Magnm-Token"] = token
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        err_msg = f"Engine HTTP {exc.code}"
        err_code = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                err_msg = str(parsed.get("error") or err_msg)
                err_code = parsed.get("error_code")
        except Exception:
            pass
        raise _HttpStatusError(exc.code, err_msg, error_code=err_code) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise _EngineNotRunning() from exc


def _validate_arguments(tool_name, arguments):
    spec = TOOLS_BY_NAME.get(tool_name)
    if spec is None:
        return f"Unknown tool: {tool_name}"
    if arguments is None:
        args = {}
    elif isinstance(arguments, dict):
        args = arguments
    else:
        return "arguments must be an object"

    schema = spec["inputSchema"]
    props = schema.get("properties") or {}
    required = schema.get("required") or []

    if schema.get("additionalProperties") is False:
        unknown = [k for k in args if k not in props]
        if unknown:
            return f"Unexpected argument(s): {', '.join(sorted(unknown))}"

    for req_key in required:
        if req_key not in args or args[req_key] is None:
            return f"Missing required argument: {req_key}"

    for key, val in args.items():
        if val is None and key not in required:
            continue
        prop = props.get(key) or {}
        ptype = prop.get("type")
        if ptype == "string":
            if not isinstance(val, str):
                return f"Argument '{key}' must be a string"
            if prop.get("minLength", 0) >= 1 and not val.strip():
                return f"Argument '{key}' must be a non-empty string"
            if "enum" in prop and val.strip().lower() not in prop["enum"]:
                return f"Argument '{key}' must be one of: {', '.join(prop['enum'])}"
        elif ptype == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                return f"Argument '{key}' must be an integer"
            if "minimum" in prop and val < prop["minimum"]:
                return f"Argument '{key}' must be >= {prop['minimum']}"
            if "maximum" in prop and val > prop["maximum"]:
                return f"Argument '{key}' must be <= {prop['maximum']}"

    if tool_name == "add_research_note" and args.get("url"):
        raw_url = str(args["url"]).strip()
        if raw_url:
            parsed = urllib.parse.urlsplit(raw_url)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                return "Argument 'url' must be a valid http or https URL"

    return None


def _negotiate_protocol_version(client_version):
    if isinstance(client_version, str):
        ver = client_version.strip()
        if _ISO_DATE_RE.match(ver) and ver < PROTOCOL_VERSION:
            return ver
    return PROTOCOL_VERSION


def _format_tool_text(payload):
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(text) > MAX_OUTPUT_CHARS:
        keep = max(0, MAX_OUTPUT_CHARS - len(TRUNCATE_SUFFIX))
        text = text[:keep] + TRUNCATE_SUFFIX
    return text


def _jsonrpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg, data_root):
    """Handle one parsed JSON-RPC 2.0 request/notification; return response dict or None."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        req_id = msg.get("id") if isinstance(msg, dict) else None
        return _jsonrpc_error(req_id, -32600, "Invalid Request")

    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return _jsonrpc_error(msg.get("id"), -32600, "Invalid Request")

    has_id = "id" in msg
    req_id = msg.get("id")
    params = msg.get("params")
    if params is None:
        params = {}

    if method == "notifications/initialized":
        if has_id:
            return _jsonrpc_result(req_id, {})
        return None

    # Ignore other notifications (messages without an id)
    if not has_id:
        return None

    if method == "initialize":
        if not isinstance(params, dict):
            return _jsonrpc_error(req_id, -32602, "Invalid params")
        negotiated = _negotiate_protocol_version(params.get("protocolVersion"))
        return _jsonrpc_result(
            req_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(SERVER_INFO),
            },
        )

    if method == "ping":
        return _jsonrpc_result(req_id, {})

    if method == "tools/list":
        writes_enabled = False
        try:
            settings = _engine_request(data_root, "GET", "/api/coding/mcp")
            writes_enabled = bool(settings.get("writes_enabled"))
        except (_EngineNotRunning, _HttpStatusError):
            writes_enabled = False
        tools_list = list(ALL_TOOLS) if writes_enabled else list(READ_ONLY_TOOLS)
        return _jsonrpc_result(req_id, {"tools": tools_list})

    if method == "tools/call":
        if not isinstance(params, dict):
            return _jsonrpc_error(req_id, -32602, "Invalid params")
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return _jsonrpc_error(req_id, -32602, "Missing tool name")
        tool_name = tool_name.strip()
        if tool_name not in TOOLS_BY_NAME:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")

        arguments = params.get("arguments")
        err = _validate_arguments(tool_name, arguments)
        if err:
            return _jsonrpc_error(req_id, -32602, err)

        try:
            if tool_name in WRITE_TOOL_NAMES:
                settings = _engine_request(data_root, "GET", "/api/coding/mcp")
                if not settings.get("writes_enabled"):
                    return _jsonrpc_error(
                        req_id,
                        -32602,
                        "Write tools are disabled in Magnm Studio settings.",
                    )
            resp = _engine_request(
                data_root,
                "POST",
                "/api/coding/mcp/call",
                {"name": tool_name, "arguments": arguments or {}},
            )
            result_obj = resp.get("result") if isinstance(resp, dict) and "result" in resp else resp
            return _jsonrpc_result(
                req_id,
                {"content": [{"type": "text", "text": _format_tool_text(result_obj)}]},
            )
        except _EngineNotRunning:
            return _jsonrpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": NOT_RUNNING_MESSAGE}],
                    "isError": True,
                },
            )
        except _HttpStatusError as exc:
            if exc.error_code in ("writes_disabled", "invalid_arguments", "unknown_tool"):
                return _jsonrpc_error(req_id, -32602, exc.message)
            return _jsonrpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": exc.message}],
                    "isError": True,
                },
            )

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Magnm Studio MCP stdio server")
    parser.add_argument("--data-dir", default=None, help="Override MAGNM_HOME user data directory")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    data_root = _resolve_data_root(args.data_dir)

    stop = False

    def _on_signal(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while not stop:
        try:
            line = sys.stdin.readline()
        except OSError as exc:
            _log(f"stdin error: {exc}")
            break
        if not line:
            break
        stripped = line.strip()
        if not stripped:
            continue
        try:
            msg = json.loads(stripped)
        except Exception:
            resp = _jsonrpc_error(None, -32700, "Parse error")
        else:
            try:
                resp = handle_message(msg, data_root)
            except Exception as exc:
                _log(f"internal error: {exc}")
                req_id = msg.get("id") if isinstance(msg, dict) else None
                resp = _jsonrpc_error(req_id, -32603, "Internal error") if isinstance(msg, dict) and "id" in msg else None

        if resp is not None:
            try:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                break


if __name__ == "__main__":
    main()
