#!/usr/bin/env python3
"""Local SQLite persistence layer for the Magnm Marketing engine.

Consolidates team room messages, per-agent chat history, asset metadata,
schedules, activity audit logs, and AI gateway run telemetry into
``state/magnm.db``.
"""

from datetime import datetime, timezone
import json
import os
import sqlite3
import sys
import threading
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import paths

SCHEMA_VERSION = 2

_local = threading.local()
_state_lock = threading.RLock()
_db_path_override = None
_conn_generation = 0
_initialized_keys = set()

_MESSAGE_COL_KEYS = {
    "id",
    "sender",
    "sender_name",
    "sender_code",
    "text",
    "mentions",
    "attached_assets",
    "timestamp",
}

_TURN_COL_KEYS = {"sender", "name", "text", "timestamp"}


def get_db_path():
    """Return the active SQLite database file path."""
    with _state_lock:
        if _db_path_override is not None:
            return _db_path_override
    return paths.data("state", "magnm.db")


def _legacy_state_dir():
    """Return the directory holding legacy JSON/log state files for the active DB."""
    with _state_lock:
        if _db_path_override is not None:
            return os.path.dirname(_db_path_override)
    return paths.data("state")


def _close_thread_connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _local.conn = None
    _local.path = None
    _local.gen = None


def close_connection():
    """Close the current thread's cached SQLite connection, if open."""
    _close_thread_connection()


def reset_connections():
    """Invalidate cached thread-local connections and close this thread's connection."""
    global _conn_generation
    with _state_lock:
        _conn_generation += 1
        _initialized_keys.clear()
    _close_thread_connection()


def set_db_path(path):
    """Override the database file path (for tests) and reset connections."""
    global _db_path_override
    with _state_lock:
        _db_path_override = os.path.abspath(os.fspath(path)) if path is not None else None
    reset_connections()


def connect():
    """Return a thread-local SQLite connection configured with WAL and Row factory."""
    target_path = get_db_path()
    with _state_lock:
        gen = _conn_generation

    conn = getattr(_local, "conn", None)
    if (
        conn is not None
        and getattr(_local, "path", None) == target_path
        and getattr(_local, "gen", None) == gen
    ):
        return conn

    _close_thread_connection()
    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(target_path, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    _local.path = target_path
    _local.gen = gen
    return conn


def _apply_migration_v1(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            sender TEXT,
            sender_name TEXT,
            sender_code TEXT,
            text TEXT,
            mentions_json TEXT,
            attached_assets_json TEXT,
            extra_json TEXT,
            ts TEXT
        );

        CREATE TABLE IF NOT EXISTS chat_turns (
            rowid INTEGER PRIMARY KEY,
            agent_id TEXT NOT NULL,
            sender TEXT,
            name TEXT,
            text TEXT,
            extra_json TEXT,
            ts TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_chat_turns_agent_rowid
            ON chat_turns(agent_id, rowid);

        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            position INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activity (
            rowid INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            target TEXT NOT NULL,
            task TEXT NOT NULL,
            file TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            rowid INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            role TEXT,
            provider TEXT,
            model_requested TEXT,
            model_used TEXT,
            fallback_used INTEGER NOT NULL DEFAULT 0,
            fallback_reason TEXT,
            latency_ms INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        """
    )


def _apply_migration_v2(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 2,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            output_path TEXT,
            error TEXT,
            actor TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status_created
            ON jobs(status, created_at);
        """
    )


_MIGRATIONS = (
    (1, _apply_migration_v1),
    (2, _apply_migration_v2),
)


def init_db():
    """Create tables if missing, run pending schema migrations, and migrate legacy files once."""
    with _state_lock:
        target_path = get_db_path()
        gen = _conn_generation
        conn = connect()

        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

            rows = conn.execute("SELECT version FROM schema_version").fetchall()
            applied = {int(r["version"]) for r in rows}

            for version, fn in _MIGRATIONS:
                if version not in applied:
                    fn(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(timezone.utc).isoformat()),
                    )

        summary = migrate_from_files()
        _initialized_keys.add((target_path, gen))
        return summary


def ensure_init():
    """Initialize the active database if it has not been initialized in this generation."""
    target_path = get_db_path()
    with _state_lock:
        key = (target_path, _conn_generation)
        if key in _initialized_keys and os.path.exists(target_path):
            return
    init_db()


def _serialize_message(msg):
    msg_id = msg.get("id")
    if msg_id is None or msg_id == "":
        msg_id = f"msg_{uuid.uuid4().hex}"

    extra = {k: v for k, v in msg.items() if k not in _MESSAGE_COL_KEYS}
    for k in ("sender", "sender_name", "sender_code", "text"):
        if k in msg and msg[k] is None:
            extra[k] = None

    if "timestamp" in msg:
        ts = msg["timestamp"]
        if ts is None:
            extra["timestamp"] = None
    else:
        ts = msg.get("ts")
        if ts is not None:
            extra["__no_timestamp"] = True

    mentions_json = json.dumps(msg["mentions"]) if "mentions" in msg else None
    attached_assets_json = json.dumps(msg["attached_assets"]) if "attached_assets" in msg else None
    extra_json = json.dumps(extra) if extra else None

    return (
        str(msg_id),
        msg.get("sender"),
        msg.get("sender_name"),
        msg.get("sender_code"),
        msg.get("text"),
        mentions_json,
        attached_assets_json,
        extra_json,
        ts,
    )


def _deserialize_message(row):
    d = {"id": row["id"]}
    for k in ("sender", "sender_name", "sender_code", "text"):
        if row[k] is not None:
            d[k] = row[k]
    if row["mentions_json"] is not None:
        d["mentions"] = json.loads(row["mentions_json"])
    if row["attached_assets_json"] is not None:
        d["attached_assets"] = json.loads(row["attached_assets_json"])

    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    no_ts = extra.pop("__no_timestamp", False)
    if row["ts"] is not None and not no_ts:
        d["timestamp"] = row["ts"]

    d.update(extra)
    return d


def _serialize_turn(agent_id, turn):
    extra = {k: v for k, v in turn.items() if k not in _TURN_COL_KEYS}
    for k in ("sender", "name", "text"):
        if k in turn and turn[k] is None:
            extra[k] = None

    if "timestamp" in turn:
        ts = turn["timestamp"]
        if ts is None:
            extra["timestamp"] = None
    else:
        ts = turn.get("ts")
        if ts is not None:
            extra["__no_timestamp"] = True

    extra_json = json.dumps(extra) if extra else None
    return (
        agent_id,
        turn.get("sender"),
        turn.get("name"),
        turn.get("text"),
        extra_json,
        ts,
    )


def _deserialize_turn(row):
    d = {}
    for k in ("sender", "name", "text"):
        if row[k] is not None:
            d[k] = row[k]
    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    no_ts = extra.pop("__no_timestamp", False)
    if row["ts"] is not None and not no_ts:
        d["timestamp"] = row["ts"]
    d.update(extra)
    return d


def _parse_activity_line(line):
    """Parse 'ts | actor -> target | task | file' into a 5-tuple, or None if malformed."""
    parts = [p.strip() for p in line.split("|", 3)]
    if len(parts) != 4:
        return None
    ts, route, task, file_col = parts
    if not ts or "->" not in route:
        return None
    actor, target = route.split("->", 1)
    actor = actor.strip()
    target = target.strip()
    if not actor or not target:
        return None
    return ts, actor, target, task, file_col


def _import_state_dir(conn, state_dir, deduplicate=False):
    """Import legacy JSON/log state files from state_dir into an open transaction."""
    counts = {
        "messages": 0,
        "chat_turns": 0,
        "assets": 0,
        "schedules": 0,
        "activity": 0,
        "skipped": 0,
    }
    if not state_dir or not os.path.isdir(state_dir):
        return counts

    # 1. global_messages.json
    msgs_path = os.path.join(state_dir, "global_messages.json")
    if os.path.isfile(msgs_path):
        try:
            with open(msgs_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            msgs = raw.get("messages", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else None)
            if not isinstance(msgs, list):
                counts["skipped"] += 1
            else:
                sql = (
                    """
                    INSERT OR IGNORE INTO messages (
                        id, sender, sender_name, sender_code, text,
                        mentions_json, attached_assets_json, extra_json, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    if deduplicate
                    else """
                    INSERT OR REPLACE INTO messages (
                        id, sender, sender_name, sender_code, text,
                        mentions_json, attached_assets_json, extra_json, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                for msg in msgs:
                    if not isinstance(msg, dict):
                        counts["skipped"] += 1
                        continue
                    try:
                        cur = conn.execute(sql, _serialize_message(msg))
                        if not deduplicate or cur.rowcount > 0:
                            counts["messages"] += 1
                    except Exception:
                        counts["skipped"] += 1
        except Exception:
            counts["skipped"] += 1

    # 2. chats/<agent>.json
    chats_dir = os.path.join(state_dir, "chats")
    if os.path.isdir(chats_dir):
        existing_turns = None
        if deduplicate:
            existing_turns = {
                (r["agent_id"], r["ts"], r["sender"], r["text"])
                for r in conn.execute("SELECT agent_id, ts, sender, text FROM chat_turns").fetchall()
            }
        for fname in sorted(os.listdir(chats_dir)):
            if not fname.endswith(".json"):
                continue
            agent_id = fname[:-5]
            fpath = os.path.join(chats_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    turns = json.load(f)
                if not isinstance(turns, list):
                    counts["skipped"] += 1
                    continue
                for turn in turns:
                    if not isinstance(turn, dict):
                        counts["skipped"] += 1
                        continue
                    try:
                        params = _serialize_turn(agent_id, turn)
                        if existing_turns is not None:
                            key = (params[0], params[5], params[1], params[3])
                            if key in existing_turns:
                                continue
                            existing_turns.add(key)
                        conn.execute(
                            """
                            INSERT INTO chat_turns (agent_id, sender, name, text, extra_json, ts)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            params,
                        )
                        counts["chat_turns"] += 1
                    except Exception:
                        counts["skipped"] += 1
            except Exception:
                counts["skipped"] += 1

    # 3. assets.json
    assets_path = os.path.join(state_dir, "assets.json")
    if os.path.isfile(assets_path):
        try:
            with open(assets_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            assets = raw.get("assets", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else None)
            if not isinstance(assets, list):
                counts["skipped"] += 1
            else:
                asset_sql = (
                    "INSERT OR IGNORE INTO assets (id, data_json, created_at) VALUES (?, ?, ?)"
                    if deduplicate
                    else "INSERT OR REPLACE INTO assets (id, data_json, created_at) VALUES (?, ?, ?)"
                )
                for asset in assets:
                    if not isinstance(asset, dict) or not asset.get("id"):
                        counts["skipped"] += 1
                        continue
                    cur = conn.execute(
                        asset_sql,
                        (
                            str(asset["id"]),
                            json.dumps(asset),
                            asset.get("uploaded_at") or asset.get("created_at"),
                        ),
                    )
                    if not deduplicate or cur.rowcount > 0:
                        counts["assets"] += 1
        except Exception:
            counts["skipped"] += 1

    # 4. schedules.json
    sched_path = os.path.join(state_dir, "schedules.json")
    if os.path.isfile(sched_path):
        try:
            with open(sched_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            scheds = raw.get("schedules", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else None)
            if not isinstance(scheds, list):
                counts["skipped"] += 1
            else:
                sched_sql = (
                    "INSERT OR IGNORE INTO schedules (id, data_json, position) VALUES (?, ?, ?)"
                    if deduplicate
                    else "INSERT OR REPLACE INTO schedules (id, data_json, position) VALUES (?, ?, ?)"
                )
                next_pos = 0
                if deduplicate:
                    max_row = conn.execute(
                        "SELECT COALESCE(MAX(position), -1) AS max_pos FROM schedules"
                    ).fetchone()
                    next_pos = int(max_row["max_pos"]) + 1
                for idx, sched in enumerate(scheds):
                    if not isinstance(sched, dict) or not sched.get("id"):
                        counts["skipped"] += 1
                        continue
                    pos = next_pos if deduplicate else idx
                    cur = conn.execute(sched_sql, (str(sched["id"]), json.dumps(sched), pos))
                    if not deduplicate or cur.rowcount > 0:
                        counts["schedules"] += 1
                        next_pos += 1
        except Exception:
            counts["skipped"] += 1

    # 5. activity.log
    act_path = os.path.join(state_dir, "activity.log")
    if os.path.isfile(act_path):
        existing_activity = None
        if deduplicate:
            existing_activity = {
                f"{r['ts']} | {r['actor']} -> {r['target']} | {r['task']} | {r['file']}"
                for r in conn.execute("SELECT ts, actor, target, task, file FROM activity").fetchall()
            }
        try:
            with open(act_path, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    parsed = _parse_activity_line(line)
                    if parsed is None:
                        counts["skipped"] += 1
                        continue
                    ts, actor, target, task, file_col = parsed
                    canonical = f"{ts} | {actor} -> {target} | {task} | {file_col}"
                    if existing_activity is not None:
                        if line in existing_activity or canonical in existing_activity:
                            continue
                        existing_activity.add(canonical)
                    conn.execute(
                        "INSERT INTO activity (ts, actor, target, task, file) VALUES (?, ?, ?, ?, ?)",
                        parsed,
                    )
                    counts["activity"] += 1
        except Exception:
            counts["skipped"] += 1

    return counts


def migrate_from_files():
    """Import legacy JSON/log state files into SQLite once without altering the originals."""
    with _state_lock:
        conn = connect()
        row = conn.execute("SELECT value FROM meta WHERE key = 'files_migrated'").fetchone()
        if row is not None:
            return {
                "already_migrated": True,
                "messages": 0,
                "chat_turns": 0,
                "assets": 0,
                "schedules": 0,
                "activity": 0,
                "skipped": 0,
            }

        state_dir = _legacy_state_dir()
        with conn:
            counts = _import_state_dir(conn, state_dir, deduplicate=False)
            meta_val = json.dumps(
                {
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "counts": counts,
                }
            )
            conn.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', ?)", (meta_val,))

        return {"already_migrated": False, **counts}


def import_legacy_state(src_state_dir):
    """Import legacy state files from src_state_dir into the current DB with deduplication."""
    ensure_init()
    with _state_lock:
        conn = connect()
        with conn:
            return _import_state_dir(conn, os.fspath(src_state_dir), deduplicate=True)


def load_messages(limit=80):
    """Return the most recent team room messages ordered oldest-to-newest."""
    ensure_init()
    conn = connect()
    if limit is None or limit <= 0:
        rows = conn.execute(
            """
            SELECT rowid, id, sender, sender_name, sender_code, text,
                   mentions_json, attached_assets_json, extra_json, ts
            FROM messages
            ORDER BY rowid ASC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT rowid, id, sender, sender_name, sender_code, text,
                   mentions_json, attached_assets_json, extra_json, ts
            FROM (
                SELECT rowid, id, sender, sender_name, sender_code, text,
                       mentions_json, attached_assets_json, extra_json, ts
                FROM messages
                ORDER BY rowid DESC
                LIMIT ?
            ) sub
            ORDER BY rowid ASC
            """,
            (int(limit),),
        ).fetchall()
    return [_deserialize_message(r) for r in rows]


def save_message(msg):
    """Persist a team room message dictionary to SQLite."""
    ensure_init()
    conn = connect()
    params = _serialize_message(msg)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO messages (
                id, sender, sender_name, sender_code, text,
                mentions_json, attached_assets_json, extra_json, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )


def get_agent_chat_history(agent_id):
    """Return all chat turns for agent_id in insertion order."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT rowid, agent_id, sender, name, text, extra_json, ts
        FROM chat_turns
        WHERE agent_id = ?
        ORDER BY rowid ASC
        """,
        (agent_id,),
    ).fetchall()
    return [_deserialize_turn(r) for r in rows]


def save_agent_chat_history(agent_id, history):
    """Replace all chat turns for agent_id in a single transaction."""
    ensure_init()
    conn = connect()
    serialized = [_serialize_turn(agent_id, turn) for turn in history]
    with conn:
        conn.execute("DELETE FROM chat_turns WHERE agent_id = ?", (agent_id,))
        conn.executemany(
            """
            INSERT INTO chat_turns (agent_id, sender, name, text, extra_json, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            serialized,
        )


def get_agent_chat_counts():
    """Return a dict mapping agent_id to stored turn count."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT agent_id, COUNT(*) AS cnt FROM chat_turns GROUP BY agent_id"
    ).fetchall()
    return {r["agent_id"]: int(r["cnt"]) for r in rows}


def load_assets():
    """Return all registered asset dictionaries in stored order."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT data_json FROM assets ORDER BY rowid ASC").fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def save_assets(assets):
    """Replace the stored asset registry in a single transaction."""
    ensure_init()
    conn = connect()
    rows = [
        (
            str(asset["id"]),
            json.dumps(asset),
            asset.get("uploaded_at") or asset.get("created_at"),
        )
        for asset in assets
    ]
    with conn:
        conn.execute("DELETE FROM assets")
        conn.executemany(
            "INSERT INTO assets (id, data_json, created_at) VALUES (?, ?, ?)",
            rows,
        )


def load_schedules():
    """Return all schedules ordered by position."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT data_json FROM schedules ORDER BY position ASC, rowid ASC"
    ).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def save_schedules(schedules):
    """Replace all schedules in a single transaction, preserving list order."""
    ensure_init()
    conn = connect()
    rows = [
        (str(s.get("id") or f"sched_{idx}"), json.dumps(s), idx)
        for idx, s in enumerate(schedules)
    ]
    with conn:
        conn.execute("DELETE FROM schedules")
        conn.executemany(
            "INSERT INTO schedules (id, data_json, position) VALUES (?, ?, ?)",
            rows,
        )


def log_activity(actor, target, task, file, ts=None):
    """Insert an audit log entry into the activity table."""
    ensure_init()
    conn = connect()
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute(
            "INSERT INTO activity (ts, actor, target, task, file) VALUES (?, ?, ?, ?, ?)",
            (str(ts), str(actor), str(target), str(task), str(file)),
        )


def get_activity_lines(limit=10):
    """Return recent activity entries formatted as 'ts | actor -> target | task | file'."""
    ensure_init()
    conn = connect()
    if limit is None or limit <= 0:
        rows = conn.execute(
            "SELECT rowid, ts, actor, target, task, file FROM activity ORDER BY rowid ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT rowid, ts, actor, target, task, file
            FROM (
                SELECT rowid, ts, actor, target, task, file
                FROM activity
                ORDER BY rowid DESC
                LIMIT ?
            ) sub
            ORDER BY rowid ASC
            """,
            (int(limit),),
        ).fetchall()
    return [
        f"{r['ts']} | {r['actor']} -> {r['target']} | {r['task']} | {r['file']}"
        for r in rows
    ]


def log_run(
    role=None,
    provider=None,
    model_requested=None,
    model_used=None,
    fallback_used=False,
    fallback_reason=None,
    latency_ms=None,
    prompt_tokens=None,
    completion_tokens=None,
    ok=True,
    error=None,
    ts=None,
):
    """Record a single AI gateway attempt row in the runs table."""
    ensure_init()
    conn = connect()
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO runs (
                ts, role, provider, model_requested, model_used,
                fallback_used, fallback_reason, latency_ms,
                prompt_tokens, completion_tokens, ok, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(ts),
                role,
                provider,
                model_requested,
                model_used,
                1 if fallback_used else 0,
                fallback_reason,
                int(latency_ms) if latency_ms is not None else None,
                int(prompt_tokens) if prompt_tokens is not None else None,
                int(completion_tokens) if completion_tokens is not None else None,
                1 if ok else 0,
                error,
            ),
        )


def get_recent_runs(limit=50):
    """Return recent AI gateway run logs ordered newest-first."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT rowid, ts, role, provider, model_requested, model_used,
               fallback_used, fallback_reason, latency_ms,
               prompt_tokens, completion_tokens, ok, error
        FROM runs
        ORDER BY rowid DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "id": r["rowid"],
            "ts": r["ts"],
            "role": r["role"],
            "provider": r["provider"],
            "model_requested": r["model_requested"],
            "model_used": r["model_used"],
            "fallback_used": bool(r["fallback_used"]),
            "fallback_reason": r["fallback_reason"],
            "latency_ms": r["latency_ms"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "ok": bool(r["ok"]),
            "error": r["error"],
        }
        for r in rows
    ]


_JOB_SELECT_COLS = (
    "id, name, status, progress, message, attempts, max_attempts, "
    "created_at, started_at, finished_at, output_path, error, actor, cancel_requested"
)


def _deserialize_job(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "progress": int(row["progress"] or 0),
        "message": row["message"] or "",
        "attempts": int(row["attempts"] or 0),
        "max_attempts": int(row["max_attempts"] or 0),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "output_path": row["output_path"],
        "error": row["error"],
        "actor": row["actor"],
        "cancel_requested": bool(row["cancel_requested"]),
    }


def create_job(job):
    """Insert a new job row and return the persisted job dictionary."""
    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, name, status, progress, message, attempts, max_attempts,
                created_at, started_at, finished_at, output_path, error, actor, cancel_requested
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(job["id"]),
                str(job["name"]),
                str(job.get("status", "queued")),
                int(job.get("progress", 0)),
                job.get("message", ""),
                int(job.get("attempts", 0)),
                int(job.get("max_attempts", 2)),
                str(job["created_at"]),
                job.get("started_at"),
                job.get("finished_at"),
                job.get("output_path"),
                job.get("error"),
                job.get("actor", "UI"),
                1 if job.get("cancel_requested") else 0,
            ),
        )
    return get_job(job["id"])


def get_job(job_id):
    """Return a single job dictionary by ID, or None if missing."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        f"SELECT {_JOB_SELECT_COLS} FROM jobs WHERE id = ?",
        (str(job_id),),
    ).fetchone()
    return _deserialize_job(row)


def list_jobs(limit=20):
    """Return recent jobs ordered newest-first."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        f"SELECT {_JOB_SELECT_COLS} FROM jobs ORDER BY rowid DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [_deserialize_job(r) for r in rows]


def get_latest_job():
    """Return the active running job if present, otherwise the most recent job."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        f"""
        SELECT {_JOB_SELECT_COLS}
        FROM jobs
        ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    return _deserialize_job(row)


def get_next_queued_job():
    """Return the oldest queued job in FIFO order, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        f"""
        SELECT {_JOB_SELECT_COLS}
        FROM jobs
        WHERE status = 'queued' AND cancel_requested = 0
        ORDER BY created_at ASC, rowid ASC
        LIMIT 1
        """
    ).fetchone()
    return _deserialize_job(row)


_UPDATABLE_JOB_FIELDS = {
    "status",
    "progress",
    "message",
    "attempts",
    "max_attempts",
    "started_at",
    "finished_at",
    "output_path",
    "error",
    "cancel_requested",
}


def update_job(job_id, **fields):
    """Update selected columns of a job row and return the updated job dict."""
    ensure_init()
    updates = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_JOB_FIELDS:
            raise ValueError(f"Unsupported job field: {k}")
        updates.append(f"{k} = ?")
        if k == "cancel_requested":
            params.append(1 if v else 0)
        elif k in ("progress", "attempts", "max_attempts") and v is not None:
            params.append(int(v))
        else:
            params.append(v)

    if not updates:
        return get_job(job_id)

    params.append(str(job_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_job(job_id)


def cancel_job_record(job_id, finished_at):
    """Cancel a queued job or mark a running job cancel-requested; return updated job or None."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', finished_at = ?, message = 'Cancelled', cancel_requested = 1
            WHERE id = ? AND status = 'queued'
            """,
            (finished_at, str(job_id)),
        )
        if cur.rowcount == 0:
            conn.execute(
                """
                UPDATE jobs
                SET cancel_requested = 1, message = 'Cancel requested'
                WHERE id = ? AND status = 'running'
                """,
                (str(job_id),),
            )
    return get_job(job_id)


def fail_stale_running_jobs(finished_at, error="engine restarted"):
    """Mark any jobs left in 'running' status as failed and return the updated rows."""
    ensure_init()
    conn = connect()
    with conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status = 'running' ORDER BY rowid ASC"
        ).fetchall()
        stale_ids = [r["id"] for r in rows]
        if stale_ids:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', finished_at = ?, error = ?, message = ?
                WHERE status = 'running'
                """,
                (finished_at, str(error), f"Failed: {error}"),
            )
    return [get_job(jid) for jid in stale_ids]
