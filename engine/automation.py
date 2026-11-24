#!/usr/bin/env python3
"""Automatic work: the owner's pause switch, missed runs, and the automatic content pipeline.

The pipeline (job `auto_pipeline`, scheduled like every other schedule and off by default) picks one random repo the
owner added, syncs it, researches it, and starts the ordinary content workflows for it. Those workflows stop at their
normal approval steps: nothing here ever approves, publishes or posts. When every workflow it started has settled,
the owner is told once (notification list and desktop notification, activity feed, and the cloud copy the phone reads).

The switch "Pause automatic work" stops schedules from firing and makes queued background jobs and automatic runs
wait. Manual actions are never held back. Resuming fires nothing by itself: what was missed is listed, and the owner
decides what to run.

A confidential repo is used only when local AI is ready (every step of such a run already goes to local AI only,
see workflows._run_repo); its name never reaches a notification, the activity feed or the phone.
"""

import json
import os
import random
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db

PAUSED_KEY = "automation_paused"
MISSED_KEY = "automation_missed"
SETTINGS_KEY = "automation_settings"
HISTORY_KEY = "automation_history"

PIPELINE_JOB = "auto_pipeline"
PIPELINE_KINDS = ("post", "blog", "video_script")
PIPELINE_PLATFORMS = ("x", "linkedin", "instagram", "youtube")
# Jobs started by these actors are background work. A person's click (UI) or a phone request is manual.
AUTOMATIC_ACTORS = ("scheduler", "auto_pipeline")
DEFAULT_SETTINGS = {"kinds": ["post"], "platforms": ["linkedin", "x"], "skip_recent_days": 7}
MAX_SKIP_DAYS = 90
MAX_HISTORY = 30
MAX_MISSED = 20
UNSETTLED_RUNS = ("queued", "running", "paused")
RESEARCH_TOOLS = ("read_product_facts", "read_repo_changes", "shipped_this_week", "search_research", "save_research_note")

BRIEFS = {
    "post": (
        "Share a recent update from the chosen repository: what changed lately and why it matters to people who use "
        "the product. Use only approved product facts and the saved research notes, and follow the product rules. "
        "Never publish or post anything."
    ),
    "blog": (
        "Explain a recent change in the chosen repository for a technical reader. Use only approved product facts and "
        "the saved research notes, and follow the product rules."
    ),
    "video_script": (
        "Script a short video about a recent change in the chosen repository. Use only approved product facts and the "
        "saved research notes, and follow the product rules."
    ),
}

_RUN_LOCK = threading.Lock()
_META_LOCK = threading.RLock()


class PipelineError(Exception):
    """A pipeline step stopped. The message is plain text the owner can read; it never carries raw error text."""


class NothingToPick(PipelineError):
    """No repository can be used for a run right now."""


class AlreadyRunning(Exception):
    """An automatic run is already queued or running."""


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(when=None):
    return (when or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _get_json(key, default):
    try:
        raw = db.get_meta(key)
    except Exception:
        return default
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _set_json(key, value):
    db.set_meta(key, json.dumps(value, ensure_ascii=False, sort_keys=True))


# --- The global switch -------------------------------------------------------------------------------------------

def pause_state():
    """{"paused", "since", "by"}. A stored value that cannot be read counts as paused (fail closed)."""
    try:
        raw = db.get_meta(PAUSED_KEY)
    except Exception:
        return {"paused": True, "since": None, "by": None}
    if not raw:
        return {"paused": False, "since": None, "by": None}
    try:
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError
    except ValueError:
        return {"paused": True, "since": None, "by": None}
    paused = state.get("paused") is True
    return {"paused": paused, "since": state.get("since") if paused else None, "by": state.get("by") if paused else None}


def is_paused():
    """True while automatic work is paused."""
    return pause_state()["paused"]


def is_automatic_job(job):
    """True for a queued job that counts as background work: scheduled, started by the pipeline, or a step of an
    automatic workflow run. Manual jobs (a click, a phone request) are never automatic."""
    if not isinstance(job, dict):
        return False
    if job.get("actor") in AUTOMATIC_ACTORS:
        return True
    if job.get("name") == "workflow_step":
        try:
            run = db.get_workflow_run((job.get("params") or {}).get("run_id"))
        except Exception:
            return False
        return is_automatic_run(run)
    return False


def is_automatic_run(run):
    """True for a workflow run the pipeline started."""
    return bool(run) and bool((run.get("state") or {}).get("automatic"))


def _due_schedules(now):
    """Enabled schedules that are due at `now`, as {id, name, due_at}."""
    import cmo_runtime
    import scheduler_loop

    out = []
    for sched in db.load_schedules():
        cmd = sched.get("command")
        if not cmd or cmd not in cmo_runtime.ALLOWED_JOBS:
            continue
        if scheduler_loop.is_due(sched, now):
            out.append({"id": str(sched.get("id")), "name": str(sched.get("name") or sched.get("id"))[:120], "due_at": _iso(now)})
    return out


def note_missed(sched, now=None):
    """Remember that a schedule was due while automatic work was paused. The first time it was missed is kept."""
    sid = str(sched.get("id") or "")
    if not sid:
        return
    with _META_LOCK:
        missed = _get_json(MISSED_KEY, [])
        if any(m.get("id") == sid for m in missed if isinstance(m, dict)):
            return
        missed.append({"id": sid, "name": str(sched.get("name") or sid)[:120], "due_at": _iso(now)})
        _set_json(MISSED_KEY, missed[-MAX_MISSED:])


def missed_runs():
    """What was due while paused and has not been run or dismissed: [{id, name, due_at}]."""
    out = []
    for m in _get_json(MISSED_KEY, []):
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            out.append({"id": m["id"], "name": str(m.get("name") or m["id"])[:120], "due_at": str(m.get("due_at") or "")})
    return out


def _mark_handled(ids, now, status):
    """Count these schedules as handled for the slot that was missed, so nothing fires by itself."""
    ids = set(ids)
    if not ids:
        return

    def mark(schedules):
        for sched in schedules:
            if str(sched.get("id")) in ids:
                sched["last_run"] = _iso(now)
                sched["last_status"] = status

    db.modify_schedules(mark)


def set_paused(paused, by="laptop", now=None):
    """Turn "Pause automatic work" on or off. Resuming starts nothing: missed schedules are listed for the owner."""
    if not isinstance(paused, bool):
        raise ValueError("Paused must be true or false.")
    by = by if by in ("laptop", "phone") else "laptop"
    when = now or _utcnow()
    with _META_LOCK:
        current = pause_state()
        if current["paused"] == paused:
            return current
        if paused:
            _set_json(PAUSED_KEY, {"paused": True, "since": _iso(when), "by": by})
            return pause_state()
        for item in _due_schedules(when):
            note_missed(item, when)
        _mark_handled([m["id"] for m in missed_runs()], when, "skipped")
        _set_json(PAUSED_KEY, {"paused": False, "since": None, "by": None})
    try:
        import workflows
        workflows.resume_global_paused()
    except Exception:
        pass
    try:
        import jobs
        jobs.wake()
    except Exception:
        pass
    return pause_state()


def dismiss_missed(sched_id=None):
    """Forget one missed run (or all of them). They stay handled: nothing fires for them."""
    now = _utcnow()
    with _META_LOCK:
        missed = missed_runs()
        gone = [m["id"] for m in missed if sched_id is None or m["id"] == sched_id]
        _mark_handled(gone, now, "skipped")
        _set_json(MISSED_KEY, [m for m in missed if m["id"] not in gone])
    return missed_runs()


def run_missed(sched_id):
    """Run one missed schedule now, as a manual job."""
    import cmo_runtime
    import jobs

    now = _utcnow()
    with _META_LOCK:
        missed = missed_runs()
        if not any(m["id"] == sched_id for m in missed):
            raise LookupError("That run is not in the missed list.")
        sched = next((s for s in db.load_schedules() if str(s.get("id")) == sched_id), None)
        cmd = (sched or {}).get("command")
        if cmd not in cmo_runtime.ALLOWED_JOBS:
            raise LookupError("That schedule no longer exists.")
        if db.has_active_job(cmd):
            raise ValueError("That job is already queued or running.")
        job = request_run(actor="UI") if cmd == PIPELINE_JOB else jobs.enqueue(cmd, actor="UI")
        _mark_handled([sched_id], now, "queued")
        _set_json(MISSED_KEY, [m for m in missed if m["id"] != sched_id])
    return job


# --- Settings ----------------------------------------------------------------------------------------------------

def _clean_kinds(value):
    if not isinstance(value, list) or not 1 <= len(value) <= len(PIPELINE_KINDS):
        raise ValueError("Choose at least one kind of content.")
    if any(not isinstance(k, str) or k not in PIPELINE_KINDS for k in value):
        raise ValueError("Choose from: " + ", ".join(PIPELINE_KINDS) + ".")
    return list(dict.fromkeys(value))


def _clean_platforms(value):
    if not isinstance(value, list) or not 1 <= len(value) <= len(PIPELINE_PLATFORMS):
        raise ValueError("Choose at least one platform.")
    if any(not isinstance(p, str) or p not in PIPELINE_PLATFORMS for p in value):
        raise ValueError("Choose from: " + ", ".join(PIPELINE_PLATFORMS) + ".")
    return list(dict.fromkeys(value))


def _clean_days(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SKIP_DAYS:
        raise ValueError(f"Days must be a whole number from 0 to {MAX_SKIP_DAYS}.")
    return value


_CLEANERS = {"kinds": _clean_kinds, "platforms": _clean_platforms, "skip_recent_days": _clean_days}


def get_settings():
    """The pipeline settings. A damaged stored value falls back to the default for that field."""
    stored = _get_json(SETTINGS_KEY, {})
    out = {}
    for key, clean in _CLEANERS.items():
        default = DEFAULT_SETTINGS[key]
        default = list(default) if isinstance(default, list) else default
        try:
            out[key] = clean(stored[key]) if key in stored else default
        except ValueError:
            out[key] = default
    return out


def update_settings(changes):
    """Change the pipeline settings. Every value is checked first, so a refused change changes nothing."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Nothing to change.")
    unknown = [k for k in changes if k not in _CLEANERS]
    if unknown:
        raise ValueError("That setting cannot be changed.")
    clean = {key: _CLEANERS[key](value) for key, value in changes.items()}
    with _META_LOCK:
        merged = get_settings()
        merged.update(clean)
        _set_json(SETTINGS_KEY, merged)
    return get_settings()


# --- History -----------------------------------------------------------------------------------------------------

def history(limit=MAX_HISTORY):
    """Recent pipeline runs, newest first."""
    entries = [e for e in _get_json(HISTORY_KEY, []) if isinstance(e, dict)]
    return list(reversed(entries))[: max(1, min(int(limit or MAX_HISTORY), MAX_HISTORY))]


def _append_history(entry):
    with _META_LOCK:
        entries = [e for e in _get_json(HISTORY_KEY, []) if isinstance(e, dict)]
        entries.append(entry)
        _set_json(HISTORY_KEY, entries[-MAX_HISTORY:])


def _save_entry(entry):
    with _META_LOCK:
        entries = [e for e in _get_json(HISTORY_KEY, []) if isinstance(e, dict)]
        for i, old in enumerate(entries):
            if old.get("id") == entry.get("id"):
                entries[i] = entry
                break
        else:
            entries.append(entry)
        _set_json(HISTORY_KEY, entries[-MAX_HISTORY:])


def _entry(entry_id):
    return next((e for e in _get_json(HISTORY_KEY, []) if isinstance(e, dict) and e.get("id") == entry_id), None)


# --- Picking a repo ----------------------------------------------------------------------------------------------

def local_ai_ready():
    """True if local AI can answer, which is the only place a confidential repo's text may go."""
    import repos

    return bool(repos._ollama_is_available())


def _repo_is_confidential(repo):
    import confidential_guard

    return str(repo.get("status") or "").strip().lower() == "secret" or bool(confidential_guard.is_confidential_repo(repo))


def _recently_used(now, days):
    """Ids of repos an earlier run really worked on in the last `days` days."""
    if days <= 0:
        return set()
    cutoff = now - timedelta(days=days)
    used = set()
    for entry in _get_json(HISTORY_KEY, []):
        if not isinstance(entry, dict) or entry.get("status") in ("failed", "skipped"):
            continue
        started = _parse(entry.get("started_at"))
        rid = (entry.get("repo") or {}).get("id") if isinstance(entry.get("repo"), dict) else None
        if rid and started is not None and started >= cutoff:
            used.add(rid)
    return used


def pick_repo(settings, now=None, rng=None):
    """Pick one random repo and say why. Archived repos are skipped; so are confidential ones unless local AI is
    ready; repos an earlier run used in the last N days are skipped while others exist."""
    import repos

    now = now or _utcnow()
    rng = rng or random
    try:
        added = repos.list_repos()
    except Exception:
        raise NothingToPick("The repositories could not be read, so no repository was picked.")
    local = None
    archived = confidential_waiting = 0
    eligible = []
    for repo in added:
        if not isinstance(repo, dict) or not repo.get("id"):
            continue
        if str(repo.get("status") or "").strip().lower() == "archived" or repo.get("archived") is True:
            archived += 1
            continue
        if _repo_is_confidential(repo):
            if local is None:
                try:
                    local = bool(local_ai_ready())
                except Exception:
                    local = False
            if not local:
                confidential_waiting += 1
                continue
        eligible.append(repo)
    notes = []
    if archived:
        notes.append(f"{archived} archived")
    if confidential_waiting:
        notes.append(f"{confidential_waiting} confidential without local AI ready")
    if not eligible:
        detail = f" (skipped: {', '.join(notes)})" if notes else ""
        raise NothingToPick(f"No repository can be used right now{detail}. Add or check a repository first.")
    days = settings.get("skip_recent_days", 0)
    used = _recently_used(now, days)
    fresh = [r for r in eligible if r["id"] not in used]
    skipped_recent = len(eligible) - len(fresh)
    if skipped_recent and fresh:
        pool = fresh
        notes.append(f"{skipped_recent} used in the last {days} days")
        why = f"Random pick from {len(pool)} repositories"
    elif skipped_recent:
        pool = eligible
        why = f"All {len(eligible)} usable repositories were used in the last {days} days, so a random pick from all of them"
    else:
        pool = eligible
        why = f"Random pick from {len(pool)} repositories"
    if notes:
        why += f" (skipped: {', '.join(notes)})"
    repo = rng.choice(pool)
    return {
        "repo": repo,
        "scope": "team" if repo.get("scope") == "global" else "personal",
        "confidential": _repo_is_confidential(repo),
        "why": why + ".",
    }


# --- Running the pipeline ----------------------------------------------------------------------------------------

def request_run(actor="UI"):
    """Queue one pipeline run. Refused while one is queued or running; the job is never retried."""
    import jobs

    if _RUN_LOCK.locked() or db.has_active_job(PIPELINE_JOB):
        raise AlreadyRunning("An automatic run is already in progress.")
    job = jobs.enqueue(PIPELINE_JOB, actor=actor, max_attempts=1)
    jobs.start_worker()
    return job


def _new_entry(trigger, now):
    return {
        "id": f"ap_{int(now.timestamp() * 1000)}_{uuid.uuid4().hex[:6]}",
        "started_at": _iso(now),
        "finished_at": None,
        "trigger": "manual" if trigger == "manual" else "schedule",
        "status": "running",
        "repo": None,
        "why": "",
        "steps": [],
        "runs": [],
        "error": None,
        "notified": False,
    }


def _step(entry, name, fn):
    """Run one step, recording it. A PipelineError is kept as it is; any other error becomes a plain message."""
    record = {"name": name, "status": "running", "detail": ""}
    entry["steps"].append(record)
    _save_entry(entry)
    try:
        result = fn()
    except PipelineError as exc:
        record.update(status="failed", detail=str(exc))
        _fail(entry, str(exc))
        raise
    except Exception:
        message = f"The {name} step stopped unexpectedly. Check the engine log and run it again."
        record.update(status="failed", detail=message)
        _fail(entry, message)
        raise PipelineError(message)
    record["status"] = "done"
    _save_entry(entry)
    return result


def _fail(entry, message):
    entry.update(status="failed", error=message, finished_at=_iso())
    _save_entry(entry)
    _tell_failed(entry)


def _ready_text(entry):
    kinds = sorted({r.get("kind") for r in entry.get("runs", []) if r.get("kind")})
    return ", ".join(kinds) or "content"


def _public_name(entry):
    repo = entry.get("repo") or {}
    return "a confidential repository" if repo.get("confidential") else (repo.get("name") or "a repository")


def _tell(kind_title, body, route):
    try:
        import notifications
        notifications.notify("auto_pipeline", kind_title, body, route=route)
    except Exception:
        pass


def _log(entry, what):
    # The activity feed goes to the cloud and carries no repo name.
    try:
        db.log_activity("Pipeline", "CMO", f"auto_pipeline:{what}:{entry['id']}", "")
    except Exception:
        pass
    try:
        import cloud_sync
        cloud_sync.request_sync()
    except Exception:
        pass


def _tell_failed(entry):
    _tell("Automatic pipeline stopped", str(entry.get("error") or "A step failed."), "#/plan")
    _log(entry, "failed")


def _research_instruction(repo):
    import workflows

    clause = workflows._repo_clause({"inputs": {"repo_id": repo["id"]}})
    return (
        "Automatic research for the repository chosen for this run. Call read_product_facts and read_repo_changes "
        "for it (and shipped_this_week if useful), then call save_research_note once with what recently changed and "
        "what is safe to say about it under the product rules. Do not write drafts, do not ask questions, and never "
        "publish or post anything." + clause
    )


def _sync(repo):
    import repos

    data = repos.sync_repo(repo["id"], write_files=False)
    if not isinstance(data, dict) or data.get("synced") is not True:
        err = str((data or {}).get("error") or "") if isinstance(data, dict) else ""
        if isinstance(data, dict) and data.get("rate_limited"):
            raise PipelineError("The repository could not be synced: GitHub rate limit reached. Run it again later.")
        if err.startswith("GitHub API HTTP ") and err[16:].isdigit():
            raise PipelineError(f"The repository could not be synced ({err}).")
        raise PipelineError("The repository could not be synced. Check its folder or GitHub address on the Repos page.")
    return data


def _research(repo, confidential):
    import agent_runtime
    import agents

    agent = agents.get_agent("researcher")
    if agent is None:
        raise PipelineError("The researcher agent is not set up, so there was nothing to research with.")
    allowed = [t for t in (agent.get("tools") or []) if t in RESEARCH_TOOLS]
    session = agent_runtime.start_session(
        agent["id"],
        _research_instruction(repo),
        system_prompt=agents.build_system_prompt(agent["id"], confidential=confidential),
        allowed_tools=allowed,
        confidential=confidential,
        title="Automatic research",
    )
    status = (session or {}).get("status")
    if status == "waiting_user":
        try:
            agent_runtime.cancel_session(session["id"])
        except Exception:
            pass
        raise PipelineError("The researcher needed an answer, so the run stopped. Run it again or research by hand.")
    if status != "done":
        raise PipelineError("The researcher could not finish. Check that AI access works, then run it again.")
    return session


def _create(entry, repo, settings):
    import jobs
    import workflows

    runs = []
    for kind in settings["kinds"]:
        inputs = {"repo_id": repo["id"]}
        if kind == "post":
            inputs.update(brief=BRIEFS[kind], platforms=list(settings["platforms"]))
        else:
            inputs.update(topic=BRIEFS[kind])
        run = workflows.start(
            kind, inputs, actor="auto_pipeline", start_worker=False, automatic=entry["trigger"] != "manual",
            auto_run_id=entry["id"],
        )
        runs.append({"id": run["id"], "kind": kind, "status": run["status"]})
        entry["runs"] = list(runs)
        _save_entry(entry)
    jobs.start_worker()
    return runs


def run_pipeline(job_id=None, trigger="schedule", rng=None, now=None):
    """One automatic run: pick, sync, research, start the content workflows. Returns the history entry.

    Raises PipelineError when a step fails (the job then shows as failed and is not retried), AlreadyRunning when
    another run is in progress. A scheduled run does nothing while automatic work is paused; "Run now" still works.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        raise AlreadyRunning("An automatic run is already in progress.")
    try:
        now = now or _utcnow()
        entry = _new_entry(trigger, now)
        if trigger != "manual" and is_paused():
            entry.update(status="skipped", why="Automatic work is paused.", finished_at=_iso(now))
            _append_history(entry)
            return entry
        _append_history(entry)
        settings = get_settings()

        def progress(pct, text):
            if job_id:
                try:
                    import jobs
                    jobs.progress(job_id, pct, text)
                except Exception:
                    pass

        progress(10, "Picking a repository...")

        def pick():
            return pick_repo(settings, now=now, rng=rng)

        picked = _step(entry, "pick", pick)
        repo = picked["repo"]
        entry["repo"] = {
            "id": repo["id"], "name": str(repo.get("name") or repo["id"])[:120], "scope": picked["scope"],
            "confidential": picked["confidential"],
        }
        entry["why"] = picked["why"]
        _save_entry(entry)
        progress(25, "Syncing the repository...")
        _step(entry, "sync", lambda: _sync(repo))
        progress(50, "Researching...")
        _step(entry, "research", lambda: _research(repo, picked["confidential"]))
        progress(80, "Starting content...")
        _step(entry, "create", lambda: _create(entry, repo, settings))
        entry["status"] = "working"
        _save_entry(entry)
        _log(entry, "started")
        return entry
    finally:
        _RUN_LOCK.release()


def on_run_settled(run_id):
    """Called when a workflow run ends or stops for approval. When every workflow of an automatic run has settled the
    owner is told once that the content is ready (or that it failed)."""
    run = db.get_workflow_run(run_id)
    state = (run or {}).get("state") or {}
    entry_id = state.get("auto_run_id")
    if not entry_id:
        return None
    with _META_LOCK:
        entry = _entry(entry_id)
        if entry is None or entry.get("notified") or entry.get("status") == "failed":
            return entry
        statuses = {}
        for item in entry.get("runs", []):
            current = db.get_workflow_run(item.get("id"))
            item["status"] = (current or {}).get("status") or item.get("status")
            statuses[item["id"]] = item["status"]
        if not statuses or any(s in UNSETTLED_RUNS for s in statuses.values()):
            _save_entry(entry)
            return entry
        good = [s for s in statuses.values() if s in ("done", "waiting_user")]
        entry["finished_at"] = _iso()
        entry["notified"] = True
        if good:
            entry["status"] = "ready"
        elif all(s == "cancelled" for s in statuses.values()):
            entry["status"] = "cancelled"
        else:
            entry["status"] = "failed"
            entry["error"] = "Every workflow the run started failed. Open the board to see why."
        _save_entry(entry)
    if entry["status"] == "ready":
        _tell("New content ready to review",
              f"The automatic pipeline made {_ready_text(entry)} for {_public_name(entry)}. Nothing is posted until you approve it.",
              "#/review")
        _log(entry, "ready")
    elif entry["status"] == "failed":
        _tell_failed(entry)
    return entry


# --- What the screens read ---------------------------------------------------------------------------------------

def _schedule_summary():
    for sched in db.load_schedules():
        if sched.get("id") == PIPELINE_JOB:
            return {
                "id": PIPELINE_JOB, "enabled": sched.get("enabled", True) is not False, "cron": sched.get("cron"),
                "interval_hours": sched.get("interval_hours"), "last_run": sched.get("last_run"),
                "last_status": sched.get("last_status"),
            }
    return None


def has_state():
    """True once there is anything for the phone to show: paused, something missed, the pipeline on, or a past run."""
    sched = _schedule_summary() or {}
    return bool(is_paused() or missed_runs() or sched.get("enabled") or history(limit=1) or db.get_meta(SETTINGS_KEY))


def summary_for_phone():
    """What the phone shows about the pipeline: the switch, the schedule, the settings and the last run (a
    confidential repo shows no name)."""
    last = (history(limit=1) or [None])[0]
    last_run = None
    if last:
        repo = last.get("repo") or {}
        last_run = {"status": last.get("status"), "started_at": last.get("started_at"), "trigger": last.get("trigger"),
                    "confidential": bool(repo.get("confidential"))}
        if not repo.get("confidential"):
            last_run.update(repo_name=repo.get("name"), scope=repo.get("scope"), why=last.get("why"),
                            error=last.get("error"))
    sched = _schedule_summary() or {}
    settings = get_settings()
    return {
        "enabled": bool(sched.get("enabled")), "cron": sched.get("cron"), "interval_hours": sched.get("interval_hours"),
        "kinds": settings["kinds"], "skip_recent_days": settings["skip_recent_days"], "last_run": last_run,
    }


def state_for_ui():
    """Everything the Plan page's pipeline card needs in one read."""
    try:
        local = bool(local_ai_ready())
    except Exception:
        local = False
    return {
        **{"paused": is_paused()},
        "paused_since": pause_state()["since"],
        "paused_by": pause_state()["by"],
        "missed": missed_runs(),
        "settings": get_settings(),
        "history": history(),
        "running": _RUN_LOCK.locked() or db.has_active_job(PIPELINE_JOB),
        "allowed_kinds": list(PIPELINE_KINDS),
        "allowed_platforms": list(PIPELINE_PLATFORMS),
        "schedule": _schedule_summary(),
        "local_ai": local,
    }
