#!/usr/bin/env python3
"""Magnm Agent Chat & Roster Service
Manages all 13 PR & Marketing team members, capability contracts, live status,
asset validation, 1-on-1 chats routed via ai_gateway (NVIDIA NIM per-role models),
and automatic escalation of specialist tasks to @cmo in the Global Team Room.

Zero emojis; uniform role identifiers.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import assets_manager
import paths
import product_rules

ROOT = paths.DATA_ROOT
CHATS_DIR = paths.data("state", "chats")
CAPABILITIES_FILE = paths.data("config", "capabilities.json")
os.makedirs(CHATS_DIR, exist_ok=True)

AGENTS_ROSTER = [
    {
        "id": "cmo",
        "name": "CMO (Chief Marketing Officer)",
        "role": "Marketing Director & Orchestrator",
        "code": "CMO",
        "description": "Orchestrates daily PR & social cycles, delegates tasks across specialists, manages deadlines, and stages packages for CEO approval.",
        "file": "agents/cmo.md",
        "output_dir": "logs/"
    },
    {
        "id": "strategist",
        "name": "Strategist",
        "role": "Brand Positioning & Roadmap",
        "code": "STR",
        "description": "Defines positioning for Cero v2.2.3 and MAGNM agency services, target developer & freelance segments, and monthly milestones.",
        "file": "agents/strategist.md",
        "output_dir": "outputs/strategy/"
    },
    {
        "id": "advisor",
        "name": "Advisor",
        "role": "Editorial & Campaign Counsel",
        "code": "ADV",
        "description": "Advises the CMO on weekly editorial slots, content hooks, effort-vs-impact tradeoffs, and factual risk.",
        "file": "agents/advisor.md",
        "output_dir": "reviews/"
    },
    {
        "id": "researcher",
        "name": "Market Researcher",
        "role": "Ecosystem & Business Opportunity Scanner",
        "code": "RES",
        "description": "Gathers sourced signals from Hacker News, Reddit, GitHub, and client markets to score opportunities (Score / 625).",
        "file": "agents/researcher.md",
        "output_dir": "outputs/research/"
    },
    {
        "id": "blog-writer",
        "name": "Blog Writer",
        "role": "Technical Content Engine",
        "code": "BLG",
        "description": "Authors 1,500-2,500 word engineering deep dives and tutorials grounded strictly in verified Cero architecture and commits.",
        "file": "agents/blog-writer.md",
        "output_dir": "outputs/blogs/"
    },
    {
        "id": "writer",
        "name": "Social Copywriter",
        "role": "Posts, Threads & Captions",
        "code": "WRT",
        "description": "Crafts high-signal X threads, LinkedIn announcements, Instagram captions, and video scripts in MAGNM's builder voice.",
        "file": "agents/writer.md",
        "output_dir": "outputs/posts/"
    },
    {
        "id": "designer",
        "name": "Designer",
        "role": "Visuals & Typography",
        "code": "DSG",
        "description": "Audits screenshots and prepares typographic cards in 4 ratios via Palette v3 (templates/card.py).",
        "file": "agents/designer.md",
        "output_dir": "outputs/posts/"
    },
    {
        "id": "video-producer",
        "name": "Video Producer",
        "role": "Motion Graphics & Reels",
        "code": "VID",
        "description": "Builds Remotion/HyperFrames 9:16 reels and 16:9 videos using authentic Cero footage, captions, and sound.",
        "file": "agents/video-producer.md",
        "output_dir": "outputs/video/"
    },
    {
        "id": "reviewer",
        "name": "Reviewer (Gatekeeper)",
        "role": "Fact-Checking & Quality Gate",
        "code": "REV",
        "description": "Enforces zero hallucinated claims against product-facts.md, checks drafts against product rules, and checks tone and platform rules.",
        "file": "agents/reviewer.md",
        "output_dir": "reviews/"
    },
    {
        "id": "analyst",
        "name": "Analyst",
        "role": "Metrics & Growth Tracking",
        "code": "ANA",
        "description": "Interprets verified impressions, views, GitHub referral clicks, and experiment metrics without extrapolating missing numbers.",
        "file": "agents/analyst.md",
        "output_dir": "outputs/analytics/"
    },
    {
        "id": "listener",
        "name": "Listener",
        "role": "Community Sentiment",
        "code": "LIS",
        "description": "Classifies incoming developer comments, questions, and social sentiment across channels and escalates negative signals.",
        "file": "agents/listener.md",
        "output_dir": "outputs/feedback/"
    },
    {
        "id": "community",
        "name": "Community & Crisis Lead",
        "role": "PR Replies & Backlash Defense",
        "code": "COM",
        "description": "Drafts calm, factual PR replies, freelance client communications, and L1-L3 crisis responses for CEO sign-off.",
        "file": "agents/community.md",
        "output_dir": "outputs/community/"
    },
    {
        "id": "growth",
        "name": "Growth Lead",
        "role": "Experiments & Bounties",
        "code": "GRO",
        "description": "Designs non-content developer distribution experiments, open-source bounties, and partnership funnels.",
        "file": "agents/growth.md",
        "output_dir": "outputs/growth/"
    }
]


def load_capabilities():
    if not os.path.exists(CAPABILITIES_FILE):
        return {}
    try:
        with open(CAPABILITIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("agents", {})
    except Exception:
        return {}


def get_roster_status():
    """Return full 13-agent team roster with live outputs, capability contracts, and NIM model mapping."""
    capabilities = load_capabilities()
    model_matrix = {m["role_id"]: m for m in ai_gateway.get_role_model_matrix()}

    roster = []
    for member in AGENTS_ROSTER:
        out_dir = paths.data(member["output_dir"])
        latest_file = None
        if os.path.exists(out_dir):
            files = []
            for root_d, _, f_list in os.walk(out_dir):
                for fn in f_list:
                    files.append(os.path.join(root_d, fn))
            if files:
                latest_file = os.path.relpath(max(files, key=os.path.getmtime), paths.DATA_ROOT)

        chat_file = os.path.join(CHATS_DIR, f"{member['id']}.json")
        msg_count = 0
        if os.path.exists(chat_file):
            try:
                with open(chat_file, "r", encoding="utf-8") as cf:
                    msg_count = len(json.load(cf))
            except Exception:
                pass

        cap = capabilities.get(member["id"], {})
        m_info = model_matrix.get(member["id"], {})

        roster.append({
            **member,
            "status": "Ready",
            "latest_output": latest_file or "None yet",
            "message_count": msg_count,
            "can": cap.get("can", []),
            "cannot": cap.get("cannot", []),
            "redirect": cap.get("redirect", {}),
            "allowed_asset_groups": cap.get("allowed_asset_groups", ["document"]),
            "model_info": m_info
        })
    return roster


def get_agent_chat_history(agent_id):
    chat_file = os.path.join(CHATS_DIR, f"{agent_id}.json")
    if os.path.exists(chat_file):
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_agent_chat_history(agent_id, history):
    os.makedirs(CHATS_DIR, exist_ok=True)
    chat_file = os.path.join(CHATS_DIR, f"{agent_id}.json")
    with open(chat_file, "w", encoding="utf-8") as f:
        json.dump(history[-100:], f, indent=2)


def evaluate_capability_guardrail(agent_id, message, attached_asset_ids=None):
    """Deterministically enforce role boundaries and asset compatibility.
    Returns dict with violated=True and redirect_to if task is out of scope.
    """
    capabilities = load_capabilities()
    agent_cap = capabilities.get(agent_id, {})
    agent_name = agent_cap.get("name", agent_id)
    allowed_groups = agent_cap.get("allowed_asset_groups", ["document"])

    # 1. Validate attached asset types
    if attached_asset_ids:
        all_assets = assets_manager.get_assets()
        for aid in attached_asset_ids:
            asset = next((a for a in all_assets if a["id"] == aid), None)
            if asset:
                fg = asset.get("file_group", "document")
                if fg not in allowed_groups:
                    target = "video-producer" if fg in ("video", "audio") else ("designer" if fg == "image" else "cmo")
                    reason = f"{agent_name} does not handle {fg} files directly (attached '{asset['name']}')."
                    return {
                        "violated": True,
                        "reason": reason,
                        "redirect_to": target,
                        "response": f"I cannot perform this task because {reason} You should talk to @{target} who specializes in processing {fg} assets."
                    }

    if agent_id == "cmo":
        return {"violated": False}

    text = (message or "").lower().strip()

    # 2. Check Video / 3D Animation / MP4 production requests
    is_video_render_req = bool(
        re.search(r"\b(render|export|compile|create|make|build|edit|produce|animate)\b.*\b(3d|animation|mp4|video film|reel|remotion|ffmpeg|hyperframes|motion graphic)\b", text)
        or re.search(r"\b(3d animation|export.*\bmp4\b|render.*\bvideo\b)\b", text)
    )
    # Allow script writing by writer or blog-writer unless render/mp4/3d/ffmpeg is requested
    is_script_only = bool(re.search(r"\b(script|storyboard|voiceover copy|caption|shot list)\b", text)) and not bool(
        re.search(r"\b(render|export|mp4|3d|ffmpeg|remotion|animate)\b", text)
    )
    if is_video_render_req and not is_script_only and agent_id != "video-producer":
        reason = f"{agent_name} does not render videos, 3D animations, or export MP4 files."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "video-producer",
            "response": f"I cannot perform this task because {reason} You should talk to @video-producer who handles Remotion, HyperFrames, and MP4 video rendering."
        }

    # 3. Check Visual / Card / Logo Design requests
    is_design_req = bool(
        re.search(r"\b(design|draw|generate|render|create|make)\b.*\b(card|social card|poster|banner|logo|illustration|thumbnail|graphic|png|svg|visual layout)\b", text)
    )
    if is_design_req and agent_id not in ("designer", "video-producer"):
        reason = f"{agent_name} does not design graphic cards, visual layouts, or brand imagery."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "designer",
            "response": f"I cannot perform this task because {reason} You should talk to @designer who manages Palette v3 visuals and templates/card.py."
        }

    # 4. Check Long-form Technical Blog Writing
    is_blog_req = bool(
        re.search(r"\b(write|author|draft|compose)\b.*\b(long-form|technical blog|engineering blog|deep dive article|blog article|2000-word)\b", text)
    )
    if is_blog_req and agent_id != "blog-writer":
        reason = f"{agent_name} is not assigned to author long-form technical engineering blog posts."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "blog-writer",
            "response": f"I cannot perform this task because {reason} You should talk to @blog-writer who handles technical deep dives grounded in Cero code."
        }

    # 5. Check Short Social Copywriting asked of non-writing specialists
    is_social_copy_req = bool(
        re.search(r"\b(write|draft|compose|create)\b.*\b(x thread|tweet|linkedin post|instagram caption|ad copy|launch copy)\b", text)
    )
    if is_social_copy_req and agent_id not in ("writer", "community"):
        reason = f"{agent_name} does not write platform social copy or threads."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "writer",
            "response": f"I cannot perform this task because {reason} You should talk to @writer who writes X threads, LinkedIn posts, and captions."
        }

    # 6. Check Live Publishing / Autonomous Budget Spend
    is_publish_req = bool(
        re.search(r"\b(publish live|post directly to instagram|post directly to x|tweet right now|spend budget|wire money)\b", text)
    )
    if is_publish_req:
        reason = f"{agent_name} cannot publish directly to external social handles or spend funds without CEO approval."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "cmo",
            "response": f"I cannot perform this task because {reason} Please route campaign staging through @cmo and finalize approval in the CEO Review Station."
        }

    # 7. Check Direct Code Modification / Production Backend Code
    is_app_code_req = bool(
        re.search(r"\b(modify cero|edit rust code|commit to cero repo|push code to github|deploy server)\b", text)
    )
    if is_app_code_req:
        reason = f"Marketing agents have read-only codebase intelligence access and cannot push product code."
        return {
            "violated": True,
            "reason": reason,
            "redirect_to": "cmo",
            "response": f"I cannot perform this task because {reason} Discuss engineering changes directly or ask @cmo to sync verified commits."
        }

    return {"violated": False}


def _load_file_safe(rel_path, limit=3000):
    full_path = paths.data(rel_path)
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read(limit)
        except Exception:
            pass
    return ""


def chat_with_agent(agent_id, user_message, attached_assets=None):
    """Execute a 1-on-1 chat with an agent persona, enforcing capability contracts,
    routing inference through NVIDIA NIM / ai_gateway, and escalating specialist tasks to @cmo.
    """
    if attached_assets is None:
        attached_assets = []

    agent_info = next((a for a in AGENTS_ROSTER if a["id"] == agent_id), None)
    if not agent_info:
        return {"error": f"Agent '{agent_id}' not found."}

    capabilities = load_capabilities()
    agent_cap = capabilities.get(agent_id, {})
    history = get_agent_chat_history(agent_id)
    ts = datetime.now(timezone.utc).isoformat()

    # Step A: Deterministic capability & asset check
    guard = evaluate_capability_guardrail(agent_id, user_message, attached_assets)
    if guard.get("violated"):
        refusal_text = guard["response"]
        history.append({"sender": "user", "text": user_message, "attached_assets": attached_assets, "timestamp": ts})
        history.append({
            "sender": agent_info["id"],
            "name": agent_info["name"],
            "text": refusal_text,
            "capability_redirect": True,
            "redirect_to": guard["redirect_to"],
            "model_used": "capability-guardrail",
            "provider": "deterministic",
            "timestamp": ts
        })
        save_agent_chat_history(agent_id, history)
        return {
            "success": True,
            "capability_redirect": True,
            "redirect_to": guard["redirect_to"],
            "agent": agent_info,
            "response": refusal_text,
            "model_used": "capability-guardrail",
            "provider": "deterministic",
            "escalated_to_cmo": False,
            "history": history
        }

    # Step B: Build grounded prompt context
    agent_system_prompt = _load_file_safe(agent_info["file"], limit=2500)
    facts = _load_file_safe("research/product-facts.md", limit=2000)
    repo_sync = _load_file_safe("research/repo-sync.md", limit=2000)

    asset_ctx = ""
    image_paths = []
    if attached_assets:
        all_assets = assets_manager.get_assets()
        ctx_parts = ["[ATTACHED ASSETS]:"]
        for aid in attached_assets:
            a = next((x for x in all_assets if x["id"] == aid), None)
            if a:
                ctx_parts.append(f"- {a['name']} ({a['mime_type']}, {a['size']}): {a.get('description', '')}")
                full_p = paths.data(a["relative_path"])
                if a.get("file_group") == "image" and os.path.exists(full_p):
                    image_paths.append(full_p)
                elif a.get("file_group") == "document" and os.path.exists(full_p):
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as df:
                            ctx_parts.append(f"  Excerpt: {df.read(600)}")
                    except Exception:
                        pass
        asset_ctx = "\n".join(ctx_parts)

    recent_turns = history[-4:]
    recent_ctx = "\n".join([f"{t['sender']}: {t['text'][:300]}" for t in recent_turns]) if recent_turns else "None"

    system_prompt = f"""You are acting strictly as the MAGNM PR & Marketing Team member: {agent_info['name']} ({agent_info['role']}).

Role Instructions:
{agent_system_prompt}

Capability Boundaries:
CAN: {', '.join(agent_cap.get('can', []))}
CANNOT: {', '.join(agent_cap.get('cannot', []))}
Redirection peers: {json.dumps(agent_cap.get('redirect', {}))}

Verified Ground Truth (ONLY claim what is written here):
{facts}

Live Codebase Intelligence:
{repo_sync}

Product rules:
{product_rules.prompt_block()}

Hard Rules:
1. ZERO EMOJIS. Use direct, professional, builder-to-builder PR & engineering tone.
2. Stay strictly inside your role capabilities. If asked to do work outside your role, refuse clearly and redirect to the right @specialist.
3. If the CEO is assigning a production task to you (and you are not CMO), confirm your role readiness and explicitly note that you are escalating scheduling & delegation to @cmo in the Global Team Room.
"""

    user_prompt = f"""Recent Chat Context:
{recent_ctx}

{asset_ctx}

CEO / User Message:
{user_message}
"""

    try:
        tier = "reasoning" if agent_id in ("cmo", "strategist", "advisor", "reviewer") else "fast"
        ai_res = ai_gateway.generate_completion_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_tier=tier,
            image_paths=image_paths or None,
            agent_role=agent_id
        )
        reply_text = ai_res["text"]

        history.append({"sender": "user", "text": user_message, "attached_assets": attached_assets, "timestamp": ts})
        history.append({
            "sender": agent_info["id"],
            "name": agent_info["name"],
            "text": reply_text,
            "model_used": ai_res.get("model_used"),
            "provider": ai_res.get("provider"),
            "fallback_used": ai_res.get("fallback_used", False),
            "fallback_reason": ai_res.get("fallback_reason"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        save_agent_chat_history(agent_id, history)

        # Step C: Automatic Escalation from non-CMO specialist chat -> Global Team Room (@cmo)
        escalated = False
        global_replies = []
        if agent_id != "cmo":
            import team_room
            global_replies = team_room.escalate_from_agent_chat(
                agent_id=agent_id,
                user_message=user_message,
                agent_response=reply_text,
                attached_assets=attached_assets
            )
            escalated = True

        return {
            "success": True,
            "capability_redirect": False,
            "agent": agent_info,
            "response": reply_text,
            "model_used": ai_res.get("model_used"),
            "model_requested": ai_res.get("model_requested"),
            "provider": ai_res.get("provider"),
            "fallback_used": ai_res.get("fallback_used", False),
            "fallback_reason": ai_res.get("fallback_reason"),
            "escalated_to_cmo": escalated,
            "global_replies": global_replies,
            "history": history
        }
    except ai_gateway.AIAuthError as e:
        return {
            "success": False,
            "error_code": "ai_auth_error",
            "error": str(e),
            "agent": agent_info,
        }
    except ai_gateway.AINotConnected as e:
        return {
            "success": False,
            "error_code": "ai_not_connected",
            "error": str(e),
            "agent": agent_info,
        }
    except Exception as e:
        return {"success": False, "error": f"Agent query failed: {e}", "agent": agent_info}


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print(json.dumps(chat_with_agent(sys.argv[1], sys.argv[2]), indent=2))
    else:
        print(json.dumps(get_roster_status(), indent=2))
