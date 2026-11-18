#!/usr/bin/env python3
"""Magnm CMO Runtime Orchestrator
Deterministic runtime for managing marketing cycles, task queues,
approval state machines, delivery packages, manifest generation, and job execution.
"""

import argparse
import contextlib
import glob
import io
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db
import paths
import scheduler

ROOT = paths.DATA_ROOT
QUEUE_DIR = paths.data("queue", "agy")
POSTS_DIR = paths.data("outputs", "posts")
BLOGS_DIR = paths.data("outputs", "blogs")
STATE_DIR = paths.data("state")
LOGS_DIR = paths.data("logs")
JOBS_LOG_DIR = os.path.join(LOGS_DIR, "jobs")
os.makedirs(JOBS_LOG_DIR, exist_ok=True)

VALID_STATES = ["draft", "generated", "under_review", "revision_required", "approved", "published"]
ALLOWED_JOBS = {
    "daily_cycle": "Daily CMO Cycle (Digest, Board & Plan Check)",
    "market_scan": "Live Developer & Market Opportunity Scanner",
    "repo_sync": "Codebase & GitHub Intelligence Sync",
    "generate_blog": "Generate Technical Blog & Repurposed Assets",
    "render_cards": "Render Visual Social Cards",
    "workflow_step": "Execute Workflow Step",
}

def log_activity(actor, target_role, task_name, output_file):
    """Record an audit entry in the SQLite activity table."""
    db.log_activity(actor, target_role, task_name, output_file)

def get_activity_log(limit=10):
    """Return recent activity log lines formatted as 'ts | actor -> target | task | file'."""
    return db.get_activity_lines(limit=limit)

def run_digest():
    """Execute CMO daily digest in-process."""
    import digest
    return digest.build_digest()

def run_research():
    """Execute live research scanner in-process and return collected output."""
    import research_scanner
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = research_scanner.run_scan()
    out = buf.getvalue()
    if not out and res:
        return str(res)
    return out

def run_repo_sync():
    """Execute repo sync in-process and return collected output."""
    import repo_sync
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = repo_sync.run_sync()
    out = buf.getvalue()
    if not out and res:
        return json.dumps(res, indent=2) if isinstance(res, dict) else str(res)
    return out

def run_job_body(job_name, job_id=None, actor="UI", log_file=None, params=None):
    """Run the body of an allowed marketing job and write its log output."""
    if job_name not in ALLOWED_JOBS:
        raise ValueError(f"Unknown job '{job_name}'. Allowed jobs: {list(ALLOWED_JOBS.keys())}")

    if job_id is None:
        job_id = f"{job_name}_{int(time.time())}"
    if log_file is None:
        log_file = paths.data("logs", "jobs", f"{job_id}.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    import jobs as jobs_mod

    def _checkpoint(pct, message):
        if jobs_mod.is_cancel_requested(job_id):
            raise jobs_mod.JobCancelled(f"Job '{job_name}' cancelled")
        jobs_mod.progress(job_id, pct, message)

    log_activity(actor, "CMO", f"start_job:{job_name}", log_file)
    output = ""

    if job_name == "daily_cycle":
        _checkpoint(10, "Running codebase and GitHub sync...")
        output += "=== RUNNING DAILY CMO CYCLE ===\n"
        output += run_repo_sync() + "\n"
        _checkpoint(60, "Running daily digest...")
        output += run_digest() + "\n"
    elif job_name == "market_scan":
        _checkpoint(15, "Running market research scan...")
        output += "=== RUNNING MARKET RESEARCH SCAN ===\n"
        output += run_research() + "\n"
    elif job_name == "repo_sync":
        _checkpoint(20, "Running codebase and GitHub sync...")
        output += "=== RUNNING CODEBASE & GITHUB SYNC ===\n"
        output += run_repo_sync() + "\n"
    elif job_name == "workflow_step":
        import workflows
        res = workflows.execute_workflow_job(params or {}, job_id=job_id, log_file=log_file)
        output += json.dumps(res, indent=2, default=str) + "\n"

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(output)

    if jobs_mod.is_cancel_requested(job_id):
        raise jobs_mod.JobCancelled(f"Job '{job_name}' cancelled")

    jobs_mod.progress(job_id, 95, "Finalizing job...")
    log_activity("CMO", actor, f"finish_job:{job_name}", log_file)
    return output


def execute_job(job_name, actor="UI"):
    """Execute an allowed marketing job directly and synchronously (CLI / standalone)."""
    if job_name not in ALLOWED_JOBS:
        raise ValueError(f"Unknown job '{job_name}'. Allowed jobs: {list(ALLOWED_JOBS.keys())}")

    job_id = f"{job_name}_{int(time.time())}"
    log_file = paths.data("logs", "jobs", f"{job_id}.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    start_ts = datetime.now(timezone.utc).isoformat()

    try:
        output = run_job_body(job_name, job_id=job_id, actor=actor, log_file=log_file)
        return {
            "success": True,
            "job_id": job_id,
            "job_name": job_name,
            "started_at": start_ts,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "output": output
        }
    except Exception as e:
        err_msg = f"Job failed: {e}"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n" + err_msg)
        return {"success": False, "error": err_msg}

def get_post_manifest(post_id):
    """Load or initialize manifest.json for a post package."""
    post_dir = os.path.join(POSTS_DIR, post_id)
    os.makedirs(post_dir, exist_ok=True)
    manifest_path = os.path.join(post_dir, "manifest.json")

    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    manifest = {
        "id": post_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "draft",
        "target_platforms": ["x", "linkedin", "instagram"],
        "assets": {},
        "review": {
            "verdict": "pending",
            "reviewed_by": None,
            "reviewed_at": None
        },
        "approval": {
            "approved": False,
            "approved_by": None,
            "approved_at": None
        },
        "publication": {
            "published": False,
            "published_at": None
        }
    }
    save_post_manifest(post_id, manifest)
    return manifest

def save_post_manifest(post_id, manifest):
    """Save manifest.json atomically."""
    post_dir = os.path.join(POSTS_DIR, post_id)
    manifest_path = os.path.join(post_dir, "manifest.json")
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp_path, manifest_path)

def set_post_state(post_id, new_state, actor="CEO"):
    """Enforce state machine transition."""
    if new_state not in VALID_STATES:
        raise ValueError(f"Invalid state: {new_state}. Must be one of {VALID_STATES}")

    manifest = get_post_manifest(post_id)
    old_state = manifest.get("status", "draft")
    manifest["status"] = new_state

    if new_state == "approved":
        manifest["approval"]["approved"] = True
        manifest["approval"]["approved_by"] = actor
        manifest["approval"]["approved_at"] = datetime.now(timezone.utc).isoformat()
    elif new_state == "published":
        manifest["publication"]["published"] = True
        manifest["publication"]["published_at"] = datetime.now(timezone.utc).isoformat()

    save_post_manifest(post_id, manifest)
    log_activity(actor, "CMO", f"state_change:{old_state}->{new_state}", f"outputs/posts/{post_id}/manifest.json")
    return manifest

def build_delivery_package(post_id):
    """Package all drafts, cards, videos, and posting kits into a structured bundle."""
    post_dir = os.path.join(POSTS_DIR, post_id)
    manifest = get_post_manifest(post_id)

    cards = glob.glob(os.path.join(post_dir, "*.png")) + glob.glob(os.path.join(post_dir, "*.jpg"))
    manifest["assets"]["images"] = [os.path.relpath(c, paths.DATA_ROOT) for c in cards]

    video_dir = paths.data("outputs", "video", post_id)
    if os.path.exists(video_dir):
        videos = glob.glob(os.path.join(video_dir, "*.mp4"))
        manifest["assets"]["videos"] = [os.path.relpath(v, paths.DATA_ROOT) for v in videos]

    kit_path = os.path.join(post_dir, "POSTING-KIT.md")
    if not os.path.exists(kit_path) and os.path.exists(os.path.join(video_dir, "POSTING-KIT.md")):
        shutil.copy(os.path.join(video_dir, "POSTING-KIT.md"), kit_path)

    manifest["posting_kit"] = os.path.relpath(kit_path, paths.DATA_ROOT) if os.path.exists(kit_path) else None
    save_post_manifest(post_id, manifest)
    return manifest

def get_system_status():
    """Return dictionary of full system state for UI/API."""
    queue_status = {}
    for folder in ["inbox", "running", "done", "failed"]:
        p = os.path.join(QUEUE_DIR, folder)
        queue_status[folder] = len(glob.glob(os.path.join(p, "*.md")))

    packages = []
    if os.path.exists(POSTS_DIR):
        for p_id in sorted(os.listdir(POSTS_DIR)):
            p_path = os.path.join(POSTS_DIR, p_id)
            if os.path.isdir(p_path):
                manifest = get_post_manifest(p_id)
                kit_file = os.path.join(p_path, "POSTING-KIT.md")
                kit_content = ""
                if os.path.exists(kit_file):
                    with open(kit_file, "r", encoding="utf-8") as kf:
                        kit_content = kf.read()

                packages.append({
                    "id": p_id,
                    "manifest": manifest,
                    "posting_kit_content": kit_content,
                    "images": [os.path.basename(img) for img in manifest.get("assets", {}).get("images", [])],
                    "videos": [os.path.basename(v) for v in manifest.get("assets", {}).get("videos", [])]
                })

    # Read activity log
    activity_lines = get_activity_log(limit=10)

    # Read repo sync
    repo_sync_data = {}
    sync_json = paths.data("research", "repo-sync.json")
    if os.path.exists(sync_json):
        try:
            with open(sync_json, "r", encoding="utf-8") as f:
                repo_sync_data = json.load(f)
        except Exception:
            pass

    # Read schedules
    schedules_data = scheduler.load_schedules()

    return {
        "status": "ready",
        "queue": queue_status,
        "packages": packages,
        "activity_log": activity_lines,
        "repos": repo_sync_data,
        "schedules": schedules_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

def main():
    parser = argparse.ArgumentParser(description="Magnm Marketing CMO Runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show system status and packages")
    subparsers.add_parser("digest", help="Run CMO daily digest")
    subparsers.add_parser("research", help="Run live research scanner")
    subparsers.add_parser("repo-sync", help="Run codebase intelligence sync")

    run_job_p = subparsers.add_parser("run-job", help="Run allowed job safely")
    run_job_p.add_argument("job_name", choices=list(ALLOWED_JOBS.keys()))

    pkg_p = subparsers.add_parser("package", help="Build or refresh delivery package")
    pkg_p.add_argument("post_id")

    appr_p = subparsers.add_parser("approve", help="Mark a post package approved")
    appr_p.add_argument("post_id")
    appr_p.add_argument("--actor", default="CEO")

    pub_p = subparsers.add_parser("publish", help="Mark a post package published")
    pub_p.add_argument("post_id")

    args = parser.parse_args()

    if args.command == "status":
        state = get_system_status()
        print(json.dumps(state, indent=2))
    elif args.command == "digest":
        print(run_digest())
    elif args.command == "research":
        print(run_research())
    elif args.command == "repo-sync":
        print(run_repo_sync())
    elif args.command == "run-job":
        res = execute_job(args.job_name)
        print(json.dumps(res, indent=2))
    elif args.command == "package":
        m = build_delivery_package(args.post_id)
        print(f"[OK] Packaged {args.post_id}: {m['status']}")
    elif args.command == "approve":
        m = set_post_state(args.post_id, "approved", actor=args.actor)
        print(f"[OK] Approved {args.post_id}")
    elif args.command == "publish":
        m = set_post_state(args.post_id, "published")
        print(f"[OK] Published {args.post_id}")

if __name__ == "__main__":
    main()
