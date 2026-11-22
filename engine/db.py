#!/usr/bin/env python3
"""Local SQLite persistence layer for the Magnm Marketing engine.

Consolidates team room messages, per-agent chat history, asset metadata,
schedules, activity audit logs, and AI gateway run telemetry into
``state/magnm.db``.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import sqlite3
import sys
import threading
import uuid

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import events
import paths

SCHEMA_VERSION = 24

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


def _apply_migration_v5(conn):
    job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "params_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN params_json TEXT")

    content_cols = {r["name"] for r in conn.execute("PRAGMA table_info(content_items)").fetchall()}
    if "posted_url" not in content_cols:
        conn.execute("ALTER TABLE content_items ADD COLUMN posted_url TEXT")
    if "posted_at" not in content_cols:
        conn.execute("ALTER TABLE content_items ADD COLUMN posted_at TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            current_step INTEGER NOT NULL DEFAULT 0,
            inputs_json TEXT NOT NULL,
            state_json TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_updated
            ON workflow_runs(status, updated_at);

        CREATE TABLE IF NOT EXISTS gates (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            step INTEGER NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer TEXT,
            feedback TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            answered_at TEXT,
            FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_gates_run_step
            ON gates(run_id, step);
        """
    )


def _apply_migration_v6(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repo_snapshots (
            id TEXT PRIMARY KEY,
            repo_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            data_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_repo_snapshots_repo_fetched
            ON repo_snapshots(repo_id, fetched_at);

        CREATE TABLE IF NOT EXISTS product_facts (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            text TEXT NOT NULL,
            source_url TEXT,
            source_kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_by TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_product_facts_product_status
            ON product_facts(product_id, status);

        CREATE INDEX IF NOT EXISTS idx_product_facts_status_created
            ON product_facts(status, created_at);
        """
    )


def _apply_migration_v7(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_cache (
            source TEXT NOT NULL,
            query TEXT NOT NULL,
            results_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (source, query)
        );

        CREATE TABLE IF NOT EXISTS watch_topics (
            id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            sources_json TEXT,
            cadence_hours INTEGER NOT NULL DEFAULT 24,
            days INTEGER NOT NULL DEFAULT 7,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_items (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            snippet TEXT,
            source TEXT NOT NULL,
            published_at TEXT,
            topic_id TEXT,
            found_at TEXT NOT NULL,
            score REAL
        );

        CREATE INDEX IF NOT EXISTS idx_research_items_topic_found
            ON research_items(topic_id, found_at);

        CREATE INDEX IF NOT EXISTS idx_research_items_found
            ON research_items(found_at);
        """
    )


def _apply_migration_v8(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_memory (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT NOT NULL,
            content_id TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agent_memory_agent_created
            ON agent_memory(agent_id, pinned DESC, created_at DESC);
        """
    )


def _apply_migration_v9(conn):
    job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "result_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN result_json TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_comments (
            id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            kind TEXT NOT NULL,
            anchor_json TEXT NOT NULL,
            body TEXT NOT NULL,
            author TEXT NOT NULL,
            parent_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            mentions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_review_comments_content_created
            ON review_comments(content_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_review_comments_parent
            ON review_comments(parent_id);

        CREATE TABLE IF NOT EXISTS review_suggestions (
            id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            base_version INTEGER NOT NULL,
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            original TEXT NOT NULL,
            replacement TEXT NOT NULL,
            author TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_review_suggestions_content_status
            ON review_suggestions(content_id, status, created_at);

        CREATE TABLE IF NOT EXISTS review_checklist (
            content_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT NOT NULL,
            checked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (content_id, item_key),
            FOREIGN KEY (content_id) REFERENCES content_items(id) ON DELETE CASCADE
        );
        """
    )


def _apply_migration_v10(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pitch_proposals (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'proposal',
            status TEXT NOT NULL DEFAULT 'draft',
            client_name TEXT,
            business_name TEXT,
            contact_json TEXT,
            preset_json TEXT NOT NULL,
            price_overrides_json TEXT NOT NULL DEFAULT '{}',
            custom_items_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            created_by TEXT,
            share_id TEXT NOT NULL UNIQUE,
            pitch_commit TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pitch_proposals_status_updated
            ON pitch_proposals(status, updated_at);

        CREATE INDEX IF NOT EXISTS idx_pitch_proposals_kind_updated
            ON pitch_proposals(kind, updated_at);

        CREATE TABLE IF NOT EXISTS pitch_base_prices (
            item_id TEXT PRIMARY KEY,
            price INTEGER NOT NULL,
            updated_by TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_outbox (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sync_outbox_sent_created
            ON sync_outbox(sent_at, created_at);
        """
    )


def _apply_migration_v11(conn):
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(pitch_proposals)").fetchall()
    }
    if "payment_plan_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN payment_plan_json TEXT NOT NULL DEFAULT '[]'")
    if "payment_note" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN payment_note TEXT NOT NULL DEFAULT ''")
    if "timeline_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN timeline_json TEXT NOT NULL DEFAULT '[]'")
    if "service_plans_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN service_plans_json TEXT NOT NULL DEFAULT '[]'")

    existing = conn.execute(
        "SELECT id FROM pitch_proposals WHERE kind = 'default' OR slug = 'default-quotation' LIMIT 1"
    ).fetchone()
    if existing is None:
        share_id = secrets.token_urlsafe(12)
        while conn.execute("SELECT 1 FROM pitch_proposals WHERE share_id = ?", (share_id,)).fetchone() is not None:
            share_id = secrets.token_urlsafe(12)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO pitch_proposals (
                id, slug, kind, status, client_name, business_name,
                contact_json, preset_json, price_overrides_json, custom_items_json,
                notes, created_by, share_id, pitch_commit,
                payment_plan_json, payment_note, timeline_json, service_plans_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prop_default",
                "default-quotation",
                "default",
                "ready",
                "",
                "",
                "{}",
                "{}",
                "{}",
                "[]",
                "",
                "system",
                share_id,
                "",
                "[]",
                "",
                "[]",
                "[]",
                now,
                now,
            ),
        )


def _apply_migration_v12(conn):
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(pitch_proposals)").fetchall()
    }
    if "hidden_sheets_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN hidden_sheets_json TEXT NOT NULL DEFAULT '[]'")
    if "hidden_about_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN hidden_about_json TEXT NOT NULL DEFAULT '[]'")
    if "custom_categories_json" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN custom_categories_json TEXT NOT NULL DEFAULT '[]'")


def _apply_migration_v13(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cloud_sync_state (
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            local_id TEXT NOT NULL,
            hash TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY (user_id, kind, local_id)
        );
        """
    )


def _apply_migration_v14(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_upload_queue (
            user_id TEXT NOT NULL,
            local_source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, local_source_id, kind)
        );
        """
    )


def _apply_migration_v15(conn):
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(pitch_proposals)").fetchall()
    }
    if "published_at" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN published_at TEXT")
    if "link_status" not in cols:
        conn.execute("ALTER TABLE pitch_proposals ADD COLUMN link_status TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pitch_quote_requests (
            id TEXT PRIMARY KEY,
            share_id TEXT NOT NULL,
            local_proposal_id TEXT,
            choices_json TEXT NOT NULL,
            contact_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            read_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pitch_quote_requests_created
            ON pitch_quote_requests(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_pitch_quote_requests_read
            ON pitch_quote_requests(read_at, created_at DESC);
        """
    )


def _apply_migration_v16(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS team_knowledge_state (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            applied_hash TEXT,
            applied_remote_updated_at TEXT,
            published_hash TEXT,
            published_at TEXT,
            PRIMARY KEY (user_id, key)
        );
        """
    )


def _apply_migration_v17(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            route TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_notifications_created
            ON notifications(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_notifications_read
            ON notifications(read_at, created_at DESC);

        CREATE TABLE IF NOT EXISTS notification_settings (
            kind TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _apply_migration_v18(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            due_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'custom',
            content_id TEXT,
            created_by TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            done_at TEXT,
            snoozed_until TEXT,
            fired_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_reminders_due
            ON reminders(done_at, due_at);

        CREATE INDEX IF NOT EXISTS idx_reminders_content_kind
            ON reminders(content_id, kind);

        CREATE TABLE IF NOT EXISTS metric_proposals (
            id TEXT PRIMARY KEY,
            content_id TEXT,
            platform TEXT,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            image_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_metric_proposals_status_created
            ON metric_proposals(status, created_at DESC);
        """
    )


def _apply_migration_v19(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS weekly_summaries (
            id TEXT PRIMARY KEY,
            week_start TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            highlights_json TEXT NOT NULL DEFAULT '{}',
            narrative TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ready'
        );

        CREATE INDEX IF NOT EXISTS idx_weekly_summaries_week_start
            ON weekly_summaries(week_start DESC);

        CREATE TABLE IF NOT EXISTS learnings (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            applies_to_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            source_summary_id TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_learnings_status_created
            ON learnings(status, created_at DESC);
        """
    )


def _apply_migration_v20(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS coding_runs (
            id TEXT PRIMARY KEY,
            engine TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'read_only',
            prompt TEXT NOT NULL,
            repo_ids_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'queued',
            workspace TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            exit_code INTEGER,
            summary TEXT,
            log_path TEXT,
            out_files_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            transport TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_coding_runs_created
            ON coding_runs(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_coding_runs_status_engine
            ON coding_runs(status, engine);
        """
    )


def _apply_migration_v21(conn):
    """Add single-use approval, confidentiality, and requester columns to coding_runs idempotently."""
    existing_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(coding_runs)").fetchall()
    }
    columns_to_add = (
        ("approval_hash", "TEXT"),
        ("approved_at", "TEXT"),
        ("approval_consumed", "INTEGER NOT NULL DEFAULT 0"),
        ("confidential", "INTEGER NOT NULL DEFAULT 0"),
        ("requested_by", "TEXT"),
        ("session_id", "TEXT"),
    )
    for col_name, col_def in columns_to_add:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE coding_runs ADD COLUMN {col_name} {col_def}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_coding_runs_status_approved
            ON coding_runs(status, approval_consumed)
        """
    )


def _apply_migration_v22(conn):
    """Add phone_devices and phone_inbox_receipts tables idempotently."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS phone_devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT,
            last_ip TEXT,
            revoked_at TEXT,
            owner_user_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_phone_devices_token_hash
            ON phone_devices(token_hash);

        CREATE INDEX IF NOT EXISTS idx_phone_devices_created
            ON phone_devices(created_at DESC);

        CREATE TABLE IF NOT EXISTS phone_inbox_receipts (
            device_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (device_id, client_id)
        );
        """
    )


def _apply_migration_v23(conn):
    """Add social_settings, social_runs, social_page_counts, and social_challenges tables idempotently."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS social_settings (
            platform TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            acknowledged_at TEXT,
            acknowledgement_text TEXT,
            paused_until TEXT,
            pause_reason TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS social_runs (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            recipe TEXT NOT NULL,
            argument TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'awaiting_start',
            approval_hash TEXT NOT NULL,
            approved_at TEXT,
            approval_consumed INTEGER NOT NULL DEFAULT 0,
            requested_by TEXT,
            session_id TEXT,
            pages_used INTEGER NOT NULL DEFAULT 0,
            challenge_kind TEXT,
            challenge_detected_at TEXT,
            challenge_notif_id TEXT,
            result_json TEXT,
            summary TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_social_runs_created
            ON social_runs(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_social_runs_status
            ON social_runs(status);

        CREATE TABLE IF NOT EXISTS social_page_counts (
            platform TEXT NOT NULL,
            day_utc TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (platform, day_utc)
        );

        CREATE TABLE IF NOT EXISTS social_challenges (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            run_id TEXT,
            challenge_kind TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            resolution TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_social_challenges_platform_detected
            ON social_challenges(platform, detected_at DESC);
        """
    )


def _apply_migration_v24(conn):
    """Add posting_accounts (no secrets) and posting_attempts tables idempotently."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS posting_accounts (
            platform TEXT PRIMARY KEY,
            account_id TEXT,
            username TEXT,
            display_name TEXT,
            client_id TEXT,
            connected_at TEXT NOT NULL,
            expires_at TEXT,
            last_refreshed_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS posting_attempts (
            id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            account_id TEXT,
            confirmation_id TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            options_json TEXT,
            status TEXT NOT NULL DEFAULT 'prepared',
            post_id TEXT,
            post_url TEXT,
            error TEXT,
            prepared_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            confirmed_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_posting_attempts_content
            ON posting_attempts(content_id, prepared_at DESC);

        CREATE INDEX IF NOT EXISTS idx_posting_attempts_status
            ON posting_attempts(status);
        """
    )


_MIGRATIONS = (
    (1, _apply_migration_v1),
    (2, _apply_migration_v2),
    (3, _apply_migration_v3),
    (4, _apply_migration_v4),
    (5, _apply_migration_v5),
    (6, _apply_migration_v6),
    (7, _apply_migration_v7),
    (8, _apply_migration_v8),
    (9, _apply_migration_v9),
    (10, _apply_migration_v10),
    (11, _apply_migration_v11),
    (12, _apply_migration_v12),
    (13, _apply_migration_v13),
    (14, _apply_migration_v14),
    (15, _apply_migration_v15),
    (16, _apply_migration_v16),
    (17, _apply_migration_v17),
    (18, _apply_migration_v18),
    (19, _apply_migration_v19),
    (20, _apply_migration_v20),
    (21, _apply_migration_v21),
    (22, _apply_migration_v22),
    (23, _apply_migration_v23),
    (24, _apply_migration_v24),
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


SCHEDULES_LOCK = threading.RLock()


def modify_schedules(mutator):
    """Load the schedules, let `mutator(list)` change them in place, and save them if they changed.

    Every writer goes through here so the scheduler thread and a user edit can't overwrite each other.
    Whatever `mutator` returns is returned; if it raises, nothing is saved.
    """
    with SCHEDULES_LOCK:
        schedules = load_schedules()
        before = json.dumps(schedules, sort_keys=True)
        result = mutator(schedules)
        if json.dumps(schedules, sort_keys=True) != before:
            save_schedules(schedules)
        return result


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


def count_mcp_calls_today(now=None):
    """Return the count of MCP tool calls logged today in IST (Asia/Kolkata)."""
    ensure_init()
    conn = connect()
    ist = timezone(timedelta(hours=5, minutes=30))  # IST has no DST; Windows has no tz database.
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    now_ist = ref.astimezone(ist)
    start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    end_ist = start_ist + timedelta(days=1)
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)
    # Query with a generous UTC window then compare parsed timestamps exactly.
    floor_iso = (start_utc - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT ts FROM activity WHERE target = 'MCP' AND ts >= ?",
        (floor_iso,),
    ).fetchall()
    count = 0
    for row in rows:
        raw = str(row["ts"] or "").strip()
        if not raw:
            continue
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        if start_utc <= dt_utc < end_utc:
            count += 1
    return count


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
    "created_at, started_at, finished_at, output_path, error, actor, cancel_requested, params_json, result_json"
)


def _deserialize_job(row):
    if row is None:
        return None
    keys = row.keys()
    raw_params = row["params_json"] if "params_json" in keys else None
    params = json.loads(raw_params) if raw_params else {}
    raw_result = row["result_json"] if "result_json" in keys else None
    result = json.loads(raw_result) if raw_result else None
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
        "params": params,
        "params_json": raw_params,
        "result": result,
    }


def create_job(job):
    """Insert a new job row and return the persisted job dictionary."""
    ensure_init()
    conn = connect()
    if job.get("params") is not None:
        params_json = json.dumps(job["params"], ensure_ascii=False)
    else:
        params_json = job.get("params_json")
    with conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, name, status, progress, message, attempts, max_attempts,
                created_at, started_at, finished_at, output_path, error, actor, cancel_requested, params_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                params_json,
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
    "result",
    "result_json",
}


def update_job(job_id, **fields):
    """Update selected columns of a job row and return the updated job dict."""
    ensure_init()
    updates = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_JOB_FIELDS:
            raise ValueError(f"Unsupported job field: {k}")
        if k == "result":
            updates.append("result_json = ?")
            params.append(json.dumps(v, ensure_ascii=False, default=str) if v is not None else None)
        else:
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


def list_research_notes(limit=20):
    """Return research_notes ordered newest first."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 20), 500))
    rows = conn.execute(
        """
        SELECT id, title, body, url, source, agent_id, created_at
        FROM research_notes
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (cap,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "body": r["body"],
            "url": r["url"] or "",
            "source": r["source"] or "research_notes",
            "agent_id": r["agent_id"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


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
    keys = row.keys()
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
        "posted_url": row["posted_url"] if "posted_url" in keys else None,
        "posted_at": row["posted_at"] if "posted_at" in keys else None,
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


def set_content_files(content_id, entries):
    """Replace files_json for content_id with entries and return the updated item."""
    ensure_init()
    conn = connect()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        row = conn.execute("SELECT id FROM content_items WHERE id = ?", (str(content_id),)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE content_items SET files_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(list(entries or []), ensure_ascii=False), now, str(content_id)),
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


def mark_ready(content_id):
    """Transition a content item from draft to ready and return the updated item."""
    ensure_init()
    item = get_content_item(content_id)
    if item is None:
        raise ValueError(f"Content item '{content_id}' not found")
    if item["status"] == "posted":
        raise ValueError(f"Content item '{content_id}' is already posted")
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE content_items SET status = 'ready', updated_at = ? WHERE id = ?",
            (now, str(content_id)),
        )
    events.publish("content_status", {"content_id": str(content_id), "status": "ready"})
    return get_content_item(content_id, include_versions=True)


def _parse_utc_dt(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_utc_iso(value, fallback=None):
    dt = _parse_utc_dt(value)
    if dt is not None:
        return dt.isoformat()
    if fallback is not None:
        return str(fallback)
    return datetime.now(timezone.utc).isoformat()


def mark_posted(content_id, url=None, platform=None, posted_at=None):
    """Mark a content item as posted, record URL/timestamp, schedule results reminders, log activity, and publish event."""
    ensure_init()
    item = get_content_item(content_id)
    if item is None:
        raise ValueError(f"Content item '{content_id}' not found")
    now = datetime.now(timezone.utc).isoformat()
    posted_ts = _normalize_utc_iso(posted_at, fallback=now) if posted_at else now
    target_platform = str(platform) if platform is not None and str(platform).strip() else item.get("platform")
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE content_items
            SET status = 'posted', posted_url = ?, posted_at = ?, platform = ?, updated_at = ?
            WHERE id = ?
            """,
            (url, posted_ts, target_platform, now, str(content_id)),
        )
    ensure_results_reminders_for_post(
        content_id=str(content_id),
        title=item.get("title") or str(content_id),
        posted_at=posted_ts,
    )
    log_activity(
        "CEO",
        str(target_platform or "social"),
        f"mark_posted:{content_id}",
        str(url or content_id),
    )
    events.publish(
        "content_status",
        {
            "content_id": str(content_id),
            "status": "posted",
            "url": url,
            "platform": target_platform,
            "posted_at": posted_ts,
        },
    )
    return get_content_item(content_id, include_versions=True)


def unmark_posted(content_id, status="ready"):
    """Revert a posted content item back to ready/draft, clearing posted fields and pending results reminders."""
    ensure_init()
    target_status = str(status or "ready").strip().lower()
    if target_status not in ("ready", "draft"):
        raise ValueError("status must be 'ready' or 'draft'")
    item = get_content_item(content_id)
    if item is None:
        raise ValueError(f"Content item '{content_id}' not found")
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE content_items
            SET status = ?, posted_url = NULL, posted_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (target_status, now, str(content_id)),
        )
    delete_pending_results_reminders(content_id)
    events.publish(
        "content_status",
        {"content_id": str(content_id), "status": target_status},
    )
    return get_content_item(content_id, include_versions=True)


def _deserialize_workflow_run(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "current_step": int(row["current_step"] or 0),
        "inputs": json.loads(row["inputs_json"]) if row["inputs_json"] else {},
        "state": json.loads(row["state_json"]) if row["state_json"] else {},
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_workflow_run(run):
    """Insert a new workflow_runs row and return the deserialized dict."""
    ensure_init()
    conn = connect()
    now = datetime.now(timezone.utc).isoformat()
    created_at = str(run.get("created_at") or now)
    updated_at = str(run.get("updated_at") or created_at)
    with conn:
        conn.execute(
            """
            INSERT INTO workflow_runs (
                id, kind, status, current_step, inputs_json, state_json, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run["id"]),
                str(run["kind"]),
                str(run.get("status", "queued")),
                int(run.get("current_step", 0)),
                json.dumps(run.get("inputs") or {}, ensure_ascii=False),
                json.dumps(run.get("state") or {}, ensure_ascii=False),
                run.get("error"),
                created_at,
                updated_at,
            ),
        )
    return get_workflow_run(run["id"])


def get_workflow_run(run_id):
    """Return a single workflow_runs row by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM workflow_runs WHERE id = ?",
        (str(run_id),),
    ).fetchone()
    return _deserialize_workflow_run(row)


def list_workflow_runs(limit=20, status=None):
    """Return recent workflow runs ordered newest-first, optionally filtered by status."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 20), 500))
    if status:
        rows = conn.execute(
            "SELECT * FROM workflow_runs WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (str(status), max_rows),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workflow_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max_rows,),
        ).fetchall()
    return [_deserialize_workflow_run(r) for r in rows]


_UPDATABLE_WORKFLOW_FIELDS = {"status", "current_step", "inputs", "state", "error", "updated_at"}


def update_workflow_run(run_id, **fields):
    """Update selected columns on a workflow_runs row and return the updated dict."""
    ensure_init()
    updates = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_WORKFLOW_FIELDS:
            raise ValueError(f"Unsupported workflow_run field: {k}")
        if k == "inputs":
            updates.append("inputs_json = ?")
            params.append(json.dumps(v or {}, ensure_ascii=False))
        elif k == "state":
            updates.append("state_json = ?")
            params.append(json.dumps(v or {}, ensure_ascii=False))
        elif k == "current_step":
            updates.append("current_step = ?")
            params.append(int(v))
        else:
            updates.append(f"{k} = ?")
            params.append(v)

    if "updated_at" not in fields:
        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

    params.append(str(run_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE workflow_runs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_workflow_run(run_id)


def _deserialize_gate(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step": int(row["step"]),
        "question": row["question"],
        "options": json.loads(row["options_json"]) if row["options_json"] else [],
        "answer": row["answer"],
        "feedback": row["feedback"],
        "status": row["status"],
        "created_at": row["created_at"],
        "answered_at": row["answered_at"],
    }


def create_gate(run_id, step, question, options=None, gate_id=None):
    """Create a pending human decision gate row and return it."""
    ensure_init()
    gid = str(gate_id or f"gate_{uuid.uuid4().hex[:12]}")
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO gates (
                id, run_id, step, question, options_json, answer, feedback, status, created_at, answered_at
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 'pending', ?, NULL)
            """,
            (
                gid,
                str(run_id),
                int(step),
                str(question),
                json.dumps(list(options or []), ensure_ascii=False),
                now,
            ),
        )
    return get_gate(gid)


def get_gate(gate_id):
    """Return a single gate dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM gates WHERE id = ?", (str(gate_id),)).fetchone()
    return _deserialize_gate(row)


def list_gates_for_run(run_id):
    """Return all gates for a workflow run in creation order."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM gates WHERE run_id = ? ORDER BY rowid ASC",
        (str(run_id),),
    ).fetchall()
    return [_deserialize_gate(r) for r in rows]


def answer_gate_record(gate_id, answer, feedback=None, answered_at=None):
    """Persist an answer on a pending gate and return the updated gate dict."""
    ensure_init()
    now = str(answered_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE gates
            SET answer = ?, feedback = ?, status = 'answered', answered_at = ?
            WHERE id = ?
            """,
            (str(answer), feedback, now, str(gate_id)),
        )
    return get_gate(gate_id)


def cancel_pending_gates(run_id):
    """Mark all pending gates for run_id as cancelled."""
    ensure_init()
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE gates
            SET status = 'cancelled', answered_at = COALESCE(answered_at, ?)
            WHERE run_id = ? AND status = 'pending'
            """,
            (now, str(run_id)),
        )


def find_waiting_workflow_for_session(session_id):
    """Return workflow runs currently waiting_user on the given agent session_id."""
    if not session_id:
        return []
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM workflow_runs WHERE status = 'waiting_user' ORDER BY rowid ASC"
    ).fetchall()
    matches = []
    for r in rows:
        run = _deserialize_workflow_run(r)
        if (run.get("state") or {}).get("waiting_session_id") == str(session_id):
            matches.append(run)
    return matches


def list_stale_running_workflows():
    """Return workflow runs in 'running' status (or 'queued' with no active job) for crash recovery."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM workflow_runs WHERE status = 'running' ORDER BY created_at ASC, rowid ASC"
    ).fetchall()
    return [_deserialize_workflow_run(r) for r in rows]


def has_active_workflow_job(run_id):
    """Return True if a queued or running workflow_step job already exists for run_id."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT params_json FROM jobs WHERE name = 'workflow_step' AND status IN ('queued', 'running')"
    ).fetchall()
    for r in rows:
        if not r["params_json"]:
            continue
        try:
            p = json.loads(r["params_json"])
        except Exception:
            continue
        if p.get("run_id") == str(run_id):
            return True
    return False


def cancel_workflow_jobs(run_id, finished_at):
    """Cancel any queued or running workflow_step jobs belonging to run_id."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT id, status, params_json FROM jobs WHERE name = 'workflow_step' AND status IN ('queued', 'running')"
    ).fetchall()
    for r in rows:
        try:
            p = json.loads(r["params_json"]) if r["params_json"] else {}
        except Exception:
            p = {}
        if p.get("run_id") == str(run_id):
            cancel_job_record(r["id"], finished_at)


def has_active_job(job_name):
    """Return True if a job with job_name is currently queued or running."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE name = ? AND status IN ('queued', 'running') LIMIT 1",
        (str(job_name),),
    ).fetchone()
    return row is not None


def count_session_tool_calls(session_id, tool_name):
    """Count logged tool calls for a given session_id and tool name."""
    if not session_id:
        return 0
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM tool_calls WHERE session_id = ? AND tool = ? AND ok = 1",
        (str(session_id), str(tool_name)),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def save_repo_snapshot(repo_id, fetched_at, data, keep=10):
    """Persist a repo snapshot and keep only the latest `keep` snapshots per repo_id."""
    ensure_init()
    conn = connect()
    snap_id = f"snap_{uuid.uuid4().hex[:12]}"
    payload = json.dumps(data, ensure_ascii=False)
    with conn:
        conn.execute(
            "INSERT INTO repo_snapshots (id, repo_id, fetched_at, data_json) VALUES (?, ?, ?, ?)",
            (snap_id, str(repo_id), str(fetched_at), payload),
        )
        rows = conn.execute(
            "SELECT id FROM repo_snapshots WHERE repo_id = ? ORDER BY fetched_at DESC, rowid DESC",
            (str(repo_id),),
        ).fetchall()
        if len(rows) > int(keep):
            to_delete = [(r["id"],) for r in rows[int(keep):]]
            conn.executemany("DELETE FROM repo_snapshots WHERE id = ?", to_delete)
    return {"id": snap_id, "repo_id": str(repo_id), "fetched_at": str(fetched_at), "data": data}


def get_latest_repo_snapshot(repo_id):
    """Return the most recent snapshot dict {id, repo_id, fetched_at, data} for repo_id, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT id, repo_id, fetched_at, data_json FROM repo_snapshots WHERE repo_id = ? ORDER BY fetched_at DESC, rowid DESC LIMIT 1",
        (str(repo_id),),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "repo_id": row["repo_id"],
        "fetched_at": row["fetched_at"],
        "data": json.loads(row["data_json"]),
    }


def list_repo_snapshots(repo_id, limit=10):
    """Return recent snapshots for repo_id ordered newest-first."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT id, repo_id, fetched_at, data_json FROM repo_snapshots WHERE repo_id = ? ORDER BY fetched_at DESC, rowid DESC LIMIT ?",
        (str(repo_id), int(limit)),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "repo_id": r["repo_id"],
            "fetched_at": r["fetched_at"],
            "data": json.loads(r["data_json"]),
        }
        for r in rows
    ]


_VALID_FACT_SOURCE_KINDS = ("readme", "release", "pr", "commit", "manual")
_VALID_FACT_STATUSES = ("pending", "approved", "rejected")


def _deserialize_product_fact(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "text": row["text"],
        "source_url": row["source_url"],
        "source_kind": row["source_kind"],
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def create_product_fact(
    product_id,
    text,
    source_url=None,
    source_kind="manual",
    status="pending",
    created_by=None,
    fact_id=None,
    created_at=None,
    decided_at=None,
):
    """Insert a product_facts record and return it."""
    pid = str(product_id or "").strip()
    clean_text = str(text or "").strip()
    if not pid:
        raise ValueError("product_id is required")
    if not clean_text:
        raise ValueError("text is required")
    kind = str(source_kind or "manual").strip().lower()
    if kind not in _VALID_FACT_SOURCE_KINDS:
        raise ValueError(f"Invalid source_kind '{kind}'. Must be one of {_VALID_FACT_SOURCE_KINDS}")
    st = str(status or "pending").strip().lower()
    if st not in _VALID_FACT_STATUSES:
        raise ValueError(f"Invalid status '{st}'. Must be one of {_VALID_FACT_STATUSES}")

    ensure_init()
    conn = connect()
    fid = fact_id or f"fact_{uuid.uuid4().hex[:12]}"
    now = created_at or datetime.now(timezone.utc).isoformat()
    src_url = str(source_url).strip() if source_url else None
    with conn:
        conn.execute(
            """
            INSERT INTO product_facts (
                id, product_id, text, source_url, source_kind, status, created_by, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fid,
                pid,
                clean_text,
                src_url,
                kind,
                st,
                str(created_by) if created_by else None,
                now,
                decided_at,
            ),
        )
    return get_product_fact(fid)


def get_product_fact(fact_id):
    """Return a single product_fact dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM product_facts WHERE id = ?", (str(fact_id),)).fetchone()
    return _deserialize_product_fact(row)


def list_product_facts(product_id=None, status=None, limit=200):
    """Return product_facts filtered optionally by product_id and/or status, newest-first."""
    ensure_init()
    conn = connect()
    clauses = []
    params = []
    if product_id:
        clauses.append("product_id = ?")
        params.append(str(product_id).strip())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM product_facts {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        params,
    ).fetchall()
    return [_deserialize_product_fact(r) for r in rows]


def decide_product_fact(fact_id, status, text=None, decided_at=None):
    """Approve or reject a product fact, optionally updating its text."""
    st = str(status or "").strip().lower()
    if st not in ("approved", "rejected"):
        raise ValueError("status must be 'approved' or 'rejected'")
    ensure_init()
    existing = get_product_fact(fact_id)
    if existing is None:
        raise ValueError(f"Fact '{fact_id}' not found")
    decided = decided_at or datetime.now(timezone.utc).isoformat()
    new_text = existing["text"]
    if text is not None and str(text).strip():
        new_text = str(text).strip()
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE product_facts SET status = ?, text = ?, decided_at = ? WHERE id = ?",
            (st, new_text, decided, str(fact_id)),
        )
    return get_product_fact(fact_id)


def get_search_cache(source, query):
    """Return cached {source, query, results, fetched_at} or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT source, query, results_json, fetched_at FROM search_cache WHERE source = ? AND query = ?",
        (str(source), str(query)),
    ).fetchone()
    if row is None:
        return None
    return {
        "source": row["source"],
        "query": row["query"],
        "results": json.loads(row["results_json"]) if row["results_json"] else [],
        "fetched_at": row["fetched_at"],
    }


def set_search_cache(source, query, results, fetched_at=None):
    """Upsert cached search results for (source, query)."""
    ensure_init()
    conn = connect()
    now = fetched_at or datetime.now(timezone.utc).isoformat()
    payload = json.dumps(list(results or []), ensure_ascii=False)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO search_cache (source, query, results_json, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(source), str(query), payload, str(now)),
        )
    return {"source": str(source), "query": str(query), "results": list(results or []), "fetched_at": str(now)}


def _deserialize_watch_topic(row):
    if row is None:
        return None
    sources = json.loads(row["sources_json"]) if row["sources_json"] else None
    return {
        "id": row["id"],
        "query": row["query"],
        "sources": sources,
        "cadence_hours": int(row["cadence_hours"] or 24),
        "days": int(row["days"] or 7),
        "enabled": bool(row["enabled"]),
        "last_run": row["last_run"],
        "created_at": row["created_at"],
    }


def create_watch_topic(
    query,
    sources=None,
    cadence_hours=24,
    days=7,
    enabled=True,
    topic_id=None,
    created_at=None,
    last_run=None,
):
    """Create a watch_topics record and return it."""
    q = str(query or "").strip()
    if not q:
        raise ValueError("query is required")
    cadence = max(1, int(cadence_hours if cadence_hours is not None else 24))
    lookback = max(1, int(days if days is not None else 7))
    sources_val = list(sources) if sources else None
    tid = str(topic_id or f"topic_{uuid.uuid4().hex[:12]}")
    now = str(created_at or datetime.now(timezone.utc).isoformat())

    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO watch_topics (
                id, query, sources_json, cadence_hours, days, enabled, last_run, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                q,
                json.dumps(sources_val, ensure_ascii=False) if sources_val is not None else None,
                cadence,
                lookback,
                1 if enabled else 0,
                last_run,
                now,
            ),
        )
    return get_watch_topic(tid)


def get_watch_topic(topic_id):
    """Return a watch_topic dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM watch_topics WHERE id = ?", (str(topic_id),)).fetchone()
    return _deserialize_watch_topic(row)


def list_watch_topics(enabled_only=False):
    """List watch topics ordered newest-created first."""
    ensure_init()
    conn = connect()
    if enabled_only:
        rows = conn.execute(
            "SELECT * FROM watch_topics WHERE enabled = 1 ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM watch_topics ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    return [_deserialize_watch_topic(r) for r in rows]


def toggle_watch_topic(topic_id):
    """Flip the enabled flag on a watch_topic and return the updated dict, or None if missing."""
    ensure_init()
    existing = get_watch_topic(topic_id)
    if existing is None:
        return None
    new_val = 0 if existing["enabled"] else 1
    conn = connect()
    with conn:
        conn.execute("UPDATE watch_topics SET enabled = ? WHERE id = ?", (new_val, str(topic_id)))
    return get_watch_topic(topic_id)


def delete_watch_topic(topic_id):
    """Delete a watch_topic row by ID; return True if deleted, False if missing."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute("DELETE FROM watch_topics WHERE id = ?", (str(topic_id),))
    return cur.rowcount > 0


def update_watch_topic_last_run(topic_id, last_run=None):
    """Update last_run timestamp on a watch_topic and return the updated dict."""
    ensure_init()
    ts = str(last_run or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute("UPDATE watch_topics SET last_run = ? WHERE id = ?", (ts, str(topic_id)))
    return get_watch_topic(topic_id)


def _deserialize_research_item(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "snippet": row["snippet"] or "",
        "source": row["source"],
        "published_at": row["published_at"],
        "topic_id": row["topic_id"],
        "found_at": row["found_at"],
        "score": row["score"],
    }


def insert_research_items(items, topic_id=None, found_at=None):
    """Insert research items deduped by URL; return count of newly inserted items."""
    ensure_init()
    conn = connect()
    now = str(found_at or datetime.now(timezone.utc).isoformat())
    inserted = 0
    with conn:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip() or url
            if not url:
                continue
            item_id = str(item.get("id") or f"ritem_{uuid.uuid4().hex[:12]}")
            score_val = item.get("score")
            try:
                score_num = float(score_val) if score_val is not None else None
            except (TypeError, ValueError):
                score_num = None
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO research_items (
                    id, url, title, snippet, source, published_at, topic_id, found_at, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    url,
                    title,
                    str(item.get("snippet") or ""),
                    str(item.get("source") or "web"),
                    item.get("published_at"),
                    topic_id if topic_id is not None else item.get("topic_id"),
                    str(item.get("found_at") or now),
                    score_num,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    return inserted


def list_research_items(limit=50, topic_id=None):
    """List research_items ordered newest-found first, optionally filtered by topic_id."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 50), 500))
    if topic_id:
        rows = conn.execute(
            """
            SELECT * FROM research_items
            WHERE topic_id = ?
            ORDER BY found_at DESC, rowid DESC
            LIMIT ?
            """,
            (str(topic_id), max_rows),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM research_items
            ORDER BY found_at DESC, rowid DESC
            LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
    return [_deserialize_research_item(r) for r in rows]


def search_research_items(query, limit=20):
    """Search research_items case-insensitively by keyword tokens."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM research_items ORDER BY found_at DESC, rowid DESC"
    ).fetchall()
    tokens = [t for t in str(query or "").lower().split() if t]
    hits = []
    for r in rows:
        haystack = " ".join(
            str(x or "") for x in (r["title"], r["snippet"], r["url"], r["source"])
        ).lower()
        if not tokens or all(tok in haystack for tok in tokens):
            hits.append(_deserialize_research_item(r))
            if len(hits) >= int(limit):
                break
    return hits


_VALID_MEMORY_SOURCES = ("feedback", "learning", "manual")


def _deserialize_agent_memory(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "text": row["text"],
        "source": row["source"],
        "content_id": row["content_id"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    }


def create_agent_memory(
    agent_id,
    text,
    source="manual",
    content_id=None,
    pinned=False,
    memory_id=None,
    created_at=None,
    last_used_at=None,
):
    """Insert an agent_memory entry and return its dict."""
    aid = str(agent_id or "").strip()
    body = str(text or "").strip()
    src = str(source or "manual").strip().lower()
    if not aid:
        raise ValueError("agent_id is required")
    if not body:
        raise ValueError("text is required")
    if src not in _VALID_MEMORY_SOURCES:
        raise ValueError(f"source must be one of {_VALID_MEMORY_SOURCES}")

    mid = str(memory_id or f"mem_{uuid.uuid4().hex[:12]}")
    now = str(created_at or datetime.now(timezone.utc).isoformat())

    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO agent_memory (
                id, agent_id, text, source, content_id, pinned, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                aid,
                body,
                src,
                str(content_id) if content_id else None,
                1 if pinned else 0,
                now,
                str(last_used_at) if last_used_at else None,
            ),
        )
    return get_agent_memory(mid)


def get_agent_memory(memory_id):
    """Fetch a single agent_memory row by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM agent_memory WHERE id = ?",
        (str(memory_id),),
    ).fetchone()
    return _deserialize_agent_memory(row)


def list_agent_memory(agent_id=None, source=None, limit=100):
    """List agent_memory rows ordered pinned-first then newest-first."""
    ensure_init()
    conn = connect()
    clauses = []
    params = []
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(str(agent_id).strip())
    if source:
        clauses.append("source = ?")
        params.append(str(source).strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    max_rows = max(1, min(int(limit or 100), 1000))
    params.append(max_rows)
    rows = conn.execute(
        f"""
        SELECT * FROM agent_memory
        {where}
        ORDER BY pinned DESC, created_at DESC, rowid DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_deserialize_agent_memory(r) for r in rows]


def list_agent_memory_shared(agent_id, shared_feedback_agents=None, limit=12):
    """List agent_memory for agent_id plus optional shared feedback from peer agents, pinned-first then newest."""
    ensure_init()
    conn = connect()
    aid = str(agent_id or "").strip()
    peers = [str(p).strip() for p in (shared_feedback_agents or []) if p and str(p).strip() != aid]
    max_rows = max(1, min(int(limit or 12), 1000))
    if peers:
        placeholders = ", ".join("?" for _ in peers)
        params = [aid, *peers, max_rows]
        rows = conn.execute(
            f"""
            SELECT * FROM agent_memory
            WHERE agent_id = ? OR (agent_id IN ({placeholders}) AND source = 'feedback')
            ORDER BY pinned DESC, created_at DESC, rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM agent_memory
            WHERE agent_id = ?
            ORDER BY pinned DESC, created_at DESC, rowid DESC
            LIMIT ?
            """,
            (aid, max_rows),
        ).fetchall()
    return [_deserialize_agent_memory(r) for r in rows]


def list_recent_feedback_for_agents(agent_ids, limit=5):
    """Return newest feedback rows across agent_ids ordered newest-first."""
    ids = [str(a).strip() for a in (agent_ids or []) if str(a or "").strip()]
    if not ids:
        return []
    ensure_init()
    conn = connect()
    placeholders = ", ".join("?" for _ in ids)
    max_rows = max(1, min(int(limit or 5), 100))
    rows = conn.execute(
        f"""
        SELECT * FROM agent_memory
        WHERE source = 'feedback' AND agent_id IN ({placeholders})
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        [*ids, max_rows],
    ).fetchall()
    return [_deserialize_agent_memory(r) for r in rows]


def update_agent_memory(memory_id, pinned=None, text=None, last_used_at=None):
    """Update pinned, text, and/or last_used_at on an agent_memory row."""
    ensure_init()
    existing = get_agent_memory(memory_id)
    if existing is None:
        return None
    updates = []
    params = []
    if pinned is not None:
        updates.append("pinned = ?")
        params.append(1 if pinned else 0)
    if text is not None:
        clean = str(text).strip()
        if not clean:
            raise ValueError("text cannot be empty")
        updates.append("text = ?")
        params.append(clean)
    if last_used_at is not None:
        updates.append("last_used_at = ?")
        params.append(str(last_used_at))
    if not updates:
        return existing
    params.append(str(memory_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE agent_memory SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_agent_memory(memory_id)


def delete_agent_memory(memory_id):
    """Delete an agent_memory row by ID. Returns True if a row was deleted."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute("DELETE FROM agent_memory WHERE id = ?", (str(memory_id),))
        return cur.rowcount > 0


def touch_agent_memory_used(memory_ids, used_at=None):
    """Set last_used_at on the given memory IDs."""
    ids = [str(mid) for mid in (memory_ids or []) if mid]
    if not ids:
        return
    ensure_init()
    ts = str(used_at or datetime.now(timezone.utc).isoformat())
    placeholders = ", ".join("?" for _ in ids)
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE agent_memory SET last_used_at = ? WHERE id IN ({placeholders})",
            [ts, *ids],
        )


def _deserialize_review_comment(row):
    if row is None:
        return None
    anchor = json.loads(row["anchor_json"]) if row["anchor_json"] else {}
    mentions = json.loads(row["mentions_json"]) if row["mentions_json"] else []
    return {
        "id": row["id"],
        "content_id": row["content_id"],
        "version": int(row["version"]),
        "kind": row["kind"],
        "anchor": anchor,
        "anchor_json": anchor,
        "body": row["body"],
        "author": row["author"],
        "parent_id": row["parent_id"],
        "status": row["status"],
        "mentions": mentions,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_review_comment(
    content_id,
    version,
    kind,
    anchor,
    body,
    author="user",
    parent_id=None,
    status="open",
    mentions=None,
    comment_id=None,
    created_at=None,
):
    """Insert a review_comments row and return the deserialized dict."""
    ensure_init()
    cid = str(comment_id or f"cmt_{uuid.uuid4().hex[:12]}")
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    anchor_str = json.dumps(anchor or {}, ensure_ascii=False)
    mentions_str = json.dumps(list(mentions or []), ensure_ascii=False)
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO review_comments (
                id, content_id, version, kind, anchor_json, body, author,
                parent_id, status, mentions_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                str(content_id),
                int(version),
                str(kind),
                anchor_str,
                str(body),
                str(author),
                str(parent_id) if parent_id else None,
                str(status or "open"),
                mentions_str,
                now,
                now,
            ),
        )
    return get_review_comment(cid)


def get_review_comment(comment_id):
    """Fetch a single review comment by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM review_comments WHERE id = ?",
        (str(comment_id),),
    ).fetchone()
    return _deserialize_review_comment(row)


def list_review_comments(content_id, version=None, status=None):
    """List review comments for content_id ordered chronologically."""
    ensure_init()
    conn = connect()
    clauses = ["content_id = ?"]
    params = [str(content_id)]
    if version is not None:
        clauses.append("version = ?")
        params.append(int(version))
    if status is not None:
        clauses.append("status = ?")
        params.append(str(status))
    rows = conn.execute(
        f"""
        SELECT * FROM review_comments
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at ASC, rowid ASC
        """,
        params,
    ).fetchall()
    return [_deserialize_review_comment(r) for r in rows]


def update_review_comment(
    comment_id,
    *,
    body=None,
    status=None,
    anchor=None,
    version=None,
    mentions=None,
    updated_at=None,
):
    """Update fields on a review_comments row and return the updated dict."""
    ensure_init()
    existing = get_review_comment(comment_id)
    if existing is None:
        return None
    updates = []
    params = []
    if body is not None:
        updates.append("body = ?")
        params.append(str(body))
    if status is not None:
        updates.append("status = ?")
        params.append(str(status))
    if anchor is not None:
        updates.append("anchor_json = ?")
        params.append(json.dumps(anchor, ensure_ascii=False))
    if version is not None:
        updates.append("version = ?")
        params.append(int(version))
    if mentions is not None:
        updates.append("mentions_json = ?")
        params.append(json.dumps(list(mentions), ensure_ascii=False))
    if not updates:
        return existing
    ts = str(updated_at or datetime.now(timezone.utc).isoformat())
    updates.append("updated_at = ?")
    params.append(ts)
    params.append(str(comment_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE review_comments SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_review_comment(comment_id)


def delete_review_comment(comment_id):
    """Delete a review comment and its direct/descendant replies. Returns True if deleted."""
    ensure_init()
    conn = connect()
    to_delete = [str(comment_id)]
    idx = 0
    while idx < len(to_delete):
        pid = to_delete[idx]
        idx += 1
        children = conn.execute(
            "SELECT id FROM review_comments WHERE parent_id = ?",
            (pid,),
        ).fetchall()
        for ch in children:
            if ch["id"] not in to_delete:
                to_delete.append(ch["id"])
    placeholders = ", ".join("?" for _ in to_delete)
    with conn:
        cur = conn.execute(
            f"DELETE FROM review_comments WHERE id IN ({placeholders})",
            to_delete,
        )
        return cur.rowcount > 0


def _deserialize_review_suggestion(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "content_id": row["content_id"],
        "base_version": int(row["base_version"]),
        "start": int(row["start"]),
        "end": int(row["end"]),
        "original": row["original"],
        "replacement": row["replacement"],
        "author": row["author"],
        "reason": row["reason"],
        "status": row["status"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def create_review_suggestion(
    content_id,
    base_version,
    start,
    end,
    original,
    replacement,
    author="user",
    reason=None,
    status="pending",
    suggestion_id=None,
    created_at=None,
):
    """Insert a review_suggestions row and return the deserialized dict."""
    ensure_init()
    sid = str(suggestion_id or f"sug_{uuid.uuid4().hex[:12]}")
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO review_suggestions (
                id, content_id, base_version, start, end, original, replacement,
                author, reason, status, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                str(content_id),
                int(base_version),
                int(start),
                int(end),
                str(original),
                str(replacement),
                str(author),
                str(reason) if reason is not None else None,
                str(status or "pending"),
                now,
                None,
            ),
        )
    return get_review_suggestion(sid)


def get_review_suggestion(suggestion_id):
    """Fetch a single review suggestion by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM review_suggestions WHERE id = ?",
        (str(suggestion_id),),
    ).fetchone()
    return _deserialize_review_suggestion(row)


def list_review_suggestions(content_id, status=None):
    """List review suggestions for content_id ordered by start offset then creation time."""
    ensure_init()
    conn = connect()
    clauses = ["content_id = ?"]
    params = [str(content_id)]
    if status is not None:
        clauses.append("status = ?")
        params.append(str(status))
    rows = conn.execute(
        f"""
        SELECT * FROM review_suggestions
        WHERE {' AND '.join(clauses)}
        ORDER BY start ASC, created_at ASC, rowid ASC
        """,
        params,
    ).fetchall()
    return [_deserialize_review_suggestion(r) for r in rows]


def update_review_suggestion(suggestion_id, *, status=None, reason=None, decided_at=None):
    """Update status/reason/decided_at on a review_suggestions row."""
    ensure_init()
    existing = get_review_suggestion(suggestion_id)
    if existing is None:
        return None
    updates = []
    params = []
    if status is not None:
        updates.append("status = ?")
        params.append(str(status))
    if reason is not None:
        updates.append("reason = ?")
        params.append(str(reason))
    if decided_at is not None:
        updates.append("decided_at = ?")
        params.append(str(decided_at))
    if not updates:
        return existing
    params.append(str(suggestion_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE review_suggestions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_review_suggestion(suggestion_id)


def _deserialize_checklist_item(row):
    if row is None:
        return None
    return {
        "content_id": row["content_id"],
        "item_key": row["item_key"],
        "label": row["label"],
        "source": row["source"],
        "checked": bool(row["checked"]),
        "updated_at": row["updated_at"],
    }


def upsert_review_checklist_item(content_id, item_key, label, source, checked, updated_at=None):
    """Insert or update a review_checklist row and return the deserialized dict."""
    ensure_init()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO review_checklist (content_id, item_key, label, source, checked, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_id, item_key) DO UPDATE SET
                label = excluded.label,
                source = excluded.source,
                checked = excluded.checked,
                updated_at = excluded.updated_at
            """,
            (
                str(content_id),
                str(item_key),
                str(label),
                str(source),
                1 if checked else 0,
                now,
            ),
        )
    row = conn.execute(
        "SELECT * FROM review_checklist WHERE content_id = ? AND item_key = ?",
        (str(content_id), str(item_key)),
    ).fetchone()
    return _deserialize_checklist_item(row)


def list_review_checklist(content_id):
    """List review checklist items for content_id."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT * FROM review_checklist
        WHERE content_id = ?
        ORDER BY CASE source WHEN 'auto' THEN 0 WHEN 'platform' THEN 1 ELSE 2 END, rowid ASC
        """,
        (str(content_id),),
    ).fetchall()
    return [_deserialize_checklist_item(r) for r in rows]


def delete_review_checklist_by_sources(content_id, sources):
    """Delete checklist items for content_id matching any of sources."""
    src_list = [str(s) for s in (sources or []) if s]
    if not src_list:
        return
    ensure_init()
    placeholders = ", ".join("?" for _ in src_list)
    conn = connect()
    with conn:
        conn.execute(
            f"DELETE FROM review_checklist WHERE content_id = ? AND source IN ({placeholders})",
            [str(content_id), *src_list],
        )


def _deserialize_pitch_proposal(row):
    if row is None:
        return None
    contact = json.loads(row["contact_json"]) if row["contact_json"] else {}
    preset = json.loads(row["preset_json"]) if row["preset_json"] else {}
    price_overrides = json.loads(row["price_overrides_json"]) if row["price_overrides_json"] else {}
    custom_items = json.loads(row["custom_items_json"]) if row["custom_items_json"] else []
    keys = row.keys()
    hidden_sheets = json.loads(row["hidden_sheets_json"]) if ("hidden_sheets_json" in keys and row["hidden_sheets_json"]) else []
    hidden_about = json.loads(row["hidden_about_json"]) if ("hidden_about_json" in keys and row["hidden_about_json"]) else []
    custom_categories = json.loads(row["custom_categories_json"]) if ("custom_categories_json" in keys and row["custom_categories_json"]) else []
    payment_plan = json.loads(row["payment_plan_json"]) if ("payment_plan_json" in keys and row["payment_plan_json"]) else []
    payment_note = (row["payment_note"] or "") if "payment_note" in keys else ""
    timeline = json.loads(row["timeline_json"]) if ("timeline_json" in keys and row["timeline_json"]) else []
    service_plans = json.loads(row["service_plans_json"]) if ("service_plans_json" in keys and row["service_plans_json"]) else []
    return {
        "id": row["id"],
        "slug": row["slug"],
        "kind": row["kind"],
        "status": row["status"],
        "client_name": row["client_name"] or "",
        "business_name": row["business_name"] or "",
        "contact": contact,
        "preset": preset,
        "price_overrides": price_overrides,
        "custom_items": custom_items,
        "hidden_sheets": hidden_sheets,
        "hidden_about": hidden_about,
        "custom_categories": custom_categories,
        "payment_plan": payment_plan,
        "payment_note": payment_note,
        "timeline": timeline,
        "service_plans": service_plans,
        "notes": row["notes"] or "",
        "created_by": row["created_by"] or "user",
        "share_id": row["share_id"],
        "pitch_commit": row["pitch_commit"] or "",
        "published_at": row["published_at"] if "published_at" in keys else None,
        "link_status": row["link_status"] if "link_status" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_pitch_proposal(
    *,
    proposal_id,
    slug,
    kind,
    status,
    client_name,
    business_name,
    contact,
    preset,
    price_overrides,
    custom_items,
    notes,
    created_by,
    share_id,
    pitch_commit,
    hidden_sheets=None,
    hidden_about=None,
    custom_categories=None,
    payment_plan=None,
    payment_note="",
    timeline=None,
    service_plans=None,
    created_at=None,
):
    """Insert a pitch_proposals row and return the deserialized dict."""
    ensure_init()
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO pitch_proposals (
                id, slug, kind, status, client_name, business_name,
                contact_json, preset_json, price_overrides_json, custom_items_json,
                hidden_sheets_json, hidden_about_json, custom_categories_json,
                notes, created_by, share_id, pitch_commit,
                payment_plan_json, payment_note, timeline_json, service_plans_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(proposal_id),
                str(slug),
                str(kind),
                str(status),
                str(client_name or ""),
                str(business_name or ""),
                json.dumps(contact or {}, ensure_ascii=False),
                json.dumps(preset or {}, ensure_ascii=False),
                json.dumps(price_overrides or {}, ensure_ascii=False),
                json.dumps(custom_items or [], ensure_ascii=False),
                json.dumps(hidden_sheets if hidden_sheets is not None else [], ensure_ascii=False),
                json.dumps(hidden_about if hidden_about is not None else [], ensure_ascii=False),
                json.dumps(custom_categories if custom_categories is not None else [], ensure_ascii=False),
                str(notes or ""),
                str(created_by or "user"),
                str(share_id),
                str(pitch_commit or ""),
                json.dumps(payment_plan if payment_plan is not None else [], ensure_ascii=False),
                str(payment_note or ""),
                json.dumps(timeline if timeline is not None else [], ensure_ascii=False),
                json.dumps(service_plans if service_plans is not None else [], ensure_ascii=False),
                now,
                now,
            ),
        )
    return get_pitch_proposal(proposal_id)


def get_pitch_proposal(proposal_id):
    """Fetch a pitch proposal by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM pitch_proposals WHERE id = ?",
        (str(proposal_id),),
    ).fetchone()
    return _deserialize_pitch_proposal(row)


def get_pitch_proposal_by_slug(slug):
    """Fetch a pitch proposal by slug, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM pitch_proposals WHERE slug = ?",
        (str(slug),),
    ).fetchone()
    return _deserialize_pitch_proposal(row)


def get_pitch_proposal_by_kind(kind):
    """Fetch the first pitch proposal matching kind, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM pitch_proposals WHERE kind = ? ORDER BY rowid ASC LIMIT 1",
        (str(kind),),
    ).fetchone()
    return _deserialize_pitch_proposal(row)


def pitch_share_id_exists(share_id):
    """Return True if share_id is already present in pitch_proposals."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT 1 FROM pitch_proposals WHERE share_id = ?",
        (str(share_id),),
    ).fetchone()
    return row is not None


def get_pitch_proposal_by_share_id(share_id):
    """Fetch a pitch proposal by share_id, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM pitch_proposals WHERE share_id = ? LIMIT 1",
        (str(share_id),),
    ).fetchone()
    return _deserialize_pitch_proposal(row)


def list_live_pitch_proposals():
    """List pitch proposals whose link_status is 'live'."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM pitch_proposals WHERE link_status = 'live' ORDER BY updated_at ASC, rowid ASC"
    ).fetchall()
    return [_deserialize_pitch_proposal(r) for r in rows]


_UNSET = object()


def set_pitch_share_id(proposal_id, share_id):
    """Persist a new share_id on pitch_proposals without altering updated_at."""
    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE pitch_proposals SET share_id = ? WHERE id = ?",
            (str(share_id), str(proposal_id)),
        )
    return get_pitch_proposal(proposal_id)


def set_pitch_link_state(proposal_id, link_status, published_at=_UNSET, share_id=_UNSET):
    """Update link_status (and optionally published_at/share_id) without altering updated_at."""
    ensure_init()
    sets = ["link_status = ?"]
    params = [link_status]
    if published_at is not _UNSET:
        sets.append("published_at = ?")
        params.append(published_at)
    if share_id is not _UNSET:
        sets.append("share_id = ?")
        params.append(str(share_id))
    params.append(str(proposal_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE pitch_proposals SET {', '.join(sets)} WHERE id = ?",
            params,
        )
    return get_pitch_proposal(proposal_id)


def _deserialize_pitch_quote_request(row):
    if row is None:
        return None
    choices = json.loads(row["choices_json"]) if row["choices_json"] else {}
    contact = json.loads(row["contact_json"]) if row["contact_json"] else {}
    return {
        "id": row["id"],
        "share_id": row["share_id"],
        "local_proposal_id": row["local_proposal_id"],
        "choices": choices if isinstance(choices, dict) else {},
        "contact": contact if isinstance(contact, dict) else {},
        "created_at": row["created_at"],
        "received_at": row["received_at"],
        "read_at": row["read_at"],
    }


def insert_pitch_quote_request(
    *,
    request_id,
    share_id,
    local_proposal_id,
    choices,
    contact,
    created_at,
    received_at=None,
):
    """Insert a quote request if not already present; return True if newly inserted."""
    ensure_init()
    rec_ts = str(received_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO pitch_quote_requests (
                id, share_id, local_proposal_id, choices_json, contact_json,
                created_at, received_at, read_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                str(request_id),
                str(share_id),
                str(local_proposal_id) if local_proposal_id else None,
                json.dumps(choices or {}, ensure_ascii=False),
                json.dumps(contact or {}, ensure_ascii=False),
                str(created_at),
                rec_ts,
            ),
        )
        return cur.rowcount > 0


def get_pitch_quote_request(request_id):
    """Fetch a single stored quote request by id, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM pitch_quote_requests WHERE id = ?",
        (str(request_id),),
    ).fetchone()
    return _deserialize_pitch_quote_request(row)


def list_pitch_quote_requests(limit=200):
    """List stored quote requests newest first."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 200), 1000))
    rows = conn.execute(
        """
        SELECT * FROM pitch_quote_requests
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (max_rows,),
    ).fetchall()
    return [_deserialize_pitch_quote_request(r) for r in rows]


def count_unread_pitch_quote_requests():
    """Return count of unread stored quote requests."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM pitch_quote_requests WHERE read_at IS NULL"
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def mark_pitch_quote_request_read(request_id, read_at=None):
    """Stamp read_at on a quote request if not already read, returning the row or None."""
    ensure_init()
    existing = get_pitch_quote_request(request_id)
    if existing is None:
        return None
    ts = str(read_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE pitch_quote_requests SET read_at = COALESCE(read_at, ?) WHERE id = ?",
            (ts, str(request_id)),
        )
    return get_pitch_quote_request(request_id)


def list_pitch_proposals(status=None, kind=None, limit=100):
    """List pitch proposals ordered by updated_at descending."""
    ensure_init()
    conn = connect()
    clauses = []
    params = []
    if status is not None:
        clauses.append("status = ?")
        params.append(str(status))
    if kind is not None:
        clauses.append("kind = ?")
        params.append(str(kind))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    max_rows = max(1, min(int(limit or 100), 500))
    params.append(max_rows)
    rows = conn.execute(
        f"""
        SELECT * FROM pitch_proposals
        {where}
        ORDER BY updated_at DESC, rowid DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_deserialize_pitch_proposal(r) for r in rows]


def update_pitch_proposal(proposal_id, updates, updated_at=None):
    """Update columns on a pitch_proposals row and return the updated dict."""
    ensure_init()
    existing = get_pitch_proposal(proposal_id)
    if existing is None:
        return None
    col_map = {
        "slug": lambda v: str(v),
        "kind": lambda v: str(v),
        "status": lambda v: str(v),
        "client_name": lambda v: str(v or ""),
        "business_name": lambda v: str(v or ""),
        "payment_note": lambda v: str(v or ""),
        "notes": lambda v: str(v or ""),
    }
    json_map = {
        "contact": "contact_json",
        "preset": "preset_json",
        "price_overrides": "price_overrides_json",
        "custom_items": "custom_items_json",
        "hidden_sheets": "hidden_sheets_json",
        "hidden_about": "hidden_about_json",
        "custom_categories": "custom_categories_json",
        "payment_plan": "payment_plan_json",
        "timeline": "timeline_json",
        "service_plans": "service_plans_json",
    }
    sets = []
    params = []
    for k, conv in col_map.items():
        if k in updates:
            sets.append(f"{k} = ?")
            params.append(conv(updates[k]))
    for k, col in json_map.items():
        if k in updates:
            sets.append(f"{col} = ?")
            params.append(json.dumps(updates[k], ensure_ascii=False))
    if not sets:
        return existing
    ts = str(updated_at or datetime.now(timezone.utc).isoformat())
    sets.append("updated_at = ?")
    params.append(ts)
    params.append(str(proposal_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE pitch_proposals SET {', '.join(sets)} WHERE id = ?",
            params,
        )
    return get_pitch_proposal(proposal_id)


def delete_pitch_proposal(proposal_id):
    """Hard-delete a pitch proposal row. Returns True if deleted."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            "DELETE FROM pitch_proposals WHERE id = ?",
            (str(proposal_id),),
        )
        return cur.rowcount > 0


def list_pitch_base_prices():
    """Return {item_id: {item_id, price, updated_by, updated_at}} for admin overrides."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT item_id, price, updated_by, updated_at FROM pitch_base_prices ORDER BY item_id ASC"
    ).fetchall()
    return {
        r["item_id"]: {
            "item_id": r["item_id"],
            "price": int(r["price"]),
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


def get_pitch_base_price(item_id):
    """Return a single admin base price override dict or None."""
    ensure_init()
    conn = connect()
    r = conn.execute(
        "SELECT item_id, price, updated_by, updated_at FROM pitch_base_prices WHERE item_id = ?",
        (str(item_id),),
    ).fetchone()
    if r is None:
        return None
    return {
        "item_id": r["item_id"],
        "price": int(r["price"]),
        "updated_by": r["updated_by"],
        "updated_at": r["updated_at"],
    }


def set_pitch_base_price(item_id, price, updated_by="admin", updated_at=None):
    """Upsert an admin base price override for item_id."""
    ensure_init()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO pitch_base_prices (item_id, price, updated_by, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                price = excluded.price,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (str(item_id), int(price), str(updated_by or "admin"), now),
        )
    return get_pitch_base_price(item_id)


def delete_pitch_base_price(item_id):
    """Remove an admin base price override. Returns True if a row was deleted."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            "DELETE FROM pitch_base_prices WHERE item_id = ?",
            (str(item_id),),
        )
        return cur.rowcount > 0


def replace_pitch_base_prices(items, updated_by="team", updated_at=None):
    """Atomically replace all pitch_base_prices rows with items [(item_id, price), ...]."""
    ensure_init()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    actor = str(updated_by or "team")
    conn = connect()
    with conn:
        conn.execute("DELETE FROM pitch_base_prices")
        for item_id, price in items:
            conn.execute(
                """
                INSERT INTO pitch_base_prices (item_id, price, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(item_id), int(price), actor, now),
            )
    return list_pitch_base_prices()


def _deserialize_sync_outbox(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        "created_at": row["created_at"],
        "sent_at": row["sent_at"],
    }


def append_sync_outbox(kind, payload, event_id=None, created_at=None):
    """Insert an event into sync_outbox for Phase 4 cloud sync."""
    ensure_init()
    eid = str(event_id or f"evt_{uuid.uuid4().hex[:16]}")
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO sync_outbox (id, kind, payload_json, created_at, sent_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (eid, str(kind), json.dumps(payload or {}, ensure_ascii=False), now),
        )
    row = conn.execute("SELECT * FROM sync_outbox WHERE id = ?", (eid,)).fetchone()
    return _deserialize_sync_outbox(row)


def list_sync_outbox(pending_only=False, limit=100):
    """List sync_outbox rows, optionally filtering to unsent rows (sent_at IS NULL)."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 100), 500))
    where = "WHERE sent_at IS NULL" if pending_only else ""
    rows = conn.execute(
        f"""
        SELECT * FROM sync_outbox
        {where}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (max_rows,),
    ).fetchall()
    return [_deserialize_sync_outbox(r) for r in rows]


def list_pending_sync_outbox(limit=200):
    """Return unsent sync_outbox rows ordered oldest-first."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 200), 1000))
    rows = conn.execute(
        """
        SELECT * FROM sync_outbox
        WHERE sent_at IS NULL
        ORDER BY created_at ASC, rowid ASC
        LIMIT ?
        """,
        (max_rows,),
    ).fetchall()
    return [_deserialize_sync_outbox(r) for r in rows]


def count_pending_sync_outbox():
    """Return the count of unsent rows in sync_outbox."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM sync_outbox WHERE sent_at IS NULL").fetchone()
    return int(row["cnt"] if row else 0)


def mark_sync_outbox_sent(event_ids, sent_at=None):
    """Mark sync_outbox rows as sent by setting sent_at."""
    ids = [str(eid) for eid in (event_ids or []) if eid]
    if not ids:
        return 0
    ensure_init()
    ts = str(sent_at or datetime.now(timezone.utc).isoformat())
    placeholders = ", ".join("?" for _ in ids)
    conn = connect()
    with conn:
        cur = conn.execute(
            f"UPDATE sync_outbox SET sent_at = ? WHERE id IN ({placeholders}) AND sent_at IS NULL",
            [ts, *ids],
        )
        return cur.rowcount


def list_all_agent_sessions():
    """Return all agent_sessions rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        f"SELECT {_SESSION_SELECT_COLS} FROM agent_sessions ORDER BY rowid ASC"
    ).fetchall()
    return [_deserialize_session(r) for r in rows]


def list_session_flags():
    """Return {id, parent_session_id, confidential} for every session, without the (large) messages."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT id, parent_session_id, confidential FROM agent_sessions ORDER BY rowid ASC").fetchall()
    return [
        {"id": r["id"], "parent_session_id": r["parent_session_id"], "confidential": bool(r["confidential"])}
        for r in rows
    ]


def list_all_chat_turns():
    """Return all legacy chat_turns rows with rowid for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT rowid, agent_id, sender, name, text, ts FROM chat_turns ORDER BY rowid ASC"
    ).fetchall()
    return [
        {
            "rowid": int(r["rowid"]),
            "agent_id": r["agent_id"],
            "sender": r["sender"],
            "name": r["name"],
            "text": r["text"],
            "ts": r["ts"],
        }
        for r in rows
    ]


def list_all_content_items():
    """Return all content_items rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM content_items ORDER BY rowid ASC").fetchall()
    return [_deserialize_content_item(r) for r in rows]


def list_all_content_versions():
    """Return all content_versions rows ordered by content_id and version."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM content_versions ORDER BY content_id ASC, version ASC, rowid ASC"
    ).fetchall()
    return [_deserialize_content_version(r) for r in rows]


def list_all_research_notes():
    """Return all research_notes rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT id, title, body, url, source, agent_id, created_at FROM research_notes ORDER BY rowid ASC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "body": r["body"],
            "url": r["url"],
            "source": r["source"],
            "agent_id": r["agent_id"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def list_all_review_comments():
    """Return all review_comments rows without anchor geometry for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT id, content_id, version, kind, body, author, status, parent_id, created_at, updated_at
        FROM review_comments
        ORDER BY rowid ASC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "content_id": r["content_id"],
            "version": int(r["version"]),
            "kind": r["kind"],
            "body": r["body"],
            "author": r["author"],
            "status": r["status"],
            "parent_id": r["parent_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def list_all_metrics():
    """Return all metrics rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM metrics ORDER BY rowid ASC").fetchall()
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


def list_all_workflow_runs():
    """Return workflow_runs rows without full state_json for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT id, kind, status, current_step, inputs_json, error, created_at, updated_at
        FROM workflow_runs
        ORDER BY rowid ASC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "status": r["status"],
            "current_step": int(r["current_step"] or 0),
            "inputs": json.loads(r["inputs_json"]) if r["inputs_json"] else {},
            "error": r["error"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def list_all_jobs():
    """Return all jobs rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        f"SELECT {_JOB_SELECT_COLS} FROM jobs ORDER BY rowid ASC"
    ).fetchall()
    return [_deserialize_job(r) for r in rows]


def has_running_jobs():
    """Return True if any local job currently has status='running'."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT 1 FROM jobs WHERE status = 'running' LIMIT 1").fetchone()
    return row is not None


def list_activity_since(last_rowid=0, limit=500):
    """Return activity rows with rowid > last_rowid ordered ascending."""
    ensure_init()
    conn = connect()
    max_rows = max(1, min(int(limit or 500), 2000))
    rows = conn.execute(
        """
        SELECT rowid, ts, actor, target, task, file
        FROM activity
        WHERE rowid > ?
        ORDER BY rowid ASC
        LIMIT ?
        """,
        (int(last_rowid or 0), max_rows),
    ).fetchall()
    return [
        {
            "rowid": int(r["rowid"]),
            "ts": r["ts"],
            "actor": r["actor"],
            "target": r["target"],
            "task": r["task"],
            "file": r["file"],
        }
        for r in rows
    ]


def count_activity_since(last_rowid=0):
    """Return count of activity rows with rowid > last_rowid."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM activity WHERE rowid > ?",
        (int(last_rowid or 0),),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def get_cloud_sync_state(user_id):
    """Return {(kind, local_id): {hash, synced_at, error}} for user_id."""
    if not user_id:
        return {}
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT kind, local_id, hash, synced_at, error
        FROM cloud_sync_state
        WHERE user_id = ?
        """,
        (str(user_id),),
    ).fetchall()
    return {
        (r["kind"], r["local_id"]): {
            "hash": r["hash"],
            "synced_at": r["synced_at"],
            "error": r["error"],
        }
        for r in rows
    }


def get_cloud_sync_value(user_id, kind, local_id):
    """Return stored hash/value for (user_id, kind, local_id), or None."""
    if not user_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT hash FROM cloud_sync_state
        WHERE user_id = ? AND kind = ? AND local_id = ?
        """,
        (str(user_id), str(kind), str(local_id)),
    ).fetchone()
    return row["hash"] if row is not None else None


def upsert_cloud_sync_state(user_id, entries):
    """Upsert (kind, local_id, hash, synced_at, error) rows for user_id."""
    if not user_id or not entries:
        return 0
    ensure_init()
    conn = connect()
    uid = str(user_id)
    params = [
        (uid, str(kind), str(local_id), str(hash_val), str(synced_at), error)
        for kind, local_id, hash_val, synced_at, error in entries
    ]
    with conn:
        conn.executemany(
            """
            INSERT INTO cloud_sync_state (user_id, kind, local_id, hash, synced_at, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, kind, local_id) DO UPDATE SET
                hash = excluded.hash,
                synced_at = excluded.synced_at,
                error = excluded.error
            """,
            params,
        )
    return len(params)


def get_media_upload_queue(user_id):
    """Return {(local_source_id, kind): row_dict} for user_id."""
    if not user_id:
        return {}
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT user_id, local_source_id, kind, source_hash, storage_path,
               state, attempts, next_attempt_at, error, updated_at
        FROM media_upload_queue
        WHERE user_id = ?
        ORDER BY rowid ASC
        """,
        (str(user_id),),
    ).fetchall()
    return {
        (r["local_source_id"], r["kind"]): {
            "user_id": r["user_id"],
            "local_source_id": r["local_source_id"],
            "kind": r["kind"],
            "source_hash": r["source_hash"],
            "storage_path": r["storage_path"],
            "state": r["state"],
            "attempts": int(r["attempts"] or 0),
            "next_attempt_at": float(r["next_attempt_at"] or 0.0),
            "error": r["error"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


def upsert_media_upload_queue(user_id, entries):
    """Upsert media_upload_queue rows for user_id."""
    if not user_id or not entries:
        return 0
    ensure_init()
    conn = connect()
    uid = str(user_id)
    params = []
    for e in entries:
        if isinstance(e, dict):
            params.append((
                uid,
                str(e["local_source_id"]),
                str(e["kind"]),
                str(e["source_hash"]),
                str(e.get("storage_path") or ""),
                str(e["state"]),
                int(e.get("attempts") or 0),
                float(e.get("next_attempt_at") or 0.0),
                e.get("error"),
                str(e["updated_at"]),
            ))
        else:
            local_source_id, kind, source_hash, storage_path, state, attempts, next_attempt_at, error, updated_at = e
            params.append((
                uid,
                str(local_source_id),
                str(kind),
                str(source_hash),
                str(storage_path or ""),
                str(state),
                int(attempts or 0),
                float(next_attempt_at or 0.0),
                error,
                str(updated_at),
            ))
    with conn:
        conn.executemany(
            """
            INSERT INTO media_upload_queue (
                user_id, local_source_id, kind, source_hash, storage_path,
                state, attempts, next_attempt_at, error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, local_source_id, kind) DO UPDATE SET
                source_hash = excluded.source_hash,
                storage_path = excluded.storage_path,
                state = excluded.state,
                attempts = excluded.attempts,
                next_attempt_at = excluded.next_attempt_at,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            params,
        )
    return len(params)


def get_media_queue_summary(user_id):
    """Return {uploaded, waiting, failed, last_error} counts for user_id."""
    if not user_id:
        return {"uploaded": 0, "waiting": 0, "failed": 0, "last_error": None}
    ensure_init()
    conn = connect()
    uid = str(user_id)
    rows = conn.execute(
        """
        SELECT state, COUNT(*) AS cnt
        FROM media_upload_queue
        WHERE user_id = ?
        GROUP BY state
        """,
        (uid,),
    ).fetchall()
    counts = {r["state"]: int(r["cnt"]) for r in rows}
    err_row = conn.execute(
        """
        SELECT error
        FROM media_upload_queue
        WHERE user_id = ? AND error IS NOT NULL AND error <> ''
        ORDER BY updated_at DESC, rowid DESC
        LIMIT 1
        """,
        (uid,),
    ).fetchone()
    return {
        "uploaded": counts.get("uploaded", 0),
        "waiting": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "last_error": err_row["error"] if err_row is not None else None,
    }


_TK_UNSET = object()


def get_team_knowledge_state(user_id):
    """Return {key: state_dict} from team_knowledge_state for user_id."""
    if not user_id:
        return {}
    ensure_init()
    conn = connect()
    rows = conn.execute(
        """
        SELECT user_id, key, applied_hash, applied_remote_updated_at, published_hash, published_at
        FROM team_knowledge_state
        WHERE user_id = ?
        ORDER BY key ASC
        """,
        (str(user_id),),
    ).fetchall()
    return {
        r["key"]: {
            "user_id": r["user_id"],
            "key": r["key"],
            "applied_hash": r["applied_hash"],
            "applied_remote_updated_at": r["applied_remote_updated_at"],
            "published_hash": r["published_hash"],
            "published_at": r["published_at"],
        }
        for r in rows
    }


def get_team_knowledge_key_state(user_id, key):
    """Return a single team_knowledge_state row dict for (user_id, key), or None."""
    if not user_id or not key:
        return None
    ensure_init()
    conn = connect()
    r = conn.execute(
        """
        SELECT user_id, key, applied_hash, applied_remote_updated_at, published_hash, published_at
        FROM team_knowledge_state
        WHERE user_id = ? AND key = ?
        """,
        (str(user_id), str(key)),
    ).fetchone()
    if r is None:
        return None
    return {
        "user_id": r["user_id"],
        "key": r["key"],
        "applied_hash": r["applied_hash"],
        "applied_remote_updated_at": r["applied_remote_updated_at"],
        "published_hash": r["published_hash"],
        "published_at": r["published_at"],
    }


def upsert_team_knowledge_state(
    user_id,
    key,
    *,
    applied_hash=_TK_UNSET,
    applied_remote_updated_at=_TK_UNSET,
    published_hash=_TK_UNSET,
    published_at=_TK_UNSET,
):
    """Upsert team_knowledge_state fields for (user_id, key) without clobbering unset fields."""
    if not user_id or not key:
        return None
    ensure_init()
    existing = get_team_knowledge_key_state(user_id, key) or {}
    next_applied_hash = existing.get("applied_hash") if applied_hash is _TK_UNSET else applied_hash
    next_applied_at = (
        existing.get("applied_remote_updated_at")
        if applied_remote_updated_at is _TK_UNSET
        else applied_remote_updated_at
    )
    next_pub_hash = existing.get("published_hash") if published_hash is _TK_UNSET else published_hash
    next_pub_at = existing.get("published_at") if published_at is _TK_UNSET else published_at

    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO team_knowledge_state (
                user_id, key, applied_hash, applied_remote_updated_at, published_hash, published_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                applied_hash = excluded.applied_hash,
                applied_remote_updated_at = excluded.applied_remote_updated_at,
                published_hash = excluded.published_hash,
                published_at = excluded.published_at
            """,
            (
                str(user_id),
                str(key),
                next_applied_hash,
                next_applied_at,
                next_pub_hash,
                next_pub_at,
            ),
        )
    return get_team_knowledge_key_state(user_id, key)


def get_meta(key, default=None):
    """Return a value from the meta key/value table, or default if missing."""
    if not key:
        return default
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (str(key),)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_meta(key, value):
    """Store a string value in the meta key/value table."""
    if not key:
        return
    ensure_init()
    conn = connect()
    with conn:
        if value is None:
            conn.execute("DELETE FROM meta WHERE key = ?", (str(key),))
        else:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )


def delete_meta(key):
    """Delete a key from the meta key/value table."""
    if not key:
        return
    ensure_init()
    conn = connect()
    with conn:
        conn.execute("DELETE FROM meta WHERE key = ?", (str(key),))


def _row_to_notification(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "route": row["route"],
        "created_at": row["created_at"],
        "read_at": row["read_at"],
    }


def insert_notification(notif_id, kind, title, body, route, created_at, max_keep=200):
    """Insert a notification row and trim older rows beyond max_keep."""
    ensure_init()
    conn = connect()
    cap = max(1, int(max_keep or 200))
    with conn:
        conn.execute(
            """
            INSERT INTO notifications (id, kind, title, body, route, created_at, read_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (str(notif_id), str(kind), str(title), str(body), str(route), str(created_at)),
        )
        conn.execute(
            """
            DELETE FROM notifications
            WHERE rowid NOT IN (
                SELECT rowid
                FROM notifications
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
            )
            """,
            (cap,),
        )
    row = conn.execute(
        "SELECT id, kind, title, body, route, created_at, read_at FROM notifications WHERE id = ?",
        (str(notif_id),),
    ).fetchone()
    return _row_to_notification(row) if row is not None else None


def list_notifications(limit=50):
    """Return newest-first notification rows up to limit."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 50), 200))
    rows = conn.execute(
        """
        SELECT id, kind, title, body, route, created_at, read_at
        FROM notifications
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (cap,),
    ).fetchall()
    return [_row_to_notification(r) for r in rows]


def count_unread_notifications():
    """Return total count of unread notifications."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM notifications WHERE read_at IS NULL").fetchone()
    return int(row["cnt"] if row else 0)


def mark_notifications_read(ids=None, mark_all=False, read_at=None):
    """Mark specific notification ids or all unread notifications as read, returning unread count."""
    ensure_init()
    conn = connect()
    ts = str(read_at or datetime.now(timezone.utc).isoformat())
    with conn:
        if mark_all:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE read_at IS NULL",
                (ts,),
            )
        elif ids:
            clean_ids = [str(i).strip() for i in ids if isinstance(i, str) and str(i).strip()]
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                conn.execute(
                    f"UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND id IN ({placeholders})",
                    [ts, *clean_ids],
                )
    return count_unread_notifications()


def get_notification_settings():
    """Return {kind: enabled_bool} map from notification_settings."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT kind, enabled FROM notification_settings").fetchall()
    return {r["kind"]: bool(r["enabled"]) for r in rows}


def set_notification_setting(kind, enabled, updated_at=None):
    """Upsert a per-kind enabled boolean in notification_settings."""
    ensure_init()
    conn = connect()
    ts = str(updated_at or datetime.now(timezone.utc).isoformat())
    with conn:
        conn.execute(
            """
            INSERT INTO notification_settings (kind, enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(kind) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (str(kind), 1 if enabled else 0, ts),
        )
    return {"kind": str(kind), "enabled": bool(enabled), "updated_at": ts}


# --- Reminders ---

def _row_to_reminder(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "note": row["note"] or "",
        "due_at": row["due_at"],
        "kind": row["kind"],
        "content_id": row["content_id"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "done_at": row["done_at"],
        "snoozed_until": row["snoozed_until"],
        "fired_at": row["fired_at"],
    }


def create_reminder(
    title,
    due_at,
    *,
    note="",
    kind="custom",
    content_id=None,
    created_by="user",
    reminder_id=None,
    created_at=None,
):
    """Insert a reminder row and return its dict representation."""
    ensure_init()
    rid = str(reminder_id or f"rem_{uuid.uuid4().hex[:12]}")
    now = datetime.now(timezone.utc).isoformat()
    created_ts = _normalize_utc_iso(created_at, fallback=now) if created_at else now
    due_ts = _normalize_utc_iso(due_at, fallback=str(due_at))
    cid = str(content_id).strip() if content_id and str(content_id).strip() else None
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO reminders (
                id, title, note, due_at, kind, content_id, created_by,
                created_at, done_at, snoozed_until, fired_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                rid,
                str(title).strip(),
                str(note or "").strip(),
                due_ts,
                str(kind or "custom").strip(),
                cid,
                str(created_by or "user").strip(),
                created_ts,
            ),
        )
    return get_reminder(rid)


def get_reminder(reminder_id):
    """Return a single reminder dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (str(reminder_id),)).fetchone()
    return _row_to_reminder(row)


def list_reminders(status="open", limit=200):
    """List reminders filtered by status ('open', 'done', 'all')."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 200), 500))
    norm = str(status or "open").strip().lower()
    if norm == "open":
        rows = conn.execute(
            "SELECT * FROM reminders WHERE done_at IS NULL ORDER BY due_at ASC, rowid ASC LIMIT ?",
            (cap,),
        ).fetchall()
    elif norm == "done":
        rows = conn.execute(
            "SELECT * FROM reminders WHERE done_at IS NOT NULL ORDER BY done_at DESC, rowid DESC LIMIT ?",
            (cap,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reminders ORDER BY CASE WHEN done_at IS NULL THEN 0 ELSE 1 END, due_at ASC, rowid ASC LIMIT ?",
            (cap,),
        ).fetchall()
    return [_row_to_reminder(r) for r in rows]


def list_all_reminders():
    """Return all reminder rows in creation order."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM reminders ORDER BY created_at ASC, rowid ASC").fetchall()
    return [_row_to_reminder(r) for r in rows]


def list_unfired_open_reminders():
    """Return all reminders that are not done and not yet fired."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE done_at IS NULL AND fired_at IS NULL ORDER BY due_at ASC, rowid ASC"
    ).fetchall()
    return [_row_to_reminder(r) for r in rows]


def mark_reminder_done(reminder_id, done_at=None):
    """Mark a reminder done and return the updated dict, or None if not found."""
    ensure_init()
    if get_reminder(reminder_id) is None:
        return None
    ts = _normalize_utc_iso(done_at) if done_at else datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE reminders SET done_at = COALESCE(done_at, ?) WHERE id = ?",
            (ts, str(reminder_id)),
        )
    return get_reminder(reminder_id)


def mark_content_results_reminders_done(content_id, done_at=None):
    """Mark open results_due reminders for content_id as done, returning updated count."""
    if not content_id or not str(content_id).strip():
        return 0
    ensure_init()
    ts = _normalize_utc_iso(done_at) if done_at else datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        cur = conn.execute(
            "UPDATE reminders SET done_at = ? WHERE content_id = ? AND kind = 'results_due' AND done_at IS NULL",
            (ts, str(content_id).strip()),
        )
    return int(cur.rowcount or 0)


def snooze_reminder(reminder_id, snoozed_until):
    """Snooze an open reminder until snoozed_until (clearing fired_at so it fires when snooze ends)."""
    ensure_init()
    existing = get_reminder(reminder_id)
    if existing is None:
        return None
    until_ts = _normalize_utc_iso(snoozed_until)
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE reminders SET snoozed_until = ?, fired_at = NULL WHERE id = ?",
            (until_ts, str(reminder_id)),
        )
    return get_reminder(reminder_id)


def mark_reminders_fired(reminder_ids, fired_at=None):
    """Set fired_at on the given reminder IDs if not already set."""
    clean_ids = [str(rid).strip() for rid in (reminder_ids or []) if rid and str(rid).strip()]
    if not clean_ids:
        return 0
    ensure_init()
    ts = _normalize_utc_iso(fired_at) if fired_at else datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in clean_ids)
    conn = connect()
    with conn:
        cur = conn.execute(
            f"UPDATE reminders SET fired_at = COALESCE(fired_at, ?) WHERE id IN ({placeholders})",
            [ts, *clean_ids],
        )
    return int(cur.rowcount or 0)


def delete_reminder(reminder_id):
    """Delete a reminder row by ID, returning True if deleted."""
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (str(reminder_id),))
    return cur.rowcount > 0


def delete_pending_results_reminders(content_id):
    """Delete open (done_at IS NULL) results_due reminders for content_id."""
    if not content_id or not str(content_id).strip():
        return 0
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE content_id = ? AND kind = 'results_due' AND done_at IS NULL",
            (str(content_id).strip(),),
        )
    return int(cur.rowcount or 0)


def ensure_results_reminders_for_post(content_id, title, posted_at):
    """Create day-1 (+24h) and day-7 (+7d) results_due reminders for a posted item without duplicating."""
    cid = str(content_id or "").strip()
    if not cid:
        return []
    ensure_init()
    posted_dt = _parse_utc_dt(posted_at) or datetime.now(timezone.utc)
    clean_title = str(title or cid).strip() or cid

    conn = connect()
    existing_rows = conn.execute(
        "SELECT * FROM reminders WHERE content_id = ? AND kind = 'results_due'",
        (cid,),
    ).fetchall()
    existing_titles = {str(r["title"] or "") for r in existing_rows}

    created = []
    specs = (
        (f"Add results for {clean_title} (day 1)", "(day 1)", posted_dt + timedelta(hours=24)),
        (f"Add results for {clean_title} (day 7)", "(day 7)", posted_dt + timedelta(days=7)),
    )
    for rem_title, suffix, due_dt in specs:
        if any(t.endswith(suffix) for t in existing_titles):
            continue
        created.append(
            create_reminder(
                rem_title,
                due_dt.astimezone(timezone.utc).isoformat(),
                note="",
                kind="results_due",
                content_id=cid,
                created_by="analyst",
            )
        )
    return created


# --- Metric Proposals ---

def _row_to_metric_proposal(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "content_id": row["content_id"],
        "platform": row["platform"],
        "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else {},
        "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else {},
        "image_path": row["image_path"],
        "status": row["status"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def create_metric_proposal(
    proposal_id,
    *,
    content_id=None,
    platform=None,
    metrics=None,
    evidence=None,
    image_path=None,
    status="pending",
    created_at=None,
):
    """Insert a metric_proposals row and return its dict."""
    ensure_init()
    pid = str(proposal_id or f"mprop_{uuid.uuid4().hex[:12]}")
    now = datetime.now(timezone.utc).isoformat()
    created_ts = _normalize_utc_iso(created_at, fallback=now) if created_at else now
    cid = str(content_id).strip() if content_id and str(content_id).strip() else None
    plat = str(platform).strip() if platform and str(platform).strip() else None
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO metric_proposals (
                id, content_id, platform, metrics_json, evidence_json,
                image_path, status, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                pid,
                cid,
                plat,
                json.dumps(dict(metrics or {}), ensure_ascii=False),
                json.dumps(dict(evidence or {}), ensure_ascii=False),
                image_path,
                str(status or "pending"),
                created_ts,
            ),
        )
    return get_metric_proposal(pid)


def get_metric_proposal(proposal_id):
    """Return a single metric proposal dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM metric_proposals WHERE id = ?", (str(proposal_id),)).fetchone()
    return _row_to_metric_proposal(row)


def list_metric_proposals(status="pending", limit=100):
    """List metric proposals filtered by status ('pending', 'saved', 'discarded', or 'all')."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 100), 500))
    norm = str(status or "pending").strip().lower()
    if norm in ("all", ""):
        rows = conn.execute(
            "SELECT * FROM metric_proposals ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (cap,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM metric_proposals WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (norm, cap),
        ).fetchall()
    return [_row_to_metric_proposal(r) for r in rows]


def decide_metric_proposal(
    proposal_id,
    status,
    *,
    content_id=_TK_UNSET,
    platform=_TK_UNSET,
    metrics=_TK_UNSET,
    decided_at=None,
):
    """Update status ('saved' or 'discarded') and optional overrides on a metric proposal."""
    ensure_init()
    existing = get_metric_proposal(proposal_id)
    if existing is None:
        return None
    ts = _normalize_utc_iso(decided_at) if decided_at else datetime.now(timezone.utc).isoformat()
    next_cid = existing["content_id"] if content_id is _TK_UNSET else (
        str(content_id).strip() if content_id and str(content_id).strip() else None
    )
    next_plat = existing["platform"] if platform is _TK_UNSET else (
        str(platform).strip() if platform and str(platform).strip() else None
    )
    next_metrics = existing["metrics"] if metrics is _TK_UNSET else dict(metrics or {})
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE metric_proposals
            SET status = ?, content_id = ?, platform = ?, metrics_json = ?, decided_at = ?
            WHERE id = ?
            """,
            (
                str(status),
                next_cid,
                next_plat,
                json.dumps(next_metrics, ensure_ascii=False),
                ts,
                str(proposal_id),
            ),
        )
    return get_metric_proposal(proposal_id)


# --- Weekly Summaries ---

def _row_to_weekly_summary(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "week_start": row["week_start"],
        "created_at": row["created_at"],
        "data": json.loads(row["data_json"]) if row["data_json"] else {},
        "highlights": json.loads(row["highlights_json"]) if row["highlights_json"] else {},
        "narrative": row["narrative"] or "",
        "status": row["status"] or "ready",
    }


def upsert_weekly_summary(
    week_start,
    data,
    highlights,
    narrative="",
    status="ready",
    summary_id=None,
    created_at=None,
):
    """Insert or replace the weekly summary for week_start (one row per week)."""
    ensure_init()
    ws = str(week_start).strip()
    now = _normalize_utc_iso(created_at) if created_at else datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        existing = conn.execute(
            "SELECT id FROM weekly_summaries WHERE week_start = ?",
            (ws,),
        ).fetchone()
        sid = str(summary_id or (existing["id"] if existing is not None else f"wsum_{uuid.uuid4().hex[:12]}"))
        if existing is not None and existing["id"] != sid:
            conn.execute("DELETE FROM weekly_summaries WHERE week_start = ?", (ws,))
        conn.execute(
            """
            INSERT INTO weekly_summaries (
                id, week_start, created_at, data_json, highlights_json, narrative, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                id = excluded.id,
                created_at = excluded.created_at,
                data_json = excluded.data_json,
                highlights_json = excluded.highlights_json,
                narrative = excluded.narrative,
                status = excluded.status
            """,
            (
                sid,
                ws,
                now,
                json.dumps(dict(data or {}), ensure_ascii=False),
                json.dumps(dict(highlights or {}), ensure_ascii=False),
                str(narrative or ""),
                str(status or "ready"),
            ),
        )
    return get_weekly_summary_by_week(ws)


def get_weekly_summary(summary_id):
    """Fetch a single weekly summary by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM weekly_summaries WHERE id = ?", (str(summary_id),)).fetchone()
    return _row_to_weekly_summary(row)


def get_weekly_summary_by_week(week_start):
    """Fetch the weekly summary for week_start (YYYY-MM-DD), or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM weekly_summaries WHERE week_start = ?",
        (str(week_start).strip(),),
    ).fetchone()
    return _row_to_weekly_summary(row)


def list_weekly_summaries(limit=52):
    """List weekly summaries ordered newest week_start first."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 52), 500))
    rows = conn.execute(
        "SELECT * FROM weekly_summaries ORDER BY week_start DESC, created_at DESC LIMIT ?",
        (cap,),
    ).fetchall()
    return [_row_to_weekly_summary(r) for r in rows]


def list_all_weekly_summaries():
    """Return all weekly_summaries rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM weekly_summaries ORDER BY week_start ASC, rowid ASC"
    ).fetchall()
    return [_row_to_weekly_summary(r) for r in rows]


# --- Learnings ---

def _row_to_learning(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "text": row["text"],
        "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else [],
        "applies_to": json.loads(row["applies_to_json"]) if row["applies_to_json"] else [],
        "status": row["status"],
        "source_summary_id": row["source_summary_id"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def create_learning(
    text,
    evidence=None,
    applies_to=None,
    *,
    status="pending",
    source_summary_id=None,
    learning_id=None,
    created_at=None,
    decided_at=None,
):
    """Insert a learning row and return its dict."""
    ensure_init()
    lid = str(learning_id or f"lrn_{uuid.uuid4().hex[:12]}")
    now = datetime.now(timezone.utc).isoformat()
    created_ts = _normalize_utc_iso(created_at, fallback=now) if created_at else now
    decided_ts = _normalize_utc_iso(decided_at) if decided_at else None
    src_id = str(source_summary_id).strip() if source_summary_id and str(source_summary_id).strip() else None
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO learnings (
                id, text, evidence_json, applies_to_json, status, source_summary_id, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid,
                str(text).strip(),
                json.dumps(list(evidence or []), ensure_ascii=False),
                json.dumps(list(applies_to or ["all"]), ensure_ascii=False),
                str(status or "pending"),
                src_id,
                created_ts,
                decided_ts,
            ),
        )
    return get_learning(lid)


def get_learning(learning_id):
    """Return a single learning dict by ID, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute("SELECT * FROM learnings WHERE id = ?", (str(learning_id),)).fetchone()
    return _row_to_learning(row)


def list_learnings(status=None, limit=200):
    """List learnings optionally filtered by status ('pending'|'approved'|'rejected'|'retired')."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 200), 1000))
    norm = str(status or "").strip().lower()
    if norm and norm != "all":
        rows = conn.execute(
            "SELECT * FROM learnings WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (norm, cap),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM learnings ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (cap,),
        ).fetchall()
    return [_row_to_learning(r) for r in rows]


def list_all_learnings():
    """Return all learnings rows for cloud mirror sync."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM learnings ORDER BY created_at ASC, rowid ASC").fetchall()
    return [_row_to_learning(r) for r in rows]


def list_approved_learnings(limit=100):
    """Return approved learnings ordered most recently decided/created first."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 100), 500))
    rows = conn.execute(
        """
        SELECT * FROM learnings
        WHERE status = 'approved'
        ORDER BY COALESCE(decided_at, created_at) DESC, rowid DESC
        LIMIT ?
        """,
        (cap,),
    ).fetchall()
    return [_row_to_learning(r) for r in rows]


def update_learning_decision(learning_id, status, *, text=None, decided_at=None):
    """Update status, optional edited text, and decided_at on a learning row."""
    ensure_init()
    existing = get_learning(learning_id)
    if existing is None:
        return None
    ts = _normalize_utc_iso(decided_at) if decided_at else datetime.now(timezone.utc).isoformat()
    next_text = str(text).strip() if text is not None else existing["text"]
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE learnings
            SET status = ?, text = ?, decided_at = ?
            WHERE id = ?
            """,
            (str(status), next_text, ts, str(learning_id)),
        )
    return get_learning(learning_id)


def delete_pending_learnings_for_summary(source_summary_id):
    """Remove un-decided pending learnings tied to source_summary_id before regenerating."""
    if not source_summary_id or not str(source_summary_id).strip():
        return 0
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            "DELETE FROM learnings WHERE source_summary_id = ? AND status = 'pending'",
            (str(source_summary_id).strip(),),
        )
    return int(cur.rowcount or 0)


_UPDATABLE_CODING_RUN_FIELDS = {
    "status",
    "started_at",
    "finished_at",
    "exit_code",
    "summary",
    "log_path",
    "out_files_json",
    "out_files",
    "error",
    "transport",
}


def _row_to_coding_run(row):
    if row is None:
        return None
    keys = row.keys()
    try:
        repo_ids = json.loads(row["repo_ids_json"]) if row["repo_ids_json"] else []
    except Exception:
        repo_ids = []
    try:
        out_files = json.loads(row["out_files_json"]) if row["out_files_json"] else []
    except Exception:
        out_files = []
    return {
        "id": row["id"],
        "engine": row["engine"],
        "mode": row["mode"],
        "prompt": row["prompt"],
        "repo_ids": repo_ids if isinstance(repo_ids, list) else [],
        "status": row["status"],
        "workspace": row["workspace"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
        "summary": row["summary"],
        "log_path": row["log_path"],
        "out_files": out_files if isinstance(out_files, list) else [],
        "error": row["error"],
        "transport": row["transport"],
        "approval_hash": row["approval_hash"] if "approval_hash" in keys else None,
        "approved_at": row["approved_at"] if "approved_at" in keys else None,
        "approval_consumed": bool(row["approval_consumed"]) if "approval_consumed" in keys else False,
        "confidential": bool(row["confidential"]) if "confidential" in keys else False,
        "requested_by": row["requested_by"] if "requested_by" in keys else None,
        "session_id": row["session_id"] if "session_id" in keys else None,
    }


def create_coding_run(run):
    """Insert a new coding_runs row and return the serialized run dict."""
    ensure_init()
    conn = connect()
    repo_ids_json = json.dumps(list(run.get("repo_ids") or []), ensure_ascii=False)
    out_files_json = json.dumps(list(run.get("out_files") or []), ensure_ascii=False)
    with conn:
        conn.execute(
            """
            INSERT INTO coding_runs (
                id, engine, mode, prompt, repo_ids_json, status, workspace,
                created_at, started_at, finished_at, exit_code, summary,
                log_path, out_files_json, error, transport,
                approval_hash, approved_at, approval_consumed,
                confidential, requested_by, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run["id"]),
                str(run["engine"]),
                str(run.get("mode") or "read_only"),
                str(run["prompt"]),
                repo_ids_json,
                str(run.get("status") or "awaiting_approval"),
                str(run["workspace"]),
                str(run["created_at"]),
                run.get("started_at"),
                run.get("finished_at"),
                run.get("exit_code"),
                run.get("summary"),
                run.get("log_path"),
                out_files_json,
                run.get("error"),
                run.get("transport"),
                run.get("approval_hash"),
                run.get("approved_at"),
                1 if run.get("approval_consumed") else 0,
                1 if run.get("confidential") else 0,
                run.get("requested_by"),
                run.get("session_id"),
            ),
        )
    return get_coding_run(run["id"])


def get_coding_run(run_id):
    """Return a single coding_runs dict by ID, or None if missing."""
    if not run_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM coding_runs WHERE id = ?",
        (str(run_id),),
    ).fetchone()
    return _row_to_coding_run(row)


def list_coding_runs(limit=50, engine=None, status=None):
    """Return coding runs ordered newest-first, optionally filtered by engine or status."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 50), 500))
    clauses = []
    params = []
    if engine:
        clauses.append("engine = ?")
        params.append(str(engine))
    if status:
        clauses.append("status = ?")
        params.append(str(status))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(cap)
    rows = conn.execute(
        f"SELECT * FROM coding_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_row_to_coding_run(r) for r in rows]


def get_next_queued_coding_run(engine):
    """Return the oldest approved, queued coding run for engine in FIFO order, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT * FROM coding_runs
        WHERE engine = ?
          AND status = 'queued'
          AND approval_consumed = 1
          AND approved_at IS NOT NULL
        ORDER BY created_at ASC, rowid ASC
        LIMIT 1
        """,
        (str(engine),),
    ).fetchone()
    return _row_to_coding_run(row)


def approve_coding_run(run_id, approval_hash, approved_at, confidential=None):
    """Atomically consume single-use approval and transition awaiting_approval -> queued."""
    ensure_init()
    conn = connect()
    with conn:
        if confidential is None:
            cur = conn.execute(
                """
                UPDATE coding_runs
                SET status = 'queued',
                    approved_at = ?,
                    approval_consumed = 1
                WHERE id = ?
                  AND status = 'awaiting_approval'
                  AND approval_consumed = 0
                  AND approved_at IS NULL
                  AND approval_hash = ?
                """,
                (str(approved_at), str(run_id), str(approval_hash)),
            )
        else:
            cur = conn.execute(
                """
                UPDATE coding_runs
                SET status = 'queued',
                    approved_at = ?,
                    approval_consumed = 1,
                    confidential = ?
                WHERE id = ?
                  AND status = 'awaiting_approval'
                  AND approval_consumed = 0
                  AND approved_at IS NULL
                  AND approval_hash = ?
                """,
                (str(approved_at), 1 if confidential else 0, str(run_id), str(approval_hash)),
            )
    if cur.rowcount != 1:
        return None
    return get_coding_run(run_id)


def update_coding_run(run_id, **fields):
    """Update allowed fields on coding_runs and return the updated dict."""
    ensure_init()
    assignments = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_CODING_RUN_FIELDS:
            raise ValueError(f"Unsupported coding_run field '{k}'")
        if k == "out_files":
            assignments.append("out_files_json = ?")
            params.append(json.dumps(list(v or []), ensure_ascii=False))
        else:
            assignments.append(f"{k} = ?")
            params.append(v)
    if not assignments:
        return get_coding_run(run_id)
    params.append(str(run_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE coding_runs SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
    return get_coding_run(run_id)


def cancel_coding_run_queued(run_id, finished_at):
    """Cancel an awaiting_approval or queued coding run immediately; returns updated dict or None."""
    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE coding_runs
            SET status = 'cancelled', finished_at = ?, error = COALESCE(error, 'cancelled by user')
            WHERE id = ? AND status IN ('awaiting_approval', 'queued')
            """,
            (str(finished_at), str(run_id)),
        )
    return get_coding_run(run_id)


def fail_stale_running_coding_runs(finished_at, error="engine restarted"):
    """Mark any coding runs left in 'running' state after restart as failed."""
    ensure_init()
    conn = connect()
    with conn:
        rows = conn.execute(
            "SELECT id FROM coding_runs WHERE status = 'running'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(
                """
                UPDATE coding_runs
                SET status = 'failed', finished_at = ?, error = ?
                WHERE status = 'running'
                """,
                (str(finished_at), str(error)),
            )
    return [get_coding_run(rid) for rid in ids]


def _row_to_phone_device(row, *, include_hash=False):
    if row is None:
        return None
    out = {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
        "revoked_at": row["revoked_at"],
        "owner_user_id": row["owner_user_id"],
    }
    if include_hash:
        out["token_hash"] = row["token_hash"]
    return out


def create_phone_device(
    device_id,
    name,
    token_hash,
    *,
    created_at=None,
    last_seen_at=None,
    last_ip=None,
    owner_user_id=None,
):
    """Insert a paired phone device record storing only the SHA-256 token hash."""
    ensure_init()
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO phone_devices (
                id, name, token_hash, created_at, last_seen_at, last_ip, revoked_at, owner_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                str(device_id),
                str(name),
                str(token_hash),
                now,
                last_seen_at,
                last_ip,
                owner_user_id,
            ),
        )
    return get_phone_device(device_id)


def get_phone_device(device_id):
    """Return a phone device dict (without token_hash) by ID, or None."""
    if not device_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT id, name, created_at, last_seen_at, last_ip, revoked_at, owner_user_id
        FROM phone_devices
        WHERE id = ?
        """,
        (str(device_id),),
    ).fetchone()
    return _row_to_phone_device(row, include_hash=False)


def get_phone_device_by_token_hash(token_hash):
    """Look up a phone device row by sha256(token) hex digest."""
    if not token_hash:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM phone_devices WHERE token_hash = ?",
        (str(token_hash),),
    ).fetchone()
    return _row_to_phone_device(row, include_hash=False)


def list_phone_devices(include_revoked=False):
    """Return paired phone devices ordered newest-created first (without token_hash)."""
    ensure_init()
    conn = connect()
    if include_revoked:
        rows = conn.execute(
            """
            SELECT id, name, created_at, last_seen_at, last_ip, revoked_at, owner_user_id
            FROM phone_devices
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, name, created_at, last_seen_at, last_ip, revoked_at, owner_user_id
            FROM phone_devices
            WHERE revoked_at IS NULL
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
    return [_row_to_phone_device(r, include_hash=False) for r in rows]


def touch_phone_device(device_id, last_seen_at=None, last_ip=None):
    """Update last_seen_at and last_ip on an active phone device."""
    if not device_id:
        return None
    ensure_init()
    ts = str(last_seen_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE phone_devices
            SET last_seen_at = ?, last_ip = COALESCE(?, last_ip)
            WHERE id = ? AND revoked_at IS NULL
            """,
            (ts, last_ip, str(device_id)),
        )
    return get_phone_device(device_id)


def revoke_phone_device(device_id, revoked_at=None):
    """Mark a paired phone device as revoked; returns updated dict or None."""
    if not device_id:
        return None
    ensure_init()
    ts = str(revoked_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        cur = conn.execute(
            """
            UPDATE phone_devices
            SET revoked_at = ?
            WHERE id = ? AND revoked_at IS NULL
            """,
            (ts, str(device_id)),
        )
    if cur.rowcount == 0:
        existing = get_phone_device(device_id)
        return existing
    return get_phone_device(device_id)


def get_phone_inbox_receipt(device_id, client_id):
    """Return an existing inbox receipt for (device_id, client_id), or None."""
    if not device_id or not client_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT device_id, client_id, kind, target_id, created_at
        FROM phone_inbox_receipts
        WHERE device_id = ? AND client_id = ?
        """,
        (str(device_id), str(client_id)),
    ).fetchone()
    if row is None:
        return None
    return {
        "device_id": row["device_id"],
        "client_id": row["client_id"],
        "kind": row["kind"],
        "target_id": row["target_id"],
        "created_at": row["created_at"],
    }


def save_phone_inbox_receipt(device_id, client_id, kind, target_id, created_at=None):
    """Persist an idempotency receipt for a phone inbox item."""
    ensure_init()
    now = str(created_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO phone_inbox_receipts (
                device_id, client_id, kind, target_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(device_id), str(client_id), str(kind), str(target_id), now),
        )
    return get_phone_inbox_receipt(device_id, client_id)


# --- Social research persistence (schema v23; never synced to cloud) ---

_UPDATABLE_SOCIAL_RUN_FIELDS = frozenset({
    "status",
    "pages_used",
    "challenge_kind",
    "challenge_detected_at",
    "challenge_notif_id",
    "result",
    "summary",
    "error",
    "started_at",
    "finished_at",
    "updated_at",
})


def _row_to_social_setting(row):
    if row is None:
        return None
    return {
        "platform": row["platform"],
        "enabled": bool(row["enabled"]),
        "acknowledged_at": row["acknowledged_at"],
        "acknowledgement_text": row["acknowledgement_text"],
        "paused_until": row["paused_until"],
        "pause_reason": row["pause_reason"],
        "updated_at": row["updated_at"],
    }


def get_social_setting(platform):
    """Return social_settings row for platform, or None if never configured."""
    if not platform:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM social_settings WHERE platform = ?",
        (str(platform).strip().lower(),),
    ).fetchone()
    return _row_to_social_setting(row)


def list_social_settings():
    """Return all rows in social_settings keyed by platform."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM social_settings").fetchall()
    return {r["platform"]: _row_to_social_setting(r) for r in rows}


def upsert_social_setting(
    platform,
    *,
    enabled,
    acknowledged_at=None,
    acknowledgement_text=None,
    updated_at=None,
):
    """Create or update enabled/acknowledgement fields on social_settings without clearing pause state."""
    ensure_init()
    plat = str(platform).strip().lower()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO social_settings (
                platform, enabled, acknowledged_at, acknowledgement_text, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform) DO UPDATE SET
                enabled = excluded.enabled,
                acknowledged_at = COALESCE(excluded.acknowledged_at, social_settings.acknowledged_at),
                acknowledgement_text = COALESCE(excluded.acknowledgement_text, social_settings.acknowledgement_text),
                updated_at = excluded.updated_at
            """,
            (
                plat,
                1 if enabled else 0,
                acknowledged_at,
                acknowledgement_text,
                now,
            ),
        )
    return get_social_setting(plat)


def set_social_platform_paused(platform, paused_until, pause_reason=None, updated_at=None):
    """Persist a platform cooldown pause in social_settings (never cleared by restart or agents)."""
    ensure_init()
    plat = str(platform).strip().lower()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO social_settings (
                platform, enabled, paused_until, pause_reason, updated_at
            ) VALUES (?, 0, ?, ?, ?)
            ON CONFLICT(platform) DO UPDATE SET
                paused_until = excluded.paused_until,
                pause_reason = excluded.pause_reason,
                updated_at = excluded.updated_at
            """,
            (plat, paused_until, pause_reason, now),
        )
    return get_social_setting(plat)


def _row_to_social_run(row):
    if row is None:
        return None
    result = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except Exception:
            result = None
    return {
        "id": row["id"],
        "platform": row["platform"],
        "recipe": row["recipe"],
        "argument": row["argument"],
        "status": row["status"],
        "approval_hash": row["approval_hash"],
        "approved_at": row["approved_at"],
        "approval_consumed": bool(row["approval_consumed"]),
        "requested_by": row["requested_by"],
        "session_id": row["session_id"],
        "pages_used": int(row["pages_used"] or 0),
        "challenge_kind": row["challenge_kind"],
        "challenge_detected_at": row["challenge_detected_at"],
        "challenge_notif_id": row["challenge_notif_id"],
        "result": result,
        "summary": row["summary"],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "updated_at": row["updated_at"],
    }


def create_social_run(run):
    """Insert a new social_runs row and return the serialized dict."""
    ensure_init()
    now = str(run.get("created_at") or datetime.now(timezone.utc).isoformat())
    updated = str(run.get("updated_at") or now)
    res_json = json.dumps(run["result"], ensure_ascii=False) if run.get("result") is not None else None
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO social_runs (
                id, platform, recipe, argument, status, approval_hash,
                approved_at, approval_consumed, requested_by, session_id,
                pages_used, challenge_kind, challenge_detected_at, challenge_notif_id,
                result_json, summary, error, created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run["id"]),
                str(run["platform"]),
                str(run["recipe"]),
                str(run["argument"]),
                str(run.get("status") or "awaiting_start"),
                str(run["approval_hash"]),
                run.get("approved_at"),
                1 if run.get("approval_consumed") else 0,
                run.get("requested_by"),
                run.get("session_id"),
                int(run.get("pages_used") or 0),
                run.get("challenge_kind"),
                run.get("challenge_detected_at"),
                run.get("challenge_notif_id"),
                res_json,
                run.get("summary"),
                run.get("error"),
                now,
                run.get("started_at"),
                run.get("finished_at"),
                updated,
            ),
        )
    return get_social_run(run["id"])


def get_social_run(run_id):
    """Return a single social_runs dict by ID, or None."""
    if not run_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM social_runs WHERE id = ?",
        (str(run_id),),
    ).fetchone()
    return _row_to_social_run(row)


def list_social_runs(limit=50, platform=None, status=None):
    """Return social runs newest-first, optionally filtered by platform or status."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 50), 500))
    clauses = []
    params = []
    if platform:
        clauses.append("platform = ?")
        params.append(str(platform).strip().lower())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(cap)
    rows = conn.execute(
        f"SELECT * FROM social_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_row_to_social_run(r) for r in rows]


def get_active_social_run():
    """Return the currently running or waiting_user social run, or None."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT * FROM social_runs
        WHERE status IN ('running', 'waiting_user')
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    return _row_to_social_run(row)


def start_social_run_atomic(run_id, approval_hash, approved_at):
    """Atomically consume single-use approval and transition awaiting_start -> running if no other run is active."""
    ensure_init()
    conn = connect()
    ts = str(approved_at)
    with conn:
        cur = conn.execute(
            """
            UPDATE social_runs
            SET status = 'running',
                approved_at = ?,
                approval_consumed = 1,
                started_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'awaiting_start'
              AND approval_consumed = 0
              AND approved_at IS NULL
              AND approval_hash = ?
              AND NOT EXISTS (
                  SELECT 1 FROM social_runs WHERE status IN ('running', 'waiting_user')
              )
            """,
            (ts, ts, ts, str(run_id), str(approval_hash)),
        )
    if cur.rowcount != 1:
        return None
    return get_social_run(run_id)


def update_social_run(run_id, **fields):
    """Update allowed fields on social_runs and return the updated dict."""
    ensure_init()
    assignments = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_SOCIAL_RUN_FIELDS:
            raise ValueError(f"Unsupported social_run field '{k}'")
        if k == "result":
            assignments.append("result_json = ?")
            params.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
        else:
            assignments.append(f"{k} = ?")
            params.append(v)
    if not assignments:
        return get_social_run(run_id)
    if "updated_at" not in fields:
        assignments.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
    params.append(str(run_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE social_runs SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
    return get_social_run(run_id)


def update_social_run_if_status(run_id, expected_statuses, **fields):
    """Update allowed fields on social_runs only when status is in expected_statuses."""
    ensure_init()
    statuses = [str(s) for s in (expected_statuses or ()) if s]
    if not statuses:
        return None
    assignments = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_SOCIAL_RUN_FIELDS:
            raise ValueError(f"Unsupported social_run field '{k}'")
        if k == "result":
            assignments.append("result_json = ?")
            params.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
        else:
            assignments.append(f"{k} = ?")
            params.append(v)
    if not assignments:
        return get_social_run(run_id)
    if "updated_at" not in fields:
        assignments.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
    placeholders = ",".join("?" for _ in statuses)
    params.append(str(run_id))
    params.extend(statuses)
    conn = connect()
    with conn:
        cur = conn.execute(
            f"UPDATE social_runs SET {', '.join(assignments)} WHERE id = ? AND status IN ({placeholders})",
            tuple(params),
        )
    if cur.rowcount != 1:
        return None
    return get_social_run(run_id)


def fail_stale_active_social_runs(finished_at, error="engine restarted"):
    """Mark any social runs left in running or waiting_user state after restart as incomplete."""
    ensure_init()
    ts = str(finished_at)
    conn = connect()
    with conn:
        rows = conn.execute(
            "SELECT id, platform FROM social_runs WHERE status IN ('running', 'waiting_user')"
        ).fetchall()
        ids = [r["id"] for r in rows]
        for r in rows:
            plat = r["platform"]
            conn.execute(
                """
                UPDATE social_runs
                SET status = 'incomplete',
                    challenge_kind = NULL,
                    summary = COALESCE(summary, ?),
                    error = COALESCE(error, ?),
                    finished_at = ?,
                    updated_at = ?
                WHERE id = ? AND status IN ('running', 'waiting_user')
                """,
                (f"{plat} part incomplete", str(error), ts, ts, r["id"]),
            )
    return [get_social_run(rid) for rid in ids]


def mark_social_challenge_notifications_read(read_at=None):
    """Mark all unread social_challenge notifications as read."""
    ensure_init()
    ts = str(read_at or datetime.now(timezone.utc).isoformat())
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE kind = 'social_challenge' AND read_at IS NULL",
            (ts,),
        )
    return count_unread_notifications()


def get_social_daily_pages(platform, day_utc):
    """Return page count used on platform for day_utc (YYYY-MM-DD)."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT count FROM social_page_counts WHERE platform = ? AND day_utc = ?",
        (str(platform).strip().lower(), str(day_utc)),
    ).fetchone()
    return int(row["count"]) if row else 0


def increment_social_daily_pages(platform, day_utc, max_daily, updated_at=None):
    """Atomically increment daily page count if below max_daily; return new count or None if limit reached."""
    ensure_init()
    plat = str(platform).strip().lower()
    day = str(day_utc)
    ts = str(updated_at or datetime.now(timezone.utc).isoformat())
    limit = int(max_daily)
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO social_page_counts (platform, day_utc, count, updated_at)
            VALUES (?, ?, 0, ?)
            """,
            (plat, day, ts),
        )
        cur = conn.execute(
            """
            UPDATE social_page_counts
            SET count = count + 1, updated_at = ?
            WHERE platform = ? AND day_utc = ? AND count < ?
            """,
            (ts, plat, day, limit),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT count FROM social_page_counts WHERE platform = ? AND day_utc = ?",
            (plat, day),
        ).fetchone()
        return int(row["count"]) if row else None


def record_social_challenge(challenge_id, platform, run_id, challenge_kind, detected_at):
    """Insert a social_challenges audit row."""
    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO social_challenges (
                id, platform, run_id, challenge_kind, detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(challenge_id),
                str(platform).strip().lower(),
                str(run_id) if run_id else None,
                str(challenge_kind),
                str(detected_at),
            ),
        )


def count_recent_social_challenges(platform, since_iso):
    """Return count of challenges recorded for platform at or after since_iso."""
    ensure_init()
    conn = connect()
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM social_challenges
        WHERE platform = ? AND detected_at >= ?
        """,
        (str(platform).strip().lower(), str(since_iso)),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def resolve_social_challenges_for_run(run_id, resolved_at, resolution):
    """Mark unresolved social_challenges rows for run_id as resolved."""
    if not run_id:
        return
    ensure_init()
    conn = connect()
    with conn:
        conn.execute(
            """
            UPDATE social_challenges
            SET resolved_at = ?, resolution = ?
            WHERE run_id = ? AND resolved_at IS NULL
            """,
            (str(resolved_at), str(resolution), str(run_id)),
        )


def _row_to_posting_account(row):
    if row is None:
        return None
    return {
        "platform": row["platform"],
        "account_id": row["account_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "client_id": row["client_id"],
        "connected_at": row["connected_at"],
        "expires_at": row["expires_at"],
        "last_refreshed_at": row["last_refreshed_at"],
        "updated_at": row["updated_at"],
    }


def upsert_posting_account(
    platform,
    *,
    account_id=None,
    username=None,
    display_name=None,
    client_id=None,
    connected_at=None,
    expires_at=None,
    last_refreshed_at=None,
    updated_at=None,
):
    """Insert or update non-secret posting account metadata for platform."""
    ensure_init()
    plat = str(platform or "").strip().lower()
    now = str(updated_at or datetime.now(timezone.utc).isoformat())
    existing = get_posting_account(plat)
    conn_ts = str(connected_at or (existing["connected_at"] if existing else now))
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO posting_accounts (
                platform, account_id, username, display_name, client_id,
                connected_at, expires_at, last_refreshed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform) DO UPDATE SET
                account_id = excluded.account_id,
                username = excluded.username,
                display_name = excluded.display_name,
                client_id = COALESCE(excluded.client_id, posting_accounts.client_id),
                connected_at = excluded.connected_at,
                expires_at = excluded.expires_at,
                last_refreshed_at = COALESCE(excluded.last_refreshed_at, posting_accounts.last_refreshed_at),
                updated_at = excluded.updated_at
            """,
            (
                plat,
                str(account_id) if account_id is not None else None,
                str(username) if username is not None else None,
                str(display_name) if display_name is not None else None,
                str(client_id) if client_id is not None else None,
                conn_ts,
                str(expires_at) if expires_at is not None else None,
                str(last_refreshed_at) if last_refreshed_at is not None else None,
                now,
            ),
        )
    return get_posting_account(plat)


def get_posting_account(platform):
    """Return non-secret posting account row for platform, or None."""
    if not platform:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM posting_accounts WHERE platform = ?",
        (str(platform).strip().lower(),),
    ).fetchone()
    return _row_to_posting_account(row)


def list_posting_accounts():
    """Return all posting_accounts rows keyed by platform."""
    ensure_init()
    conn = connect()
    rows = conn.execute("SELECT * FROM posting_accounts").fetchall()
    return {r["platform"]: _row_to_posting_account(r) for r in rows}


def delete_posting_account(platform):
    """Delete posting_accounts row for platform."""
    if not platform:
        return False
    ensure_init()
    conn = connect()
    with conn:
        cur = conn.execute(
            "DELETE FROM posting_accounts WHERE platform = ?",
            (str(platform).strip().lower(),),
        )
    return cur.rowcount > 0


_UPDATABLE_POSTING_ATTEMPT_FIELDS = frozenset({
    "status",
    "post_id",
    "post_url",
    "error",
    "confirmed_at",
    "finished_at",
    "updated_at",
})


def _row_to_posting_attempt(row):
    if row is None:
        return None
    opts = {}
    if row["options_json"]:
        try:
            loaded = json.loads(row["options_json"])
            if isinstance(loaded, dict):
                opts = loaded
        except Exception:
            opts = {}
    return {
        "id": row["id"],
        "content_id": row["content_id"],
        "platform": row["platform"],
        "account_id": row["account_id"],
        "confirmation_id": row["confirmation_id"],
        "payload_hash": row["payload_hash"],
        "options": opts,
        "status": row["status"],
        "post_id": row["post_id"],
        "post_url": row["post_url"],
        "error": row["error"],
        "prepared_at": row["prepared_at"],
        "expires_at": row["expires_at"],
        "confirmed_at": row["confirmed_at"],
        "finished_at": row["finished_at"],
        "updated_at": row["updated_at"],
    }


def create_posting_attempt(attempt):
    """Insert a new posting_attempts row in prepared status."""
    ensure_init()
    now = str(attempt.get("prepared_at") or datetime.now(timezone.utc).isoformat())
    updated = str(attempt.get("updated_at") or now)
    opts_json = json.dumps(attempt["options"], ensure_ascii=False) if attempt.get("options") is not None else None
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO posting_attempts (
                id, content_id, platform, account_id, confirmation_id, payload_hash,
                options_json, status, post_id, post_url, error, prepared_at, expires_at,
                confirmed_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(attempt["id"]),
                str(attempt["content_id"]),
                str(attempt["platform"]).strip().lower(),
                attempt.get("account_id"),
                str(attempt["confirmation_id"]),
                str(attempt["payload_hash"]),
                opts_json,
                str(attempt.get("status") or "prepared"),
                attempt.get("post_id"),
                attempt.get("post_url"),
                attempt.get("error"),
                now,
                str(attempt["expires_at"]),
                attempt.get("confirmed_at"),
                attempt.get("finished_at"),
                updated,
            ),
        )
    return get_posting_attempt(attempt["id"])


def get_posting_attempt(attempt_id):
    """Return a single posting_attempts dict by id, or None."""
    if not attempt_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM posting_attempts WHERE id = ?",
        (str(attempt_id),),
    ).fetchone()
    return _row_to_posting_attempt(row)


def get_posting_attempt_by_confirmation(confirmation_id):
    """Return a single posting_attempts dict by confirmation_id, or None."""
    if not confirmation_id:
        return None
    ensure_init()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM posting_attempts WHERE confirmation_id = ?",
        (str(confirmation_id),),
    ).fetchone()
    return _row_to_posting_attempt(row)


def list_posting_attempts(content_id=None, platform=None, limit=50):
    """Return posting attempts newest-first, optionally filtered by content_id or platform."""
    ensure_init()
    conn = connect()
    cap = max(1, min(int(limit or 50), 500))
    clauses = []
    params = []
    if content_id:
        clauses.append("content_id = ?")
        params.append(str(content_id))
    if platform:
        clauses.append("platform = ?")
        params.append(str(platform).strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(cap)
    rows = conn.execute(
        f"SELECT * FROM posting_attempts {where} ORDER BY prepared_at DESC, rowid DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_row_to_posting_attempt(r) for r in rows]


def list_executed_posting_attempts(content_id, platform):
    """Every attempt that reached sending, newest first (previews never hide an unknown or posted one)."""
    ensure_init()
    rows = connect().execute(
        """
        SELECT * FROM posting_attempts
        WHERE content_id = ? AND platform = ? AND status IN ('sending', 'unknown', 'posted', 'failed')
        ORDER BY confirmed_at DESC, rowid DESC
        """,
        (str(content_id), str(platform).strip().lower()),
    ).fetchall()
    return [_row_to_posting_attempt(r) for r in rows]


def acquire_posting_attempt_atomic(confirmation_id, payload_hash, content_id, platform, confirmed_at):
    """Atomically consume a prepared posting_attempt and transition prepared -> sending."""
    ensure_init()
    ts = str(confirmed_at)
    plat = str(platform).strip().lower()
    cid = str(content_id)
    conn = connect()
    with conn:
        cur = conn.execute(
            """
            UPDATE posting_attempts
            SET status = 'sending',
                confirmed_at = ?,
                updated_at = ?
            WHERE confirmation_id = ?
              AND payload_hash = ?
              AND content_id = ?
              AND platform = ?
              AND status = 'prepared'
              AND NOT EXISTS (
                  SELECT 1 FROM posting_attempts
                  WHERE content_id = ? AND platform = ? AND status = 'sending'
              )
            """,
            (ts, ts, str(confirmation_id), str(payload_hash), cid, plat, cid, plat),
        )
    if cur.rowcount != 1:
        return None
    return get_posting_attempt_by_confirmation(confirmation_id)


def update_posting_attempt(attempt_id, **fields):
    """Update allowed fields on posting_attempts and return the updated dict."""
    ensure_init()
    assignments = []
    params = []
    for k, v in fields.items():
        if k not in _UPDATABLE_POSTING_ATTEMPT_FIELDS:
            raise ValueError(f"Unsupported posting_attempt field '{k}'")
        assignments.append(f"{k} = ?")
        params.append(v)
    if not assignments:
        return get_posting_attempt(attempt_id)
    if "updated_at" not in fields:
        assignments.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
    params.append(str(attempt_id))
    conn = connect()
    with conn:
        conn.execute(
            f"UPDATE posting_attempts SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
    return get_posting_attempt(attempt_id)


def recover_stale_posting_attempts(finished_at=None):
    """Mark any posting_attempts left in 'sending' after restart as 'unknown'."""
    ensure_init()
    ts = str(finished_at or datetime.now(timezone.utc).isoformat())
    msg = "Post status is unknown (engine restarted while sending); check the platform before trying again."
    conn = connect()
    with conn:
        rows = conn.execute(
            "SELECT id FROM posting_attempts WHERE status = 'sending'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(
                """
                UPDATE posting_attempts
                SET status = 'unknown',
                    error = COALESCE(error, ?),
                    finished_at = ?,
                    updated_at = ?
                WHERE status = 'sending'
                """,
                (msg, ts, ts),
            )
    return [get_posting_attempt(aid) for aid in ids]




