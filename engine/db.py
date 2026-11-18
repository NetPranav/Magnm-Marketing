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

SCHEMA_VERSION = 4

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


def _apply_migration_v3(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            rowid INTEGER PRIMARY KEY,
            session_id TEXT,
            agent_id TEXT,
            tool TEXT NOT NULL,
            args_json TEXT,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            duration_ms INTEGER,
            ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tool_calls_session
            ON tool_calls(session_id, rowid);

        CREATE TABLE IF NOT EXISTS research_notes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            url TEXT,
            source TEXT,
            agent_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_research_notes_created
            ON research_notes(created_at);

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            status TEXT NOT NULL,
            messages_json TEXT NOT NULL,
            pending_question_json TEXT,
            parent_session_id TEXT,
            depth INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            result_text TEXT,
            error TEXT,
            allowed_tools_json TEXT,
            confidential INTEGER NOT NULL DEFAULT 0,
            steps INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agent_sessions_status_updated
            ON agent_sessions(status, updated_at);
        """
    )


def _apply_migration_v4(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS content_items (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            platform TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            product_id TEXT,
            created_by TEXT,
            session_id TEXT,
            current_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            files_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_content_items_status_updated
            ON content_items(status, updated_at);

        CREATE INDEX IF NOT EXISTS idx_content_items_type_updated
            ON content_items(type, updated_at);

        CREATE INDEX IF NOT EXISTS idx_content_items_session
            ON content_items(session_id);

        CREATE TABLE IF NOT EXISTS content_versions (
            id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            body TEXT NOT NULL,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_content_versions_content_ver
            ON content_versions(content_id, version);

        CREATE TABLE IF NOT EXISTS metrics (
            id TEXT PRIMARY KEY,
            content_id TEXT,
            platform TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            source TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_content_captured
            ON metrics(content_id, captured_at);
        """
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()}
    if "model_used" not in cols:
        conn.execute("ALTER TABLE agent_sessions ADD COLUMN model_used TEXT")
    if "provider" not in cols:
        conn.execute("ALTER TABLE agent_sessions ADD COLUMN provider TEXT")
    if "fallback_used" not in cols:
        conn.execute("ALTER TABLE agent_sessions ADD COLUMN fallback_used INTEGER NOT NULL DEFAULT 0")


_MIGRATIONS = (
    (1, _apply_migration_v1),
    (2, _apply_migration_v2),
    (3, _apply_migration_v3),
    (4, _apply_migration_v4),
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


def log_tool_call(
    session_id,
    agent_id,
    tool,
    args_json,
    ok=True,
    error=None,
    duration_ms=0,
    ts=None,
):
    """Record a tool execution row in the tool_calls table."""
    ensure_init()
    conn = connect()
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO tool_calls (
                session_id, agent_id, tool, args_json, ok, error, duration_ms, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                agent_id,
                str(tool),
                args_json,
                1 if ok else 0,
                error,
                int(duration_ms) if duration_ms is not None else 0,
                str(ts),
            ),
        )


def list_tool_calls(session_id=None, limit=50):
    """Return recent tool_calls rows, optionally filtered by session_id."""
    ensure_init()
    conn = connect()
    if session_id is not None:
        rows = conn.execute(
            """
            SELECT rowid, session_id, agent_id, tool, args_json, ok, error, duration_ms, ts
            FROM tool_calls
            WHERE session_id = ?
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (str(session_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT rowid, session_id, agent_id, tool, args_json, ok, error, duration_ms, ts
            FROM tool_calls
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        {
            "id": r["rowid"],
            "session_id": r["session_id"],
            "agent_id": r["agent_id"],
            "tool": r["tool"],
            "args_json": r["args_json"],
            "ok": bool(r["ok"]),
            "error": r["error"],
            "duration_ms": r["duration_ms"],
            "ts": r["ts"],
        }
        for r in rows
    ]


def save_research_note(note_id, title, body, url=None, source=None, agent_id=None, created_at=None):
    """Insert a research note into research_notes and return its dict."""
    ensure_init()
    conn = connect()
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO research_notes (id, title, body, url, source, agent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(note_id),
                str(title),
                str(body),
                url,
                source,
                agent_id,
                str(created_at),
            ),
        )
    return {
        "id": str(note_id),
        "title": str(title),
        "body": str(body),
        "url": url,
        "source": source,
        "agent_id": agent_id,
        "created_at": str(created_at),
    }


def search_research_notes(query, limit=20):
    """Search research_notes case-insensitively by keyword tokens."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT id, title, body, url, source, agent_id, created_at
        FROM research_notes
        ORDER BY created_at DESC, rowid DESC
        """
    ).fetchall()
    tokens = [t for t in str(query or "").lower().split() if t]
    hits = []
    for r in rows:
        haystack = " ".join(
            str(x or "") for x in (r["title"], r["body"], r["url"], r["source"])
        ).lower()
        if not tokens or all(tok in haystack for tok in tokens):
            hits.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "body": r["body"],
                    "url": r["url"] or "",
                    "source": r["source"] or "research_notes",
                    "agent_id": r["agent_id"],
                    "created_at": r["created_at"],
                }
            )
            if len(hits) >= int(limit):
                break
    return hits


_SESSION_SELECT_COLS = (
    "id, agent_id, status, messages_json, pending_question_json, "
    "parent_session_id, depth, title, result_text, error, "
    "allowed_tools_json, confidential, steps, model_used, provider, fallback_used, "
    "created_at, updated_at"
)


def _deserialize_session(row):
    if row is None:
        return None
    messages = json.loads(row["messages_json"]) if row["messages_json"] else []
    pending = json.loads(row["pending_question_json"]) if row["pending_question_json"] else None
    allowed_tools = json.loads(row["allowed_tools_json"]) if row["allowed_tools_json"] else []
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "status": row["status"],
        "messages": messages,
        "pending_question": pending,
        "parent_session_id": row["parent_session_id"],
        "depth": int(row["depth"] or 0),
        "title": row["title"],
        "result_text": row["result_text"],
        "error": row["error"],
        "allowed_tools": allowed_tools,
        "confidential": bool(row["confidential"]),
        "steps": int(row["steps"] or 0),
        "model_used": row["model_used"],
        "provider": row["provider"],
        "fallback_used": bool(row["fallback_used"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_agent_session(session):
    """Insert a new agent_sessions row and return the deserialized session dict."""
    ensure_init()
    conn = connect()
    now = datetime.now(timezone.utc).isoformat()
    created_at = session.get("created_at") or now
    updated_at = session.get("updated_at") or created_at
    with conn:
        conn.execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, status, messages_json, pending_question_json,
                parent_session_id, depth, title, result_text, error,
                allowed_tools_json, confidential, steps, model_used, provider, fallback_used,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session["id"]),
                str(session["agent_id"]),
                str(session.get("status", "running")),
                json.dumps(session.get("messages") or [], ensure_ascii=False),
                json.dumps(session["pending_question"], ensure_ascii=False)
                if session.get("pending_question") is not None
                else None,
                session.get("parent_session_id"),
                int(session.get("depth", 0)),
                session.get("title"),
                session.get("result_text"),
                session.get("error"),
                json.dumps(list(session.get("allowed_tools") or []), ensure_ascii=False),
                1 if session.get("confidential") else 0,
                int(session.get("steps", 0)),
                session.get("model_used"),
                session.get("provider"),
                1 if session.get("fallback_used") else 0,
                str(created_at),
                str(updated_at),
            ),
        )
    return get_agent_session(session["id"])


def get_agent_session(session_id):
    """Return a single agent session dict by ID, or None if missing."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        f"SELECT {_SESSION_SELECT_COLS} FROM agent_sessions WHERE id = ?",
        (str(session_id),),
    ).fetchone()
    return _deserialize_session(row)


def list_agent_sessions(limit=20, status=None):
    """Return recent agent sessions ordered newest-first, optionally filtered by status."""
    ensure_init()
    conn = connect()
    if status:
        rows = conn.execute(
            f"""
            SELECT {_SESSION_SELECT_COLS}
            FROM agent_sessions
            WHERE status = ?
            ORDER BY updated_at DESC, rowid DESC
            LIMIT ?
            """,
            (str(status), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT {_SESSION_SELECT_COLS}
            FROM agent_sessions
            ORDER BY updated_at DESC, rowid DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [_deserialize_session(r) for r in rows]


_UPDATABLE_SESSION_FIELDS = {
    "status",
    "messages",
    "pending_question",
    "title",
    "result_text",
    "error",
    "allowed_tools",
    "confidential",
    "steps",
    "model_used",
    "provider",
    "fallback_used",
    "updated_at",
}


def update_agent_session(session_id, **fields):
    """Update selected fields on an agent_sessions row and return the updated dict."""
    ensure_init()
    updates = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_SESSION_FIELDS:
            raise ValueError(f"Unsupported agent_session field: {k}")
        if k == "messages":
            updates.append("messages_json = ?")
            params.append(json.dumps(v or [], ensure_ascii=False))
        elif k == "pending_question":
            updates.append("pending_question_json = ?")
            params.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
        elif k == "allowed_tools":
            updates.append("allowed_tools_json = ?")
            params.append(json.dumps(list(v or []), ensure_ascii=False))
        elif k in ("confidential", "fallback_used"):
            updates.append(f"{k} = ?")
            params.append(1 if v else 0)
        elif k == "steps":
            updates.append("steps = ?")
            params.append(int(v))
        else:
            updates.append(f"{k} = ?")
            params.append(v)

    if "updated_at" not in fields:
        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

    params.append(str(session_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE agent_sessions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_agent_session(session_id)


def fail_stale_running_sessions(updated_at, error="engine restarted"):
    """Mark any agent_sessions left in 'running' state as failed and return them."""
    ensure_init()
    conn = connect()
    with conn:
        rows = conn.execute(
            "SELECT id FROM agent_sessions WHERE status = 'running' ORDER BY rowid ASC"
        ).fetchall()
        stale_ids = [r["id"] for r in rows]
        if stale_ids:
            conn.execute(
                """
                UPDATE agent_sessions
                SET status = 'failed', error = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (str(error), str(updated_at)),
            )
    return [get_agent_session(sid) for sid in stale_ids]


def get_latest_waiting_session(agent_id, top_level_only=True):
    """Return the most recent waiting_user session for agent_id, or None."""
    ensure_init()
    conn = connect()
    where_parent = "AND parent_session_id IS NULL" if top_level_only else ""
    row = conn.execute(
        f"""
        SELECT {_SESSION_SELECT_COLS}
        FROM agent_sessions
        WHERE agent_id = ? AND status = 'waiting_user' {where_parent}
        ORDER BY updated_at DESC, rowid DESC
        LIMIT 1
        """,
        (str(agent_id),),
    ).fetchone()
    return _deserialize_session(row)


def get_agent_live_statuses():
    """Return a mapping {agent_id: 'working' | 'waiting_user'} for active sessions."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT agent_id, status
        FROM agent_sessions
        WHERE status IN ('running', 'waiting_user')
        """
    ).fetchall()
    statuses = {}
    for r in rows:
        aid = r["agent_id"]
        st = "working" if r["status"] == "running" else "waiting_user"
        if statuses.get(aid) == "working":
            continue
        statuses[aid] = st
    return statuses


def _deserialize_content_item(row):
    if row is None:
        return None
    files = json.loads(row["files_json"]) if row["files_json"] else []
    return {
        "id": row["id"],
        "type": row["type"],
        "platform": row["platform"],
        "title": row["title"],
        "status": row["status"],
        "product_id": row["product_id"],
        "created_by": row["created_by"],
        "session_id": row["session_id"],
        "current_version": int(row["current_version"] or 1),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "files": files,
    }


def _deserialize_content_version(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "content_id": row["content_id"],
        "version": int(row["version"]),
        "body": row["body"],
        "notes": row["notes"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def create_content_item(
    content_id,
    content_type,
    platform,
    title,
    body,
    notes=None,
    status="draft",
    product_id=None,
    created_by=None,
    session_id=None,
    files=None,
    created_at=None,
):
    """Insert a new content_items row and version 1 in content_versions."""
    ensure_init()
    conn = connect()
    now = created_at or datetime.now(timezone.utc).isoformat()
    ver_id = f"ver_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            """
            INSERT INTO content_items (
                id, type, platform, title, status, product_id, created_by,
                session_id, current_version, created_at, updated_at, files_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(content_id),
                str(content_type),
                platform,
                str(title),
                str(status or "draft"),
                product_id,
                created_by,
                session_id,
                1,
                str(now),
                str(now),
                json.dumps(list(files or []), ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO content_versions (
                id, content_id, version, body, notes, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ver_id,
                str(content_id),
                1,
                str(body),
                notes,
                created_by,
                str(now),
            ),
        )
    return get_content_item(content_id, include_versions=True)


def add_content_version(
    content_id,
    body,
    notes=None,
    created_by=None,
    title=None,
    platform=None,
    product_id=None,
    created_at=None,
):
    """Append the next version to an existing content item and return it."""
    ensure_init()
    conn = connect()
    now = created_at or datetime.now(timezone.utc).isoformat()
    with conn:
        row = conn.execute(
            "SELECT current_version, title, platform, product_id FROM content_items WHERE id = ?",
            (str(content_id),),
        ).fetchone()
        if row is None:
            return None
        next_ver = int(row["current_version"] or 0) + 1
        ver_id = f"ver_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO content_versions (
                id, content_id, version, body, notes, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ver_id,
                str(content_id),
                next_ver,
                str(body),
                notes,
                created_by,
                str(now),
            ),
        )
        conn.execute(
            """
            UPDATE content_items
            SET current_version = ?, title = ?, platform = ?, product_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_ver,
                str(title) if title else row["title"],
                platform if platform is not None else row["platform"],
                product_id if product_id is not None else row["product_id"],
                str(now),
                str(content_id),
            ),
        )
    return get_content_item(content_id, version=next_ver, include_versions=True)


def append_content_files(content_id, new_entries):
    """Append file metadata entries to content_items.files_json."""
    ensure_init()
    conn = connect()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        row = conn.execute(
            "SELECT files_json FROM content_items WHERE id = ?",
            (str(content_id),),
        ).fetchone()
        if row is None:
            return None
        existing = json.loads(row["files_json"]) if row["files_json"] else []
        existing.extend(new_entries or [])
        conn.execute(
            "UPDATE content_items SET files_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(existing, ensure_ascii=False), now, str(content_id)),
        )
    return get_content_item(content_id)


def get_content_item(content_id, version=None, include_versions=False):
    """Fetch a content item with its selected version (default current) and optional all versions."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM content_items WHERE id = ?",
        (str(content_id),),
    ).fetchone()
    if row is None:
        return None
    item = _deserialize_content_item(row)
    target_ver = int(version) if version is not None else item["current_version"]
    ver_row = conn.execute(
        "SELECT * FROM content_versions WHERE content_id = ? AND version = ?",
        (str(content_id), target_ver),
    ).fetchone()
    if ver_row is not None:
        v_dict = _deserialize_content_version(ver_row)
        item["version"] = v_dict["version"]
        item["body"] = v_dict["body"]
        item["notes"] = v_dict["notes"]
    else:
        item["version"] = target_ver
        item["body"] = ""
        item["notes"] = None
    if include_versions:
        v_rows = conn.execute(
            "SELECT * FROM content_versions WHERE content_id = ? ORDER BY version ASC",
            (str(content_id),),
        ).fetchall()
        item["versions"] = [_deserialize_content_version(vr) for vr in v_rows]
    return item


def list_content_items(status=None, content_type=None, limit=20):
    """List content items ordered newest-updated first."""
    ensure_init()
    conn = connect()
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(str(status))
    if content_type:
        clauses.append("type = ?")
        params.append(str(content_type))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit or 20), 500)))
    rows = conn.execute(
        f"""
        SELECT * FROM content_items
        {where}
        ORDER BY updated_at DESC, rowid DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_deserialize_content_item(r) for r in rows]


def list_content_ids_for_session(session_id):
    """Return content_item IDs created during session_id in creation order."""
    if not session_id:
        return []
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT id FROM content_items WHERE session_id = ? ORDER BY rowid ASC",
        (str(session_id),),
    ).fetchall()
    return [r["id"] for r in rows]


def record_metric(metric_id, content_id, platform, metrics, source, captured_at=None, created_by=None):
    """Insert a metrics row and return its deserialized dict."""
    ensure_init()
    conn = connect()
    now = datetime.now(timezone.utc).isoformat()
    cap = str(captured_at or now)
    with conn:
        conn.execute(
            """
            INSERT INTO metrics (
                id, content_id, platform, metrics_json, source, captured_at, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(metric_id),
                content_id,
                str(platform),
                json.dumps(dict(metrics or {}), ensure_ascii=False),
                str(source),
                cap,
                created_by,
                now,
            ),
        )
    return {
        "id": str(metric_id),
        "content_id": content_id,
        "platform": str(platform),
        "metrics": dict(metrics or {}),
        "source": str(source),
        "captured_at": cap,
        "created_by": created_by,
        "created_at": now,
    }


def list_metrics(content_id=None, limit=20):
    """List recorded metrics ordered by captured_at DESC."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 20), 500))
    if content_id:
        rows = conn.execute(
            """
            SELECT * FROM metrics
            WHERE content_id = ?
            ORDER BY captured_at DESC, rowid DESC
            LIMIT ?
            """,
            (str(content_id), max_rows),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM metrics
            ORDER BY captured_at DESC, rowid DESC
            LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "content_id": r["content_id"],
            "platform": r["platform"],
            "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else {},
            "source": r["source"],
            "captured_at": r["captured_at"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
