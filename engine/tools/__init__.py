#!/usr/bin/env python3
"""Tool registry, schema builder, validation, and execution logging."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import json
import os
import sys
import time
from typing import Any, Callable

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import events

MAX_RESULT_CHARS = 12000
MAX_ARG_VALUE_CHARS = 2000
_TRUNCATION_NOTE = "Output truncated to 12,000 characters."


@dataclass
class ToolContext:
    """Execution context passed to every tool invocation."""

    session_id: str
    agent_id: str
    user_id: str = "local"
    depth: int = 0
    confidential: bool = False
    publish: Callable[[str, Any], None] = field(default=events.publish)
    mark_confidential: Callable[[], None] = field(default=lambda: None)
    is_cancelled: Callable[[], bool] = field(default=lambda: False)
    attachments: list[str] = field(default_factory=list)


@dataclass
class ToolSpec:
    """Registered tool metadata and callable."""

    name: str
    description: str
    parameters_schema: dict
    side_effects: bool
    fn: Callable[..., dict]


_REGISTRY: dict[str, ToolSpec] = {}


def tool(name, description, parameters_schema, side_effects=False):
    """Decorator to register a tool function fn(ctx, **args) -> JSON-serializable dict."""
    def decorator(fn):
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters_schema=parameters_schema or {"type": "object", "properties": {}},
            side_effects=bool(side_effects),
            fn=fn,
        )
        return fn

    return decorator


def get_tool(name):
    """Return the registered ToolSpec for name, or None."""
    return _REGISTRY.get(name)


def list_registered_tools():
    """Return registered tool names in registration order."""
    return list(_REGISTRY.keys())


def schemas_for(names):
    """Return the OpenAI-compatible tools schema list for the requested tool names."""
    out = []
    seen = set()
    for name in names or []:
        if name in seen:
            continue
        spec = _REGISTRY.get(name)
        if spec is None:
            continue
        seen.add(name)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters_schema,
                },
            }
        )
    return out


def _matches_json_type(value, expected_type):
    """Check basic JSON Schema types: string, integer, number, boolean, array, object."""
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, t) for t in expected_type)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "null":
        return value is None
    return True


def _validate_args(schema, args):
    """Validate args dict against parameters_schema; return error message or None."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "invalid arguments: expected object"

    properties = schema.get("properties") or {}
    if "_raw" in args and "_raw" not in properties:
        return "invalid arguments: JSON parse failed"

    required = schema.get("required") or []
    for req in required:
        if req not in args or args[req] is None:
            return f"missing required parameter: {req}"

    for key, val in args.items():
        if key not in properties:
            continue
        prop_spec = properties[key]
        if not isinstance(prop_spec, dict):
            continue
        expected_type = prop_spec.get("type")
        if expected_type and not _matches_json_type(val, expected_type):
            return f"invalid type for parameter '{key}': expected {expected_type}"
    return None


def _truncate_arg_values(val):
    """Recursively truncate string values over MAX_ARG_VALUE_CHARS for DB logging."""
    if isinstance(val, str):
        if len(val) > MAX_ARG_VALUE_CHARS:
            return val[:MAX_ARG_VALUE_CHARS] + "...[truncated]"
        return val
    if isinstance(val, list):
        return [_truncate_arg_values(item) for item in val]
    if isinstance(val, dict):
        return {k: _truncate_arg_values(v) for k, v in val.items()}
    return val


def _serialize_args_for_log(args):
    try:
        cleaned = _truncate_arg_values(args if args is not None else {})
        return json.dumps(cleaned, ensure_ascii=False)
    except Exception:
        return json.dumps({"_unserializable": str(args)[:MAX_ARG_VALUE_CHARS]})


def _truncate_result_if_needed(result):
    """Ensure JSON-serialized tool result does not exceed MAX_RESULT_CHARS, adding a note if truncated."""
    if not isinstance(result, dict):
        result = {"result": result}

    try:
        serialized = json.dumps(result, ensure_ascii=False)
    except TypeError:
        result = {"result": str(result)}
        serialized = json.dumps(result, ensure_ascii=False)

    if len(serialized) <= MAX_RESULT_CHARS:
        return result

    trimmed = dict(result)
    trimmed["truncated"] = True
    trimmed["note"] = _TRUNCATION_NOTE

    # Shrink whichever top-level string or list field is largest in serialized size first.
    while len(json.dumps(trimmed, ensure_ascii=False)) > MAX_RESULT_CHARS:
        cur_total = len(json.dumps(trimmed, ensure_ascii=False))
        excess = cur_total - MAX_RESULT_CHARS
        str_keys = [
            k for k, v in trimmed.items()
            if k not in ("note",) and isinstance(v, str) and len(v) > 80
        ]
        list_keys = [
            k for k, v in trimmed.items()
            if isinstance(v, list) and len(v) > 0
        ]

        biggest_sk = max(str_keys, key=lambda k: len(trimmed[k])) if str_keys else None
        biggest_s_len = len(trimmed[biggest_sk]) if biggest_sk else 0

        biggest_lk = None
        biggest_l_len = 0
        if list_keys:
            biggest_lk = max(list_keys, key=lambda k: len(json.dumps(trimmed[k], ensure_ascii=False)))
            biggest_l_len = len(json.dumps(trimmed[biggest_lk], ensure_ascii=False))

        if biggest_lk and biggest_l_len >= biggest_s_len:
            trimmed[biggest_lk] = trimmed[biggest_lk][:-1]
            continue
        if biggest_sk:
            cur = trimmed[biggest_sk]
            keep = max(0, len(cur) - max(excess + 32, 64))
            trimmed[biggest_sk] = cur[:keep] + "...[truncated]"
            continue

        break

    if len(json.dumps(trimmed, ensure_ascii=False)) <= MAX_RESULT_CHARS:
        return trimmed

    # Hard fallback if nested non-string objects still exceed budget.
    budget = MAX_RESULT_CHARS - 160
    preview = serialized[:max(0, budget)]
    fallback = {
        "truncated": True,
        "note": _TRUNCATION_NOTE,
        "preview": preview + "...[truncated]",
    }
    while len(json.dumps(fallback, ensure_ascii=False)) > MAX_RESULT_CHARS and len(preview) > 0:
        preview = preview[: max(0, len(preview) - 200)]
        fallback["preview"] = preview + "...[truncated]"
    return fallback


def _record_tool_call(ctx, name, args, ok, error, duration_ms):
    try:
        db.log_tool_call(
            session_id=ctx.session_id if ctx else None,
            agent_id=ctx.agent_id if ctx else None,
            tool=name,
            args_json=_serialize_args_for_log(args),
            ok=ok,
            error=error,
            duration_ms=duration_ms,
            ts=datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        pass


def run_tool(name, ctx, args, allowed_names):
    """Validate, execute, truncate, and log a single tool invocation."""
    t0 = time.monotonic()
    allowed_set = set(allowed_names or [])
    spec = _REGISTRY.get(name)

    if spec is None or name not in allowed_set:
        err = "tool not allowed for this agent"
        _record_tool_call(ctx, name, args, ok=False, error=err, duration_ms=0)
        return {"error": err}

    normalized_args = {} if args is None else args
    validation_err = _validate_args(spec.parameters_schema, normalized_args)
    if validation_err:
        _record_tool_call(ctx, name, normalized_args, ok=False, error=validation_err, duration_ms=0)
        return {"error": validation_err}

    properties = spec.parameters_schema.get("properties") or {}
    try:
        sig = inspect.signature(spec.fn)
        accepts_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except (TypeError, ValueError):
        accepts_kw = False

    if accepts_kw or not properties:
        call_kwargs = dict(normalized_args)
    else:
        call_kwargs = {k: v for k, v in normalized_args.items() if k in properties}

    try:
        raw_result = spec.fn(ctx, **call_kwargs)
        result = _truncate_result_if_needed(raw_result)
        duration_ms = int((time.monotonic() - t0) * 1000)
        err_val = result.get("error") if isinstance(result, dict) else None
        _record_tool_call(
            ctx,
            name,
            normalized_args,
            ok=(err_val is None),
            error=str(err_val)[:300] if err_val is not None else None,
            duration_ms=duration_ms,
        )
        return result
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        short_err = str(exc).strip() or exc.__class__.__name__
        short_err = short_err[:300]
        _record_tool_call(ctx, name, normalized_args, ok=False, error=short_err, duration_ms=duration_ms)
        return {"error": short_err}


from . import core  # noqa: E402, F401
from . import content  # noqa: E402, F401
from . import design  # noqa: E402, F401
from . import delegation  # noqa: E402, F401
from . import research  # noqa: E402, F401
