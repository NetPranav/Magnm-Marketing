#!/usr/bin/env python3
"""The task board: everything the team is doing, in six columns.

Agent tasks, workflow runs (with their steps), approvals waiting, scheduled jobs and reminders, in the columns
Queued, Running, Waiting for you, Paused, Done and Failed. Done items fade after 7 days (a failure is kept for 14).
The laptop sees everything in full. The copy for the phone (mirror_records) shows a confidential item as a stub
with no text, and offers only the two actions a phone may queue: pause and resume a run.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import db

COLUMNS = (
    ("queued", "Queued"),
    ("running", "Running"),
    ("waiting", "Waiting for you"),
    ("paused", "Paused"),
    ("done", "Done"),
    ("failed", "Failed"),
)
COLUMN_IDS = tuple(c[0] for c in COLUMNS)
KINDS = ("agent_task", "workflow", "approval", "job", "reminder")
ACTION_IDS = ("approve", "pause", "resume", "cancel", "retry")
ACTION_LABELS = {
    "approve": "Review and answer", "pause": "Pause", "resume": "Resume", "cancel": "Cancel", "retry": "Retry",
}
DONE_FADE_DAYS = 7
FAILED_KEEP_DAYS = 14
MAX_RUNS = 150
MAX_SESSIONS = 150
MAX_JOBS = 300
MAX_DONE_SESSIONS = 15
MAX_FAILED_JOBS_PER_NAME = 3
MAX_BOARD_ITEMS = 120  # the phone copy
STUB_TITLE = "Confidential item"
ITEM_ID_RE = re.compile(r"(run|job|session|gate|reminder):[A-Za-z0-9_.\-]{1,128}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

RUN_COLUMN = {
    "queued": "queued", "running": "running", "waiting_user": "waiting", "paused": "paused",
    "done": "done", "failed": "failed", "cancelled": "done",
}
SESSION_COLUMN = {"running": "running", "waiting_user": "waiting", "done": "done", "failed": "failed"}
JOB_COLUMN = {"queued": "queued", "running": "running", "done": "done", "failed": "failed"}


class BadRequest(ValueError):
    """The request itself is malformed (HTTP 400)."""


class Conflict(ValueError):
    """The item exists but is in a state that does not allow this (HTTP 409)."""


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


def _one_line(value, limit):
    return _CONTROL_RE.sub(" ", str(value or "")).strip()[:limit]


def _actions(*ids):
    return [{"id": i, "label": ACTION_LABELS[i]} for i in ids]


def _run_actions(status):
    return {
        "queued": ("pause", "cancel"), "running": ("pause", "cancel"), "paused": ("resume", "cancel"),
        "waiting_user": ("approve", "cancel"), "failed": ("retry",),
    }.get(status, ())


class _Context:
    """What one board build needs: the clock, the confidential rules and the repos, each read once."""

    def __init__(self, now):
        import confidential_guard
        import repos

        self.now = now
        self.scope = confidential_guard.scope()
        try:
            self.repos = {str(r.get("id")): r for r in repos.list_repos() if isinstance(r, dict)}
        except Exception:
            self.repos = {}

    def repo(self, repo_id):
        return self.repos.get(str(repo_id or ""))

    def age_days(self, stamp):
        when = _parse(stamp)
        return max(0, (self.now - when).days) if when else 0


def _item(ctx, **kw):
    item = {
        "id": "", "kind": "", "title": "", "column": "queued", "status": "", "agents": [], "repo": "", "repo_name": "",
        "scope": "", "updated_at": "", "age_days": 0, "confidential": False, "route": "", "automatic": False,
        "detail": {}, "actions": [],
    }
    item.update(kw)
    return item


def _repo_fields(ctx, repo_id):
    repo = ctx.repo(repo_id) if repo_id else None
    if not repo:
        return {"repo": str(repo_id or ""), "repo_name": "", "scope": ""}
    return {"repo": repo["id"], "repo_name": str(repo.get("name") or repo["id"]), "scope": "team" if repo.get("scope") == "global" else "personal"}


def _run_route(run):
    return "#/plan" if run.get("kind") == "weekly_plan" else "#/create"


def _run_items(ctx):
    import cloud_sync
    import workflows

    items, claimed = [], set()
    for run in db.list_workflow_runs(limit=MAX_RUNS):
        state = run.get("state") or {}
        claimed.update(state.get("session_ids") or [])
        claimed.update((state.get("step_sessions") or {}).values())
        column = RUN_COLUMN.get(run["status"])
        if column is None:
            continue
        stamp = run.get("updated_at") or run.get("created_at")
        age = ctx.age_days(stamp)
        if (column == "done" and age > DONE_FADE_DAYS) or (column == "failed" and age > FAILED_KEEP_DAYS):
            continue
        inputs = run.get("inputs") or {}
        enriched = workflows._enrich_run(run) or run
        repo_id = str(inputs.get("repo_id") or "")
        repo, repo_conf = workflows._run_repo(run)
        agents = sorted({
            s.get("agent_id") for s in (db.get_agent_session(sid) for sid in state.get("session_ids") or []) if s and s.get("agent_id")
        })
        title = cloud_sync._brief_title(run.get("kind"), inputs) or str(run.get("kind"))
        confidential = bool(repo_conf) or ctx.scope.text(title, inputs)
        common = dict(
            kind="workflow", title=title, status=run["status"], agents=agents, updated_at=str(stamp or ""), age_days=age,
            confidential=confidential, route=_run_route(run), automatic=bool(state.get("automatic")),
            **_repo_fields(ctx, repo_id),
        )
        steps = [{"name": s["name"], "status": s["status"]} for s in enriched.get("steps") or []]
        detail = {
            "run_kind": run.get("kind"), "steps": steps, "current_step": run.get("current_step"),
            "error": run.get("error") or "", "content_ids": list(enriched.get("content_ids") or [])[:10],
            "paused_by": enriched.get("paused_by"),
        }
        gates = [g for g in (enriched.get("gates") or []) if g.get("status") == "pending"] if run["status"] == "waiting_user" else []
        if gates:
            gate = gates[-1]
            items.append(_item(
                ctx, id=f"gate:{gate['id']}", column="waiting", **{**common, "kind": "approval", "title": str(gate.get("question") or "Decision needed")},
                detail={**detail, "run_id": run["id"], "options": list(gate.get("options") or []), "gate_id": gate["id"]},
                actions=_actions("approve", "cancel"),
            ))
            continue
        items.append(_item(ctx, id=f"run:{run['id']}", column=column, detail=detail, actions=_actions(*_run_actions(run["status"])), **common))
    return items, claimed


def _session_items(ctx, claimed):
    items, done = [], 0
    for sess in db.list_agent_sessions(limit=MAX_SESSIONS):
        if sess["id"] in claimed or sess.get("parent_session_id"):
            continue
        column = SESSION_COLUMN.get(sess["status"])
        if column is None:
            continue
        stamp = sess.get("updated_at") or sess.get("created_at")
        age = ctx.age_days(stamp)
        if (column == "done" and (age > DONE_FADE_DAYS or done >= MAX_DONE_SESSIONS)) or (column == "failed" and age > FAILED_KEEP_DAYS):
            continue
        done += column == "done"
        title = str(sess.get("title") or "").strip() or f"{sess.get('agent_id')} task"
        confidential = bool(sess.get("confidential")) or ctx.scope.session(sess["id"]) or ctx.scope.text(title)
        acts = {"running": ("cancel",), "waiting_user": ("approve", "cancel")}.get(sess["status"], ())
        items.append(_item(
            ctx, id=f"session:{sess['id']}", kind="agent_task", title=title, column=column, status=sess["status"],
            agents=[sess["agent_id"]] if sess.get("agent_id") else [], updated_at=str(stamp or ""), age_days=age,
            confidential=confidential, route="#/team", detail={"error": sess.get("error") or ""}, actions=_actions(*acts),
        ))
    return items


def _job_items(ctx, paused):
    import automation
    import cmo_runtime

    items, done_names, failed_counts = [], set(), {}
    for job in db.list_jobs(limit=MAX_JOBS):
        if job["name"] == "workflow_step":
            continue
        column = JOB_COLUMN.get(job["status"])
        if column is None:
            continue
        if column == "queued" and paused and automation.is_automatic_job(job):
            column = "paused"
        stamp = job.get("finished_at") or job.get("started_at") or job.get("created_at")
        age = ctx.age_days(stamp)
        if column == "done":
            if age > DONE_FADE_DAYS or job["name"] in done_names:
                continue  # a job that runs every few hours shows once, not once per run
            done_names.add(job["name"])
        if column == "failed":
            failed_counts[job["name"]] = failed_counts.get(job["name"], 0) + 1
            if age > FAILED_KEEP_DAYS or failed_counts[job["name"]] > MAX_FAILED_JOBS_PER_NAME:
                continue
        label = cmo_runtime.ALLOWED_JOBS.get(job["name"], job["name"]).split(" (")[0]
        acts = {"queued": ("cancel",), "paused": ("cancel",), "running": ("cancel",), "failed": ("retry",)}.get(column, ())
        confidential = ctx.scope.text(job.get("params") or "") if job.get("params") else False
        items.append(_item(
            ctx, id=f"job:{job['id']}", kind="job", title=label, column=column, status=job["status"],
            updated_at=str(stamp or ""), age_days=age, confidential=confidential, automatic=job.get("actor") in automation.AUTOMATIC_ACTORS,
            detail={"message": job.get("message") or "", "error": job.get("error") or "", "progress": job.get("progress") or 0},
            actions=_actions(*acts),
        ))
    return items


def _reminder_items(ctx):
    items = []
    for rem in db.list_all_reminders():
        if rem.get("done_at"):
            column, stamp = "done", rem["done_at"]
        else:
            due = _parse(rem.get("snoozed_until")) or _parse(rem.get("due_at"))
            column = "waiting" if due is not None and due <= ctx.now else "queued"
            stamp = rem.get("due_at") or rem.get("created_at")
        age = ctx.age_days(stamp)
        if column == "done" and age > DONE_FADE_DAYS:
            continue
        items.append(_item(
            ctx, id=f"reminder:{rem['id']}", kind="reminder", title=str(rem.get("title") or "Reminder"), column=column,
            status="done" if column == "done" else "open", updated_at=str(stamp or ""), age_days=age,
            confidential=bool(ctx.scope.linked(rem, "title", "note")), route="#/today",
            detail={"due_at": rem.get("due_at") or "", "note": rem.get("note") or ""},
        ))
    return items


def _stub(item):
    """A confidential item for the phone: where it is, never what it is."""
    return _item(None, id=item["id"], kind=item["kind"], title=STUB_TITLE, column=item["column"], status=item["status"],
                 updated_at=item["updated_at"], age_days=item["age_days"], confidential=True)


def _matches(item, agent, repo, kind):
    if agent is not None and (not isinstance(agent, str) or agent not in item["agents"]):
        return False
    if repo is not None and (not isinstance(repo, str) or repo != item["repo"]):
        return False
    return kind is None or (isinstance(kind, str) and kind == item["kind"])


def build(agent=None, repo=None, kind=None, now=None, for_phone=False):
    """The board: columns, items (optionally filtered) and what the filters can offer."""
    import automation

    ctx = _Context(now or datetime.now(timezone.utc))
    runs, claimed = _run_items(ctx)
    items = runs + _session_items(ctx, claimed) + _job_items(ctx, automation.is_paused()) + _reminder_items(ctx)
    if ctx.scope.broken:
        for item in items:
            item["confidential"] = True
    # The phone's choices never list a confidential repo or agent task, so they cannot name one.
    offered = [i for i in items if not (for_phone and i["confidential"])]
    filters = {
        "agents": sorted({a for i in offered for a in i["agents"]}),
        "repos": sorted({(i["repo"], i["repo_name"]) for i in offered if i["repo"]}, key=lambda r: (r[1].lower(), r[0])),
        "kinds": sorted({i["kind"] for i in offered}),
    }
    filters["repos"] = [{"id": rid, "name": name or rid} for rid, name in filters["repos"]]
    items = [i for i in items if _matches(i, agent, repo, kind)]
    order = {c: n for n, c in enumerate(COLUMN_IDS)}
    items.sort(key=lambda i: i["updated_at"], reverse=True)
    items.sort(key=lambda i: order[i["column"]])
    if for_phone:
        items = [_phone_item(i) for i in items]
    return {
        "columns": [{"id": cid, "label": label} for cid, label in COLUMNS],
        "items": items,
        "filters": filters,
        "paused": automation.is_paused(),
        "generated_at": ctx.now.isoformat(),
    }


def _phone_item(item):
    if item["confidential"]:
        return _stub(item)
    out = dict(item)
    detail = item["detail"]
    clean = {"steps": [{"name": _one_line(s.get("name"), 40), "status": _one_line(s.get("status"), 20)} for s in (detail.get("steps") or [])[:12]]}
    if item["kind"] == "approval":
        clean["options"] = [_one_line(o, 40) for o in (detail.get("options") or [])[:6]]
    if detail.get("error"):
        import notifications
        clean["error"] = notifications._clean_text(detail["error"], 160)
    out["detail"] = clean
    out["title"] = _one_line(item["title"], 200)
    out["route"] = ""
    # A phone may pause and resume a run. Nothing else leaves the laptop.
    out["actions"] = [a["id"] for a in item["actions"] if item["kind"] == "workflow" and a["id"] in ("pause", "resume")]
    return out


def mirror_records(make_record, fallback_ts, guard):
    """One cloud-mirror record per board item for the phone. Raises when the confidential rules cannot be read, so
    the caller keeps what the cloud already has instead of uploading text it cannot check."""
    if guard is None:
        raise RuntimeError("The confidential rules could not be read, so the board was not copied.")
    board = build(for_phone=True)
    order = {c: n for n, c in enumerate(COLUMN_IDS)}
    # Work that needs attention comes first, so the cap never drops it for old finished items.
    priority = {"waiting": 0, "running": 1, "paused": 2, "queued": 3, "failed": 4, "done": 5}
    items = sorted(board["items"], key=lambda i: (priority[i["column"]], order[i["column"]]))[:MAX_BOARD_ITEMS]
    records = []
    for item in items:
        data = {k: item[k] for k in (
            "kind", "column", "status", "agents", "repo_name", "scope", "age_days", "confidential", "automatic", "detail", "actions"
        )}
        data["updated_at"] = item["updated_at"]
        records.append(make_record("board_item", item["id"], None, item["title"], data, item["updated_at"] or fallback_ts))
    return records


# --- Acting on an item ---------------------------------------------------------------------------------------------

def _find(item_id):
    return next((i for i in build()["items"] if i["id"] == item_id), None)


def act(item_id, action, from_phone=False):
    """Do one action an item offers. Raises BadRequest, LookupError (gone), PermissionError (confidential, from the
    phone) or Conflict (the item is in a state that does not allow it). Approve only returns where to go."""
    if not isinstance(item_id, str) or not ITEM_ID_RE.fullmatch(item_id):
        raise BadRequest("That is not a valid board item.")
    if not isinstance(action, str) or action not in ACTION_IDS:
        raise BadRequest("That action is not known.")
    item = _find(item_id)
    if item is None:
        raise LookupError("That item is not on the board any more.")
    if from_phone and item["confidential"]:
        raise PermissionError("Confidential items can only be changed on the laptop.")
    if action not in [a["id"] for a in item["actions"]]:
        raise Conflict("That action is not available for this item right now.")
    import agent_runtime
    import jobs
    import workflows

    kind, _, raw = item_id.partition(":")
    by = "phone" if from_phone else "laptop"
    if action == "approve":
        return {"route": item["route"]}
    if kind in ("run", "gate"):
        run_id = raw if kind == "run" else item["detail"]["run_id"]
        if action == "pause":
            run = workflows.pause(run_id, by=by)
        elif action == "resume":
            run = workflows.resume(run_id)
        elif action == "cancel":
            run = workflows.cancel(run_id)
        else:
            run = workflows.retry(run_id)
        if run is None:
            raise LookupError("That run is gone.")
        return {"run": run}
    if kind == "job":
        job = jobs.retry(raw) if action == "retry" else jobs.cancel(raw)
        if job is None:
            raise LookupError("That job is gone.")
        return {"job": job}
    if kind == "session" and action == "cancel":
        sess = agent_runtime.cancel_session(raw)
        if sess is None:
            raise LookupError("That task is gone.")
        return {"session": sess}
    raise Conflict("That action is not available for this item.")
