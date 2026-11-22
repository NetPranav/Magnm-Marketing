#!/usr/bin/env python3
"""CMO delegation tool for synchronous sub-agent sessions."""

from datetime import datetime, timezone
import os
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.dirname(_TOOLS_DIR)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_runtime
import agents
import confidential_guard
import db
from tools import tool

MAX_DELEGATION_DEPTH = 2


@tool(
    name="delegate",
    description="CMO-only: run a specialist sub-agent synchronously with a specific instruction and return its result and created content_ids.",
    parameters_schema={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "Target agent id (researcher, writer, designer, video-producer, reviewer, analyst).",
            },
            "instruction": {
                "type": "string",
                "description": "Clear, complete task instruction for the specialist (include platform, audience, and content_ids if any).",
            },
        },
        "required": ["agent_id", "instruction"],
    },
    side_effects=True,
)
def delegate(ctx, agent_id, instruction):
    if not ctx or ctx.agent_id != "cmo":
        return {"error": "only cmo can delegate"}

    next_depth = int(ctx.depth or 0) + 1
    if next_depth > MAX_DELEGATION_DEPTH:
        return {"error": f"max delegation depth ({MAX_DELEGATION_DEPTH}) exceeded"}

    target = agents.get_agent(agent_id)
    if target is None:
        return {"error": f"Unknown agent '{agent_id}'"}

    canon_id = target["id"]
    if canon_id == "cmo":
        return {"error": "cmo cannot delegate to itself"}

    system_prompt = agents.build_system_prompt(canon_id, confidential=bool(ctx.confidential))
    sub_sess = agent_runtime.start_session(
        canon_id,
        instruction,
        system_prompt=system_prompt,
        allowed_tools=agents.effective_tools(canon_id),
        parent_session_id=ctx.session_id,
        depth=next_depth,
        confidential=bool(ctx.confidential),
        title=str(instruction or "")[:100],
    )

    content_ids = db.list_content_ids_for_session(sub_sess["id"])
    status = sub_sess["status"]

    # What a confidential child session says is confidential: the parent switches to local AI before it sees it,
    # or gets nothing if it can't switch.
    child = db.get_agent_session(sub_sess["id"]) or sub_sess
    if child.get("confidential"):
        refusal = confidential_guard.local_or_refusal(ctx, True)
        if refusal:
            return {
                "agent_id": canon_id,
                "status": "confidential",
                "error": refusal["error"],
                "content_ids": content_ids,
                "session_id": sub_sess["id"],
            }

    if status == "waiting_user":
        pending = sub_sess.get("pending_question") or {}
        return {
            "agent_id": canon_id,
            "status": "waiting_user",
            "question": pending.get("question") or "",
            "options": pending.get("options") or [],
            "content_ids": content_ids,
            "session_id": sub_sess["id"],
        }

    if status == "done":
        result_text = sub_sess.get("result_text") or ""
        import team_room
        team_room.save_message(
            {
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "sender": canon_id,
                "sender_name": target.get("name", canon_id),
                "sender_code": target.get("code", canon_id.upper()[:4]),
                "text": result_text,
                "mentions": team_room.extract_mentions(result_text),
                "session_id": sub_sess["id"],
                "content_ids": content_ids,
                "model_used": sub_sess.get("model_used"),
                "provider": sub_sess.get("provider"),
                "fallback_used": bool(sub_sess.get("fallback_used")),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {
            "agent_id": canon_id,
            "status": "done",
            "result_text": result_text,
            "content_ids": content_ids,
            "session_id": sub_sess["id"],
        }

    return {
        "agent_id": canon_id,
        "status": status,
        "error": sub_sess.get("error") or "sub-session did not complete",
        "result_text": sub_sess.get("result_text") or "",
        "content_ids": content_ids,
        "session_id": sub_sess["id"],
    }
