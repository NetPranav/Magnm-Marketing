#!/usr/bin/env python3
"""Coding agent tool: creates an awaiting_approval coding run and returns at once."""

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import coding_agents
from tools import tool


@tool(
    name="run_coding_agent",
    description=(
        "Request a confined coding agent run (read-only repo copies + out/ folder). "
        "Creates the run in 'awaiting_approval' status and returns immediately; "
        "nothing executes until the user presses Start in Team > Coding runs."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Clear coding, repository analysis, or file generation prompt.",
            },
            "repo_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional connected repository IDs to copy read-only into workspace/repos/.",
            },
            "mode": {
                "type": "string",
                "enum": ["read_only", "write_out"],
                "description": "Permission mode: 'read_only' (default) or 'write_out'.",
            },
        },
        "required": ["prompt"],
    },
    side_effects=True,
)
def run_coding_agent(ctx, prompt, repo_ids=None, mode="read_only"):
    agent_id = ctx.agent_id if ctx else None
    engine_id = coding_agents.get_agent_engine(agent_id)
    if engine_id != coding_agents.CONFINED_ENGINE_ID:
        return {
            "error": (
                f"Agent '{agent_id or 'unknown'}' uses the AI gateway engine. "
                "Select a confined coding CLI in Settings > Coding agents to request coding runs."
            )
        }

    if ctx and callable(getattr(ctx, "is_cancelled", None)) and ctx.is_cancelled():
        return {"error": "session cancelled"}

    selected_repos = list(repo_ids or [])
    try:
        pending = coding_agents.request_run(
            engine=engine_id,
            prompt=prompt,
            repo_ids=selected_repos,
            mode=mode or "read_only",
            requested_by=agent_id,
            session_id=ctx.session_id if ctx else None,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    if pending.get("confidential") and ctx and callable(getattr(ctx, "mark_confidential", None)):
        ctx.mark_confidential()

    return {
        "run_id": pending["id"],
        "engine": pending["engine"],
        "mode": pending["mode"],
        "repo_ids": pending.get("repo_ids") or [],
        "status": pending["status"],
        "confidential": bool(pending.get("confidential")),
        "approval_hash": pending.get("approval_hash"),
        "message": (
            "Coding run created with status 'awaiting_approval'. "
            "Ask the user to review and press Start in Team > Coding runs."
        ),
    }
