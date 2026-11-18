#!/usr/bin/env python3
"""Magnm Central AI Gateway
Unified multi-model inference adapter supporting:
- NVIDIA NIM (per-role keys & catalog-verified tier models with backup fallback)
- OpenAI, Anthropic, Google Gemini, Groq, Ollama
- Local ChatGPT CLI (opt-in via AI_USE_CHATGPT_CLI_FALLBACK with fast CDP port 9222 check)
"""

import base64
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

import model_registry
import paths

ROOT = paths.DATA_ROOT
ENV_PATH = paths.data(".env")
DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

_sleep = time.sleep


class AINotConnected(RuntimeError):
    """Raised when no configured AI provider or local Ollama instance can answer."""


class AIAuthError(RuntimeError):
    """Raised when an AI provider rejects authentication credentials (HTTP 401/403)."""


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
        "tier": "research",
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

    global_nim_key = get_val("NVIDIA_NIM_API_KEY") or get_val("NVIDIA_API_KEY")
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


def _resolve_for_role(role_id, cfg, models_cfg, nim_key):
    """Fetch catalog (if key present) and resolve primary, backup, warnings for role_id."""
    catalog = model_registry.get_catalog(nim_key, cfg["NVIDIA_NIM_BASE_URL"]) if nim_key else None
    merged_cfg = {**models_cfg, "ROLE_MODEL_OVERRIDES": cfg.get("ROLE_MODEL_OVERRIDES", {})}
    return model_registry.resolve_role_models(role_id, merged_cfg, catalog)


def get_role_model_matrix():
    """Return per-role model & API key readiness metadata (never returning raw secrets)."""
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    global_key = cfg["NVIDIA_NIM_API_KEY"]

    catalog_by_key = {}
    matrix = []
    for role_id, spec in ROLE_MODEL_SPEC.items():
        role_key = cfg["ROLE_KEYS"].get(role_id, "")
        effective_key = role_key or global_key
        key_source = "role" if role_key else ("global" if global_key else "none")

        if effective_key:
            if effective_key not in catalog_by_key:
                catalog_by_key[effective_key] = model_registry.get_catalog(
                    effective_key, cfg["NVIDIA_NIM_BASE_URL"]
                )
            catalog = catalog_by_key[effective_key]
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
            "has_key": bool(effective_key),
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


def _http_post_json(url, payload, headers, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _single_nim_request(api_key, base_url, system_prompt, user_prompt, model, images=None, max_tokens=1500, timeout=60):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    has_images = False
    content_parts = [{"type": "text", "text": user_prompt}]
    if images and model_registry.is_vision_model(model):
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

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": max_tokens
    }
    res = _http_post_json(url, payload, headers, timeout=timeout)
    raw = res["choices"][0]["message"].get("content")
    cleaned = _strip_thinking_tags(raw)
    if not cleaned or not cleaned.strip():
        raise EmptyModelResponse("model returned no answer (all tokens spent on reasoning)")
    return cleaned


def _read_http_error_body(err):
    try:
        return err.read().decode("utf-8", errors="ignore")[:300]
    except Exception:
        return ""


def _parse_retry_after(err, default_wait):
    """Extract Retry-After seconds from an HTTPError if valid, capped at 20s."""
    headers = getattr(err, "headers", None)
    if headers:
        raw = headers.get("Retry-After")
        if raw is not None:
            try:
                val = float(str(raw).strip())
                if val >= 0:
                    return min(val, 20.0)
            except (ValueError, TypeError):
                pass
    return min(float(default_wait), 20.0)


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
):
    """Execute completion against NVIDIA NIM with 429 retries and backup model fallback."""
    fallback_reason = None
    last_error = None

    for attempt in range(3):
        try:
            text = _single_nim_request(
                api_key, base_url, system_prompt, user_prompt, model,
                images=images, max_tokens=max_tokens, timeout=timeout
            )
            return {
                "text": text,
                "provider": "nvidia_nim",
                "model_requested": model,
                "model_used": model,
                "fallback_used": False,
                "fallback_reason": None
            }
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
                    wait_s = _parse_retry_after(e, default_wait)
                    _sleep(wait_s)
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
            text = _single_nim_request(
                api_key, base_url, system_prompt, user_prompt, fallback_model,
                images=images, max_tokens=max_tokens, timeout=timeout
            )
            return {
                "text": text,
                "provider": "nvidia_nim",
                "model_requested": model,
                "model_used": fallback_model,
                "fallback_used": True,
                "fallback_reason": fallback_reason
            }
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


def generate_completion_with_meta(
    system_prompt,
    user_prompt,
    model_tier="fast",
    image_paths=None,
    agent_role="",
    force_live_only=False,
    confidential=False,
    max_tokens=None,
):
    """Unified entry point returning generated text plus full model & fallback telemetry."""
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    ollama_model = models_cfg.get("local", {}).get("ollama_model", "llama3.2")

    # Confidential mode: local Ollama only, never call NIM or any cloud provider
    if confidential:
        base_url = cfg["OLLAMA_BASE_URL"]
        if not base_url or not _ollama_reachable(base_url):
            raise AINotConnected(
                "This is confidential work and needs local AI (Ollama), which is not running."
            )
        try:
            txt = complete_ollama(
                base_url, system_prompt, user_prompt, model=ollama_model
            )
            if txt:
                return {
                    "text": txt,
                    "provider": "ollama",
                    "model_requested": ollama_model,
                    "model_used": f"ollama/{ollama_model}",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "warnings": [],
                }
            raise AINotConnected("Ollama couldn't answer: empty response")
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

    role_key = (agent_role or "").lower().strip()
    nim_key = cfg["ROLE_KEYS"].get(role_key) or cfg["NVIDIA_NIM_API_KEY"]
    requested_model, backup_model, warnings = _resolve_for_role(role_key, cfg, models_cfg, nim_key)
    tier_info = model_registry.tier_settings(role_key)
    effective_timeout = tier_info["timeout_s"]
    effective_max_tokens = tier_info["max_tokens"] if max_tokens is None else int(max_tokens)
    failures = {}

    # 1. Primary Provider: NVIDIA NIM
    if nim_key:
        try:
            result = complete_nvidia_nim(
                api_key=nim_key,
                base_url=cfg["NVIDIA_NIM_BASE_URL"],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=requested_model,
                images=image_paths,
                fallback_model=backup_model,
                max_tokens=effective_max_tokens,
                timeout=effective_timeout,
            )
            result["warnings"] = warnings
            return result
        except AIAuthError:
            raise
        except Exception as e:
            failures["nvidia_nim"] = _short_error_reason(e)
            print(f"[ai_gateway] NVIDIA NIM failed for role '{role_key}': {e}", file=sys.stderr)
            if force_live_only:
                raise

    # 2. Secondary API Providers (OpenAI, Anthropic, Gemini, Groq)
    sec_cfg = models_cfg.get("secondary_providers", {})

    if cfg["OPENAI_API_KEY"]:
        oai_cfg = sec_cfg.get("openai", {})
        oai_model = oai_cfg.get("fast", "gpt-4o-mini") if model_tier == "fast" else oai_cfg.get("reasoning", "gpt-4o")
        try:
            txt = complete_openai_compat(
                "https://api.openai.com/v1/chat/completions",
                cfg["OPENAI_API_KEY"], system_prompt, user_prompt,
                model=oai_model, images=image_paths, provider_name="openai"
            )
            return {
                "text": txt, "provider": "openai",
                "model_requested": oai_model, "model_used": oai_model,
                "fallback_used": True, "fallback_reason": "Secondary provider OpenAI",
                "warnings": warnings,
            }
        except Exception as e:
            failures["openai"] = _short_error_reason(e)
            print(f"[ai_gateway] OpenAI failed: {e}", file=sys.stderr)

    if cfg["ANTHROPIC_API_KEY"]:
        ant_model = sec_cfg.get("anthropic", {}).get("default", "claude-sonnet-5-5")
        try:
            txt = complete_anthropic(cfg["ANTHROPIC_API_KEY"], system_prompt, user_prompt, model=ant_model)
            return {
                "text": txt, "provider": "anthropic",
                "model_requested": ant_model, "model_used": ant_model,
                "fallback_used": True, "fallback_reason": "Secondary provider Anthropic",
                "warnings": warnings,
            }
        except Exception as e:
            failures["anthropic"] = _short_error_reason(e)
            print(f"[ai_gateway] Anthropic failed: {e}", file=sys.stderr)

    if cfg["GEMINI_API_KEY"]:
        gem_model = sec_cfg.get("gemini", {}).get("default", "gemini-1.5-flash")
        try:
            txt = complete_gemini(cfg["GEMINI_API_KEY"], system_prompt, user_prompt, model=gem_model)
            return {
                "text": txt, "provider": "gemini",
                "model_requested": gem_model, "model_used": gem_model,
                "fallback_used": True, "fallback_reason": "Secondary provider Gemini",
                "warnings": warnings,
            }
        except Exception as e:
            failures["gemini"] = _short_error_reason(e)
            print(f"[ai_gateway] Gemini failed: {e}", file=sys.stderr)

    if cfg["GROQ_API_KEY"]:
        groq_model = sec_cfg.get("groq", {}).get("default", "llama-3.3-70b-versatile")
        try:
            txt = complete_openai_compat(
                "https://api.groq.com/openai/v1/chat/completions",
                cfg["GROQ_API_KEY"], system_prompt, user_prompt,
                model=groq_model, provider_name="groq"
            )
            return {
                "text": txt, "provider": "groq",
                "model_requested": groq_model, "model_used": groq_model,
                "fallback_used": True, "fallback_reason": "Secondary provider Groq",
                "warnings": warnings,
            }
        except Exception as e:
            failures["groq"] = _short_error_reason(e)
            print(f"[ai_gateway] Groq failed: {e}", file=sys.stderr)

    # 3. ChatGPT CLI (opt-in only via AI_USE_CHATGPT_CLI_FALLBACK=true)
    if not force_live_only and cfg.get("USE_CHATGPT_CLI_FALLBACK"):
        cli_resp = complete_chatgpt_cli(system_prompt, user_prompt)
        if cli_resp:
            return {
                "text": cli_resp, "provider": "chatgpt_cli",
                "model_requested": requested_model, "model_used": "chatgpt_cli",
                "fallback_used": True, "fallback_reason": "Browser ChatGPT CLI session",
                "warnings": warnings,
            }

    # 4. Ollama local server (if reachable)
    if not force_live_only and cfg["OLLAMA_BASE_URL"] and _ollama_reachable(cfg["OLLAMA_BASE_URL"]):
        try:
            txt = complete_ollama(
                cfg["OLLAMA_BASE_URL"], system_prompt, user_prompt, model=ollama_model
            )
            if txt:
                return {
                    "text": txt, "provider": "ollama",
                    "model_requested": ollama_model, "model_used": f"ollama/{ollama_model}",
                    "fallback_used": True, "fallback_reason": "Local Ollama fallback",
                    "warnings": warnings,
                }
            failures["ollama"] = "empty response"
        except Exception as e:
            failures["ollama"] = _short_error_reason(e)

    raise AINotConnected(_build_not_connected_message(bool(nim_key), failures))


def generate_completion(system_prompt, user_prompt, model_tier="fast", image_paths=None, agent_role="", confidential=False, max_tokens=None):
    """Backward-compatible wrapper returning completion string only."""
    return generate_completion_with_meta(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_tier=model_tier,
        image_paths=image_paths,
        agent_role=agent_role,
        confidential=confidential,
        max_tokens=max_tokens,
    )["text"]


def test_role_endpoint(role_id):
    """Run a live diagnostic inference check for a given role against its configured NIM/provider endpoint."""
    cfg = get_config()
    models_cfg = model_registry.load_models_config()
    role_key = (role_id or "cmo").lower().strip()
    if role_key not in ROLE_MODEL_SPEC:
        return {"success": False, "role_id": role_key, "error": f"Unknown role '{role_key}'"}

    spec = ROLE_MODEL_SPEC[role_key]
    nim_key = cfg["ROLE_KEYS"].get(role_key) or cfg["NVIDIA_NIM_API_KEY"]
    primary, backup, warnings = _resolve_for_role(role_key, cfg, models_cfg, nim_key)

    if not nim_key and not (cfg["OPENAI_API_KEY"] or cfg["ANTHROPIC_API_KEY"] or cfg["GEMINI_API_KEY"] or cfg["GROQ_API_KEY"]):
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
