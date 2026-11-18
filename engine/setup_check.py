#!/usr/bin/env python3
"""First-run environment and dependency setup diagnostics."""

from concurrent.futures import ThreadPoolExecutor, wait
import inspect
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import ai_gateway
import model_registry
import paths

CHECK_TIMEOUT_S = 3.0


def _make_check(cid, label, status, detail, fix=""):
    return {
        "id": cid,
        "label": label,
        "status": status,
        "detail": detail,
        "fix": fix,
    }


def _check_python():
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if (ver.major, ver.minor) >= (3, 9):
        return _make_check("python", "Python Runtime", "ok", f"Python {ver_str}")
    return _make_check(
        "python",
        "Python Runtime",
        "missing",
        f"Python {ver_str} (< 3.9)",
        "Install Python 3.9 or newer",
    )


def _check_disk():
    target = paths.DATA_ROOT
    while target and not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    if not target or not os.path.exists(target):
        target = os.path.abspath(os.sep)

    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024 ** 3)
    detail = f"{free_gb:.1f} GB free in {paths.DATA_ROOT}"
    if free_gb < 1.0:
        return _make_check("disk", "Disk Space", "missing", detail, "Free at least 1 GB of disk space")
    if free_gb < 5.0:
        return _make_check("disk", "Disk Space", "warn", detail, "Free at least 5 GB for media renders")
    return _make_check("disk", "Disk Space", "ok", detail)


def _check_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return _make_check("ffmpeg", "FFmpeg", "ok", found)
    return _make_check(
        "ffmpeg",
        "FFmpeg",
        "missing",
        "ffmpeg not found on PATH (needed for video)",
        "Install ffmpeg and add it to PATH",
    )


def _playwright_cache_dirs():
    candidates = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(os.path.join(local_app_data, "ms-playwright"))
    return candidates


def _check_browser():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return _make_check("browser", "Chromium / Playwright", "ok", found)

    for cache_dir in _playwright_cache_dirs():
        if os.path.isdir(cache_dir):
            try:
                if os.listdir(cache_dir):
                    return _make_check("browser", "Chromium / Playwright", "ok", cache_dir)
            except OSError:
                pass

    return _make_check(
        "browser",
        "Chromium / Playwright",
        "missing",
        "No Chromium-family browser or Playwright cache found (needed for research and video)",
        "Install Chrome/Chromium or run: npx playwright install chromium",
    )


def _check_node():
    found = shutil.which("node")
    if found:
        return _make_check("node", "Node.js", "ok", found)
    return _make_check(
        "node",
        "Node.js",
        "missing",
        "node not found on PATH (needed for HyperFrames video)",
        "Install Node.js LTS and add it to PATH",
    )


def _check_git():
    found = shutil.which("git")
    if found:
        return _make_check("git", "Git", "ok", found)
    return _make_check(
        "git",
        "Git",
        "missing",
        "git not found on PATH (needed for repo sync)",
        "Install Git from git-scm.com",
    )


def _ollama_model_matches(installed_names, wanted):
    if not wanted:
        return bool(installed_names)
    if ":" in wanted:
        return wanted in installed_names
    return any(name == wanted or name.split(":", 1)[0] == wanted for name in installed_names)


def _check_ollama():
    cfg = ai_gateway.get_config()
    ollama_url = (cfg.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    try:
        models_cfg = model_registry.load_models_config()
        wanted_model = models_cfg.get("local", {}).get("ollama_model", "llama3.2")
    except Exception:
        wanted_model = "llama3.2"

    if not ai_gateway._ollama_reachable(ollama_url):
        return _make_check(
            "ollama",
            "Ollama Local AI",
            "warn",
            f"Ollama not reachable at {ollama_url}",
            "Start Ollama (ollama serve) or use NVIDIA NIM",
        )

    tags_url = f"{ollama_url}/api/tags"
    req = urllib.request.Request(tags_url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = payload.get("models") if isinstance(payload, dict) else []
        names = set()
        for item in models or []:
            if isinstance(item, dict):
                n = item.get("name") or item.get("model")
                if n:
                    names.add(str(n).strip())
            elif isinstance(item, str) and item.strip():
                names.add(item.strip())
    except Exception:
        return _make_check(
            "ollama",
            "Ollama Local AI",
            "warn",
            f"Ollama reachable at {ollama_url}, but failed to query /api/tags",
            f"ollama pull {wanted_model}",
        )

    if _ollama_model_matches(names, wanted_model):
        return _make_check(
            "ollama",
            "Ollama Local AI",
            "ok",
            f"Reachable; model {wanted_model} installed",
        )

    return _make_check(
        "ollama",
        "Ollama Local AI",
        "warn",
        f"Reachable, but model '{wanted_model}' is not installed",
        f"ollama pull {wanted_model}",
    )


def _call_get_catalog(api_key, base_url):
    fn = model_registry.get_catalog
    kwargs = {}
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        if "force" in params:
            kwargs["force"] = True
        if "timeout" in params:
            kwargs["timeout"] = 2.5
    except (TypeError, ValueError):
        pass
    return fn(api_key, base_url, **kwargs)


def _check_nvidia_key():
    cfg = ai_gateway.get_config()
    api_key = (cfg.get("NVIDIA_NIM_API_KEY") or "").strip()
    if not api_key:
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "missing",
            "No NVIDIA NIM API key configured",
            "Add NVIDIA_NIM_API_KEY in Settings > Keys or .env",
        )

    base_url = cfg.get("NVIDIA_NIM_BASE_URL") or ai_gateway.DEFAULT_NIM_BASE_URL
    if hasattr(model_registry, "_catalog_state"):
        model_registry._catalog_state.last_error = None

    try:
        catalog = _call_get_catalog(api_key, base_url)
    except ai_gateway.AIAuthError:
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "warn",
            "key rejected",
            "Verify NVIDIA_NIM_API_KEY in Settings > Keys or .env",
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return _make_check(
                "nvidia_key",
                "NVIDIA NIM API Key",
                "warn",
                "key rejected",
                "Verify NVIDIA_NIM_API_KEY in Settings > Keys or .env",
            )
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "warn",
            "offline",
            "Check internet connectivity to integrate.api.nvidia.com",
        )
    except Exception:
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "warn",
            "offline",
            "Check internet connectivity to integrate.api.nvidia.com",
        )

    last_err = (
        model_registry.get_last_catalog_error()
        if hasattr(model_registry, "get_last_catalog_error")
        else None
    )
    if last_err is not None:
        if isinstance(last_err, urllib.error.HTTPError) and last_err.code in (401, 403):
            return _make_check(
                "nvidia_key",
                "NVIDIA NIM API Key",
                "warn",
                "key rejected",
                "Verify NVIDIA_NIM_API_KEY in Settings > Keys or .env",
            )
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "warn",
            "offline",
            "Check internet connectivity to integrate.api.nvidia.com",
        )

    if catalog is None:
        return _make_check(
            "nvidia_key",
            "NVIDIA NIM API Key",
            "warn",
            "offline",
            "Check internet connectivity to integrate.api.nvidia.com",
        )

    return _make_check(
        "nvidia_key",
        "NVIDIA NIM API Key",
        "ok",
        f"Key configured; catalog reachable ({len(catalog)} models)",
    )


def run_setup_checks(timeout_s=CHECK_TIMEOUT_S):
    """Run all first-run setup checks in parallel with a bounded per-check timeout."""
    specs = [
        ("python", "Python Runtime", _check_python),
        ("disk", "Disk Space", _check_disk),
        ("ffmpeg", "FFmpeg", _check_ffmpeg),
        ("browser", "Chromium / Playwright", _check_browser),
        ("node", "Node.js", _check_node),
        ("git", "Git", _check_git),
        ("ollama", "Ollama Local AI", _check_ollama),
        ("nvidia_key", "NVIDIA NIM API Key", _check_nvidia_key),
    ]

    pool = ThreadPoolExecutor(max_workers=len(specs))
    futures = {pool.submit(fn): (cid, label) for cid, label, fn in specs}
    done, not_done = wait(futures.keys(), timeout=timeout_s)

    results = {}
    for fut in done:
        cid, label = futures[fut]
        try:
            results[cid] = fut.result()
        except Exception as exc:
            results[cid] = _make_check(cid, label, "warn", f"Check error: {exc}", "Retry setup check")

    for fut in not_done:
        cid, label = futures[fut]
        fut.cancel()
        results[cid] = _make_check(
            cid,
            label,
            "warn",
            f"Timed out after {timeout_s:.0f}s",
            "Retry setup check",
        )

    pool.shutdown(wait=False)

    ordered = [results[cid] for cid, _, _ in specs]
    ok = all(item.get("status") != "missing" for item in ordered)
    return {"checks": ordered, "ok": ok}
