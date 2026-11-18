#!/usr/bin/env python3
"""Magnm Global Team Room & Swarm Messaging Service
Powers shared PR & Marketing team chat, @mentions across all 13 roles,
deterministic capability guardrails, asset attachments, and CMO orchestration.

Zero emojis; uniform role identifiers.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import assets_manager
import db
import paths
import product_rules

ROOT = paths.DATA_ROOT
MESSAGES_FILE = paths.data("state", "global_messages.json")
CAPABILITIES_FILE = paths.data("config", "capabilities.json")


def load_capabilities():
    if not os.path.exists(CAPABILITIES_FILE):
        return {}
    try:
        with open(CAPABILITIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("agents", {})
    except Exception:
        return {}


def load_messages(limit=80):
    """Return the most recent team room messages from SQLite."""
    return db.load_messages(limit=limit)


def save_message(msg):
    """Persist a team room message to SQLite."""
    db.save_message(msg)


def extract_mentions(text):
    """Find @agent_id mentions in text."""
    matches = re.findall(r"@([a-zA-Z0-9_-]+)", text or "")
    seen = []
    for m in matches:
        ml = m.lower()
        if ml not in seen:
            seen.append(ml)
    return seen


def _facts_excerpt(limit=1800):
    """Return the start of research/product-facts.md (verified claims only), or a note if missing."""
    path = paths.data("research", "product-facts.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(limit).strip()
    except OSError:
        return "(research/product-facts.md not found: make no product claims)"


def build_asset_context(asset_ids):
    """Retrieve metadata and excerpts for attached assets."""
    if not asset_ids:
        return ""
    all_assets = assets_manager.get_assets()
    ctx_parts = ["\n[ATTACHED ASSETS]:"]
    for aid in asset_ids:
        a = next((x for x in all_assets if x["id"] == aid), None)
        if a:
            ctx_parts.append(f"- Asset ID: {a['id']} ({a['name']}) | Type: {a['mime_type']} | Size: {a['size']}")
            ctx_parts.append(f"  Description: {a.get('description', 'None')}")
            if a["mime_type"].startswith("text/") or a["name"].endswith((".md", ".txt")):
                full_path = paths.data(a["relative_path"])
                if os.path.exists(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as tf:
                            preview = tf.read(500)
                            ctx_parts.append(f"  Preview: {preview}...")
                    except Exception:
                        pass
    return "\n".join(ctx_parts)


def _get_image_paths(asset_ids):
    if not asset_ids:
        return None
    all_assets = assets_manager.get_assets()
    paths = []
    for aid in asset_ids:
        a = next((x for x in all_assets if x["id"] == aid), None)
        if a and a.get("file_group") == "image":
            fp = paths.data(a["relative_path"])
            if os.path.exists(fp):
                paths.append(fp)
    return paths or None


def _post_system_error(error_text):
    """Persist and return a single system message when AI inference cannot proceed."""
    sys_msg = {
        "id": f"msg_{int(time.time()*1000)+6}",
        "sender": "system",
        "sender_name": "System",
        "sender_code": "SYS",
        "text": str(error_text),
        "mentions": [],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_message(sys_msg)
    return sys_msg


def post_message(sender, text, attached_assets=None, depth=0):
    """Post a message to the Global Team Room.
    Handles @mentions across all 13 roles, capability checks, task escalation to CMO, and replies.
    """
    if attached_assets is None:
        attached_assets = []

    capabilities = load_capabilities()
    mentions = extract_mentions(text)

    if sender == "CEO":
        sender_name = "CEO (Founder)"
        sender_code = "CEO"
    else:
        conf = capabilities.get(sender, {})
        sender_name = conf.get("name", sender.upper())
        sender_code = conf.get("code", sender.upper()[:4])

    msg_id = f"msg_{int(time.time()*1000)}"
    new_msg = {
        "id": msg_id,
        "sender": sender,
        "sender_name": sender_name,
        "sender_code": sender_code,
        "text": text,
        "mentions": mentions,
        "attached_assets": attached_assets,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_message(new_msg)

    if depth >= 3:
        return [new_msg]

    replies = [new_msg]

    for agent_id in mentions:
        if agent_id not in capabilities or agent_id == sender:
            continue
        agent_conf = capabilities[agent_id]
        agent_code = agent_conf.get("code", agent_id.upper()[:4])

        if agent_id == "cmo":
            try:
                cmo_res = handle_cmo_orchestration(text, sender, attached_assets, capabilities)
            except (ai_gateway.AINotConnected, ai_gateway.AIAuthError) as e:
                replies.append(_post_system_error(str(e)))
                break
            reply_msg = {
                "id": f"msg_{int(time.time()*1000)+1}",
                "sender": "cmo",
                "sender_name": "CMO (Chief Marketing Officer)",
                "sender_code": "CMO",
                "text": cmo_res["text"],
                "mentions": extract_mentions(cmo_res["text"]),
                "model_used": cmo_res.get("model_used"),
                "provider": cmo_res.get("provider"),
                "fallback_used": cmo_res.get("fallback_used", False),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            save_message(reply_msg)
            replies.append(reply_msg)
        else:
            try:
                spec_res = handle_specialist_response(agent_id, text, sender, attached_assets, capabilities)
            except (ai_gateway.AINotConnected, ai_gateway.AIAuthError) as e:
                replies.append(_post_system_error(str(e)))
                break
            reply_text = spec_res["text"]
            reply_msg = {
                "id": f"msg_{int(time.time()*1000)+2}",
                "sender": agent_id,
                "sender_name": agent_conf["name"],
                "sender_code": agent_code,
                "text": reply_text,
                "mentions": extract_mentions(reply_text),
                "capability_redirect": spec_res.get("capability_redirect", False),
                "model_used": spec_res.get("model_used"),
                "provider": spec_res.get("provider"),
                "fallback_used": spec_res.get("fallback_used", False),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            save_message(reply_msg)
            replies.append(reply_msg)

            # Escalate specialist tasks to CMO if not already triggered or redirected
            if "@cmo" in reply_text.lower() and not spec_res.get("capability_redirect") and "cmo" not in mentions and depth < 2:
                try:
                    cmo_res = handle_cmo_orchestration(reply_text, agent_id, attached_assets, capabilities)
                except (ai_gateway.AINotConnected, ai_gateway.AIAuthError) as e:
                    replies.append(_post_system_error(str(e)))
                    break
                cmo_msg = {
                    "id": f"msg_{int(time.time()*1000)+3}",
                    "sender": "cmo",
                    "sender_name": "CMO (Chief Marketing Officer)",
                    "sender_code": "CMO",
                    "text": cmo_res["text"],
                    "mentions": extract_mentions(cmo_res["text"]),
                    "model_used": cmo_res.get("model_used"),
                    "provider": cmo_res.get("provider"),
                    "fallback_used": cmo_res.get("fallback_used", False),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                save_message(cmo_msg)
                replies.append(cmo_msg)

    return replies


def escalate_from_agent_chat(agent_id, user_message, agent_response="", attached_assets=None):
    """Mirror a valid 1-on-1 specialist assignment into Global Team Room to inform @cmo
    and get CMO's central orchestration & team delegation reply.
    """
    if attached_assets is None:
        attached_assets = []
    capabilities = load_capabilities()
    agent_conf = capabilities.get(agent_id, {})
    agent_code = agent_conf.get("code", agent_id.upper()[:4])
    agent_name = agent_conf.get("name", agent_id)

    esc_text = (
        f"@cmo Task escalated from 1-on-1 CEO chat with @{agent_id}: "
        f"\"{user_message}\". Confirming schedule, scope, and cross-team orchestration."
    )
    esc_msg = {
        "id": f"msg_{int(time.time()*1000)+4}",
        "sender": agent_id,
        "sender_name": agent_name,
        "sender_code": agent_code,
        "text": esc_text,
        "mentions": ["cmo"],
        "attached_assets": attached_assets,
        "model_used": "orchestration-router",
        "provider": "deterministic",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_message(esc_msg)

    try:
        cmo_res = handle_cmo_orchestration(
            f"CEO assigned task via @{agent_id}: {user_message}\nSpecialist note: {agent_response[:350]}",
            agent_id,
            attached_assets,
            capabilities
        )
    except (ai_gateway.AINotConnected, ai_gateway.AIAuthError) as e:
        return [esc_msg, _post_system_error(str(e))]

    cmo_msg = {
        "id": f"msg_{int(time.time()*1000)+5}",
        "sender": "cmo",
        "sender_name": "CMO (Chief Marketing Officer)",
        "sender_code": "CMO",
        "text": cmo_res["text"],
        "mentions": extract_mentions(cmo_res["text"]),
        "model_used": cmo_res.get("model_used"),
        "provider": cmo_res.get("provider"),
        "fallback_used": cmo_res.get("fallback_used", False),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_message(cmo_msg)
    return [esc_msg, cmo_msg]


def handle_cmo_orchestration(text, sender, attached_assets, capabilities):
    asset_ctx = build_asset_context(attached_assets)
    images = _get_image_paths(attached_assets)
    system_prompt = f"""You are the CMO (Chief Marketing Officer) of MAGNM PR & Marketing Studio.
You orchestrate the full PR, social marketing, and business research team. ZERO emojis allowed.

Team Roster:
- @strategist (Positioning & Roadmap)
- @advisor (Editorial Counsel & Priorities)
- @researcher (Market, HN, Reddit & Freelance Opportunity Research)
- @blog-writer (Long-Form Engineering Deep-Dives)
- @writer (Social Copy, X threads, LinkedIn, Captions, Video Scripts)
- @designer (Visuals, Screenshots & Palette v3 Cards via card.py)
- @video-producer (Remotion / HyperFrames Motion Reels)
- @reviewer (Fact-Checking & Quality Gate)
- @analyst (Metrics & Conversion Analysis)
- @listener (Social Listening & Comment Triage)
- @community (PR Responses & Crisis L1-L3)
- @growth (Distribution Experiments & Bounties)

Verified product facts (only claim what is written here):
{_facts_excerpt()}

{product_rules.prompt_block()}

Hard Rules:
1. ZERO EMOJIS. Use clean, executive PR agency tone.
2. Explicitly state your decision (whether to proceed, timeline/priority) and delegate concrete subtasks to relevant specialists using @<agent_id> mentions.
3. Confirm nothing publishes externally without @reviewer APPROVE and CEO final sign-off.
"""
    user_prompt = f"From: {sender}\nMessage: {text}\n{asset_ctx}\n\nProvide your CMO orchestration decision and delegate with @mentions:"
    return ai_gateway.generate_completion_with_meta(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_tier="reasoning",
        image_paths=images,
        agent_role="cmo"
    )


def handle_specialist_response(agent_id, text, sender, attached_assets, capabilities):
    import agent_chat

    guard = agent_chat.evaluate_capability_guardrail(agent_id, text, attached_assets)
    if guard.get("violated"):
        return {
            "text": guard["response"],
            "model_used": "capability-guardrail",
            "provider": "deterministic",
            "fallback_used": False,
            "capability_redirect": True
        }

    agent_conf = capabilities[agent_id]
    asset_ctx = build_asset_context(attached_assets)
    images = _get_image_paths(attached_assets)

    system_prompt = f"""You are {agent_conf['name']} ({agent_conf['role']}) at MAGNM. ZERO emojis allowed.

Capabilities:
CAN: {', '.join(agent_conf.get('can', []))}
CANNOT: {', '.join(agent_conf.get('cannot', []))}
Redirection peers: {json.dumps(agent_conf.get('redirect', {}))}

Verified product facts (only claim what is written here):
{_facts_excerpt()}

{product_rules.prompt_block()}

Hard Rules:
1. NEVER USE EMOJIS.
2. CAPABILITY BOUNDARY: If asked to do something outside your capabilities, respond:
   "I cannot perform this task because [reason]. You should talk to @[agent_id] who specializes in this."
3. ESCALATION PROTOCOL: If assigned a new production task directly without CMO approval, confirm readiness and tag @cmo to schedule and orchestrate it.
4. Answer strictly within verified ground truth and the product rules above.
"""
    user_prompt = f"From: {sender}\nMessage: {text}\n{asset_ctx}"
    return ai_gateway.generate_completion_with_meta(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_tier="fast",
        image_paths=images,
        agent_role=agent_id
    )


if __name__ == "__main__":
    print(json.dumps(post_message("CEO", "Status check @cmo"), indent=2))
