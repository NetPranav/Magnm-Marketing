#!/usr/bin/env python3
"""Magnm Codebase & GitHub Intelligence Sync
Extracts live development progress, commit history, tags, and features
from local repos and remote GitHub URLs to provide ground truth for marketing.
Supports dynamically adding new GitHub links.
"""

import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

ROOT = paths.DATA_ROOT
CONFIG_PATH = paths.data("config", "repos.json")
CACHE_DIR = paths.data("cache", "repos")
OUTPUT_JSON = paths.data("research", "repo-sync.json")
OUTPUT_MD = paths.data("research", "repo-sync.md")

os.makedirs(CACHE_DIR, exist_ok=True)

def run_git(cmd, cwd):
    """Execute read-only git command."""
    try:
        res = subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def ensure_repo_path(repo_conf):
    """Ensure a repo has a local working tree (either local path or shallow clone in cache)."""
    local_path = repo_conf.get("local_path")
    if local_path and os.path.exists(local_path):
        return local_path

    # If no local path or doesn't exist, use cached clone from remote_url
    remote_url = repo_conf.get("remote_url")
    if not remote_url:
        return None

    # Derive directory name from remote URL
    clean_url = remote_url.rstrip("/").rstrip(".git")
    parsed = urllib.parse.urlparse(clean_url)
    parts = parsed.path.strip("/").split("/")
    repo_name = "_".join(parts) if parts else repo_conf.get("id", "repo")
    target_dir = os.path.join(CACHE_DIR, repo_name)

    if os.path.exists(target_dir):
        # Update existing cache shallowly
        subprocess.run(["git", "fetch", "--depth", "20"], cwd=target_dir, capture_output=True)
    else:
        # Shallow clone
        print(f"Cloning {remote_url} into cache for inspection...")
        subprocess.run(["git", "clone", "--depth", "20", remote_url, target_dir], capture_output=True)

    return target_dir if os.path.exists(target_dir) else None

def sync_repository(repo_conf):
    """Sync a single repository."""
    repo_id = repo_conf["id"]
    path = ensure_repo_path(repo_conf)

    if not path or not os.path.exists(path):
        return {
            **repo_conf,
            "synced": False,
            "error": f"Failed to access repository: {repo_conf.get('remote_url') or repo_conf.get('local_path')}"
        }

    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    recent_commits_raw = run_git(["log", "-5", "--pretty=format:%h|%an|%ad|%s", "--date=short"], path)

    commits = []
    for line in recent_commits_raw.split("\n"):
        parts = line.split("|")
        if len(parts) >= 4:
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": "|".join(parts[3:])
            })

    diff_stat = run_git(["show", "--stat", "--oneline", "-1"], path)
    tags_raw = run_git(["tag", "--list", "--sort=-creatordate"], path)
    tags = [t for t in tags_raw.split("\n") if t.strip()][:3]

    detected_version = repo_conf.get("version", "unknown")
    pkg_path = os.path.join(path, "package.json")
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pj = json.load(f)
                detected_version = pj.get("version", detected_version)
        except Exception:
            pass

    return {
        "id": repo_id,
        "name": repo_conf["name"],
        "product": repo_conf["product"],
        "status": repo_conf["status"],
        "marketing_angle": repo_conf["marketing_angle"],
        "local_path": path,
        "remote_url": repo_conf.get("remote_url", ""),
        "branch": branch,
        "version": detected_version,
        "latest_commit": commits[0] if commits else None,
        "recent_commits": commits,
        "diff_stat": diff_stat,
        "tags": tags,
        "synced": True,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }

def add_repository(repo_conf):
    """Add a new repo link dynamically to config/repos.json and sync."""
    if not os.path.exists(CONFIG_PATH):
        config = {"repositories": []}
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

    # Check if exists
    repos = config.get("repositories", [])
    repos = [r for r in repos if r["id"] != repo_conf["id"]]
    repos.append(repo_conf)
    config["repositories"] = repos

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return run_sync()

def remove_repository(repo_id):
    """Remove repository from config."""
    if not os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    config["repositories"] = [r for r in config.get("repositories", []) if r["id"] != repo_id]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return run_sync()

def run_sync():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Syncing codebase intelligence...")
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: Config not found at {CONFIG_PATH}", file=sys.stderr)
        return False

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    repos_data = []
    for repo_conf in config.get("repositories", []):
        print(f"- Inspecting {repo_conf['name']}...")
        data = sync_repository(repo_conf)
        repos_data.append(data)

    payload = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "repositories": repos_data
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("# Codebase & GitHub Intelligence Sync\n")
        f.write(f"Synced at: `{payload['synced_at']}`\n\n")
        f.write("This document provides verified engineering reality from local & remote repositories to prevent hallucinated product claims.\n\n")

        for r in repos_data:
            f.write(f"## {r['name']} ({r['product']})\n")
            f.write(f"- **Lifecycle Status:** `{r.get('status', 'in_development').upper()}` (Version: `{r.get('version', '0.1.0')}`)\n")
            f.write(f"- **Branch:** `{r.get('branch', 'unknown')}` | **Remote:** [{r.get('remote_url', '')}]({r.get('remote_url', '')})\n")
            f.write(f"- **Marketing Directive:** {r.get('marketing_angle', '')}\n\n")
            f.write("### Recent Commits (What Was Built):\n")
            for c in r.get("recent_commits", []):
                f.write(f"- `{c['hash']}` ({c['date']}): {c['message']}\n")
            f.write("\n")

    print(f"[OK] Codebase sync complete. Generated:\n  - {OUTPUT_JSON}\n  - {OUTPUT_MD}")
    return payload

if __name__ == "__main__":
    run_sync()
