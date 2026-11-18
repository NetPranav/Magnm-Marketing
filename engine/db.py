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

SCHEMA_VERSION = 1

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


_MIGRATIONS = (
    (1, _apply_migration_v1),
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
        counts = {
            "already_migrated": False,
            "messages": 0,
            "chat_turns": 0,
            "assets": 0,
            "schedules": 0,
            "activity": 0,
            "skipped": 0,
        }

        with conn:
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
                        for msg in msgs:
                            if not isinstance(msg, dict):
                                counts["skipped"] += 1
                                continue
                            try:
                                conn.execute(
                                    """
                                    INSERT OR REPLACE INTO messages (
                                        id, sender, sender_name, sender_code, text,
                                        mentions_json, attached_assets_json, extra_json, ts
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    _serialize_message(msg),
                                )
                                counts["messages"] += 1
                            except Exception:
                                counts["skipped"] += 1
                except Exception:
                    counts["skipped"] += 1

            # 2. chats/<agent>.json
            chats_dir = os.path.join(state_dir, "chats")
            if os.path.isdir(chats_dir):
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
                                conn.execute(
                                    """
                                    INSERT INTO chat_turns (agent_id, sender, name, text, extra_json, ts)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                                    _serialize_turn(agent_id, turn),
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
                        for asset in assets:
                            if not isinstance(asset, dict) or not asset.get("id"):
                                counts["skipped"] += 1
                                continue
                            conn.execute(
                                "INSERT OR REPLACE INTO assets (id, data_json, created_at) VALUES (?, ?, ?)",
                                (
                                    str(asset["id"]),
                                    json.dumps(asset),
                                    asset.get("uploaded_at") or asset.get("created_at"),
                                ),
                            )
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
                        for idx, sched in enumerate(scheds):
                            if not isinstance(sched, dict) or not sched.get("id"):
                                counts["skipped"] += 1
                                continue
                            conn.execute(
                                "INSERT OR REPLACE INTO schedules (id, data_json, position) VALUES (?, ?, ?)",
                                (str(sched["id"]), json.dumps(sched), idx),
                            )
                            counts["schedules"] += 1
                except Exception:
                    counts["skipped"] += 1

            # 5. activity.log
            act_path = os.path.join(state_dir, "activity.log")
            if os.path.isfile(act_path):
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
                            conn.execute(
                                "INSERT INTO activity (ts, actor, target, task, file) VALUES (?, ?, ?, ?, ?)",
                                parsed,
                            )
                            counts["activity"] += 1
                except Exception:
                    counts["skipped"] += 1

            meta_val = json.dumps(
                {
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "counts": {k: v for k, v in counts.items() if k != "already_migrated"},
                }
            )
            conn.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', ?)", (meta_val,))

        return counts


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
