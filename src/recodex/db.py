from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CatalogEntry, ImprovementDraft, ParsedTranscript, SessionRecord, TranscriptEvent
from .paths import ensure_parent


def connect(path: Path) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            id TEXT UNIQUE,
            source_path TEXT NOT NULL UNIQUE,
            source TEXT,
            project_path TEXT,
            transcript_path TEXT,
            started_at TEXT,
            updated_at TEXT,
            ended_at TEXT,
            model TEXT,
            title TEXT NOT NULL,
            status TEXT,
            raw_hash TEXT,
            tool TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            user_message_count INTEGER NOT NULL,
            assistant_message_count INTEGER NOT NULL,
            command_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            raw_preview TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            session_id TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT,
            PRIMARY KEY (session_id, event_index),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at);
        CREATE INDEX IF NOT EXISTS idx_events_session_role ON events(session_id, role);

        CREATE TABLE IF NOT EXISTS improvements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            session_id TEXT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            evidence TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_improvements_status ON improvements(status);
        CREATE INDEX IF NOT EXISTS idx_improvements_category ON improvements(category);

        CREATE TABLE IF NOT EXISTS session_catalog (
            source_path TEXT PRIMARY KEY,
            source TEXT,
            session_id TEXT NOT NULL,
            project_path TEXT,
            started_at TEXT,
            updated_at TEXT,
            model TEXT,
            title TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            cataloged_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_session_catalog_project ON session_catalog(project_path);
        CREATE INDEX IF NOT EXISTS idx_session_catalog_updated ON session_catalog(updated_at);

        CREATE TABLE IF NOT EXISTS raw_session_files (
            session_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            hot_path TEXT,
            archive_path TEXT,
            size_bytes INTEGER NOT NULL,
            mtime REAL NOT NULL,
            inode TEXT,
            sha256_prefix TEXT,
            parsed_until_offset INTEGER DEFAULT 0,
            status TEXT DEFAULT 'hot',
            first_seen_at TEXT,
            last_seen_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_raw_session_files_status ON raw_session_files(status);
        CREATE INDEX IF NOT EXISTS idx_raw_session_files_hot_path ON raw_session_files(hot_path);
        CREATE INDEX IF NOT EXISTS idx_raw_session_files_archive_path ON raw_session_files(archive_path);

        CREATE TABLE IF NOT EXISTS llm_jobs (
            id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            prompt_version TEXT,
            schema_version TEXT,
            rulebase_version TEXT,
            input_hash TEXT,
            status TEXT,
            error TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_llm_jobs_input_hash ON llm_jobs(input_hash);
        CREATE INDEX IF NOT EXISTS idx_llm_jobs_status ON llm_jobs(status);

        CREATE TABLE IF NOT EXISTS llm_outputs (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            output_json TEXT NOT NULL,
            usage_json TEXT,
            validation_status TEXT,
            created_at TEXT,
            FOREIGN KEY (job_id) REFERENCES llm_jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sync_files (
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            mtime REAL NOT NULL,
            content_hash TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (source, path)
        );

        CREATE TABLE IF NOT EXISTS import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            root_path TEXT NOT NULL,
            scanned INTEGER NOT NULL,
            imported INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS import_errors (
            run_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES import_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS watch_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            scope TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_sync_at TEXT,
            last_imported INTEGER NOT NULL DEFAULT 0,
            last_skipped INTEGER NOT NULL DEFAULT 0,
            last_failed INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            UNIQUE(source, path)
        );

        CREATE TABLE IF NOT EXISTS watch_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_source_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            imported INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (watch_source_id) REFERENCES watch_sources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS generated_reports (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            session_id TEXT,
            project_path TEXT,
            title TEXT NOT NULL,
            html_path TEXT,
            markdown_path TEXT,
            json_path TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifact_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_type TEXT NOT NULL,
            improvement_id INTEGER,
            target_path TEXT NOT NULL,
            status TEXT NOT NULL,
            conflict_policy TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (improvement_id) REFERENCES improvements(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS analysis_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            label TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_examples_source
            ON analysis_examples(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_analysis_examples_label
            ON analysis_examples(label);

        CREATE TABLE IF NOT EXISTS judge_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_version TEXT NOT NULL,
            source_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_judge_failures_workflow
            ON judge_failures(workflow_version, source_id);

        CREATE TABLE IF NOT EXISTS prompt_versions (
            version TEXT PRIMARY KEY,
            workflow_version TEXT NOT NULL,
            prompts_json TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS raw_artifacts (
            artifact_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            content_hash TEXT,
            mtime REAL,
            size_bytes INTEGER,
            ingest_run_id TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_raw_artifacts_session ON raw_artifacts(session_id);

        CREATE TABLE IF NOT EXISTS raw_records (
            raw_record_id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            provider_record_id TEXT,
            physical_index INTEGER,
            raw_json TEXT NOT NULL,
            raw_text_preview TEXT,
            raw_hash TEXT,
            created_at TEXT,
            FOREIGN KEY (artifact_id) REFERENCES raw_artifacts(artifact_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_raw_records_session ON raw_records(session_id);
        CREATE INDEX IF NOT EXISTS idx_raw_records_artifact ON raw_records(artifact_id);

        CREATE TABLE IF NOT EXISTS turns (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            initiator TEXT,
            phase_hint TEXT,
            started_at TEXT,
            ended_at TEXT,
            parent_turn_id TEXT,
            source_refs TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, turn_index);

        CREATE TABLE IF NOT EXISTS normalized_events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            event_type TEXT NOT NULL,
            kind TEXT NOT NULL,
            phase TEXT NOT NULL,
            created_at TEXT,
            source_ref TEXT NOT NULL,
            text_excerpt TEXT,
            user_input_text TEXT,
            raw_record_ids TEXT,
            metadata_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_events_source_ref
            ON normalized_events(session_id, source_ref);
        CREATE INDEX IF NOT EXISTS idx_normalized_events_turn
            ON normalized_events(turn_id, event_index);
        CREATE INDEX IF NOT EXISTS idx_normalized_events_phase
            ON normalized_events(session_id, phase);

        CREATE TABLE IF NOT EXISTS tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            tool_name TEXT,
            command TEXT,
            arguments_json TEXT,
            cwd TEXT,
            started_at TEXT,
            status TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);

        CREATE TABLE IF NOT EXISTS tool_results (
            tool_result_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            exit_code INTEGER,
            stdout_preview TEXT,
            stderr_preview TEXT,
            duration_ms INTEGER,
            status TEXT,
            error_type TEXT,
            output_hash TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (tool_call_id) REFERENCES tool_calls(tool_call_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tool_results_session ON tool_results(session_id);

        CREATE TABLE IF NOT EXISTS file_refs (
            file_ref_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            path TEXT NOT NULL,
            path_role TEXT,
            line_start INTEGER,
            line_end INTEGER,
            language TEXT,
            operation TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_file_refs_session ON file_refs(session_id);
        CREATE INDEX IF NOT EXISTS idx_file_refs_path ON file_refs(path);

        CREATE TABLE IF NOT EXISTS test_refs (
            test_ref_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            tool_call_id TEXT,
            command TEXT,
            framework TEXT,
            status TEXT,
            failure_count INTEGER,
            summary TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_test_refs_session ON test_refs(session_id);

        CREATE TABLE IF NOT EXISTS error_refs (
            error_ref_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            tool_call_id TEXT,
            error_type TEXT,
            message TEXT,
            stack_preview TEXT,
            is_recovered INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_error_refs_session ON error_refs(session_id);
        CREATE INDEX IF NOT EXISTS idx_error_refs_type ON error_refs(error_type);

        CREATE TABLE IF NOT EXISTS user_corrections (
            correction_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            correction_type TEXT,
            target_event_ids TEXT,
            summary TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES normalized_events(event_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_corrections_session ON user_corrections(session_id);

        CREATE TABLE IF NOT EXISTS transcript_edges (
            edge_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            from_type TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_type TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL,
            created_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_transcript_edges_from
            ON transcript_edges(session_id, from_type, from_id);
        CREATE INDEX IF NOT EXISTS idx_transcript_edges_to
            ON transcript_edges(session_id, to_type, to_id);

        CREATE TABLE IF NOT EXISTS normalization_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            graph_schema_version TEXT NOT NULL,
            normalizer_version TEXT NOT NULL,
            raw_record_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_normalization_runs_session ON normalization_runs(session_id);
        """
    )
    ensure_session_design_columns(conn)
    ensure_session_catalog_columns(conn)
    ensure_normalized_event_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_catalog_source ON session_catalog(source)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_id ON sessions(id)")
    init_event_search(conn)
    conn.commit()


SESSION_DESIGN_COLUMNS = (
    ("id", "TEXT"),
    ("source", "TEXT"),
    ("project_path", "TEXT"),
    ("transcript_path", "TEXT"),
    ("ended_at", "TEXT"),
    ("model", "TEXT"),
    ("status", "TEXT"),
    ("raw_hash", "TEXT"),
)

NORMALIZED_EVENT_COLUMNS = (
    ("user_input_text", "TEXT"),
)

SESSION_CATALOG_COLUMNS = (
    ("source", "TEXT"),
)


def ensure_session_design_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "sessions")
    for name, ddl in SESSION_DESIGN_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
    _backfill_sessions_column(conn, "id", "session_id")
    _backfill_sessions_column(conn, "source", "tool")
    _backfill_sessions_column(conn, "transcript_path", "source_path")
    _backfill_sessions_column(conn, "ended_at", "updated_at")
    _backfill_sessions_column(conn, "status", "'unknown'")


def ensure_session_catalog_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "session_catalog")
    for name, ddl in SESSION_CATALOG_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE session_catalog ADD COLUMN {name} {ddl}")
    conn.execute(
        """
        UPDATE session_catalog
        SET source = 'claude-code'
        WHERE (source IS NULL OR source = '')
          AND source_path LIKE '%/.claude/projects/%'
        """
    )
    conn.execute(
        """
        UPDATE session_catalog
        SET source = 'codex'
        WHERE (source IS NULL OR source = '')
          AND source_path LIKE '%/.codex/%'
        """
    )


def ensure_normalized_event_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "normalized_events")
    for name, ddl in NORMALIZED_EVENT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE normalized_events ADD COLUMN {name} {ddl}")


def _backfill_sessions_column(conn: sqlite3.Connection, column: str, expression: str) -> None:
    missing = conn.execute(
        f"SELECT 1 FROM sessions WHERE {column} IS NULL OR {column} = '' LIMIT 1"
    ).fetchone()
    if missing:
        conn.execute(f"UPDATE sessions SET {column} = {expression} WHERE {column} IS NULL OR {column} = ''")


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, now),
    )
    conn.commit()


def init_event_search(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                session_id UNINDEXED,
                event_index UNINDEXED,
                role UNINDEXED,
                kind UNINDEXED,
                text
            )
            """
        )
    except sqlite3.OperationalError:
        return
    backfill_event_search(conn)


def backfill_event_search(conn: sqlite3.Connection) -> None:
    if not event_search_available(conn):
        return
    fts_count = conn.execute("SELECT COUNT(*) AS count FROM events_fts").fetchone()["count"]
    if fts_count:
        return
    conn.execute(
        """
        INSERT INTO events_fts (session_id, event_index, role, kind, text)
        SELECT session_id, event_index, role, kind, text
        FROM events
        """
    )


def session_design_values(parsed: ParsedTranscript) -> dict[str, str | None]:
    session = parsed.session
    return {
        "id": getattr(session, "id", None) or session.session_id,
        "source": getattr(session, "source", None) or session.tool,
        "project_path": getattr(session, "project_path", None),
        "transcript_path": getattr(session, "transcript_path", None) or session.source_path,
        "ended_at": getattr(session, "ended_at", None) or session.updated_at,
        "model": getattr(session, "model", None),
        "status": getattr(session, "status", None) or "unknown",
        "raw_hash": getattr(session, "raw_hash", None) or transcript_raw_hash(parsed),
    }


def transcript_raw_hash(parsed: ParsedTranscript) -> str:
    digest = hashlib.sha256()

    def add(value: object) -> None:
        digest.update(str(value or "").encode("utf-8"))
        digest.update(b"\0")

    session = parsed.session
    for value in (
        session.session_id,
        session.source_path,
        session.started_at,
        session.updated_at,
        session.title,
        session.tool,
        session.raw_preview,
    ):
        add(value)
    for event in parsed.events:
        for value in (
            event.session_id,
            event.event_index,
            event.role,
            event.kind,
            event.text,
            event.created_at,
        ):
            add(value)
    return digest.hexdigest()


def save_transcript(conn: sqlite3.Connection, parsed: ParsedTranscript, *, commit: bool = True) -> None:
    now = now_utc()
    session = parsed.session
    design = session_design_values(parsed)
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, id, source_path, source, project_path, transcript_path,
            started_at, updated_at, ended_at, model, title, status, raw_hash,
            tool, message_count, user_message_count, assistant_message_count,
            command_count, error_count, raw_preview, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            id = excluded.id,
            source_path = excluded.source_path,
            source = excluded.source,
            project_path = excluded.project_path,
            transcript_path = excluded.transcript_path,
            started_at = excluded.started_at,
            updated_at = excluded.updated_at,
            ended_at = excluded.ended_at,
            model = excluded.model,
            title = excluded.title,
            status = excluded.status,
            raw_hash = excluded.raw_hash,
            tool = excluded.tool,
            message_count = excluded.message_count,
            user_message_count = excluded.user_message_count,
            assistant_message_count = excluded.assistant_message_count,
            command_count = excluded.command_count,
            error_count = excluded.error_count,
            raw_preview = excluded.raw_preview,
            ingested_at = excluded.ingested_at
        """,
        (
            session.session_id,
            design["id"],
            session.source_path,
            design["source"],
            design["project_path"],
            design["transcript_path"],
            session.started_at,
            session.updated_at,
            design["ended_at"],
            design["model"],
            session.title,
            design["status"],
            design["raw_hash"],
            session.tool,
            session.message_count,
            session.user_message_count,
            session.assistant_message_count,
            session.command_count,
            session.error_count,
            session.raw_preview,
            now,
        ),
    )
    conn.execute("DELETE FROM events WHERE session_id = ?", (session.session_id,))
    delete_session_from_search(conn, session.session_id)
    conn.executemany(
        """
        INSERT INTO events (session_id, event_index, role, kind, text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                event.session_id,
                event.event_index,
                event.role,
                event.kind,
                event.text,
                event.created_at,
            )
            for event in parsed.events
        ],
    )
    insert_events_into_search(conn, parsed.events)
    from .transcript_graph import save_transcript_graph

    save_transcript_graph(conn, parsed, commit=False)
    if commit:
        conn.commit()


def latest_session(conn: sqlite3.Connection) -> SessionRecord | None:
    return get_session(conn, "latest")


def get_session(conn: sqlite3.Connection, session_id_or_latest: str) -> SessionRecord | None:
    if session_id_or_latest == "latest":
        row = conn.execute(
            """
            SELECT * FROM sessions
            ORDER BY COALESCE(ended_at, updated_at, ingested_at) DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE session_id = ? OR id = ?
            LIMIT 1
            """,
            (session_id_or_latest, session_id_or_latest),
        ).fetchone()
    return row_to_session(row) if row else None


def count_sessions(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()
    return int(row["count"])


def save_catalog_entries(conn: sqlite3.Connection, entries: list[CatalogEntry]) -> int:
    now = now_utc()
    conn.executemany(
        """
        INSERT INTO session_catalog (
            source_path, source, session_id, project_path, started_at, updated_at,
            model, title, file_size, cataloged_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_path) DO UPDATE SET
            source = excluded.source,
            session_id = excluded.session_id,
            project_path = excluded.project_path,
            started_at = excluded.started_at,
            updated_at = excluded.updated_at,
            model = excluded.model,
            title = excluded.title,
            file_size = excluded.file_size,
            cataloged_at = excluded.cataloged_at
        """,
        [
            (
                entry.source_path,
                entry.source,
                entry.session_id,
                entry.project_path,
                entry.started_at,
                entry.updated_at,
                entry.model,
                entry.title,
                entry.file_size,
                now,
            )
            for entry in entries
        ],
    )
    conn.commit()
    return len(entries)


def count_catalog_entries(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM session_catalog").fetchone()
    return int(row["count"])


def list_catalog_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list_catalog_projects_for_source(conn)


def list_catalog_projects_for_source(conn: sqlite3.Connection, source: str | None = None) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(
        conn.execute(
            f"""
            SELECT
                COALESCE(project_path, '(unknown)') AS project_path,
                COUNT(*) AS session_count,
                MAX(updated_at) AS latest_at,
                SUM(file_size) AS total_bytes,
                GROUP_CONCAT(DISTINCT COALESCE(source, 'catalog')) AS sources
            FROM session_catalog
            {where}
            GROUP BY COALESCE(project_path, '(unknown)')
            ORDER BY latest_at DESC, session_count DESC, project_path ASC
            """,
            params,
        ).fetchall()
    )


def list_catalog_entries(
    conn: sqlite3.Connection,
    *,
    project_path: str | None = None,
    session_id: str | None = None,
    source: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM session_catalog"
    clauses: list[str] = []
    params: list[Any] = []
    if project_path is not None:
        if project_path == "(unknown)":
            clauses.append("(project_path IS NULL OR project_path = '')")
        else:
            clauses.append("project_path = ?")
            params.append(project_path)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY COALESCE(updated_at, started_at, cataloged_at) DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params).fetchall())


def list_session_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                COALESCE(NULLIF(project_path, ''), '(unknown)') AS project_path,
                COUNT(*) AS session_count,
                SUM(command_count) AS command_count,
                SUM(error_count) AS error_count,
                MAX(COALESCE(updated_at, ingested_at)) AS latest_at,
                GROUP_CONCAT(DISTINCT COALESCE(source, tool)) AS sources
            FROM sessions
            GROUP BY COALESCE(NULLIF(project_path, ''), '(unknown)')
            ORDER BY latest_at DESC, session_count DESC, project_path ASC
            """
        ).fetchall()
    )


def list_sessions(
    conn: sqlite3.Connection,
    since: str | None = None,
    *,
    project_path: str | None = None,
) -> list[SessionRecord]:
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append("COALESCE(updated_at, ingested_at) >= ?")
        params.append(since)
    if project_path is not None:
        if project_path == "(unknown)":
            clauses.append("(project_path IS NULL OR project_path = '')")
        else:
            clauses.append("project_path = ?")
            params.append(project_path)
    sql = "SELECT * FROM sessions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY COALESCE(updated_at, ingested_at) DESC"
    rows = conn.execute(sql, params).fetchall()
    return [row_to_session(row) for row in rows]


def get_events(conn: sqlite3.Connection, session_id: str) -> list[TranscriptEvent]:
    rows = conn.execute(
        """
        SELECT * FROM events
        WHERE session_id = ?
        ORDER BY event_index ASC
        """,
        (session_id,),
    ).fetchall()
    return [row_to_event(row) for row in rows]


def search_events(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    cleaned = query.strip()
    if not cleaned or limit <= 0:
        return []
    if event_search_available(conn):
        try:
            return list(
                conn.execute(
                    """
                    SELECT e.*
                    FROM events_fts
                    JOIN events e
                        ON e.session_id = events_fts.session_id
                       AND e.event_index = events_fts.event_index
                    WHERE events_fts MATCH ?
                    ORDER BY bm25(events_fts), e.session_id, e.event_index
                    LIMIT ?
                    """,
                    (quote_fts_query(cleaned), limit),
                ).fetchall()
            )
        except sqlite3.OperationalError:
            pass
    return search_events_like(conn, cleaned, limit)


def event_search_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'events_fts'
        """
    ).fetchone()
    return row is not None


def delete_session_from_search(conn: sqlite3.Connection, session_id: str) -> None:
    if event_search_available(conn):
        conn.execute("DELETE FROM events_fts WHERE session_id = ?", (session_id,))


def insert_events_into_search(conn: sqlite3.Connection, events: tuple[TranscriptEvent, ...]) -> None:
    if not events or not event_search_available(conn):
        return
    conn.executemany(
        """
        INSERT INTO events_fts (session_id, event_index, role, kind, text)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                event.session_id,
                event.event_index,
                event.role,
                event.kind,
                event.text,
            )
            for event in events
        ],
    )


def quote_fts_query(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def search_events_like(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    pattern = f"%{escape_like(query)}%"
    return list(
        conn.execute(
            """
            SELECT *
            FROM events
            WHERE text LIKE ? ESCAPE '\\'
            ORDER BY session_id, event_index
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_cached_llm_output(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    input_hash: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT o.*
        FROM llm_outputs o
        JOIN llm_jobs j ON j.id = o.job_id
        WHERE j.task_type = ?
          AND j.provider = ?
          AND j.model = ?
          AND j.prompt_version = ?
          AND j.schema_version = ?
          AND j.input_hash = ?
          AND j.status = 'ok'
        ORDER BY o.created_at DESC
        LIMIT 1
        """,
        (task_type, provider, model, prompt_version, schema_version, input_hash),
    ).fetchone()


def insert_llm_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    task_type: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    rulebase_version: str,
    input_hash: str,
    status: str,
    error: str | None = None,
) -> None:
    now = now_utc()
    conn.execute(
        """
        INSERT INTO llm_jobs (
            id, task_type, provider, model, prompt_version, schema_version,
            rulebase_version, input_hash, status, error, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            task_type,
            provider,
            model,
            prompt_version,
            schema_version,
            rulebase_version,
            input_hash,
            status,
            error,
            now,
            now,
        ),
    )
    conn.commit()


def update_llm_job_status(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE llm_jobs
        SET status = ?, error = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, error, now_utc(), job_id),
    )
    conn.commit()


def insert_llm_output(
    conn: sqlite3.Connection,
    *,
    output_id: str,
    job_id: str,
    output_json: str,
    usage_json: str,
    validation_status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_outputs (
            id, job_id, output_json, usage_json, validation_status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (output_id, job_id, output_json, usage_json, validation_status, now_utc()),
    )
    conn.commit()


def insert_improvements(
    conn: sqlite3.Connection,
    drafts: list[ImprovementDraft],
) -> tuple[int, int]:
    created = 0
    skipped = 0
    now = now_utc()
    for draft in drafts:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO improvements (
                fingerprint, session_id, category, title, evidence,
                recommendation, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
            """,
            (
                draft.fingerprint,
                draft.session_id,
                draft.category,
                draft.title,
                draft.evidence,
                draft.recommendation,
                now,
            ),
        )
        if cursor.rowcount:
            created += 1
        else:
            skipped += 1
    conn.commit()
    return created, skipped


def list_improvements(
    conn: sqlite3.Connection,
    status: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM improvements"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params).fetchall())


def get_improvement(conn: sqlite3.Connection, improvement_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM improvements WHERE id = ? LIMIT 1",
        (improvement_id,),
    ).fetchone()


def update_improvement_fields(
    conn: sqlite3.Connection,
    improvement_id: int,
    *,
    title: str | None = None,
    category: str | None = None,
    evidence: str | None = None,
    recommendation: str | None = None,
    status: str | None = None,
) -> bool:
    fields = {
        "title": title,
        "category": category,
        "evidence": evidence,
        "recommendation": recommendation,
        "status": status,
    }
    assignments = [f"{name} = ?" for name, value in fields.items() if value is not None]
    values = [value for value in fields.values() if value is not None]
    if not assignments:
        return get_improvement(conn, improvement_id) is not None
    values.append(improvement_id)
    cursor = conn.execute(
        f"UPDATE improvements SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    conn.commit()
    return cursor.rowcount > 0


def update_improvement_status(conn: sqlite3.Connection, ids: list[int], status: str) -> int:
    if not ids:
        return 0
    now = now_utc()
    changed = 0
    for improvement_id in ids:
        cursor = conn.execute(
            """
            UPDATE improvements
            SET status = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (status, now, improvement_id),
        )
        changed += cursor.rowcount
    conn.commit()
    return changed


def record_analysis_feedback(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    label: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO analysis_examples (source_type, source_id, label, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_type, source_id, label, json.dumps(payload, ensure_ascii=False, sort_keys=True), now_utc()),
    )
    conn.commit()


def record_judge_failure(
    conn: sqlite3.Connection,
    *,
    workflow_version: str,
    source_id: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO judge_failures (workflow_version, source_id, reason, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (workflow_version, source_id, reason, json.dumps(payload, ensure_ascii=False, sort_keys=True), now_utc()),
    )
    conn.commit()


def record_prompt_version(
    conn: sqlite3.Connection,
    *,
    version: str,
    workflow_version: str,
    prompts: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO prompt_versions (version, workflow_version, prompts_json, schema_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(version) DO UPDATE SET
            workflow_version = excluded.workflow_version,
            prompts_json = excluded.prompts_json,
            schema_json = excluded.schema_json
        """,
        (
            version,
            workflow_version,
            json.dumps(prompts, ensure_ascii=False, sort_keys=True),
            json.dumps(schema, ensure_ascii=False, sort_keys=True),
            now_utc(),
        ),
    )
    conn.commit()


def row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        source_path=row["source_path"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        title=row["title"],
        tool=row["tool"],
        message_count=row["message_count"],
        user_message_count=row["user_message_count"],
        assistant_message_count=row["assistant_message_count"],
        command_count=row["command_count"],
        error_count=row["error_count"],
        raw_preview=row["raw_preview"],
        id=row_value(row, "id"),
        source=row_value(row, "source"),
        project_path=row_value(row, "project_path"),
        transcript_path=row_value(row, "transcript_path"),
        ended_at=row_value(row, "ended_at"),
        model=row_value(row, "model"),
        status=row_value(row, "status"),
        raw_hash=row_value(row, "raw_hash"),
    )


def row_value(row: sqlite3.Row, name: str) -> Any:
    return row[name] if name in row.keys() else None


def row_to_event(row: sqlite3.Row) -> TranscriptEvent:
    return TranscriptEvent(
        session_id=row["session_id"],
        event_index=row["event_index"],
        role=row["role"],
        kind=row["kind"],
        text=row["text"],
        created_at=row["created_at"],
    )


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
