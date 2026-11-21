#!/usr/bin/env python3
"""Sync a pinned commit of Magnm-Pitch into vendor/magnm-pitch/ and build catalog.json."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_ROOT, "vendor", "magnm-pitch")
DEFAULT_REPO_NAME = "NetPranav/Magnm-Pitch"

EXPORTED_FILES = [
    "index.html",
    "style.css",
    "app.js",
    "presets.js",
    "logo.png",
    "vendor/jspdf.umd.min.js",
    "host/magnm-host.js",
    "RULES.md",
    "scripts/check-rules.js",
]

NODE_CATALOG_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const vendorDir = process.argv[1];
const appPath = path.join(vendorDir, 'app.js');
const presetsPath = path.join(vendorDir, 'presets.js');

const app = require(appPath);
const appSource = fs.readFileSync(appPath, 'utf8');
const presetsSource = fs.readFileSync(presetsPath, 'utf8');

const presetsCtx = {};
vm.runInNewContext(presetsSource, presetsCtx, { filename: presetsPath });

const appCtx = {};
let standaloneLabel = null;
try {
  vm.runInNewContext(appSource, appCtx, { filename: appPath });
  if (typeof appCtx.STANDALONE_LABEL === 'string') {
    standaloneLabel = appCtx.STANDALONE_LABEL;
  }
} catch (_) {
  standaloneLabel = null;
}

if (standaloneLabel === null) {
  const match = appSource.match(/\bvar\s+STANDALONE_LABEL\s*=\s*(?:"([^"]*)"|'([^']*)')\s*;/);
  if (match) {
    standaloneLabel = match[1] !== undefined ? match[1] : match[2];
  }
}

const catalog = {
  items: app.ITEMS,
  groups: app.GROUPS,
  standalone_fee: app.STANDALONE_FEE,
  standalone_label: standaloneLabel,
  presets: presetsCtx.PLANNER_PRESETS,
};

process.stdout.write(JSON.stringify(catalog, null, 2) + '\n');
"""


def _git_output(repo, args, text=True):
    """Run a read-only git command against repo and return stdout."""
    res = subprocess.run(
        ["git", "-C", repo] + args,
        capture_output=True,
        text=text,
    )
    if res.returncode != 0:
        err = res.stderr if text else res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return res.stdout


def _build_catalog(vendor_dir):
    """Run Node to extract catalog.json from vendored app.js and presets.js."""
    node_bin = shutil.which("node")
    if not node_bin:
        raise SystemExit(
            "Node.js ('node') is required to generate catalog.json from vendored Pitch JS; "
            "install Node 22+ and retry."
        )
    res = subprocess.run(
        [node_bin, "-e", NODE_CATALOG_SCRIPT, vendor_dir],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to generate catalog.json via node: {res.stderr.strip()}")
    return json.loads(res.stdout)


def sync_pitch(repo_path, ref, output_dir=VENDOR_DIR):
    """Export pinned Pitch files, write PITCH_VERSION.json and catalog.json."""
    repo_abs = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_abs):
        raise SystemExit(f"Pitch repository path does not exist: {repo_abs}")

    commit = _git_output(repo_abs, ["rev-parse", f"{ref}^{{commit}}"], text=True).strip()

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    file_hashes = {}
    for rel_path in EXPORTED_FILES:
        raw = _git_output(repo_abs, ["show", f"{commit}:{rel_path}"], text=False)
        dest_path = os.path.join(output_dir, *rel_path.split("/"))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(raw)
        file_hashes[rel_path] = hashlib.sha256(raw).hexdigest()

    version_info = {
        "repo": DEFAULT_REPO_NAME,
        "ref": ref,
        "commit": commit,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "files": file_hashes,
    }
    with open(os.path.join(output_dir, "PITCH_VERSION.json"), "w", encoding="utf-8") as f:
        json.dump(version_info, f, indent=2)
        f.write("\n")

    catalog = _build_catalog(output_dir)
    with open(os.path.join(output_dir, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return version_info, catalog


def main():
    parser = argparse.ArgumentParser(description="Vendor a pinned commit of Magnm-Pitch.")
    parser.add_argument("--repo", required=True, help="Path to local Magnm-Pitch git clone")
    parser.add_argument("--ref", default="c44b7e2", help="Git ref/commit to export (default: c44b7e2)")
    args = parser.parse_args()

    version_info, catalog = sync_pitch(args.repo, args.ref)
    print(
        f"Synced {len(version_info['files'])} files from {args.repo} "
        f"({version_info['commit'][:12]}), {len(catalog['items'])} catalog items, "
        f"{len(catalog['presets'])} presets."
    )


if __name__ == "__main__":
    main()
