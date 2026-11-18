#!/usr/bin/env python3
"""Model registry, NVIDIA NIM catalog caching, and role model resolution."""

from datetime import datetime, timedelta, timezone
import json
import os
import sys
import threading
import urllib.request

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

ROOT = paths.DATA_ROOT
MODELS_CONFIG_PATH = paths.data("config", "models.json")
CATALOG_CACHE_PATH = paths.data("state", "model_catalog.json")

_catalog_state = threading.local()


def get_last_catalog_error():
    """Return the last exception raised during get_catalog on this thread, if any."""
    return getattr(_catalog_state, "last_error", None)


def load_models_config(config_path=None):
    """Load and return the model configuration from config/models.json."""
    path = config_path or MODELS_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_cached_catalog(cache_file):
    """Return (fetched_at_utc, set_of_ids) from cache_file, or (None, None)."""
    if not cache_file or not os.path.exists(cache_file):
        return None, None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_raw = data.get("fetched_at")
        models = data.get("models")
        if not isinstance(models, list):
            return None, None
        fetched_dt = None
        if isinstance(fetched_raw, str) and fetched_raw.strip():
            fetched_dt = datetime.fromisoformat(fetched_raw.strip())
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
        return fetched_dt, {str(m).strip() for m in models if m}
    except Exception:
        return None, None


def _write_cached_catalog(cache_file, model_ids):
    """Atomically write fetched catalog model IDs and timestamp to cache_file."""
    parent = os.path.dirname(cache_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "models": sorted(model_ids),
    }
    tmp_path = cache_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, cache_file)


def get_catalog(api_key=None, base_url=None, force=False, cache_path=None, ttl_hours=None, timeout=10):
    """Return the set of available NIM model IDs, using a TTL cache in state/.

    Returns the cached set if fresh, or refetches from {base_url}/models.
    When api_key is None, selects any healthy key from key_pool.
    On any network or HTTP error, returns the existing cached set if present, else None.
    """
    _catalog_state.last_error = None
    cache_file = cache_path or CATALOG_CACHE_PATH
    if ttl_hours is None:
        try:
            ttl_hours = load_models_config().get("catalog_ttl_hours", 24)
        except Exception:
            ttl_hours = 24

    fetched_dt, cached_models = _read_cached_catalog(cache_file)
    if not force and cached_models is not None and fetched_dt is not None:
        age = datetime.now(timezone.utc) - fetched_dt
        if timedelta(0) <= age < timedelta(hours=float(ttl_hours)):
            return cached_models

    if api_key is None:
        try:
            import key_pool
            api_key = key_pool.get_pool().peek_healthy_key()
        except Exception:
            api_key = ""

    if not api_key:
        return cached_models

    if not base_url:
        try:
            base_url = load_models_config().get("nim_base_url", "https://integrate.api.nvidia.com/v1")
        except Exception:
            base_url = "https://integrate.api.nvidia.com/v1"

    url = f"{base_url.rstrip('/')}/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        items = []
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []
        elif isinstance(payload, list):
            items = payload
        model_ids = set()
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                model_ids.add(str(item["id"]).strip())
            elif isinstance(item, str) and item.strip():
                model_ids.add(item.strip())
        _write_cached_catalog(cache_file, model_ids)
        return model_ids
    except Exception as exc:
        _catalog_state.last_error = exc
        return cached_models


def _role_env_name(role_key):
    """Return the NIM_MODEL_<ROLE> environment variable name for a role ID."""
    if not role_key:
        return ""
    return f"NIM_MODEL_{role_key.upper().replace('-', '_')}"


def resolve_role_models(role, cfg=None, catalog=None, tier_override=None):
    """Resolve (primary, backup, warnings) for a role using models.json and optional catalog.

    Env override NIM_MODEL_<ROLE> is used only when catalog is None or contains it.
    If the tier primary is missing from a known catalog, falls back to the tier backup
    with backup=None and warns.
    """
    if cfg is None:
        models_cfg = load_models_config()
        overrides = {}
    elif isinstance(cfg, dict) and "tiers" in cfg and "roles" in cfg:
        models_cfg = cfg
        overrides = cfg.get("ROLE_MODEL_OVERRIDES", {})
    else:
        models_cfg = load_models_config()
        overrides = cfg.get("ROLE_MODEL_OVERRIDES", {}) if isinstance(cfg, dict) else {}

    role_key = (role or "").lower().strip()
    tier_name = (tier_override or "").strip() or models_cfg.get("roles", {}).get(role_key, "fast")
    tiers = models_cfg.get("tiers", {})
    tier_spec = tiers.get(tier_name) or tiers.get("fast") or {}
    tier_primary = tier_spec.get("primary", "")
    tier_backup = tier_spec.get("backup", "")

    env_var = _role_env_name(role_key)
    if role_key in overrides:
        override = (overrides.get(role_key) or "").strip()
    elif env_var:
        override = (os.environ.get(env_var) or "").strip()
    else:
        override = ""

    warnings = []
    effective_primary = tier_primary
    effective_backup = tier_backup

    if catalog is not None and tier_primary and tier_primary not in catalog:
        if tier_backup in catalog:
            warnings.append(
                f"Tier '{tier_name}' primary '{tier_primary}' is not in NVIDIA's catalog; using {tier_backup}"
            )
            effective_primary = tier_backup
            effective_backup = None
        else:
            warnings.append(
                f"Tier '{tier_name}' primary '{tier_primary}' is not in NVIDIA's catalog; backup '{tier_backup}' also not in catalog"
            )

    if override:
        if catalog is None or override in catalog:
            backup_for_override = None if override == tier_backup else tier_backup
            return override, backup_for_override, warnings
        warnings.insert(
            0,
            f"{env_var}={override} is not in NVIDIA's catalog; using {effective_primary}",
        )

    return effective_primary, effective_backup, warnings


DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_TOKENS = 1500


def _coerce_positive_int(val, default):
    if val is None or val == "":
        return default
    try:
        parsed = int(val)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def tier_settings(role, cfg=None, tier_override=None):
    """Return {"tier": name, "timeout_s": int, "max_tokens": int} for a role or explicit tier."""
    models_cfg = cfg if (isinstance(cfg, dict) and "tiers" in cfg) else load_models_config()
    role_key = (role or "").lower().strip()
    tier_name = (tier_override or "").strip() or models_cfg.get("roles", {}).get(role_key, "fast")
    tiers = models_cfg.get("tiers", {})
    tier_spec = tiers.get(tier_name)
    if not isinstance(tier_spec, dict):
        tier_spec = tiers.get("fast")
        if not isinstance(tier_spec, dict):
            tier_spec = {}
    return {
        "tier": tier_name,
        "timeout_s": _coerce_positive_int(tier_spec.get("timeout_s"), DEFAULT_TIMEOUT_S),
        "max_tokens": _coerce_positive_int(tier_spec.get("max_tokens"), DEFAULT_MAX_TOKENS),
    }


def is_vision_model(model_id, cfg=None):
    """Return True if model_id is listed in vision_models from config/models.json."""
    if not model_id:
        return False
    models_cfg = cfg if (isinstance(cfg, dict) and "vision_models" in cfg) else load_models_config()
    vision_set = {str(m).strip().lower() for m in models_cfg.get("vision_models", [])}
    return str(model_id).strip().lower() in vision_set
