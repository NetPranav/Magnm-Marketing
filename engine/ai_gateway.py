#!/usr/bin/env python3
"""Magnm Central AI Gateway
Unified multi-model inference adapter supporting:
- NVIDIA NIM (per-role keys & catalog-verified tier models with backup fallback)
- OpenAI, Anthropic, Google Gemini, Groq, Ollama
- Local ChatGPT CLI (opt-in via AI_USE_CHATGPT_CLI_FALLBACK with fast CDP port 9222 check)
"""

import base64
from datetime import datetime, timezone
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import db
import key_pool
from key_pool import AIAuthError, AINotConnected
import model_registry
import paths

ROOT = paths.DATA_ROOT
ENV_PATH = paths.data(".env")
DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
TEAM_PROXY_TIMEOUT_S = 120
TEAM_AUTH_REQUIRED_MSG = "Sign in under Settings, Account to use the team's AI keys."
TEAM_NO_KEYS_MSG = "The team has no shared AI keys yet. Ask your admin."
DEFAULT_DAILY_REQUESTS = 200
DEFAULT_DAILY_TOKENS = 400000

_sleep = time.sleep


class EmptyModelResponse(RuntimeError):
    """Raised when a model returns no answer (e.g., all tokens spent on reasoning)."""


ROLE_MODEL_SPEC = {
    "cmo": {
        "name": "CMO (Chief Marketing Officer)",
        "code": "CMO",
        "env_suffix": "CMO",
        "model_env": "NIM_MODEL_CMO",
        "key_env": "NVIDIA_NIM_API_KEY_CMO",
        "tier": "reasoning",
        "specialization": "Executive campaign orchestration, delegation, priority triage, and approval staging."
    },
    "strategist": {
        "name": "Strategist",
        "code": "STR",
        "env_suffix": "STRATEGIST",
        "model_env": "NIM_MODEL_STRATEGIST",
        "key_env": "NVIDIA_NIM_API_KEY_STRATEGIST",
        "tier": "reasoning",
        "specialization": "Market positioning, freelance vs. product messaging, and quarterly roadmaps."
    },
    "advisor": {
        "name": "Advisor",
        "code": "ADV",
        "env_suffix": "ADVISOR",
        "model_env": "NIM_MODEL_ADVISOR",
        "key_env": "NVIDIA_NIM_API_KEY_ADVISOR",
        "tier": "reasoning",
        "specialization": "Editorial counsel, slot selection, effort-vs-impact critique, and risk review."
    },
    "researcher": {
        "name": "Market Researcher",
        "code": "RES",
        "env_suffix": "RESEARCHER",
        "model_env": "NIM_MODEL_RESEARCHER",
        "key_env": "NVIDIA_NIM_API_KEY_RESEARCHER",
        "tier": "research",
        "specialization": "Ecosystem intelligence, HN/Reddit developer trends, and freelance market discovery."
    },
    "blog-writer": {
        "name": "Blog Writer",
        "code": "BLG",
        "env_suffix": "BLOG_WRITER",
        "model_env": "NIM_MODEL_BLOG_WRITER",
        "key_env": "NVIDIA_NIM_API_KEY_BLOG_WRITER",
        "tier": "writing",
        "specialization": "1,500-2,500 word engineering breakdowns, Tauri/Rust architecture guides, and evidence logs."
    },
    "writer": {
        "name": "Social Copywriter",
        "code": "WRT",
        "env_suffix": "WRITER",
        "model_env": "NIM_MODEL_WRITER",
        "key_env": "NVIDIA_NIM_API_KEY_WRITER",
        "tier": "writing",
        "specialization": "High-signal X threads, LinkedIn announcements, PR outreach copy, and short video scripts."
    },
    "designer": {
        "name": "Designer",
        "code": "DSG",
        "env_suffix": "DESIGNER",
        "model_env": "NIM_MODEL_DESIGNER",
        "key_env": "NVIDIA_NIM_API_KEY_DESIGNER",
        "tier": "vision",
        "specialization": "Visual QA of screenshots, brand Palette v3 typography specs, and Pillow card layouts."
    },
    "video-producer": {
        "name": "Video Producer",
        "code": "VID",
        "env_suffix": "VIDEO_PRODUCER",
        "model_env": "NIM_MODEL_VIDEO_PRODUCER",
        "key_env": "NVIDIA_NIM_API_KEY_VIDEO_PRODUCER",
        "tier": "writing",
        "specialization": "Motion graphics storyboards, Remotion/HyperFrames specs, and authentic footage reels."
    },
    "reviewer": {
        "name": "Reviewer (Gatekeeper)",
        "code": "REV",
        "env_suffix": "REVIEWER",
        "model_env": "NIM_MODEL_REVIEWER",
        "key_env": "NVIDIA_NIM_API_KEY_REVIEWER",
        "tier": "vision",
        "specialization": "Strict factual verification against product-facts.md, claim auditing, and APPROVE/REJECT verdicts."
    },
    "analyst": {
        "name": "Analyst",
        "code": "ANA",
        "env_suffix": "ANALYST",
        "model_env": "NIM_MODEL_ANALYST",
        "key_env": "NVIDIA_NIM_API_KEY_ANALYST",
        "tier": "vision",
        "specialization": "Interpreting computed reach, referral clicks, GitHub traffic, and experiment attribution."
    },
    "listener": {
        "name": "Listener",
        "code": "LIS",
        "env_suffix": "LISTENER",
        "model_env": "NIM_MODEL_LISTENER",
        "key_env": "NVIDIA_NIM_API_KEY_LISTENER",
        "tier": "fast",
        "specialization": "High-speed classification of developer comments, sentiment triage, and PR issue detection."
    },
    "community": {
        "name": "Community & Crisis Lead",
        "code": "COM",
        "env_suffix": "COMMUNITY",
        "model_env": "NIM_MODEL_COMMUNITY",
        "key_env": "NVIDIA_NIM_API_KEY_COMMUNITY",
        "tier": "writing",
        "specialization": "PR response drafting, L1-L3 crisis management playbooks, and freelance client communications."
    },
    "growth": {
        "name": "Growth Lead",
        "code": "GRO",
        "env_suffix": "GROWTH",
        "model_env": "NIM_MODEL_GROWTH",
        "key_env": "NVIDIA_NIM_API_KEY_GROWTH",
        "tier": "reasoning",
        "specialization": "Distribution experiments, developer bounties, partnerships, and inbound lead pipelines."
    }
}


def _build_catalog_suggestions():
    """Build ordered list of suggested NIM model IDs from config/models.json."""
    try:
        mcfg = model_registry.load_models_config()
    except Exception:
        return []
    seen = []
    for tier_spec in mcfg.get("tiers", {}).values():
        for key in ("primary", "backup"):
            val = tier_spec.get(key)
            if val and val not in seen:
                seen.append(val)
    for val in mcfg.get("vision_models", []):
        if val and val not in seen:
            seen.append(val)
    return seen


NIM_CATALOG_SUGGESTIONS = _build_catalog_suggestions()


def load_env():
    """Load key-value pairs from .env if present."""
    env = {}
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k_clean = k.strip()
                        v_clean = v.strip()
                        if len(v_clean) >= 2 and ((v_clean[0] == '"' and v_clean[-1] == '"') or (v_clean[0] == "'" and v_clean[-1] == "'")):
                            v_clean = v_clean[1:-1]
                        env[k_clean] = v_clean
        except Exception:
            pass
    return env


def save_env_values(updates):
    """Update specific key=value pairs in .env while preserving comments and formatting."""
    if not isinstance(updates, dict):
        raise ValueError("updates must be a dictionary")

    cleaned = {}
    for k, v in updates.items():
        if k is None:
            continue
        key_name = str(k).strip().upper()
        if not key_name or not re.match(r"^[A-Z0-9_]+$", key_name):
            continue
        val_str = "" if v is None else str(v).strip().replace("\n", " ").replace("\r", "")
        cleaned[key_name] = val_str

    lines = []
    seen_keys = set()
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k_existing, _ = stripped.split("=", 1)
                    k_existing = k_existing.strip().upper()
                    if k_existing in cleaned:
                        lines.append(f"{k_existing}={cleaned[k_existing]}\n")
                        seen_keys.add(k_existing)
                        continue
                lines.append(raw_line if raw_line.endswith("\n") else raw_line + "\n")
    else:
        lines.append("# Magnm PR & Marketing Team Environment Configuration\n")

    for k_new, v_new in cleaned.items():
        if k_new not in seen_keys:
            lines.append(f"{k_new}={v_new}\n")

    tmp_path = ENV_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp_path, ENV_PATH)
    try:
        os.chmod(ENV_PATH, 0o600)
    except Exception:
        pass

    return list(cleaned.keys())


def _mask_secret(val):
    if not val:
        return ""
    if len(val) <= 8:
        return "*" * len(val)
    return f"{val[:6]}...{val[-4:]}"


def get_config():
    """Return resolved gateway configuration across env vars, .env file, and models.json."""
    file_env = load_env()
    models_cfg = model_registry.load_models_config()

    def get_val(key, default=""):
        val = file_env.get(key)
        if val:
            return val
        env_val = os.environ.get(key)
        if env_val:
            return env_val
        return default

    pool = key_pool.get_pool()
    global_nim_key = pool.first_pool_key() or get_val("NVIDIA_NIM_API_KEY") or get_val("NVIDIA_API_KEY")
    nim_base_default = models_cfg.get("nim_base_url", DEFAULT_NIM_BASE_URL)
    default_backup = models_cfg.get("tiers", {}).get("reasoning", {}).get("backup", "")

    role_models = {}
    role_model_overrides = {}
    role_keys = {}
    for role_id, spec in ROLE_MODEL_SPEC.items():
        override = get_val(spec["model_env"])
        role_model_overrides[role_id] = override
        tier_name = models_cfg.get("roles", {}).get(role_id, spec.get("tier", "fast"))
        tier_primary = models_cfg.get("tiers", {}).get(tier_name, {}).get("primary", "")
        role_models[role_id] = override or tier_primary
        role_keys[role_id] = get_val(spec["key_env"])

    return {
        "DEFAULT_PROVIDER": get_val("AI_DEFAULT_PROVIDER", "nvidia_nim").lower(),
        "NVIDIA_NIM_BASE_URL": get_val("NVIDIA_NIM_BASE_URL", nim_base_default).rstrip("/"),
        "NVIDIA_NIM_API_KEY": global_nim_key,
        "NIM_FALLBACK_MODEL": get_val("NIM_FALLBACK_MODEL", default_backup),
        "ROLE_MODELS": role_models,
        "ROLE_MODEL_OVERRIDES": role_model_overrides,
        "ROLE_KEYS": role_keys,
        "OPENAI_API_KEY": get_val("OPENAI_API_KEY"),
        "ANTHROPIC_API_KEY": get_val("ANTHROPIC_API_KEY"),
        "GEMINI_API_KEY": get_val("GEMINI_API_KEY"),
        "GROQ_API_KEY": get_val("GROQ_API_KEY"),
        "OLLAMA_BASE_URL": get_val("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        "USE_CHATGPT_CLI_FALLBACK": get_val("AI_USE_CHATGPT_CLI_FALLBACK", "false").lower() in ("1", "true", "yes")
    }


def _resolve_for_role(role_id, cfg, models_cfg, nim_key, tier=None):
    """Fetch catalog (if key present) and resolve primary, backup, warnings for role_id."""
    catalog = model_registry.get_catalog(nim_key, cfg["NVIDIA_NIM_BASE_URL"]) if nim_key else None
    merged_cfg = {**models_cfg, "ROLE_MODEL_OVERRIDES": cfg.get("ROLE_MODEL_OVERRIDES", {})}
    return model_registry.resolve_role_models(role_id, merged_cfg, catalog, tier_override=tier)


def has_local_nim_keys():
    """Return True if this laptop has any local NVIDIA NIM pool or role key configured."""
    pool = key_pool.get_pool()
    if pool.has_any_keys():
        return True
    cfg = get_config()
    if cfg.get("NVIDIA_NIM_API_KEY"):
        return True
    return any(bool(v) for v in (cfg.get("ROLE_KEYS") or {}).values())


def _get_team_ai_context():
    """Return (available, account_or_none, error_reason_or_none) for team AI proxy."""
    try:
        import team_auth
    except Exception:
        return False, None, None

    cfg = team_auth.get_config()
    if not cfg.get("configured"):
        return False, None, None

    try:
        acct = team_auth.current_account()
    except team_auth.TeamAuthError as exc:
        if exc.status == 0 or exc.code == "network_error":
            return False, None, "network error"
        if exc.status in (401, 403) or exc.code in ("not_signed_in", "session_expired"):
            return False, None, None
        return False, None, f"HTTP {exc.status}" if exc.status else "request failed"
    except Exception:
        return False, None, None

    if not isinstance(acct, dict) or not acct.get("signed_in") or acct.get("deactivated"):
        return False, acct, None
    user = acct.get("user") or {}
    if not bool(user.get("active", True)):
        return False, acct, None
    return True, acct, None


def get_account_ai_status():
    """Return {mode, today, limit} for the signed-in user or local key state."""
    own_keys = has_local_nim_keys()
    today_out = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    limit_out = {"daily_requests": DEFAULT_DAILY_REQUESTS, "daily_tokens": DEFAULT_DAILY_TOKENS}

    team_ok, acct, _ = _get_team_ai_context()
    if own_keys:
        mode = "own_keys"
    elif team_ok:
        mode = "team"
    else:
        mode = "none"

    if team_ok and isinstance(acct, dict):
        user = acct.get("user") or {}
        uid = str(user.get("id") or "").strip().lower()
        if uid:
            import team_auth

            q_uid = urllib.parse.quote(uid, safe="")
            today_utc = datetime.now(timezone.utc).date().isoformat()
            try:
                usage_rows = team_auth.rest(
                    "GET",
                    f"ai_usage_daily?owner_id=eq.{q_uid}&day=eq.{today_utc}&select=requests,prompt_tokens,completion_tokens&limit=1",
                )
                if isinstance(usage_rows, list) and usage_rows:
                    row = usage_rows[0]
                    today_out = {
                        "requests": int(row.get("requests") or 0),
                        "prompt_tokens": int(row.get("prompt_tokens") or 0),
                        "completion_tokens": int(row.get("completion_tokens") or 0),
                    }
            except team_auth.TeamAuthError:
                pass
            try:
                limit_rows = team_auth.rest(
                    "GET",
                    f"ai_limits?or=(owner_id.eq.{q_uid},owner_id.is.null)&select=owner_id,daily_requests,daily_tokens",
                )
                if isinstance(limit_rows, list) and limit_rows:
                    chosen = None
                    for row in limit_rows:
                        if isinstance(row, dict) and str(row.get("owner_id") or "").lower() == uid:
                            chosen = row
                            break
                    if chosen is None:
                        for row in limit_rows:
                            if isinstance(row, dict) and row.get("owner_id") is None:
                                chosen = row
                                break
                    if chosen is not None:
                        limit_out = {
                            "daily_requests": int(chosen.get("daily_requests") or DEFAULT_DAILY_REQUESTS),
                            "daily_tokens": int(chosen.get("daily_tokens") or DEFAULT_DAILY_TOKENS),
                        }
            except team_auth.TeamAuthError:
                pass

    result = {
        "mode": mode,
        "today": today_out,
        "limit": limit_out,
    }
    try:
        import notifications
        notifications.evaluate_ai_limit(result)
    except Exception:
        pass
    return result


def get_role_model_matrix():
    """Return per-role model & API key readiness metadata (never returning raw secrets)."""
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    pool = key_pool.get_pool()
    pool_count = pool.pool_count()
    global_key = cfg["NVIDIA_NIM_API_KEY"]
    healthy_pool_key = pool.peek_healthy_key() or global_key
    team_ai_ok = False
    if pool_count == 0 and not global_key:
        team_ai_ok, _, _ = _get_team_ai_context()

    catalog_by_key = {}
    matrix = []
    for role_id, spec in ROLE_MODEL_SPEC.items():
        role_key = cfg["ROLE_KEYS"].get(role_id, "")
        effective_key = role_key or global_key
        catalog_key = role_key or healthy_pool_key
        if role_key:
            key_source = "role"
        elif pool_count > 0:
            key_source = f"pool ({pool_count} key)" if pool_count == 1 else f"pool ({pool_count} keys)"
        elif team_ai_ok:
            key_source = "team"
        else:
            key_source = "none"

        if catalog_key:
            if catalog_key not in catalog_by_key:
                catalog_by_key[catalog_key] = model_registry.get_catalog(
                    catalog_key, cfg["NVIDIA_NIM_BASE_URL"]
                )
            catalog = catalog_by_key[catalog_key]
        else:
            catalog = None

        merged_cfg = {**models_cfg, "ROLE_MODEL_OVERRIDES": cfg.get("ROLE_MODEL_OVERRIDES", {})}
        primary, backup, warnings = model_registry.resolve_role_models(role_id, merged_cfg, catalog)
        tier_info = model_registry.tier_settings(role_id, models_cfg)
        tier_name = tier_info["tier"]
        tier_primary = models_cfg.get("tiers", {}).get(tier_name, {}).get("primary", primary)

        matrix.append({
            "role_id": role_id,
            "name": spec["name"],
            "code": spec["code"],
            "model_env": spec["model_env"],
            "key_env": spec["key_env"],
            "model": primary,
            "default_model": tier_primary,
            "primary": primary,
            "backup": backup,
            "warnings": warnings,
            "tier": tier_name,
            "timeout_s": tier_info["timeout_s"],
            "max_tokens": tier_info["max_tokens"],
            "specialization": spec["specialization"],
            "has_key": bool(role_key or pool_count > 0 or team_ai_ok),
            "key_source": key_source,
            "masked_key": _mask_secret(effective_key)
        })
    return matrix


def _strip_thinking_tags(text):
    """Remove <think>...</think> reasoning blocks returned by reasoning models."""
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _parse_tool_calls(raw_tool_calls):
    """Normalize OpenAI/Ollama tool_calls into [{id, name, arguments}]."""
    parsed_calls = []
    if not isinstance(raw_tool_calls, list):
        return parsed_calls
    for idx, tc in enumerate(raw_tool_calls):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        tc_id = tc.get("id") or f"call_{idx}"
        name = fn.get("name") or tc.get("name") or ""
        raw_args = fn.get("arguments") if "arguments" in fn else tc.get("arguments")
        if isinstance(raw_args, dict):
            arguments = dict(raw_args)
        elif isinstance(raw_args, str):
            stripped = raw_args.strip()
            if not stripped:
                arguments = {}
            else:
                try:
                    loaded = json.loads(stripped)
                    arguments = loaded if isinstance(loaded, dict) else {"_raw": raw_args}
                except Exception:
                    arguments = {"_raw": raw_args}
        elif raw_args is None:
            arguments = {}
        else:
            arguments = {"_raw": str(raw_args)}
        parsed_calls.append({"id": str(tc_id), "name": str(name), "arguments": arguments})
    return parsed_calls


def _prepare_messages_for_model(messages, model):
    """Copy messages and drop image_url content parts when model is not a vision model."""
    is_vision = model_registry.is_vision_model(model)
    prepared = []
    dropped_images = False
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        out_msg = dict(msg)
        content = msg.get("content")
        if isinstance(content, list):
            if is_vision:
                out_msg["content"] = [dict(p) if isinstance(p, dict) else p for p in content]
            else:
                kept = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        dropped_images = True
                    else:
                        kept.append(dict(part) if isinstance(part, dict) else part)
                if all(isinstance(p, dict) and p.get("type") == "text" for p in kept):
                    out_msg["content"] = "\n".join(str(p.get("text", "")) for p in kept)
                else:
                    out_msg["content"] = kept
        prepared.append(out_msg)
    warning = f"Dropped image parts for non-vision model '{model}'." if dropped_images else None
    return prepared, warning


def _extract_usage_dict(res):
    """Return {prompt_tokens, completion_tokens} or None if absent."""
    usage = res.get("usage") if isinstance(res, dict) else None
    if not isinstance(usage, dict) or not usage:
        return None
    if "prompt_tokens" not in usage and "completion_tokens" not in usage:
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def _http_post_json(url, payload, headers, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _single_openai_compat_chat_meta(
    url,
    api_key,
    messages,
    model,
    tools=None,
    max_tokens=None,
    timeout=45,
    top_p=None,
    accept_json=False,
    tool_choice=None,
    extra_headers=None,
):
    """Execute one OpenAI-compatible chat/completions call and parse text, tool_calls, and usage."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if accept_json:
        headers["Accept"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

    prepared_messages, img_warning = _prepare_messages_for_model(messages, model)
    payload = {
        "model": model,
        "messages": prepared_messages,
        "temperature": 0.4,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    res = _http_post_json(url, payload, headers, timeout=timeout)
    msg = res["choices"][0]["message"] or {}
    cleaned = _strip_thinking_tags(msg.get("content") or "")
    tool_calls = _parse_tool_calls(msg.get("tool_calls") or [])
    if not cleaned.strip() and not tool_calls:
        raise EmptyModelResponse("model returned no answer (all tokens spent on reasoning)")
    return {
        "text": cleaned,
        "tool_calls": tool_calls,
        "usage": _extract_usage_dict(res),
        "img_warning": img_warning,
    }


def _build_messages_from_prompts(system_prompt, user_prompt, images=None, vision_enabled=True):
    """Build OpenAI chat messages from system_prompt, user_prompt, and optional image paths."""
    has_images = False
    content_parts = [{"type": "text", "text": user_prompt}]
    if images and vision_enabled:
        for img_path in images:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })
                has_images = True

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content_parts if has_images else user_prompt})
    return messages


def _single_nim_request_meta(api_key, base_url, system_prompt, user_prompt, model, images=None, max_tokens=1500, timeout=60):
    url = f"{base_url.rstrip('/')}/chat/completions"
    messages = _build_messages_from_prompts(
        system_prompt,
        user_prompt,
        images=images,
        vision_enabled=model_registry.is_vision_model(model),
    )
    res = _single_openai_compat_chat_meta(
        url=url,
        api_key=api_key,
        messages=messages,
        model=model,
        tools=None,
        max_tokens=max_tokens,
        timeout=timeout,
        top_p=0.9,
        accept_json=True,
    )
    return res["text"], (res["usage"] or {})


def _single_nim_request(api_key, base_url, system_prompt, user_prompt, model, images=None, max_tokens=1500, timeout=60):
    text, _ = _single_nim_request_meta(
        api_key, base_url, system_prompt, user_prompt, model,
        images=images, max_tokens=max_tokens, timeout=timeout
    )
    return text


def _read_http_error_body(err):
    try:
        return err.read().decode("utf-8", errors="ignore")[:300]
    except Exception:
        return ""


def _extract_retry_after(err):
    """Return Retry-After seconds from an HTTPError as float, or None if missing/invalid."""
    headers = getattr(err, "headers", None)
    if headers:
        raw = headers.get("Retry-After")
        if raw is not None:
            try:
                val = float(str(raw).strip())
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass
    return None


def _parse_retry_after(err, default_wait):
    """Extract Retry-After seconds from an HTTPError if valid, capped at 20s."""
    val = _extract_retry_after(err)
    if val is not None:
        return min(val, 20.0)
    return min(float(default_wait), 20.0)


def _build_nim_success(
    model_requested,
    model_used,
    res_meta,
    fallback_reason=None,
    key_label=None,
):
    usage = res_meta.get("usage") or {}
    out = {
        "text": res_meta["text"],
        "tool_calls": res_meta.get("tool_calls") or [],
        "provider": "nvidia_nim",
        "model_requested": model_requested,
        "model_used": model_used,
        "fallback_used": model_used != model_requested,
        "fallback_reason": fallback_reason,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "usage": res_meta.get("usage"),
    }
    if res_meta.get("img_warning"):
        out["img_warning"] = res_meta["img_warning"]
    if key_label is not None:
        out["key_label"] = key_label
    return out


def _run_nim_with_pool(pool, role, model, fallback_model, call_fn):
    """Execute a NIM request callable using KeyPool rotation on 429 and 401/403."""
    api_key, key_label = pool.acquire(role=role, sleep_fn=_sleep)
    fallback_reason = None
    last_error = None
    tried_429_labels = set()
    wait_retries = 0

    while True:
        tried_429_labels.add(key_label)
        try:
            res_meta = call_fn(api_key, model)
            pool.report(key_label, "ok")
            return _build_nim_success(model, model, res_meta, key_label=key_label)
        except urllib.error.HTTPError as e:
            err_body = _read_http_error_body(e)
            last_error = e
            if e.code in (401, 403):
                pool.report(key_label, "auth_failed")
                api_key, key_label = pool.acquire(role=role, sleep_fn=_sleep)
                continue
            if e.code == 400:
                pool.report(key_label, "error")
                raise RuntimeError(
                    f"NVIDIA NIM bad request (HTTP 400) for '{model}': {err_body or e.reason}"
                ) from e
            if e.code == 429:
                raw_retry = _extract_retry_after(e)
                pool.report(key_label, "rate_limited", retry_after=raw_retry)
                next_pair = pool.try_acquire_nowait(role=role, exclude_labels=tried_429_labels)
                if next_pair is not None:
                    api_key, key_label = next_pair
                    continue
                if wait_retries < 2:
                    default_wait = 2 * (2 ** wait_retries)
                    _sleep(_parse_retry_after(e, default_wait))
                    wait_retries += 1
                    maybe = pool.try_acquire_nowait(role=role)
                    if maybe is not None:
                        api_key, key_label = maybe
                    continue
                fallback_reason = f"HTTP 429: {err_body or e.reason}"
                break
            pool.report(key_label, "error")
            if e.code in (404, 410):
                fallback_reason = "model removed from NVIDIA catalog"
                break
            if e.code in (500, 502, 503, 504):
                fallback_reason = f"HTTP {e.code}: {err_body or e.reason}"
                break
            raise RuntimeError(
                f"NVIDIA NIM error (HTTP {e.code}) for '{model}': {err_body or e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            pool.report(key_label, "error")
            last_error = e
            fallback_reason = f"Network/Timeout: {e}"
            break
        except EmptyModelResponse as e:
            pool.report(key_label, "error")
            last_error = e
            fallback_reason = "empty answer"
            break

    if fallback_model and model != fallback_model:
        print(
            f"[ai_gateway] NIM endpoint '{model}' failed ({fallback_reason}). Retrying backup '{fallback_model}'...",
            file=sys.stderr,
        )
        while True:
            try:
                res_meta = call_fn(api_key, fallback_model)
                pool.report(key_label, "ok")
                return _build_nim_success(
                    model, fallback_model, res_meta,
                    fallback_reason=fallback_reason, key_label=key_label,
                )
            except urllib.error.HTTPError as e:
                err_body = _read_http_error_body(e)
                if e.code in (401, 403):
                    pool.report(key_label, "auth_failed")
                    api_key, key_label = pool.acquire(role=role, sleep_fn=_sleep)
                    continue
                if e.code == 429:
                    pool.report(key_label, "rate_limited", retry_after=_extract_retry_after(e))
                else:
                    pool.report(key_label, "error")
                raise RuntimeError(
                    f"NVIDIA NIM backup '{fallback_model}' failed (HTTP {e.code}): {err_body or e.reason}"
                ) from e
            except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
                pool.report(key_label, "error")
                raise RuntimeError(
                    f"NVIDIA NIM backup '{fallback_model}' failed (Network/Timeout): {e}"
                ) from e
            except EmptyModelResponse as e:
                pool.report(key_label, "error")
                raise RuntimeError(
                    f"NVIDIA NIM backup '{fallback_model}' failed (empty answer): {e}"
                ) from e

    raise RuntimeError(f"NVIDIA NIM error for '{model}': {fallback_reason}") from last_error


def _run_nim_single_key(api_key, model, fallback_model, call_fn):
    """Execute a NIM request callable using a single explicit API key with retries and fallback."""
    fallback_reason = None
    last_error = None

    for attempt in range(3):
        try:
            res_meta = call_fn(api_key, model)
            return _build_nim_success(model, model, res_meta)
        except urllib.error.HTTPError as e:
            err_body = _read_http_error_body(e)
            last_error = e
            if e.code in (401, 403):
                raise AIAuthError(
                    f"NVIDIA NIM authentication failed (HTTP {e.code}) for '{model}': {err_body or e.reason}"
                ) from e
            if e.code == 400:
                raise RuntimeError(
                    f"NVIDIA NIM bad request (HTTP 400) for '{model}': {err_body or e.reason}"
                ) from e
            if e.code == 429:
                if attempt < 2:
                    default_wait = 2 * (2 ** attempt)
                    _sleep(_parse_retry_after(e, default_wait))
                    continue
                fallback_reason = f"HTTP 429: {err_body or e.reason}"
                break
            if e.code in (404, 410):
                fallback_reason = "model removed from NVIDIA catalog"
                break
            if e.code in (500, 502, 503, 504):
                fallback_reason = f"HTTP {e.code}: {err_body or e.reason}"
                break
            raise RuntimeError(
                f"NVIDIA NIM error (HTTP {e.code}) for '{model}': {err_body or e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            last_error = e
            fallback_reason = f"Network/Timeout: {e}"
            break
        except EmptyModelResponse as e:
            last_error = e
            fallback_reason = "empty answer"
            break

    if fallback_model and model != fallback_model:
        print(
            f"[ai_gateway] NIM endpoint '{model}' failed ({fallback_reason}). Retrying backup '{fallback_model}'...",
            file=sys.stderr,
        )
        try:
            res_meta = call_fn(api_key, fallback_model)
            return _build_nim_success(
                model, fallback_model, res_meta, fallback_reason=fallback_reason,
            )
        except urllib.error.HTTPError as e:
            err_body = _read_http_error_body(e)
            if e.code in (401, 403):
                raise AIAuthError(
                    f"NVIDIA NIM authentication failed (HTTP {e.code}) for '{fallback_model}': {err_body or e.reason}"
                ) from e
            raise RuntimeError(
                f"NVIDIA NIM backup '{fallback_model}' failed (HTTP {e.code}): {err_body or e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            raise RuntimeError(
                f"NVIDIA NIM backup '{fallback_model}' failed (Network/Timeout): {e}"
            ) from e
        except EmptyModelResponse as e:
            raise RuntimeError(
                f"NVIDIA NIM backup '{fallback_model}' failed (empty answer): {e}"
            ) from e

    raise RuntimeError(f"NVIDIA NIM error for '{model}': {fallback_reason}") from last_error


def _complete_nim_messages(
    api_key,
    base_url,
    messages,
    model,
    tools=None,
    fallback_model=None,
    max_tokens=1500,
    timeout=60,
    role=None,
    pool=None,
):
    """Execute multi-turn NIM completion with optional tools, key-pool rotation, and fallback."""
    url = f"{base_url.rstrip('/')}/chat/completions"

    def call_fn(key_to_use, target_model):
        return _single_openai_compat_chat_meta(
            url=url,
            api_key=key_to_use,
            messages=messages,
            model=target_model,
            tools=tools,
            max_tokens=max_tokens,
            timeout=timeout,
            top_p=0.9,
            accept_json=True,
        )

    if pool is not None or api_key is None:
        return _run_nim_with_pool(
            pool=pool if pool is not None else key_pool.get_pool(),
            role=role,
            model=model,
            fallback_model=fallback_model,
            call_fn=call_fn,
        )
    return _run_nim_single_key(api_key, model, fallback_model, call_fn)


def complete_nvidia_nim(
    api_key,
    base_url,
    system_prompt,
    user_prompt,
    model,
    images=None,
    fallback_model=None,
    max_tokens=1500,
    timeout=60,
    role=None,
    pool=None,
):
    """Execute completion against NVIDIA NIM with key-pool rotation, 429 retries, and backup fallback."""
    messages = _build_messages_from_prompts(
        system_prompt,
        user_prompt,
        images=images,
        vision_enabled=model_registry.is_vision_model(model),
    )
    out = _complete_nim_messages(
        api_key=api_key,
        base_url=base_url,
        messages=messages,
        model=model,
        tools=None,
        fallback_model=fallback_model,
        max_tokens=max_tokens,
        timeout=timeout,
        role=role,
        pool=pool,
    )
    out.pop("img_warning", None)
    return out


def complete_openai_compat(url, api_key, system_prompt, user_prompt, model, images=None, provider_name="openai"):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    content_parts = [{"type": "text", "text": user_prompt}]
    has_images = False
    if images and provider_name == "openai":
        for img_path in images:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                has_images = True

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content_parts if has_images else user_prompt})

    payload = {"model": model, "messages": messages, "temperature": 0.4}
    res = _http_post_json(url, payload, headers, timeout=45)
    return res["choices"][0]["message"]["content"].strip()


def complete_anthropic(api_key, system_prompt, user_prompt, model="claude-sonnet-5-5"):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "max_tokens": 1500,
        "system": system_prompt or "You are a helpful PR and marketing assistant.",
        "messages": [{"role": "user", "content": user_prompt}]
    }
    res = _http_post_json(url, payload, headers, timeout=45)
    return res["content"][0]["text"].strip()


def complete_gemini(api_key, system_prompt, user_prompt, model="gemini-1.5-flash"):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": f"System Directive:\n{system_prompt}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})
    payload = {"contents": contents}
    res = _http_post_json(url, payload, headers, timeout=45)
    return res["candidates"][0]["content"]["parts"][0]["text"].strip()


def complete_ollama(base_url, system_prompt, user_prompt, model="llama3.2"):
    url = f"{base_url.rstrip('/')}/api/chat"
    headers = {"Content-Type": "application/json"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {"model": model, "messages": messages, "stream": False}
    res = _http_post_json(url, payload, headers, timeout=20)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(str(res["error"]))
    return res.get("message", {}).get("content", "").strip()


def _is_port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ollama_reachable(base_url):
    """Return True if Ollama's host and port accept a TCP connection within 0.5s."""
    if not base_url or not isinstance(base_url, str):
        return False
    raw = base_url.strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname
        port = parsed.port or 11434
    except ValueError:
        return False
    if not host:
        return False
    return _is_port_open(host, port, timeout=0.5)


def complete_chatgpt_cli(system_prompt, user_prompt):
    """Attempt prompt via local ChatGPT CLI only when Chrome CDP port 9222 is reachable."""
    if not _is_port_open("127.0.0.1", 9222, timeout=0.5):
        return None

    full_prompt = f"{system_prompt}\n\nTask:\n{user_prompt}" if system_prompt else user_prompt
    if len(full_prompt) > 1800:
        full_prompt = full_prompt[:1800] + "\n[Context truncated]"

    try:
        res = subprocess.run(["chatgpt", "ask", full_prompt], capture_output=True, text=True, timeout=15)
        out = res.stdout.strip()
        if "[ChatGPT]" in out:
            out = out.split("[ChatGPT]", 1)[-1].strip()
        if out:
            return out
    except Exception:
        pass
    return None


def _short_error_reason(exc):
    """Return a concise, secret-free error summary for an exception."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "HTTP 429 rate limit"
        return f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "network timeout"
    if isinstance(exc, urllib.error.URLError):
        reason_str = str(getattr(exc, "reason", exc)).lower()
        if "timed out" in reason_str or "timeout" in reason_str:
            return "network timeout"
        return "network error"

    msg = str(exc or "")
    low = msg.lower()
    if "http 429" in low or "(429)" in low:
        return "HTTP 429 rate limit"
    m = re.search(r"\bHTTP\s+(\d{3})\b", msg)
    if m:
        return f"HTTP {m.group(1)}"
    low_without_prefix = low.replace("network/timeout", "")
    if "timed out" in low_without_prefix or "timeout" in low_without_prefix:
        return "network timeout"
    if "network" in low or "urlerror" in low or "connection" in low:
        return "network error"
    if "removed from nvidia catalog" in low or "not found" in low:
        return "model not found"
    if "empty answer" in low or "returned no answer" in low:
        return "empty answer"
    return "request failed"


def _build_not_connected_message(has_nim_key, failures, max_len=300):
    """Build a concise AINotConnected message from per-provider failure reasons."""
    if has_nim_key and "nvidia_nim" in failures:
        nim_reason = failures["nvidia_nim"]
        prefix = f"NVIDIA NIM couldn't answer right now ({nim_reason}). No other AI provider answered"
        suffix = ". Try again in a minute."
        sec_items = [(p, r) for p, r in failures.items() if p != "nvidia_nim"]
        if not sec_items:
            msg = prefix + suffix
        else:
            sec_str = ", ".join(f"{p}: {r}" for p, r in sec_items)
            candidate = f"{prefix} ({sec_str}){suffix}"
            if len(candidate) <= max_len:
                msg = candidate
            else:
                budget = max_len - len(prefix) - len(suffix) - len(" (...)")
                if budget > 8:
                    msg = f"{prefix} ({sec_str[:budget].rstrip(', ')}...){suffix}"
                else:
                    msg = prefix + suffix
        return msg[:max_len]

    if not has_nim_key and failures:
        sec_str = ", ".join(f"{p}: {r}" for p, r in failures.items())
        prefix = "AI is not connected ("
        suffix = "): add an NVIDIA NIM key in Settings (or start Ollama for local AI)."
        candidate = f"{prefix}{sec_str}{suffix}"
        if len(candidate) <= max_len:
            return candidate
        budget = max_len - len(prefix) - len(suffix) - 3
        if budget > 8:
            return f"{prefix}{sec_str[:budget].rstrip(', ')}...{suffix}"
        return "AI is not connected: add an NVIDIA NIM key in Settings (or start Ollama for local AI)."

    return "AI is not connected: add an NVIDIA NIM key in Settings (or start Ollama for local AI)."


def _record_run(
    role,
    provider,
    model_requested,
    model_used,
    fallback_used,
    fallback_reason,
    latency_ms,
    prompt_tokens=None,
    completion_tokens=None,
    ok=True,
    error=None,
):
    """Best-effort DB logging of gateway calls; never raises or stores prompts/keys."""
    try:
        db.log_run(
            role=role,
            provider=provider,
            model_requested=model_requested,
            model_used=model_used,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ok=ok,
            error=error,
        )
    except Exception:
        pass


def _messages_to_prompts(messages):
    """Collapse a messages list into (system_prompt, user_prompt) for non-tool legacy providers."""
    sys_parts = []
    user_parts = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, list):
            text_val = "\n".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text_val = str(content or "")
        if role == "system":
            if text_val:
                sys_parts.append(text_val)
        elif text_val:
            user_parts.append(text_val)
    return "\n\n".join(sys_parts), "\n\n".join(user_parts)


def _single_ollama_chat_meta(base_url, messages, model, tools=None, timeout=20):
    """Execute Ollama /api/chat with messages and optional tools, falling back without tools if unsupported."""
    url = f"{base_url.rstrip('/')}/api/chat"
    headers = {"Content-Type": "application/json"}
    prepared_messages, img_warning = _prepare_messages_for_model(messages, model)
    warnings = [img_warning] if img_warning else []

    def _post(include_tools):
        payload = {"model": model, "messages": prepared_messages, "stream": False}
        if include_tools and tools:
            payload["tools"] = tools
        res = _http_post_json(url, payload, headers, timeout=timeout)
        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(str(res["error"]))
        return res

    used_tools = bool(tools)
    try:
        res = _post(include_tools=used_tools)
    except urllib.error.HTTPError as e:
        if used_tools and e.code == 400:
            res = _post(include_tools=False)
            used_tools = False
            warnings.append(f"Ollama model '{model}' does not support tools; called without tools.")
        else:
            raise
    except Exception as e:
        if used_tools and ("tool" in str(e).lower() or "support" in str(e).lower()):
            res = _post(include_tools=False)
            used_tools = False
            warnings.append(f"Ollama model '{model}' does not support tools; called without tools.")
        else:
            raise

    msg = res.get("message") if isinstance(res, dict) else {}
    if not isinstance(msg, dict):
        msg = {}
    cleaned = _strip_thinking_tags(msg.get("content") or "")
    tool_calls = _parse_tool_calls(msg.get("tool_calls") or []) if used_tools else []
    if not cleaned.strip() and not tool_calls:
        raise AINotConnected("Ollama couldn't answer: empty response")

    usage = None
    if isinstance(res, dict) and ("prompt_eval_count" in res or "eval_count" in res):
        usage = {
            "prompt_tokens": res.get("prompt_eval_count"),
            "completion_tokens": res.get("eval_count"),
        }

    return {
        "text": cleaned,
        "tool_calls": tool_calls,
        "provider": "ollama",
        "model_requested": model,
        "model_used": f"ollama/{model}",
        "fallback_used": False,
        "fallback_reason": None,
        "warnings": warnings,
        "usage": usage,
    }


def _complete_confidential_ollama(base_url, ollama_model, messages, tools=None):
    """Run confidential completion/chat against local Ollama, mapping errors to AINotConnected."""
    if not base_url or not _ollama_reachable(base_url):
        raise AINotConnected(
            "This is confidential work and needs local AI (Ollama), which is not running."
        )
    try:
        return _single_ollama_chat_meta(
            base_url=base_url,
            messages=messages,
            model=ollama_model,
            tools=tools,
        )
    except AINotConnected:
        raise
    except urllib.error.HTTPError as e:
        err_body = _read_http_error_body(e)
        if e.code == 404 or "not found" in err_body.lower():
            raise AINotConnected(
                f"Ollama is running but the model '{ollama_model}' isn't installed. Run: ollama pull {ollama_model}"
            ) from e
        raise AINotConnected(
            f"Ollama couldn't answer: {_short_error_reason(e)}"
        ) from e
    except Exception as e:
        if "not found" in str(e).lower():
            raise AINotConnected(
                f"Ollama is running but the model '{ollama_model}' isn't installed. Run: ollama pull {ollama_model}"
            ) from e
        raise AINotConnected(
            f"Ollama couldn't answer: {_short_error_reason(e)}"
        ) from e


def _parse_proxy_error(err):
    """Extract (message, reason) from an ai-proxy HTTPError JSON or text body."""
    raw = ""
    try:
        raw = err.read().decode("utf-8", errors="ignore")
    except Exception:
        pass
    msg = ""
    reason = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("error"), str):
                    msg = parsed["error"].strip()
                elif isinstance(parsed.get("message"), str):
                    msg = parsed["message"].strip()
                if isinstance(parsed.get("reason"), str):
                    reason = parsed["reason"].strip()
        except Exception:
            msg = raw.strip()[:300]
    if not msg:
        msg = str(getattr(err, "reason", "") or f"HTTP {err.code}").strip()
    return msg[:300], reason


def _complete_team_proxy_messages(
    messages,
    model,
    tools=None,
    tool_choice=None,
    fallback_model=None,
    max_tokens=1500,
    timeout=TEAM_PROXY_TIMEOUT_S,
):
    """Execute completion through the Supabase ai-proxy Edge Function."""
    import team_auth

    cfg = team_auth.get_config()
    if not cfg.get("configured"):
        raise AIAuthError(TEAM_AUTH_REQUIRED_MSG)
    try:
        token = team_auth.get_access_token()
    except team_auth.TeamAuthError as exc:
        if exc.status in (401, 403) or exc.code in ("not_signed_in", "session_expired", "not_configured"):
            raise AIAuthError(TEAM_AUTH_REQUIRED_MSG) from exc
        raise

    url = f"{cfg['url']}/functions/v1/ai-proxy"
    extra_headers = {"apikey": cfg["publishable_key"]}
    effective_timeout = max(TEAM_PROXY_TIMEOUT_S, int(timeout or TEAM_PROXY_TIMEOUT_S))

    def _call(target_model):
        return _single_openai_compat_chat_meta(
            url=url,
            api_key=token,
            messages=messages,
            model=target_model,
            tools=tools,
            max_tokens=max_tokens,
            timeout=effective_timeout,
            top_p=0.9,
            accept_json=True,
            tool_choice=tool_choice,
            extra_headers=extra_headers,
        )

    def _build_success(model_used, res_meta, fallback_reason=None):
        usage = res_meta.get("usage") or {}
        out = {
            "text": res_meta["text"],
            "tool_calls": res_meta.get("tool_calls") or [],
            "provider": "team",
            "model_requested": model,
            "model_used": model_used,
            "fallback_used": model_used != model,
            "fallback_reason": fallback_reason,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "usage": res_meta.get("usage"),
        }
        if res_meta.get("img_warning"):
            out["img_warning"] = res_meta["img_warning"]
        return out

    def _map_http_error(err, target_model):
        err_msg, reason = _parse_proxy_error(err)
        if err.code in (401, 403):
            raise AIAuthError(TEAM_AUTH_REQUIRED_MSG) from err
        if err.code == 429:
            is_quota = (
                reason in ("daily_requests_limit", "daily_tokens_limit")
                or "daily" in err_msg.lower()
                or "limit" in err_msg.lower()
            )
            if is_quota:
                try:
                    import notifications
                    notifications.notify_ai_limit_100(err_msg)
                except Exception:
                    pass
                raise AINotConnected(err_msg) from err
            return f"HTTP 429: {err_msg}"
        if err.code == 503:
            low = err_msg.lower()
            if "no shared ai keys" in low or "no team keys" in low:
                raise AINotConnected(TEAM_NO_KEYS_MSG) from err
            raise AINotConnected(err_msg) from err
        if err.code == 400:
            raise RuntimeError(
                f"Team AI proxy bad request (HTTP 400) for '{target_model}': {err_msg}"
            ) from err
        if err.code in (404, 410, 500, 502, 504):
            return f"HTTP {err.code}: {err_msg}"
        raise RuntimeError(
            f"Team AI proxy error (HTTP {err.code}) for '{target_model}': {err_msg}"
        ) from err

    fallback_reason = None
    last_error = None
    try:
        res_meta = _call(model)
        return _build_success(model, res_meta)
    except urllib.error.HTTPError as e:
        last_error = e
        fallback_reason = _map_http_error(e, model)
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise
    except EmptyModelResponse as e:
        last_error = e
        fallback_reason = "empty answer"

    if fallback_model and fallback_model != model:
        try:
            res_meta = _call(fallback_model)
            return _build_success(fallback_model, res_meta, fallback_reason=fallback_reason)
        except urllib.error.HTTPError as e:
            _map_http_error(e, fallback_model)
            raise RuntimeError(
                f"Team AI proxy backup '{fallback_model}' failed (HTTP {e.code})"
            ) from e
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise
        except EmptyModelResponse as e:
            raise RuntimeError(
                f"Team AI proxy backup '{fallback_model}' failed (empty answer): {e}"
            ) from e

    raise RuntimeError(f"Team AI proxy error for '{model}': {fallback_reason}") from last_error


def _dispatch_non_confidential_messages(
    cfg,
    models_cfg,
    ollama_model,
    requested_model,
    backup_model,
    warnings,
    nim_key,
    role_key,
    effective_timeout,
    effective_max_tokens,
    messages,
    tools=None,
    tool_choice=None,
    model_tier="fast",
    image_paths=None,
    force_live_only=False,
    pool=None,
    allow_image_warning=False,
    use_team_proxy=False,
    team_context_error=None,
):
    """Run provider cascade for non-confidential chat/completions with optional tools."""
    failures = {}
    has_nim = bool(nim_key) or (pool is not None and pool.has_keys(role=role_key))
    base_warnings = list(warnings or [])
    has_tools = bool(tools)

    if has_nim:
        try:
            result = _complete_nim_messages(
                api_key=nim_key,
                base_url=cfg["NVIDIA_NIM_BASE_URL"],
                messages=messages,
                model=requested_model,
                tools=tools if has_tools else None,
                fallback_model=backup_model,
                max_tokens=effective_max_tokens,
                timeout=effective_timeout,
                role=role_key,
                pool=pool,
            )
            img_warn = result.pop("img_warning", None)
            out_warnings = list(base_warnings)
            if allow_image_warning and img_warn and img_warn not in out_warnings:
                out_warnings.append(img_warn)
            result["warnings"] = out_warnings
            return result
        except (AIAuthError, AINotConnected):
            raise
        except Exception as e:
            failures["nvidia_nim"] = _short_error_reason(e)
            print(f"[ai_gateway] NVIDIA NIM failed for role '{role_key}': {e}", file=sys.stderr)
            if force_live_only:
                raise
    elif use_team_proxy:
        try:
            result = _complete_team_proxy_messages(
                messages=messages,
                model=requested_model,
                tools=tools if has_tools else None,
                tool_choice=tool_choice,
                fallback_model=backup_model,
                max_tokens=effective_max_tokens,
                timeout=TEAM_PROXY_TIMEOUT_S,
            )
            img_warn = result.pop("img_warning", None)
            out_warnings = list(base_warnings)
            if allow_image_warning and img_warn and img_warn not in out_warnings:
                out_warnings.append(img_warn)
            result["warnings"] = out_warnings
            return result
        except (AIAuthError, AINotConnected):
            raise
        except Exception as e:
            failures["team"] = _short_error_reason(e)
            print(f"[ai_gateway] Team AI proxy failed for role '{role_key}': {e}", file=sys.stderr)
            if force_live_only:
                raise
    elif team_context_error:
        failures["team"] = team_context_error

    sec_cfg = models_cfg.get("secondary_providers", {})
    system_prompt, user_prompt = _messages_to_prompts(messages)

    if cfg["OPENAI_API_KEY"]:
        oai_cfg = sec_cfg.get("openai", {})
        oai_model = oai_cfg.get("fast", "gpt-4o-mini") if model_tier == "fast" else oai_cfg.get("reasoning", "gpt-4o")
        try:
            if has_tools or not image_paths:
                res_meta = _single_openai_compat_chat_meta(
                    "https://api.openai.com/v1/chat/completions",
                    cfg["OPENAI_API_KEY"],
                    messages,
                    model=oai_model,
                    tools=tools if has_tools else None,
                    max_tokens=effective_max_tokens,
                    timeout=effective_timeout,
                )
                out_warnings = list(base_warnings)
                if allow_image_warning and res_meta.get("img_warning"):
                    out_warnings.append(res_meta["img_warning"])
                return {
                    "text": res_meta["text"],
                    "tool_calls": res_meta["tool_calls"],
                    "provider": "openai",
                    "model_requested": oai_model,
                    "model_used": oai_model,
                    "fallback_used": True,
                    "fallback_reason": "Secondary provider OpenAI",
                    "warnings": out_warnings,
                    "usage": res_meta["usage"],
                }
            txt = complete_openai_compat(
                "https://api.openai.com/v1/chat/completions",
                cfg["OPENAI_API_KEY"], system_prompt, user_prompt,
                model=oai_model, images=image_paths, provider_name="openai"
            )
            return {
                "text": txt, "tool_calls": [], "provider": "openai",
                "model_requested": oai_model, "model_used": oai_model,
                "fallback_used": True, "fallback_reason": "Secondary provider OpenAI",
                "warnings": base_warnings, "usage": None,
            }
        except Exception as e:
            failures["openai"] = _short_error_reason(e)
            print(f"[ai_gateway] OpenAI failed: {e}", file=sys.stderr)

    if not has_tools and cfg["ANTHROPIC_API_KEY"]:
        ant_model = sec_cfg.get("anthropic", {}).get("default", "claude-sonnet-5-5")
        try:
            txt = complete_anthropic(cfg["ANTHROPIC_API_KEY"], system_prompt, user_prompt, model=ant_model)
            return {
                "text": txt, "tool_calls": [], "provider": "anthropic",
                "model_requested": ant_model, "model_used": ant_model,
                "fallback_used": True, "fallback_reason": "Secondary provider Anthropic",
                "warnings": base_warnings, "usage": None,
            }
        except Exception as e:
            failures["anthropic"] = _short_error_reason(e)
            print(f"[ai_gateway] Anthropic failed: {e}", file=sys.stderr)

    if not has_tools and cfg["GEMINI_API_KEY"]:
        gem_model = sec_cfg.get("gemini", {}).get("default", "gemini-1.5-flash")
        try:
            txt = complete_gemini(cfg["GEMINI_API_KEY"], system_prompt, user_prompt, model=gem_model)
            return {
                "text": txt, "tool_calls": [], "provider": "gemini",
                "model_requested": gem_model, "model_used": gem_model,
                "fallback_used": True, "fallback_reason": "Secondary provider Gemini",
                "warnings": base_warnings, "usage": None,
            }
        except Exception as e:
            failures["gemini"] = _short_error_reason(e)
            print(f"[ai_gateway] Gemini failed: {e}", file=sys.stderr)

    if cfg["GROQ_API_KEY"]:
        groq_model = sec_cfg.get("groq", {}).get("default", "llama-3.3-70b-versatile")
        try:
            res_meta = _single_openai_compat_chat_meta(
                "https://api.groq.com/openai/v1/chat/completions",
                cfg["GROQ_API_KEY"],
                messages,
                model=groq_model,
                tools=tools if has_tools else None,
                max_tokens=effective_max_tokens,
                timeout=effective_timeout,
            )
            out_warnings = list(base_warnings)
            if allow_image_warning and res_meta.get("img_warning"):
                out_warnings.append(res_meta["img_warning"])
            return {
                "text": res_meta["text"],
                "tool_calls": res_meta["tool_calls"],
                "provider": "groq",
                "model_requested": groq_model,
                "model_used": groq_model,
                "fallback_used": True,
                "fallback_reason": "Secondary provider Groq",
                "warnings": out_warnings,
                "usage": res_meta["usage"],
            }
        except Exception as e:
            failures["groq"] = _short_error_reason(e)
            print(f"[ai_gateway] Groq failed: {e}", file=sys.stderr)

    if not has_tools and not force_live_only and cfg.get("USE_CHATGPT_CLI_FALLBACK"):
        cli_resp = complete_chatgpt_cli(system_prompt, user_prompt)
        if cli_resp:
            return {
                "text": cli_resp, "tool_calls": [], "provider": "chatgpt_cli",
                "model_requested": requested_model, "model_used": "chatgpt_cli",
                "fallback_used": True, "fallback_reason": "Browser ChatGPT CLI session",
                "warnings": base_warnings, "usage": None,
            }

    if not has_tools and not force_live_only and cfg["OLLAMA_BASE_URL"] and _ollama_reachable(cfg["OLLAMA_BASE_URL"]):
        try:
            txt = complete_ollama(
                cfg["OLLAMA_BASE_URL"], system_prompt, user_prompt, model=ollama_model
            )
            if txt:
                return {
                    "text": txt, "tool_calls": [], "provider": "ollama",
                    "model_requested": ollama_model, "model_used": f"ollama/{ollama_model}",
                    "fallback_used": True, "fallback_reason": "Local Ollama fallback",
                    "warnings": base_warnings, "usage": None,
                }
            failures["ollama"] = "empty response"
        except Exception as e:
            failures["ollama"] = _short_error_reason(e)

    raise AINotConnected(_build_not_connected_message(bool(nim_key), failures))


def _execute_gateway_messages(
    messages,
    tools=None,
    tool_choice=None,
    model_tier="fast",
    image_paths=None,
    agent_role="",
    force_live_only=False,
    confidential=False,
    max_tokens=None,
    allow_image_warning=False,
    tier=None,
    model=None,
):
    """Shared gateway execution for confidential Ollama and non-confidential provider cascades."""
    t0 = time.monotonic()
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    ollama_model = models_cfg.get("local", {}).get("ollama_model", "llama3.2")
    role_key = (agent_role or "").lower().strip()

    if confidential:
        base_url = cfg["OLLAMA_BASE_URL"]
        try:
            result = _complete_confidential_ollama(
                base_url=base_url,
                ollama_model=ollama_model,
                messages=messages,
                tools=tools,
            )
        except Exception as e:
            _record_run(
                role=role_key,
                provider="ollama",
                model_requested=ollama_model,
                model_used=None,
                fallback_used=False,
                fallback_reason=None,
                latency_ms=int((time.monotonic() - t0) * 1000),
                ok=False,
                error=str(e)[:300] if isinstance(e, AINotConnected) else _short_error_reason(e),
            )
            raise

        usage = result.get("usage") or {}
        _record_run(
            role=role_key,
            provider=result["provider"],
            model_requested=result["model_requested"],
            model_used=result["model_used"],
            fallback_used=False,
            fallback_reason=None,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            ok=True,
        )
        return result

    pool = key_pool.get_pool()
    has_nim = pool.has_keys(role=role_key)
    nim_key = pool.peek_healthy_key(role=role_key) if has_nim else (cfg["ROLE_KEYS"].get(role_key) or cfg["NVIDIA_NIM_API_KEY"])
    use_team_proxy = False
    team_context_error = None
    if not has_nim and not nim_key:
        use_team_proxy, _, team_context_error = _get_team_ai_context()

    explicit_model = str(model).strip() if model else ""
    if explicit_model:
        requested_model = explicit_model
        backup_model = None
        warnings = []
    else:
        requested_model, backup_model, warnings = _resolve_for_role(role_key, cfg, models_cfg, nim_key, tier=tier)
    tier_info = model_registry.tier_settings(role_key, models_cfg, tier_override=tier)
    effective_timeout = tier_info["timeout_s"]
    effective_max_tokens = tier_info["max_tokens"] if max_tokens is None else int(max_tokens)
    if has_nim or nim_key:
        default_provider = "nvidia_nim"
    elif use_team_proxy or team_context_error:
        default_provider = "team"
    else:
        default_provider = cfg["DEFAULT_PROVIDER"]

    try:
        result = _dispatch_non_confidential_messages(
            cfg=cfg,
            models_cfg=models_cfg,
            ollama_model=ollama_model,
            requested_model=requested_model,
            backup_model=backup_model,
            warnings=warnings,
            nim_key=nim_key,
            role_key=role_key,
            effective_timeout=effective_timeout,
            effective_max_tokens=effective_max_tokens,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            model_tier=model_tier,
            image_paths=image_paths,
            force_live_only=force_live_only,
            pool=pool if has_nim else None,
            allow_image_warning=allow_image_warning,
            use_team_proxy=use_team_proxy,
            team_context_error=team_context_error,
        )
    except Exception as e:
        err_msg = str(e)[:300] if isinstance(e, AINotConnected) else _short_error_reason(e)
        _record_run(
            role=role_key,
            provider=default_provider,
            model_requested=requested_model,
            model_used=None,
            fallback_used=False,
            fallback_reason=None,
            latency_ms=int((time.monotonic() - t0) * 1000),
            ok=False,
            error=err_msg,
        )
        raise

    usage = result.get("usage") or {}
    prompt_tokens = result.get("prompt_tokens") if result.get("prompt_tokens") is not None else usage.get("prompt_tokens")
    completion_tokens = result.get("completion_tokens") if result.get("completion_tokens") is not None else usage.get("completion_tokens")
    _record_run(
        role=role_key,
        provider=result.get("provider"),
        model_requested=result.get("model_requested"),
        model_used=result.get("model_used"),
        fallback_used=bool(result.get("fallback_used")),
        fallback_reason=result.get("fallback_reason"),
        latency_ms=int((time.monotonic() - t0) * 1000),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        ok=True,
    )
    return result


def chat_with_tools(
    messages,
    tools=None,
    agent_role="",
    confidential=False,
    max_tokens=None,
    model=None,
    tool_choice=None,
):
    """Multi-turn chat completion supporting OpenAI-compatible tools, vision filtering, and confidential Ollama."""
    res = _execute_gateway_messages(
        messages=messages,
        tools=list(tools) if tools else None,
        tool_choice=tool_choice,
        agent_role=agent_role,
        confidential=confidential,
        max_tokens=max_tokens,
        allow_image_warning=True,
        model=model,
    )
    return {
        "text": res.get("text", ""),
        "tool_calls": res.get("tool_calls") or [],
        "provider": res.get("provider"),
        "model_requested": res.get("model_requested"),
        "model_used": res.get("model_used"),
        "fallback_used": bool(res.get("fallback_used")),
        "fallback_reason": res.get("fallback_reason"),
        "warnings": list(res.get("warnings") or []),
        "usage": res.get("usage"),
    }


def generate_completion_with_meta(
    system_prompt,
    user_prompt,
    model_tier="fast",
    image_paths=None,
    agent_role="",
    force_live_only=False,
    confidential=False,
    max_tokens=None,
    tier=None,
    model=None,
):
    """Unified entry point returning generated text plus full model & fallback telemetry."""
    messages = _build_messages_from_prompts(
        system_prompt,
        user_prompt,
        images=image_paths,
        vision_enabled=True,
    )
    return _execute_gateway_messages(
        messages=messages,
        tools=None,
        model_tier=model_tier,
        image_paths=image_paths,
        agent_role=agent_role,
        force_live_only=force_live_only,
        confidential=confidential,
        max_tokens=max_tokens,
        allow_image_warning=False,
        tier=tier,
        model=model,
    )


def generate_completion(system_prompt, user_prompt, model_tier="fast", image_paths=None, agent_role="", confidential=False, max_tokens=None, tier=None, model=None):
    """Backward-compatible wrapper returning completion string only."""
    return generate_completion_with_meta(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_tier=model_tier,
        image_paths=image_paths,
        agent_role=agent_role,
        confidential=confidential,
        max_tokens=max_tokens,
        tier=tier,
        model=model,
    )["text"]


def test_role_endpoint(role_id):
    """Run a live diagnostic inference check for a given role against its configured NIM/provider endpoint."""
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    role_key = (role_id or "cmo").lower().strip()
    if role_key not in ROLE_MODEL_SPEC:
        return {"success": False, "role_id": role_key, "error": f"Unknown role '{role_key}'"}

    spec = ROLE_MODEL_SPEC[role_key]
    pool = key_pool.get_pool()
    has_nim = pool.has_keys(role=role_key)
    nim_key = pool.peek_healthy_key(role=role_key) if has_nim else (cfg["ROLE_KEYS"].get(role_key) or cfg["NVIDIA_NIM_API_KEY"])
    primary, backup, warnings = _resolve_for_role(role_key, cfg, models_cfg, nim_key)
    team_ok = False
    if not has_nim and not nim_key:
        team_ok, _, _ = _get_team_ai_context()

    if not has_nim and not nim_key and not team_ok and not (cfg["OPENAI_API_KEY"] or cfg["ANTHROPIC_API_KEY"] or cfg["GEMINI_API_KEY"] or cfg["GROQ_API_KEY"]):
        return {
            "success": False,
            "role_id": role_key,
            "primary": primary,
            "backup": backup,
            "warnings": warnings,
            "model_requested": primary,
            "model_used": None,
            "error_code": "ai_not_connected",
            "error": "No API key found in .env (set NVIDIA_NIM_API_KEY or role-specific key)."
        }

    t0 = time.time()
    try:
        res = generate_completion_with_meta(
            system_prompt=f"You are {spec['name']} at MAGNM. Reply cleanly in one short sentence without emojis.",
            user_prompt="Diagnostic ping: confirm readiness.",
            agent_role=role_key,
            force_live_only=True
        )
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "success": True,
            "role_id": role_key,
            "provider": res.get("provider"),
            "primary": primary,
            "backup": backup,
            "warnings": res.get("warnings", warnings),
            "model_requested": res.get("model_requested"),
            "model_used": res.get("model_used"),
            "fallback_used": res.get("fallback_used", False),
            "fallback_reason": res.get("fallback_reason"),
            "latency_ms": latency_ms,
            "sample_reply": res.get("text", "")[:220]
        }
    except AIAuthError as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "success": False,
            "role_id": role_key,
            "primary": primary,
            "backup": backup,
            "warnings": warnings,
            "model_requested": primary,
            "latency_ms": latency_ms,
            "error_code": "ai_auth_error",
            "error": str(e)
        }
    except AINotConnected as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "success": False,
            "role_id": role_key,
            "primary": primary,
            "backup": backup,
            "warnings": warnings,
            "model_requested": primary,
            "latency_ms": latency_ms,
            "error_code": "ai_not_connected",
            "error": str(e)
        }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "success": False,
            "role_id": role_key,
            "primary": primary,
            "backup": backup,
            "warnings": warnings,
            "model_requested": primary,
            "latency_ms": latency_ms,
            "error_code": "ai_error",
            "error": str(e)
        }


if __name__ == "__main__":
    matrix = get_role_model_matrix()
    print(f"AI Gateway Ready. Roles configured: {len(matrix)}")
    for item in matrix:
        warn_suffix = f" [WARN: {'; '.join(item['warnings'])}]" if item.get("warnings") else ""
        print(f"  [{item['code']}] {item['role_id']:15} -> {item['primary']} (backup={item['backup']}, key={item['key_source']}){warn_suffix}")
