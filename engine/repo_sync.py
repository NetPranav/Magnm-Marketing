#!/usr/bin/env python3
"""Magnm Codebase & GitHub Intelligence Sync (delegates to engine/repos.py)."""

import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths
import repos

ROOT = paths.DATA_ROOT
CONFIG_PATH = paths.data("config", "repos.json")
CACHE_DIR = paths.data("cache", "repos")
OUTPUT_JSON = paths.data("research", "repo-sync.json")
OUTPUT_MD = paths.data("research", "repo-sync.md")


def sync_repository(repo_conf):
    """Sync a single repository via repos.py."""
    repo_id = repo_conf.get("id")
    if repo_id and repos.get_repo(repo_id):
        return repos.sync_repo(repo_id, write_files=False)
    if repo_conf.get("local_path"):
        return repos._sync_local_repo(repos.migrate_repo_entry(repo_conf))
    return repos._sync_github_repo(repos.migrate_repo_entry(repo_conf))


def add_repository(repo_conf):
    """Add a new repo link dynamically to config/repos.json and sync."""
    created = repos.add_repo(repo_conf)
    repos.sync_repo(created["id"])
    return run_sync()


def remove_repository(repo_id):
    """Remove repository from config and refresh outputs."""
    if not repos.remove_repo(repo_id):
        return False
    return run_sync()


def run_sync():
    """Sync all configured repositories via repos.sync_all()."""
    return repos.sync_all()


if __name__ == "__main__":
    run_sync()
