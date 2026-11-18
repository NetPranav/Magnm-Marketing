#!/usr/bin/env python3
"""Magnm Global Team Room & Swarm Messaging Service.

Routes @mentions (and unmentioned CEO messages to @cmo) through agent_runtime
sessions with per-agent prompts and tool allow-lists.
"""

from datetime import datetime, timezone
import json
import os
import re
import sys
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agent_runtime
import agents
import assets_manager
import db
import events
import paths
import product_rules

ROOT = paths.DATA_ROOT
MESSAGES_FILE = paths.data("state", "global_messages.json")
MAX_MENTIONS_PER_MESSAGE = 3


def load_messages(limit=80):
    """Return the most recent team room messages from SQLite."""
    return db.load_messages(limit=limit)


def save_message(msg):
    """Persist a team room message to SQLite and publish a live event."""
    db.save_message(msg)
    events.publish("message", msg)


def extract_mentions(text):
    """Find unique @agent_id mentions in text in order of appearance."""
    matches = re.findall(r"@([a-zA-Z0-9_-]+)", text or "")
    seen = []
    for m in matches:
        ml = m.lower()
        if ml not in seen:
            seen.append(ml)
    return seen


def _prepare_attachments(asset_ids):
    """Build text context and image file paths from attached asset IDs or paths."""
    if not asset_ids:
        return "", []
    all_assets = assets_manager.get_assets()
    ctx_parts = ["[ATTACHED ASSETS]:"]
    image_paths = []
    for item in asset_ids:
        if isinstance(item, str):
            a = next((x for x in all_assets if x["id"] == item), None)
            if a:
                ctx_parts.append(
                    f"- Asset ID: {a['id']} ({a['name']}) | Type: {a['mime_type']} | Size: {a['size']}"
                )
                ctx_parts.append(f"  Description: {a.get('description', 'None')}")
                full_p = paths.data(a["relative_path"])
                if a.get("file_group") == "image" and os.path.exists(full_p):
                    image_paths.append(full_p)
                    ctx_parts.append(f"  Path: {a['relative_path']}")
                elif a.get("file_group") == "document" and os.path.exists(full_p):
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as tf:
                            ctx_parts.append(f"  Preview: {tf.read(500)}...")
                    except Exception:
                        pass
            elif os.path.isfile(item):
                image_paths.append(item)
                ctx_parts.append(f"- File: {item}")
        elif isinstance(item, dict):
            image_paths.append(item)

    text_ctx = "\n".join(ctx_parts) if len(ctx_parts) > 1 else ""
    return text_ctx, image_paths


def _post_system_error(error_text):
    """Persist and return a single system message when AI inference cannot proceed."""
    sys_msg = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "sender": "system",
        "sender_name": "System",
        "sender_code": "SYS",
        "text": str(error_text),
        "mentions": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_message(sys_msg)
    return sys_msg


def _rules_path():
    cand = paths.data("config", "product_rules.json")
    if os.path.exists(cand):
        return cand
    fb = paths.resource("config", "product_rules.json")
    return fb if os.path.exists(fb) else cand


def _resolve_targets(sender, mentions):
    """Resolve up to MAX_MENTIONS_PER_MESSAGE unique target agents; default CEO unmentioned to CMO."""
    sender_agent = agents.get_agent(sender)
    sender_canon = sender_agent["id"] if sender_agent else (sender or "").strip().lower()

    targets = []
    seen_ids = set()
    for raw_mention in mentions:
        agent_info = agents.get_agent(raw_mention)
        if agent_info is None:
            continue
        cid = agent_info["id"]
        if cid == sender_canon or cid in seen_ids:
            continue
        seen_ids.add(cid)
        targets.append(agent_info)
        if len(targets) >= MAX_MENTIONS_PER_MESSAGE:
            break

    if not targets and (sender or "").strip().upper() == "CEO":
        cmo = agents.get_agent("cmo")
        if cmo is not None:
            targets.append(cmo)

    return targets


def post_message(sender, text, attached_assets=None, depth=0):
    """Post a message to the team room and sequentially run up to 3 mentioned agent sessions."""
    if attached_assets is None:
        attached_assets = []

    mentions = extract_mentions(text)
    if (sender or "").strip().upper() == "CEO":
        sender_name = "CEO (Founder)"
        sender_code = "CEO"
    else:
        sender_conf = agents.get_agent(sender) or {}
        sender_name = sender_conf.get("name", sender.upper())
        sender_code = sender_conf.get("code", sender.upper()[:4])

    new_msg = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "sender": sender,
        "sender_name": sender_name,
        "sender_code": sender_code,
        "text": text,
        "mentions": mentions,
        "attached_assets": attached_assets,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_message(new_msg)

    if depth >= 3:
        return [new_msg]

    targets = _resolve_targets(sender, mentions)
    if not targets:
        return [new_msg]

    asset_ctx, attachment_paths = _prepare_attachments(attached_assets)
    confidential = product_rules.mentions_confidential(text, path=_rules_path())
    replies = [new_msg]
    prior_context = []

    for target in targets:
        cid = target["id"]
        prompt_parts = [f"From {sender}: {text}"]
        if asset_ctx:
            prompt_parts.append(asset_ctx)
        if prior_context:
            prompt_parts.append("Earlier replies in this thread:\n" + "\n".join(prior_context))
        user_prompt = "\n\n".join(prompt_parts)

        system_prompt = agents.build_system_prompt(cid)
        sess = agent_runtime.start_session(
            cid,
            user_prompt,
            system_prompt=system_prompt,
            allowed_tools=target.get("tools") or [],
            attachments=attachment_paths or None,
            confidential=confidential,
            title=str(text or "")[:100],
        )

        status = sess.get("status")
        if status == "failed":
            replies.append(_post_system_error(sess.get("error") or "Agent session failed"))
            break

        content_ids = db.list_content_ids_for_session(sess["id"])
        if status == "waiting_user":
            pending = sess.get("pending_question") or {}
            reply_text = pending.get("question") or ""
        else:
            reply_text = sess.get("result_text") or ""

        reply_msg = {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "sender": cid,
            "sender_name": target.get("name", cid),
            "sender_code": target.get("code", cid.upper()[:4]),
            "text": reply_text,
            "mentions": extract_mentions(reply_text),
            "session_id": sess["id"],
            "content_ids": content_ids,
            "waiting_user": status == "waiting_user",
            "model_used": sess.get("model_used"),
            "provider": sess.get("provider"),
            "fallback_used": bool(sess.get("fallback_used")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_message(reply_msg)
        replies.append(reply_msg)

        cid_note = f" (created content_ids: {', '.join(content_ids)})" if content_ids else ""
        prior_context.append(f"- @{cid}{cid_note}: {reply_text[:400]}")

    return replies


if __name__ == "__main__":
    print(json.dumps(post_message("CEO", "Status check @cmo"), indent=2))
