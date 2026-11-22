#!/usr/bin/env python3
"""Connected coding agent CLIs: detection, confined workspace execution, approval gate, logs, and stop."""

from datetime import datetime, timezone
import fnmatch
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import agents
import assets_manager
import db
import events
import keepawake
import paths
import product_rules
import repos

DETECT_CACHE_TTL_S = 600.0
DETECT_TIMEOUT_S = 10.0
DEFAULT_RUN_TIMEOUT_S = 30 * 60
CANCEL_KILL_GRACE_S = 5.0
MAX_LOG_BYTES = 20 * 1024 * 1024
SUMMARY_MAX_CHARS = 4000
LOG_BATCH_INTERVAL_S = 0.1
LOG_BATCH_MAX_LINES = 20

VALID_MODES = ("read_only", "write_out")

CONFINED_ENGINE_ID = "claude"
CONFINED_VENDOR_BY_ENGINE = {
    "claude": "Anthropic",
    "codex": "OpenAI",
    "agy": "Google",
    "gemini": "Google",
}

CLAUDE_REQUIRED_FLAGS = (
    "--restricted",
    "--strict-mcp-config",
    "--tools",
    "--permission-mode",
)

CLAUDE_UPDATE_MESSAGE = "Update Claude Code to use it here"
UNCONFINED_TOOL_MESSAGE = "Found, not enabled: this tool can't yet be confined to a workspace"

ALLOWED_SAVE_EXTENSIONS = frozenset({".md", ".txt", ".json", ".csv", ".png", ".jpg", ".webp"})

KNOWN_CLIS = (
    {
        "id": "claude",
        "name": "Claude Code",
        "command": "claude",
        "vendor": "Anthropic",
        "install_command": "npm install -g @anthropic-ai/claude-code",
    },
    {
        "id": "codex",
        "name": "Codex",
        "command": "codex",
        "vendor": "OpenAI",
        "install_command": "npm install -g @openai/codex",
    },
    {
        "id": "agy",
        "name": "Antigravity",
        "command": "agy",
        "vendor": "Google",
        "install_command": "Install Antigravity and enable the agy CLI in PATH",
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "command": "gemini",
        "vendor": "Google",
        "install_command": "npm install -g @google/gemini-cli",
    },
)

KNOWN_CLI_IDS = tuple(item["id"] for item in KNOWN_CLIS)
_ACP_ADAPTER_BINARIES = ("claude-code-acp", "codex-acp", "agy-acp")

_ALLOWED_ENV_EXACT = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TERM",
    "TMPDIR",
})

_WINDOWS_ALLOWED_ENV_EXACT = frozenset({
    "USERPROFILE",
    "USERNAME",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
})

_SECRET_PREFIXES = (
    "NVIDIA_",
    "SUPABASE_",
    "MAGNM_",
    "OPENAI_",
    "ANTHROPIC_",
    "GEMINI_",
    "GOOGLE_",
)
_SECRET_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
)

_SKIP_DIR_NAMES = frozenset({".git", "node_modules", ".venv", ".ssh", ".aws", ".gnupg", ".kube", ".docker"})
_KEY_FILE_PATTERNS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.jks",
    "id_*",
    "credentials*",
    "service-account*.json",
    "*session*.json",
    "*.env",
    "*.pfx",
    "*.keystore",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "secrets.*",
)
_DATA_ROOT_EXCLUDED_PREFIXES = (
    "state",
    "queue",
    "logs",
    "assets/uploads",
    "assets/results",
    "outputs",
)

_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)

_detect_lock = threading.Lock()
_detect_cache = {"ts": 0.0, "data": None, "adapters": None}

_workers_lock = threading.Lock()
_engine_workers = {}
_engine_wake_events = {}
_active_runs_lock = threading.Lock()
_active_runs = {}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def strip_ansi(text):
    """Remove ANSI/VT escape sequences and normalize carriage returns."""
    if not text:
        return ""
    cleaned = _ANSI_RE.sub("", str(text))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned


def is_secret_env_name(name):
    """Return True if an environment variable name matches Magnm or AI provider secrets."""
    upper = str(name or "").strip().upper()
    if not upper:
        return False
    if any(upper.startswith(p) for p in _SECRET_PREFIXES):
        return True
    if any(upper.endswith(s) for s in _SECRET_SUFFIXES):
        return True
    return False


def build_clean_env(base_env=None):
    """Build a strict allowlisted environment for child CLI processes."""
    source = os.environ if base_env is None else base_env
    out = {}
    for key, val in source.items():
        if not isinstance(key, str):
            continue
        is_allowed = (
            key in _ALLOWED_ENV_EXACT
            or key.startswith("LC_")
            or (os.name == "nt" and key.upper() in _WINDOWS_ALLOWED_ENV_EXACT)
        )
        if not is_allowed:
            continue
        if is_secret_env_name(key):
            continue
        out[key] = val

    out.setdefault("LANG", "C.UTF-8")
    out.setdefault("TERM", "xterm-256color")
    return out


def clear_detect_cache():
    """Clear the cached CLI and adapter detection results."""
    with _detect_lock:
        _detect_cache["ts"] = 0.0
        _detect_cache["data"] = None
        _detect_cache["adapters"] = None


def _run_probe(cmd, timeout_s=None):
    """Run a quick CLI probe (--version or --help) without a shell and with allowlisted env."""
    if timeout_s is None:
        timeout_s = DETECT_TIMEOUT_S
    try:
        res = subprocess.run(
            list(cmd),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout_s,
            env=build_clean_env(),
        )
        combined = "\n".join(p for p in (res.stdout, res.stderr) if p)
        return res.returncode, strip_ansi(combined)
    except (subprocess.TimeoutExpired, OSError):
        return None, ""


def _parse_version_text(text):
    for line in (text or "").splitlines():
        clean = line.strip()
        if clean:
            return clean[:200]
    return None


def _help_has_flag(help_text, flag):
    if not help_text:
        return False
    pat = re.compile(r"(?:^|[\s,\[|(\"\'])" + re.escape(flag) + r"(?=$|[\s,=\]|)\"\'])")
    return bool(pat.search(help_text))


def _claude_supports_confinement(help_text):
    return all(_help_has_flag(help_text, flag) for flag in CLAUDE_REQUIRED_FLAGS)


def _detect_single_cli(spec):
    cli_id = spec["id"]
    cli_cmd = spec["command"]
    cli_path = shutil.which(cli_cmd)
    if not cli_path:
        return {
            "id": cli_id,
            "name": spec["name"],
            "command": cli_cmd,
            "vendor": spec.get("vendor"),
            "installed": False,
            "path": None,
            "version": None,
            "enabled": False,
            "confined": False,
            "reason": None,
            "acp": False,
            "install_command": spec["install_command"],
        }

    rc_ver, ver_out = _run_probe([cli_path, "--version"])
    version = _parse_version_text(ver_out) if rc_ver is not None else None

    if cli_id == CONFINED_ENGINE_ID:
        _, help_out = _run_probe([cli_path, "--help"])
        confined = _claude_supports_confinement(help_out)
        return {
            "id": cli_id,
            "name": spec["name"],
            "command": cli_cmd,
            "vendor": spec.get("vendor"),
            "installed": True,
            "path": cli_path,
            "version": version,
            "enabled": bool(confined),
            "confined": bool(confined),
            "reason": None if confined else CLAUDE_UPDATE_MESSAGE,
            "acp": False,
            "install_command": spec["install_command"],
        }

    return {
        "id": cli_id,
        "name": spec["name"],
        "command": cli_cmd,
        "vendor": spec.get("vendor"),
        "installed": True,
        "path": cli_path,
        "version": version,
        "enabled": False,
        "confined": False,
        "reason": UNCONFINED_TOOL_MESSAGE,
        "acp": False,
        "install_command": spec["install_command"],
    }


def _detect_adapters():
    found = []
    for adapter in _ACP_ADAPTER_BINARIES:
        adapter_path = shutil.which(adapter)
        if adapter_path:
            found.append(
                {
                    "id": adapter,
                    "name": adapter,
                    "command": adapter,
                    "installed": True,
                    "path": adapter_path,
                    "version": None,
                    "enabled": False,
                    "confined": False,
                    "reason": UNCONFINED_TOOL_MESSAGE,
                    "acp": True,
                }
            )
    return found


def detect(refresh=False):
    """Detect installed coding CLIs and confinement capability; cached for 10 minutes."""
    now = time.monotonic()
    with _detect_lock:
        cached = _detect_cache.get("data")
        if not refresh and cached is not None and (now - _detect_cache["ts"]) < DETECT_CACHE_TTL_S:
            return {k: dict(v) for k, v in cached.items()}

    results = {}
    for spec in KNOWN_CLIS:
        results[spec["id"]] = _detect_single_cli(spec)
    adapters = _detect_adapters()

    with _detect_lock:
        _detect_cache["ts"] = time.monotonic()
        _detect_cache["data"] = {k: dict(v) for k, v in results.items()}
        _detect_cache["adapters"] = [dict(a) for a in adapters]
    return results


def list_detected_agents(refresh=False):
    """Return detected coding CLIs as an ordered list."""
    detected = detect(refresh=refresh)
    return [dict(detected[cli_id]) for cli_id in KNOWN_CLI_IDS]


def list_detected_adapters(refresh=False):
    """Return detected ACP adapter binaries (all disabled because unconfined)."""
    detect(refresh=refresh)
    with _detect_lock:
        return [dict(a) for a in (_detect_cache.get("adapters") or ())]


def get_run_timeout_s():
    """Return the configured per-run timeout in seconds (default 1800 / 30 min)."""
    raw = db.get_meta("coding_run_timeout_s")
    if raw is not None:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return float(DEFAULT_RUN_TIMEOUT_S)


def set_run_timeout_s(timeout_s):
    """Persist per-run timeout in seconds in the local settings store."""
    val = float(timeout_s)
    if val <= 0:
        raise ValueError("timeout_s must be positive")
    db.set_meta("coding_run_timeout_s", str(val))
    return val


def get_agent_engines():
    """Return a dict mapping agent_id -> configured coding CLI id (or 'ai' when unset)."""
    raw = db.get_meta("agent_engines")
    stored = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                stored = parsed
        except Exception:
            stored = {}

    out = {}
    for ag in agents.load_agents():
        aid = ag["id"]
        val = str(stored.get(aid) or "").strip().lower()
        out[aid] = val if val == CONFINED_ENGINE_ID else "ai"
    return out


def get_agent_engine(agent_id):
    """Return configured engine for agent_id ('claude' or 'ai')."""
    if not agent_id:
        return "ai"
    ag = agents.get_agent(agent_id)
    canon = ag["id"] if ag else str(agent_id).strip().lower()
    return get_agent_engines().get(canon, "ai")


def set_agent_engine(agent_id, engine):
    """Store per-agent engine selection in local SQLite meta (confined engines only)."""
    ag = agents.get_agent(agent_id)
    if ag is None:
        raise ValueError(f"Unknown agent '{agent_id}'")
    canon = ag["id"]
    eng = str(engine or "").strip().lower()
    if eng in ("", "ai", "default", "nim", "nvidia_nim", "ollama"):
        target = "ai"
    elif eng == CONFINED_ENGINE_ID:
        cli_info = detect().get(eng) or {}
        if not cli_info.get("installed"):
            raise ValueError(f"Coding CLI '{eng}' is not installed on PATH")
        if not cli_info.get("enabled"):
            raise ValueError(cli_info.get("reason") or CLAUDE_UPDATE_MESSAGE)
        target = eng
    elif eng in KNOWN_CLI_IDS:
        raise ValueError(UNCONFINED_TOOL_MESSAGE)
    else:
        raise ValueError(f"Unknown engine '{engine}'")

    current = {}
    raw = db.get_meta("agent_engines")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                current = dict(parsed)
        except Exception:
            current = {}

    if target == "ai":
        current.pop(canon, None)
    else:
        current[canon] = target

    db.set_meta("agent_engines", json.dumps(current, ensure_ascii=False))
    return {"agent_id": canon, "engine": target}


def _require_confined_cli(engine):
    eng = str(engine or "").strip().lower()
    if eng not in KNOWN_CLI_IDS:
        raise ValueError(f"Unknown coding engine '{engine}'")
    if eng != CONFINED_ENGINE_ID:
        raise ValueError(UNCONFINED_TOOL_MESSAGE)
    cli_info = detect().get(eng)
    if not cli_info or not cli_info.get("installed") or not cli_info.get("path"):
        raise ValueError(f"Coding CLI '{eng}' is not installed on PATH")
    if not cli_info.get("enabled"):
        raise ValueError(cli_info.get("reason") or CLAUDE_UPDATE_MESSAGE)
    return cli_info


def build_headless_command(engine, cli_path, mode="read_only"):
    """Build exact confined Claude Code CLI argument list (shell=False; prompt via stdin)."""
    eng = str(engine or "").strip().lower()
    if eng != CONFINED_ENGINE_ID:
        raise ValueError(UNCONFINED_TOOL_MESSAGE)

    m = str(mode or "read_only").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Expected one of: {', '.join(VALID_MODES)}")

    cmd = [
        cli_path,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
    ]
    if m == "write_out":
        cmd.extend(
            [
                "--tools",
                "Read,Grep,Glob,Write,Edit",
                "--allowedTools",
                "Write(./out/**)",
                "Edit(./out/**)",
            ]
        )
    else:
        cmd.extend(["--tools", "Read,Grep,Glob"])
    return cmd


def _is_path_within(root_dir, target_path):
    real_root = os.path.realpath(root_dir)
    real_target = os.path.realpath(target_path)
    try:
        return os.path.commonpath([real_root, real_target]) == real_root
    except ValueError:
        return False


def _user_cache_dir():
    if os.name == "nt":
        base = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("TEMP")
            or os.path.expanduser("~\\AppData\\Local")
        )
        return os.path.abspath(os.path.expanduser(base))
    if sys.platform == "darwin":
        return os.path.abspath(os.path.expanduser("~/Library/Caches"))
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return os.path.abspath(os.path.expanduser(xdg))
    return os.path.abspath(os.path.expanduser("~/.cache"))


def _workspace_base_dir():
    override = os.environ.get("MAGNM_CODING_WORKSPACES", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    data_hash = hashlib.sha256(os.path.realpath(paths.DATA_ROOT).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_user_cache_dir(), "magnm-studio", "coding-runs", data_hash)


def _has_git_worktree_ancestor(target_path):
    cur = os.path.realpath(target_path)
    while True:
        git_marker = os.path.join(cur, ".git")
        if os.path.lexists(git_marker):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _has_symlink_component_below(base_dir, target_path):
    real_base = os.path.realpath(base_dir)
    norm_base = os.path.abspath(base_dir)
    if os.path.islink(norm_base):
        return True
    try:
        rel = os.path.relpath(os.path.abspath(target_path), norm_base)
    except ValueError:
        return True
    if rel == "." or rel.startswith("..") or os.path.isabs(rel):
        return True
    cur = norm_base
    for part in rel.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    if not _is_path_within(real_base, target_path):
        return True
    return False


def validate_workspace_path(workspace, expected_base=None):
    """Verify via realpath that workspace is outside DATA_ROOT, RESOURCE_ROOT, and any git work tree."""
    if not workspace or not isinstance(workspace, str):
        raise ValueError("Invalid workspace path")
    if os.path.islink(workspace):
        raise ValueError("Workspace path must not be a symlink")

    real_ws = os.path.realpath(workspace)
    if expected_base is not None:
        if os.path.islink(expected_base):
            raise ValueError("Workspace base directory must not be a symlink")
        real_base = os.path.realpath(expected_base)
        if not _is_path_within(real_base, real_ws) or real_ws == real_base:
            raise ValueError("Workspace path escapes configured coding workspaces directory")
        if _has_symlink_component_below(expected_base, workspace):
            raise ValueError("Workspace path contains a symlink component")

    if _is_path_within(paths.DATA_ROOT, real_ws):
        raise ValueError("Workspace path must not be inside DATA_ROOT")
    if _is_path_within(paths.RESOURCE_ROOT, real_ws):
        raise ValueError("Workspace path must not be inside RESOURCE_ROOT")
    if _has_git_worktree_ancestor(real_ws):
        raise ValueError("Workspace path must not be inside a git work tree")
    return real_ws


def _resolve_workspace_for_run(run_id):
    safe_run_id = _validate_safe_id(run_id, "run_id")
    base = _workspace_base_dir()
    candidate = os.path.join(base, safe_run_id)
    return validate_workspace_path(candidate, expected_base=base)


def _is_skipped_filename(name):
    low = name.lower()
    if low.startswith(".env"):
        return True
    return any(fnmatch.fnmatch(low, pat) for pat in _KEY_FILE_PATTERNS)


def _is_excluded_data_root_path(real_target, real_data_root):
    if not _is_path_within(real_data_root, real_target):
        return False
    rel = os.path.relpath(real_target, real_data_root).replace("\\", "/")
    if rel == ".":
        return False
    return any(rel == pref or rel.startswith(pref + "/") for pref in _DATA_ROOT_EXCLUDED_PREFIXES)


def _set_tree_readonly(root_dir):
    if os.name == "nt":
        for dirpath, _, filenames in os.walk(root_dir):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    os.chmod(fpath, stat.S_IREAD)
                except OSError:
                    pass
        return

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                mode = stat.S_IMODE(os.lstat(fpath).st_mode)
                os.chmod(fpath, mode & ~0o222)
            except OSError:
                pass
        for dname in dirnames:
            dpath = os.path.join(dirpath, dname)
            try:
                mode = stat.S_IMODE(os.lstat(dpath).st_mode)
                os.chmod(dpath, mode & ~0o222)
            except OSError:
                pass
    try:
        mode = stat.S_IMODE(os.lstat(root_dir).st_mode)
        os.chmod(root_dir, mode & ~0o222)
    except OSError:
        pass


def _restore_tree_writable_and_remove(root_dir):
    if not os.path.exists(root_dir):
        return
    if os.name != "nt":
        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
            try:
                os.chmod(dirpath, 0o700)
            except OSError:
                pass
            for dname in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, dname), 0o700)
                except OSError:
                    pass
            for fname in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fname), 0o600)
                except OSError:
                    pass
    else:
        for dirpath, _, filenames in os.walk(root_dir):
            for fname in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fname), stat.S_IWRITE)
                except OSError:
                    pass
    shutil.rmtree(root_dir, ignore_errors=True)


def _validate_safe_id(value, label="id"):
    s = str(value or "").strip()
    if not s or "/" in s or "\\" in s or ".." in s or s in (".", "..") or "\x00" in s:
        raise ValueError(f"Invalid {label}: '{value}'")
    return s


def _copy_regular_file(src_file, dst_file):
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    shutil.copyfile(src_file, dst_file)
    try:
        mode = stat.S_IMODE(os.lstat(src_file).st_mode)
        os.chmod(dst_file, mode)
    except OSError:
        pass


def _should_copy_rel_file(src_root, real_src_root, real_data_root, rel_parts):
    if not rel_parts:
        return False, None
    for part in rel_parts:
        if not part or part in (".", "..") or "\x00" in part or part in _SKIP_DIR_NAMES:
            return False, None
    if _is_skipped_filename(rel_parts[-1]):
        return False, None

    cur = src_root
    for part in rel_parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return False, None

    try:
        st = os.lstat(cur)
    except OSError:
        return False, None
    if not stat.S_ISREG(st.st_mode):
        return False, None

    real_file = os.path.realpath(cur)
    if not _is_path_within(real_src_root, real_file):
        return False, None
    if _is_excluded_data_root_path(real_file, real_data_root):
        return False, None
    return True, cur


def _refuse_broad_source(real_src_root, repo_id):
    """A connected "repo" must not be the home folder or a folder that contains it."""
    if _is_path_within(real_src_root, os.path.realpath(os.path.expanduser("~"))):
        raise ValueError(f"'{repo_id}' points at your home folder or above it, so it can't be sent to a coding agent.")


def _copy_repo_tree(src_root, dst_root, repo_id):
    real_src_root = os.path.realpath(src_root)
    _refuse_broad_source(real_src_root, repo_id)
    real_data_root = os.path.realpath(paths.DATA_ROOT)
    os.makedirs(dst_root, exist_ok=True)
    real_dst_root = os.path.realpath(dst_root)

    git_marker = os.path.join(src_root, ".git")
    if os.path.lexists(git_marker):
        res = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                src_root,
                "ls-files",
                "-z",
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=build_clean_env(),
            timeout=60,
        )
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Failed to list tracked files for '{repo_id}': {err}")

        for raw_entry in res.stdout.split(b"\x00"):
            if not raw_entry:
                continue
            rel = raw_entry.decode("utf-8", errors="surrogateescape")
            if rel.startswith("/") or rel.startswith("\\") or (len(rel) > 1 and rel[1] == ":"):
                continue
            rel_parts = [p for p in rel.replace("\\", "/").split("/") if p]
            ok, src_file = _should_copy_rel_file(src_root, real_src_root, real_data_root, rel_parts)
            if not ok:
                continue
            dst_file = os.path.join(dst_root, *rel_parts)
            if not _is_path_within(real_dst_root, os.path.realpath(os.path.dirname(dst_file))):
                continue
            _copy_regular_file(src_file, dst_file)
    else:
        for dirpath, dirnames, filenames in os.walk(src_root, topdown=True, followlinks=False):
            kept_dirs = []
            for dname in sorted(dirnames):
                if dname in _SKIP_DIR_NAMES:
                    continue
                dfull = os.path.join(dirpath, dname)
                if os.path.islink(dfull):
                    continue
                real_d = os.path.realpath(dfull)
                if not _is_path_within(real_src_root, real_d):
                    continue
                if _is_excluded_data_root_path(real_d, real_data_root):
                    continue
                kept_dirs.append(dname)
            dirnames[:] = kept_dirs

            rel_dir = os.path.relpath(dirpath, src_root)
            dir_parts = [] if rel_dir == "." else rel_dir.replace("\\", "/").split("/")
            for fname in sorted(filenames):
                rel_parts = dir_parts + [fname]
                ok, src_file = _should_copy_rel_file(src_root, real_src_root, real_data_root, rel_parts)
                if not ok:
                    continue
                dst_file = os.path.join(dst_root, *rel_parts)
                _copy_regular_file(src_file, dst_file)

    _set_tree_readonly(dst_root)


def prepare_workspace(run_id, repo_ids, workspace=None):
    """Create validated workspace outside DATA_ROOT/RESOURCE_ROOT/git trees with read-only repo copies and out/."""
    safe_run_id = _validate_safe_id(run_id, "run_id")
    if workspace is None:
        workspace = _resolve_workspace_for_run(safe_run_id)
    else:
        workspace = validate_workspace_path(workspace)

    repos_dir = os.path.join(workspace, "repos")
    out_dir = os.path.join(workspace, "out")
    os.makedirs(repos_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.islink(repos_dir) or os.path.islink(out_dir):
        raise ValueError("Workspace subdirectories must not be symlinks")

    for raw_rid in repo_ids or ():
        rid = _validate_safe_id(raw_rid, "repo_id")
        repo_cfg = repos.get_repo(rid)
        if repo_cfg is None:
            raise ValueError(f"Repository '{rid}' not found")
        local_path = repo_cfg.get("local_path")
        if not local_path or not os.path.isdir(local_path):
            raise ValueError(f"Repository '{rid}' has no accessible local_path")

        src_root = os.path.abspath(local_path)
        dst_root = os.path.join(repos_dir, repo_cfg["id"])
        if os.path.lexists(dst_root):
            if os.path.islink(dst_root):
                os.unlink(dst_root)
            else:
                _restore_tree_writable_and_remove(dst_root)

        _copy_repo_tree(src_root, dst_root, rid)

    return workspace


def list_out_files(workspace):
    """Return sorted list of regular non-symlink {'name', 'size'} entries whose realpath is inside out/."""
    out_dir = os.path.join(workspace, "out")
    if os.path.islink(out_dir) or not os.path.isdir(out_dir):
        return []
    real_out = os.path.realpath(out_dir)
    items = []
    for dirpath, dirnames, filenames in os.walk(out_dir, topdown=True, followlinks=False):
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not os.path.islink(os.path.join(dirpath, d))
            and _is_path_within(real_out, os.path.realpath(os.path.join(dirpath, d)))
        ]
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if os.path.islink(full):
                continue
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            real_file = os.path.realpath(full)
            if not _is_path_within(real_out, real_file) or real_file == real_out:
                continue
            rel = os.path.relpath(full, out_dir).replace("\\", "/")
            items.append({"name": rel, "size": int(st.st_size)})
    items.sort(key=lambda x: x["name"])
    return items


def _confidential_product_ids():
    try:
        for cand in (
            paths.data("config", "product_rules.json"),
            paths.resource("config", "product_rules.json"),
        ):
            if os.path.exists(cand):
                return product_rules.confidential_product_ids(cand)
    except Exception:
        pass
    return set()


def _mentions_confidential_text(text):
    if not text:
        return False
    try:
        for cand in (
            paths.data("config", "product_rules.json"),
            paths.resource("config", "product_rules.json"),
        ):
            if os.path.exists(cand):
                return bool(product_rules.mentions_confidential(str(text), path=cand))
    except Exception:
        pass
    return False


def is_repo_confidential(repo_obj):
    """Return True if repo_obj is marked confidential or maps to a confidential product."""
    if not isinstance(repo_obj, dict):
        return False
    if repo_obj.get("confidential"):
        return True
    conf_ids = _confidential_product_ids()
    pid = str(repo_obj.get("product_id") or "").strip().lower()
    rid = str(repo_obj.get("id") or "").strip().lower()
    if (pid and pid in conf_ids) or (rid and rid in conf_ids):
        return True
    return _mentions_confidential_text(repo_obj.get("name") or "")


def _validate_repos_and_confidentiality(repo_ids):
    repo_list = list(dict.fromkeys(str(r).strip() for r in (repo_ids or []) if str(r).strip()))
    conf_repos = []
    for rid in repo_list:
        _validate_safe_id(rid, "repo_id")
        repo_cfg = repos.get_repo(rid)
        if repo_cfg is None:
            raise ValueError(f"Repository '{rid}' not found")
        local_path = repo_cfg.get("local_path")
        if not local_path or not os.path.isdir(local_path):
            raise ValueError(f"Repository '{rid}' has no accessible local_path")
        if is_repo_confidential(repo_cfg):
            conf_repos.append(repo_cfg)
    return repo_list, conf_repos


def compute_approval_hash(engine, mode, repo_ids, prompt):
    """Return SHA-256 hash binding approval to (engine, mode, repo_ids, prompt)."""
    payload = {
        "engine": str(engine or "").strip().lower(),
        "mode": str(mode or "read_only").strip().lower(),
        "repo_ids": [str(r).strip() for r in (repo_ids or []) if str(r).strip()],
        "prompt": str(prompt or "").strip(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sanitize_run_for_event(run_dict):
    if not isinstance(run_dict, dict):
        return run_dict
    if not run_dict.get("confidential"):
        return dict(run_dict)
    return {
        "id": run_dict.get("id"),
        "engine": run_dict.get("engine"),
        "mode": run_dict.get("mode"),
        "prompt": "Confidential coding run",
        "repo_ids": [],
        "status": run_dict.get("status"),
        "workspace": None,
        "created_at": run_dict.get("created_at"),
        "started_at": run_dict.get("started_at"),
        "finished_at": run_dict.get("finished_at"),
        "exit_code": run_dict.get("exit_code"),
        "summary": None,
        "log_path": None,
        "out_files": [],
        "error": "Confidential coding run" if run_dict.get("error") else None,
        "transport": run_dict.get("transport"),
        "confidential": True,
        "approval_hash": run_dict.get("approval_hash"),
        "approved_at": run_dict.get("approved_at"),
        "approval_consumed": run_dict.get("approval_consumed"),
        "requested_by": run_dict.get("requested_by"),
        "session_id": run_dict.get("session_id"),
    }


def _publish_run_status(run_dict):
    if run_dict is not None:
        events.publish("coding_run.status", _sanitize_run_for_event(run_dict))
    return run_dict


class _RunLogWriter:
    """Thread-safe run.log writer with ANSI stripping, 20 MB cap, and redacted SSE events."""

    def __init__(self, run_id, log_path, max_bytes=MAX_LOG_BYTES, confidential=False):
        self.run_id = run_id
        self.log_path = log_path
        self.max_bytes = int(max_bytes)
        self.confidential = bool(confidential)
        self._lock = threading.Lock()
        self._bytes_written = 0
        self._truncated = False
        self._pending_lines = []
        self._last_flush = time.monotonic()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._fh = open(log_path, "ab")

    def write_line(self, text):
        clean = strip_ansi(text).rstrip("\n")
        self._write_lines([clean])

    def _publish_lines(self, to_publish, new_offset):
        if not to_publish:
            return
        if self.confidential:
            events.publish(
                "coding_run.log",
                {
                    "run_id": self.run_id,
                    "confidential": True,
                    "lines": ["Confidential coding run"],
                    "offset": new_offset,
                },
            )
            return
        events.publish(
            "coding_run.log",
            {
                "run_id": self.run_id,
                "lines": to_publish,
                "offset": new_offset,
            },
        )

    def _write_lines(self, lines):
        to_publish = None
        new_offset = 0
        with self._lock:
            for line in lines:
                if self._truncated:
                    break
                encoded = (line + "\n").encode("utf-8", errors="replace")
                if self._bytes_written + len(encoded) > self.max_bytes:
                    marker = b"[log truncated at 20 MB]\n"
                    remain = max(0, self.max_bytes - self._bytes_written)
                    if remain >= len(marker):
                        self._fh.write(marker)
                        self._bytes_written += len(marker)
                    elif remain > 0:
                        self._fh.write(encoded[:remain])
                        self._bytes_written += remain
                    self._truncated = True
                    self._pending_lines.append("[log truncated at 20 MB]")
                    break
                self._fh.write(encoded)
                self._bytes_written += len(encoded)
                self._pending_lines.append(line)

            self._fh.flush()
            now = time.monotonic()
            if self._pending_lines and (
                len(self._pending_lines) >= LOG_BATCH_MAX_LINES
                or (now - self._last_flush) >= LOG_BATCH_INTERVAL_S
            ):
                to_publish = list(self._pending_lines)
                self._pending_lines.clear()
                self._last_flush = now
                new_offset = self._bytes_written

        self._publish_lines(to_publish, new_offset)

    def flush(self):
        to_publish = None
        new_offset = 0
        with self._lock:
            self._fh.flush()
            if self._pending_lines:
                to_publish = list(self._pending_lines)
                self._pending_lines.clear()
                self._last_flush = time.monotonic()
                new_offset = self._bytes_written
        self._publish_lines(to_publish, new_offset)

    def close(self):
        self.flush()
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


def _terminate_process_group(proc, grace_s=CANCEL_KILL_GRACE_S):
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=min(2.0, grace_s))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            proc.terminate()
        except OSError:
            pass

    deadline = time.monotonic() + max(0.1, float(grace_s))
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def _extract_stream_text(obj):
    """Extract readable assistant text from a parsed stream-json object."""
    if not isinstance(obj, dict):
        return ""
    if obj.get("type") == "result" and isinstance(obj.get("result"), str):
        return obj["result"]
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            return "".join(parts)
    if isinstance(obj.get("delta"), dict) and isinstance(obj["delta"].get("text"), str):
        return obj["delta"]["text"]
    if isinstance(obj.get("item"), dict) and isinstance(obj["item"].get("text"), str):
        return obj["item"]["text"]
    if isinstance(obj.get("text"), str):
        return obj["text"]
    if isinstance(obj.get("content"), str):
        return obj["content"]
    return ""


def _extract_headless_summary(raw_lines):
    """Build final answer text from stream-json stdout lines."""
    structured_parts = []
    last_result = None
    for line in raw_lines:
        s = line.strip()
        if not s or not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result" and isinstance(obj.get("result"), str):
            last_result = obj["result"]
        else:
            txt = _extract_stream_text(obj)
            if txt:
                structured_parts.append(txt)
    if last_result is not None and last_result:
        return last_result
    if structured_parts:
        return "\n".join(structured_parts)

    plain = "\n".join(strip_ansi(ln) for ln in raw_lines if strip_ansi(ln).strip())
    return plain


def _popen_kwargs(workspace):
    kwargs = {
        "cwd": workspace,
        "env": build_clean_env(),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _register_active_run(run_id, proc, cancel_ev):
    with _active_runs_lock:
        _active_runs[run_id] = {"proc": proc, "cancel_event": cancel_ev}


def _update_active_proc(run_id, proc):
    with _active_runs_lock:
        entry = _active_runs.get(run_id)
        if entry is not None:
            entry["proc"] = proc


def _unregister_active_run(run_id):
    with _active_runs_lock:
        _active_runs.pop(run_id, None)


def _run_headless(run_id, cmd, prompt, workspace, log_writer, cancel_ev, timeout_s):
    """Run confined Claude Code headless with prompt passed on stdin, streaming logs."""
    raw_lines = []
    started = time.monotonic()
    deadline = started + timeout_s

    proc = subprocess.Popen(list(cmd), **_popen_kwargs(workspace))
    _update_active_proc(run_id, proc)

    def _write_stdin():
        try:
            if proc.stdin is not None:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.flush()
        except OSError:
            pass
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

    out_q = queue.Queue()

    def _reader():
        try:
            for raw_line in iter(proc.stdout.readline, b""):
                out_q.put(("line", raw_line.decode("utf-8", errors="replace")))
        except Exception:
            pass
        finally:
            out_q.put(("eof", None))

    t_in = threading.Thread(target=_write_stdin, daemon=True)
    t_reader = threading.Thread(target=_reader, daemon=True)
    t_in.start()
    t_reader.start()

    timed_out = False
    cancelled = False
    while True:
        if cancel_ev.is_set():
            cancelled = True
            _terminate_process_group(proc)
            break
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            _terminate_process_group(proc)
            break
        try:
            kind, val = out_q.get(timeout=min(0.1, deadline - now))
        except queue.Empty:
            if proc.poll() is not None and not t_reader.is_alive() and out_q.empty():
                break
            continue
        if kind == "eof":
            break
        if kind == "line":
            stripped_nl = val.rstrip("\r\n")
            raw_lines.append(stripped_nl)
            log_writer.write_line(stripped_nl)

    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass

    rc = proc.poll()
    if rc is None:
        try:
            rc = proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            _terminate_process_group(proc)
            rc = proc.returncode

    full_answer = _extract_headless_summary(raw_lines)
    if cancelled:
        return "cancelled", rc, full_answer
    if timed_out:
        return "timeout", rc, full_answer
    return ("done" if rc == 0 else "failed"), rc, full_answer


def _write_read_only_answer(workspace, answer_text):
    out_dir = os.path.join(workspace, "out")
    if os.path.islink(out_dir) or not os.path.isdir(out_dir):
        raise ValueError("Workspace out/ directory is invalid")
    real_out = os.path.realpath(out_dir)
    answer_path = os.path.join(out_dir, "answer.md")
    if os.path.islink(answer_path):
        raise ValueError("out/answer.md must not be a symlink")
    real_answer = os.path.realpath(answer_path)
    if not _is_path_within(real_out, real_answer) or real_answer == real_out:
        raise ValueError("out/answer.md resolved outside out/")
    with open(answer_path, "w", encoding="utf-8") as f:
        f.write(answer_text)


def _execute_run(run_id):
    run_row = db.get_coding_run(run_id)
    if not run_row or run_row["status"] != "queued":
        return

    engine = run_row["engine"]
    mode = run_row["mode"]
    prompt = run_row["prompt"]
    repo_ids = run_row.get("repo_ids") or []
    workspace = run_row["workspace"]
    is_conf = bool(run_row.get("confidential")) or bool(
        _mentions_confidential_text(prompt)
    )
    log_path = os.path.join(workspace, "run.log")

    # Enforce single-use approval and hash binding before executing anything.
    expected_hash = compute_approval_hash(engine, mode, repo_ids, prompt)
    if (
        not run_row.get("approval_consumed")
        or not run_row.get("approved_at")
        or not run_row.get("approval_hash")
        or not hmac.compare_digest(str(run_row["approval_hash"]), expected_hash)
    ):
        failed = db.update_coding_run(
            run_id,
            status="failed",
            finished_at=_now_iso(),
            error="Run missing valid single-use user approval",
        )
        _publish_run_status(failed)
        return

    try:
        validate_workspace_path(workspace)
        cli_info = _require_confined_cli(engine)
    except ValueError as exc:
        failed = db.update_coding_run(
            run_id,
            status="failed",
            finished_at=_now_iso(),
            error=str(exc),
        )
        _publish_run_status(failed)
        return

    cancel_ev = threading.Event()
    _register_active_run(run_id, None, cancel_ev)

    started_row = db.update_coding_run(
        run_id,
        status="running",
        started_at=_now_iso(),
        error=None,
        transport="headless",
    )
    _publish_run_status(started_row)

    log_writer = None
    try:
        with keepawake.inhibit():
            prepare_workspace(run_id, repo_ids, workspace=workspace)
            log_writer = _RunLogWriter(run_id, log_path, confidential=is_conf)

            timeout_s = get_run_timeout_s()
            cmd = build_headless_command(engine, cli_info["path"], mode=mode)
            log_writer.write_line(f"[magnm] starting {engine} confined headless ({mode})")
            outcome, exit_code, full_answer = _run_headless(
                run_id,
                cmd,
                prompt,
                workspace,
                log_writer,
                cancel_ev,
                timeout_s,
            )

            log_writer.flush()
            summary = (full_answer or "")[-SUMMARY_MAX_CHARS:]
            if not summary and os.path.isfile(log_path):
                summary = read_log_tail(log_path, max_chars=SUMMARY_MAX_CHARS)

            if mode == "read_only" and (full_answer or outcome == "done"):
                _write_read_only_answer(workspace, full_answer or summary or "")

            out_files = list_out_files(workspace)

            if outcome == "cancelled" or cancel_ev.is_set():
                final_status = "cancelled"
                err_msg = "cancelled by user"
            elif outcome == "timeout":
                final_status = "failed"
                err_msg = f"timed out after {int(timeout_s)}s"
            elif outcome == "done":
                final_status = "done"
                err_msg = None
            else:
                final_status = "failed"
                err_msg = f"exited with code {exit_code}"

            finished = db.update_coding_run(
                run_id,
                status=final_status,
                finished_at=_now_iso(),
                exit_code=exit_code,
                summary=summary,
                out_files=out_files,
                error=err_msg,
                transport="headless",
            )
            _publish_run_status(finished)

    except Exception as exc:
        if log_writer is not None:
            log_writer.write_line(f"[magnm error] {exc}")
        out_files = list_out_files(workspace) if os.path.isdir(workspace) else []
        failed = db.update_coding_run(
            run_id,
            status="cancelled" if cancel_ev.is_set() else "failed",
            finished_at=_now_iso(),
            error=str(exc),
            out_files=out_files,
        )
        _publish_run_status(failed)
    finally:
        if log_writer is not None:
            log_writer.close()
        _unregister_active_run(run_id)
        repos_dir = os.path.join(workspace, "repos")
        if os.path.isdir(repos_dir):
            _restore_tree_writable_and_remove(repos_dir)


def _engine_worker_loop(engine):
    wake_ev = _engine_wake_events[engine]
    while True:
        next_run = db.get_next_queued_coding_run(engine)
        if next_run is not None:
            _execute_run(next_run["id"])
            continue
        wake_ev.wait(timeout=0.5)
        wake_ev.clear()


def _ensure_engine_worker(engine):
    with _workers_lock:
        if engine not in _engine_wake_events:
            _engine_wake_events[engine] = threading.Event()
        thread = _engine_workers.get(engine)
        if thread is None or not thread.is_alive():
            thread = threading.Thread(
                target=_engine_worker_loop,
                args=(engine,),
                name=f"magnm-coding-{engine}",
                daemon=True,
            )
            _engine_workers[engine] = thread
            thread.start()
        _engine_wake_events[engine].set()


def wait_for_run(run_id, timeout=60.0, poll_interval=0.05):
    """Block until a coding run reaches a terminal status or timeout expires."""
    deadline = time.monotonic() + float(timeout)
    while True:
        current = db.get_coding_run(run_id)
        if current is None or current["status"] in ("done", "failed", "cancelled"):
            return current
        if time.monotonic() >= deadline:
            return current
        time.sleep(poll_interval)


def _validate_run_inputs(engine, prompt, repo_ids, mode):
    _require_confined_cli(engine)
    eng = str(engine or "").strip().lower()

    m = str(mode or "read_only").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Expected one of: {', '.join(VALID_MODES)}")

    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt is required")

    repo_list, conf_repos = _validate_repos_and_confidentiality(repo_ids)
    is_conf = bool(conf_repos) or _mentions_confidential_text(clean_prompt)
    return eng, m, clean_prompt, repo_list, conf_repos, is_conf


def request_run(engine, prompt, repo_ids=None, mode="read_only", requested_by=None, session_id=None):
    """Create a coding run in awaiting_approval status without starting execution."""
    eng, m, clean_prompt, repo_list, conf_repos, is_conf = _validate_run_inputs(
        engine, prompt, repo_ids, mode
    )

    if is_conf and session_id:
        try:
            db.update_agent_session(session_id, confidential=True, updated_at=_now_iso())
        except Exception:
            pass

    run_id = f"crun_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    workspace = _resolve_workspace_for_run(run_id)
    log_path = os.path.join(workspace, "run.log")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(os.path.join(workspace, "out"), exist_ok=True)

    approval_hash = compute_approval_hash(eng, m, repo_list, clean_prompt)
    created = db.create_coding_run(
        {
            "id": run_id,
            "engine": eng,
            "mode": m,
            "prompt": clean_prompt,
            "repo_ids": repo_list,
            "status": "awaiting_approval",
            "workspace": workspace,
            "created_at": _now_iso(),
            "log_path": log_path,
            "out_files": [],
            "approval_hash": approval_hash,
            "approved_at": None,
            "approval_consumed": False,
            "confidential": is_conf,
            "requested_by": str(requested_by).strip() if requested_by else None,
            "session_id": str(session_id).strip() if session_id else None,
        }
    )
    _publish_run_status(created)
    return created


def approve_run(run_id, expected_hash=None, confirm_confidential=False, wait=False):
    """Consume single-use approval on an awaiting_approval run and start execution."""
    existing = db.get_coding_run(run_id)
    if existing is None:
        raise ValueError(f"Coding run '{run_id}' not found")
    if existing["status"] != "awaiting_approval":
        raise ValueError(f"Coding run '{run_id}' is not awaiting approval (status={existing['status']})")
    if existing.get("approval_consumed") or existing.get("approved_at"):
        raise ValueError("Approval for this coding run has already been used")

    _require_confined_cli(existing["engine"])
    validate_workspace_path(existing["workspace"])

    actual_hash = compute_approval_hash(
        existing["engine"],
        existing["mode"],
        existing.get("repo_ids") or [],
        existing["prompt"],
    )
    stored_hash = str(existing.get("approval_hash") or "")
    if not stored_hash or not hmac.compare_digest(stored_hash, actual_hash):
        raise ValueError("Run parameters no longer match the original approval hash")
    if not expected_hash:
        raise ValueError("Approval needs the hash of the run that was shown; reload the run before starting")
    if not hmac.compare_digest(str(expected_hash), actual_hash):
        raise ValueError("Approval hash mismatch; reload the run details before starting")

    _, conf_repos = _validate_repos_and_confidentiality(existing.get("repo_ids") or [])
    if conf_repos and not confirm_confidential:
        names = ", ".join(r.get("name") or r["id"] for r in conf_repos)
        vendor = CONFINED_VENDOR_BY_ENGINE.get(existing["engine"], existing["engine"])
        raise ValueError(
            f"Explicit confirmation required: this sends {names}'s code to {vendor}"
        )

    is_conf = bool(existing.get("confidential")) or bool(conf_repos) or _mentions_confidential_text(existing["prompt"])
    if is_conf and existing.get("session_id"):
        try:
            db.update_agent_session(existing["session_id"], confidential=True, updated_at=_now_iso())
        except Exception:
            pass

    approved = db.approve_coding_run(run_id, actual_hash, _now_iso(), confidential=is_conf)
    if approved is None:
        raise ValueError("Approval for this coding run has already been used or parameters changed")

    _publish_run_status(approved)
    _ensure_engine_worker(approved["engine"])

    if wait:
        return wait_for_run(run_id, timeout=get_run_timeout_s() + 30.0)
    return approved


def run(engine, prompt, repo_ids=None, mode="read_only", wait=False, confirm_confidential=False):
    """Create and immediately start a user-approved coding run (one run at a time per engine)."""
    eng, m, clean_prompt, repo_list, conf_repos, is_conf = _validate_run_inputs(
        engine, prompt, repo_ids, mode
    )

    if conf_repos and not confirm_confidential:
        names = ", ".join(r.get("name") or r["id"] for r in conf_repos)
        vendor = CONFINED_VENDOR_BY_ENGINE.get(eng, eng)
        raise ValueError(
            f"Explicit confirmation required: this sends {names}'s code to {vendor}"
        )

    run_id = f"crun_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    workspace = _resolve_workspace_for_run(run_id)
    log_path = os.path.join(workspace, "run.log")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(os.path.join(workspace, "out"), exist_ok=True)

    now_iso = _now_iso()
    approval_hash = compute_approval_hash(eng, m, repo_list, clean_prompt)
    created = db.create_coding_run(
        {
            "id": run_id,
            "engine": eng,
            "mode": m,
            "prompt": clean_prompt,
            "repo_ids": repo_list,
            "status": "queued",
            "workspace": workspace,
            "created_at": now_iso,
            "log_path": log_path,
            "out_files": [],
            "approval_hash": approval_hash,
            "approved_at": now_iso,
            "approval_consumed": True,
            "confidential": is_conf,
        }
    )
    _publish_run_status(created)
    _ensure_engine_worker(eng)

    if wait:
        return wait_for_run(run_id, timeout=get_run_timeout_s() + 30.0)
    return created


def cancel(run_id):
    """Cancel an awaiting_approval/queued run immediately or terminate a running process group."""
    existing = db.get_coding_run(run_id)
    if existing is None:
        return None
    if existing["status"] in ("done", "failed", "cancelled"):
        return existing
    if existing["status"] in ("awaiting_approval", "queued"):
        updated = db.cancel_coding_run_queued(run_id, _now_iso())
        return _publish_run_status(updated)

    with _active_runs_lock:
        active = _active_runs.get(run_id)
    if active is not None:
        active["cancel_event"].set()
        proc = active.get("proc")
        if proc is not None:
            _terminate_process_group(proc, grace_s=CANCEL_KILL_GRACE_S)
    else:
        updated = db.update_coding_run(
            run_id,
            status="cancelled",
            finished_at=_now_iso(),
            error="cancelled by user",
        )
        return _publish_run_status(updated)

    return wait_for_run(run_id, timeout=CANCEL_KILL_GRACE_S + 3.0)


def get_run(run_id):
    """Return a single coding run dict by ID, or None."""
    return db.get_coding_run(run_id)


def list_runs(limit=50, engine=None, status=None):
    """Return recent coding runs newest-first."""
    return db.list_coding_runs(limit=limit, engine=engine, status=status)


def read_log_tail(log_path, max_chars=16000):
    """Read the last max_chars of run.log with ANSI codes stripped."""
    if not log_path or os.path.islink(log_path) or not os.path.isfile(log_path):
        return ""
    try:
        size = os.path.getsize(log_path)
        read_bytes = min(size, max_chars * 4)
        with open(log_path, "rb") as f:
            if size > read_bytes:
                f.seek(size - read_bytes)
            raw = f.read()
        text = strip_ansi(raw.decode("utf-8", errors="replace"))
        return text[-max_chars:]
    except OSError:
        return ""


def read_log_from(run_id, offset=0, max_bytes=262144):
    """Read log content starting at byte offset for incremental log polling."""
    run_row = db.get_coding_run(run_id)
    if run_row is None:
        return None
    log_path = run_row.get("log_path")
    start = max(0, int(offset or 0))
    if not log_path or os.path.islink(log_path) or not os.path.isfile(log_path):
        return {
            "run_id": run_id,
            "content": "",
            "from": start,
            "next_offset": start,
            "status": run_row["status"],
        }
    try:
        size = os.path.getsize(log_path)
        if start > size:
            start = size
        with open(log_path, "rb") as f:
            f.seek(start)
            chunk = f.read(max_bytes)
        text = strip_ansi(chunk.decode("utf-8", errors="replace"))
        return {
            "run_id": run_id,
            "content": text,
            "from": start,
            "next_offset": start + len(chunk),
            "status": run_row["status"],
        }
    except OSError:
        return {
            "run_id": run_id,
            "content": "",
            "from": start,
            "next_offset": start,
            "status": run_row["status"],
        }


def _resolve_out_file(run_id, filename):
    run_row = db.get_coding_run(run_id)
    if run_row is None:
        raise ValueError(f"Coding run '{run_id}' not found")
    raw_rel = str(filename or "").strip()
    if not raw_rel or "\x00" in raw_rel or "\\" in raw_rel or raw_rel.startswith("/"):
        raise ValueError("Invalid filename outside out/")

    parts = raw_rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("Invalid filename outside out/")

    out_dir = os.path.join(run_row["workspace"], "out")
    if os.path.islink(out_dir) or not os.path.isdir(out_dir):
        raise ValueError(f"Output file '{raw_rel}' not found")
    real_out = os.path.realpath(out_dir)

    cur = out_dir
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise ValueError("Invalid symlink output file")

    try:
        st = os.lstat(cur)
    except OSError:
        raise ValueError(f"Output file '{raw_rel}' not found")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("Output path is not a regular file")

    real_candidate = os.path.realpath(cur)
    if not _is_path_within(real_out, real_candidate) or real_candidate == real_out:
        raise ValueError("Invalid filename outside out/")
    return run_row, raw_rel, real_candidate


def resolve_out_file_for_download(run_id, filename):
    """Return (safe_basename, full_path) for a regular non-symlink file inside out/."""
    _, rel, full_path = _resolve_out_file(run_id, filename)
    return os.path.basename(rel), full_path


def _require_safe_save_extension(rel_path, full_path):
    ext_rel = os.path.splitext(rel_path)[1].lower()
    ext_full = os.path.splitext(full_path)[1].lower()
    if ext_rel not in ALLOWED_SAVE_EXTENSIONS or ext_full not in ALLOWED_SAVE_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".") for e in ALLOWED_SAVE_EXTENSIONS))
        raise ValueError(f"File type '{ext_rel or 'unknown'}' cannot be saved; allowed: {allowed}")


def save_out_file_to_library(run_id, filename, category="generated", description=""):
    """Save a safe-extension regular file from out/ into the Assets Library."""
    _, rel, full_path = _resolve_out_file(run_id, filename)
    _require_safe_save_extension(rel, full_path)
    with open(full_path, "rb") as f:
        raw_bytes = f.read()
    desc = str(description or "").strip() or f"Saved from coding run {run_id}: {rel}"
    return assets_manager.register_asset(
        os.path.basename(rel),
        raw_bytes,
        category=str(category or "generated").strip() or "generated",
        description=desc,
        tags=["coding-run", run_id],
    )


def attach_out_file_to_content(run_id, filename, content_id):
    """Copy a safe-extension regular file from out/ into the content item's outputs directory."""
    if not content_id:
        raise ValueError("content_id is required")
    item = db.get_content_item(content_id)
    if item is None:
        raise ValueError(f"Content item '{content_id}' not found")
    _, rel, full_path = _resolve_out_file(run_id, filename)
    _require_safe_save_extension(rel, full_path)
    dest_dir = paths.data("outputs", "content", str(content_id), "coding")
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = os.path.basename(rel)
    dest_path = os.path.join(dest_dir, safe_name)
    shutil.copyfile(full_path, dest_path)
    rel_data_path = os.path.relpath(dest_path, paths.DATA_ROOT).replace("\\", "/")
    updated = db.append_content_files(
        content_id,
        [{"kind": "coding_output", "path": rel_data_path, "notes": f"Coding run {run_id}"}],
    )
    return updated


def recover_stale_runs():
    """Mark any coding runs left in 'running' state after a crash/restart as failed."""
    recovered = db.fail_stale_running_coding_runs(_now_iso(), error="engine restarted")
    for item in recovered:
        _publish_run_status(item)
    return recovered
