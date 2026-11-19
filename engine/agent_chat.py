#!/usr/bin/env python3
"""Magnm Agent Chat & Roster Service.

Manages the 7-agent roster, live session status, and 1-on-1 chats backed by
agent_runtime, tool allow-lists, and system prompt assembly.
"""

from datetime import datetime, timezone
import json
import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_runtime
import agents
import ai_gateway
import assets_manager
import db
import paths
import product_rules

ROOT = paths.DATA_ROOT
CHATS_DIR = paths.data("state", "chats")
os.makedirs(CHATS_DIR, exist_ok=True)


def _canonical_id(agent_id):
    info = agents.get_agent(agent_id)
    return info["id"] if info else agent_id


def get_roster_status():
    """Return the 7-agent team roster with live session status and model mapping."""
    roster_configs = agents.load_agents()
    model_matrix = {m["role_id"]: m for m in ai_gateway.get_role_model_matrix()}
    chat_counts = db.get_agent_chat_counts()
    live_statuses = db.get_agent_live_statuses()

    roster = []
    for member in roster_configs:
        aid = member["id"]
        m_info = model_matrix.get(aid, {})
        roster.append({
            **member,
            "code": member.get("code") or aid.upper()[:3],
            "description": member.get("role", ""),
            "status": live_statuses.get(aid, "idle"),
            "latest_output": "None yet",
            "message_count": chat_counts.get(aid, 0),
            "model_info": m_info,
        })
    return roster


def get_agent_chat_history(agent_id):
    """Return chat history turns for agent_id from SQLite."""
    return db.get_agent_chat_history(_canonical_id(agent_id))


def save_agent_chat_history(agent_id, history):
    """Replace chat history turns for agent_id in SQLite."""
    db.save_agent_chat_history(_canonical_id(agent_id), history)


def _prepare_attachments(attached_assets):
    """Split attached_assets into text context lines and image/file attachments."""
    if not attached_assets:
        return "", []

    all_assets = assets_manager.get_assets()
    ctx_parts = ["[ATTACHED ASSETS]:"]
    image_paths = []

    for item in attached_assets:
        if isinstance(item, str):
            a = next((x for x in all_assets if x["id"] == item), None)
            if a:
                ctx_parts.append(
                    f"- {a['name']} ({a['mime_type']}, {a['size']}): {a.get('description', '')}"
                )
                full_p = paths.data(a["relative_path"])
                if a.get("file_group") == "image" and os.path.exists(full_p):
                    image_paths.append(full_p)
                    ctx_parts.append(f"  Path: {a['relative_path']}")
                elif a.get("file_group") == "document" and os.path.exists(full_p):
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as df:
                            ctx_parts.append(f"  Excerpt: {df.read(600)}")
                    except Exception:
                        pass
            elif os.path.isfile(item):
                image_paths.append(item)
                ctx_parts.append(f"- File: {item}")
        elif isinstance(item, dict):
            image_paths.append(item)

    text_ctx = "\n".join(ctx_parts) if len(ctx_parts) > 1 else ""
    return text_ctx, image_paths


def _infer_error_code(session):
    if session.get("error_code"):
        return session["error_code"]
    err = (session.get("error") or "").lower()
    if any(tok in err for tok in ("401", "403", "rejected", "auth")):
        return "ai_auth_error"
    if any(tok in err for tok in ("not connected", "couldn't answer", "api key", "ollama", "rate-limited")):
        return "ai_not_connected"
    return "agent_failed"


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def chat_with_agent(agent_id, user_message, attached_assets=None):
    """Run or resume a 1-on-1 chat session with a configured agent (resolving legacy IDs)."""
    if attached_assets is None:
        attached_assets = []

    agent_info = agents.get_agent(agent_id)
    if not agent_info:
        return {"success": False, "error": f"Agent '{agent_id}' not found."}

    canon_id = agent_info["id"]
    history = get_agent_chat_history(canon_id)
    ts = datetime.now(timezone.utc).isoformat()

    waiting_sess = db.get_latest_waiting_session(canon_id, top_level_only=True)
    if waiting_sess is not None:
        session = agent_runtime.resume_session(waiting_sess["id"], user_message)
    else:
        asset_ctx, attachment_paths = _prepare_attachments(attached_assets)
        recent_turns = history[-4:]
        prompt_parts = []
        if recent_turns:
            recent_ctx = "\n".join(
                f"{t.get('sender', 'user')}: {(t.get('text') or '')[:300]}"
                for t in recent_turns
            )
            prompt_parts.append(f"Recent Chat Context:\n{recent_ctx}")
        if asset_ctx:
            prompt_parts.append(asset_ctx)
        prompt_parts.append(str(user_message))
        full_user_msg = "\n\n".join(prompt_parts)

        confidential = product_rules.mentions_confidential(user_message, path=_rules_path())
        system_prompt = agents.build_system_prompt(canon_id)
        session = agent_runtime.start_session(
            canon_id,
            full_user_msg,
            system_prompt=system_prompt,
            allowed_tools=agent_info.get("tools") or [],
            attachments=attachment_paths or None,
            confidential=confidential,
            title=str(user_message or "")[:100],
        )

    status = session.get("status")
    if status == "failed":
        return {
            "success": False,
            "error_code": _infer_error_code(session),
            "error": session.get("error") or "Agent query failed",
            "agent": agent_info,
        }

    if status == "waiting_user":
        pending = session.get("pending_question") or {}
        question = pending.get("question") or ""
        options = pending.get("options") or []
        history.append({
            "sender": "user",
            "text": user_message,
            "attached_assets": attached_assets,
            "timestamp": ts,
        })
        history.append({
            "sender": canon_id,
            "name": agent_info["name"],
            "text": question,
            "waiting_user": True,
            "session_id": session["id"],
            "model_used": session.get("model_used"),
            "provider": session.get("provider"),
            "fallback_used": bool(session.get("fallback_used")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        save_agent_chat_history(canon_id, history)
        return {
            "success": True,
            "waiting_user": True,
            "session_id": session["id"],
            "question": question,
            "options": options,
            "agent": agent_info,
            "response": question,
            "model_used": session.get("model_used"),
            "provider": session.get("provider"),
            "fallback_used": bool(session.get("fallback_used")),
            "history": history,
        }

    reply_text = session.get("result_text") or ""
    history.append({
        "sender": "user",
        "text": user_message,
        "attached_assets": attached_assets,
        "timestamp": ts,
    })
    history.append({
        "sender": canon_id,
        "name": agent_info["name"],
        "text": reply_text,
        "session_id": session["id"],
        "model_used": session.get("model_used"),
        "provider": session.get("provider"),
        "fallback_used": bool(session.get("fallback_used")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    save_agent_chat_history(canon_id, history)

    return {
        "success": True,
        "waiting_user": False,
        "session_id": session["id"],
        "agent": agent_info,
        "response": reply_text,
        "model_used": session.get("model_used"),
        "provider": session.get("provider"),
        "fallback_used": bool(session.get("fallback_used")),
        "history": history,
    }


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print(json.dumps(chat_with_agent(sys.argv[1], sys.argv[2]), indent=2))
    else:
        print(json.dumps(get_roster_status(), indent=2))
