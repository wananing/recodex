from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from recodex.db import now_utc
from recodex.importers import get_importer
from recodex.sync import ImportReport, sync_import_paths


@dataclass(frozen=True)
class WatchSource:
    id: int
    source: str
    path: Path
    scope: str | None
    enabled: bool
    created_at: str
    updated_at: str
    last_sync_at: str | None
    last_imported: int
    last_skipped: int
    last_failed: int
    last_error: str | None


def add_watch_source(
    conn: sqlite3.Connection,
    *,
    source: str,
    path: Path,
    scope: str | None = None,
    enabled: bool = True,
) -> WatchSource:
    importer = get_importer(source)
    resolved = path.expanduser().resolve()
    now = now_utc()
    conn.execute(
        """
        INSERT INTO watch_sources (
            source, path, scope, enabled, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, path) DO UPDATE SET
            scope = excluded.scope,
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
        """,
        (importer.name, str(resolved), scope, int(enabled), now, now),
    )
    conn.commit()
    return get_watch_source_by_path(conn, importer.name, resolved)


def update_watch_source(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    source: str | None = None,
    path: Path | None = None,
    scope: str | None = None,
    enabled: bool | None = None,
) -> WatchSource | None:
    current = get_watch_source(conn, source_id)
    if current is None:
        return None
    next_source = get_importer(source).name if source else current.source
    next_path = path.expanduser().resolve() if path else current.path
    next_scope = scope if scope is not None else current.scope
    next_enabled = current.enabled if enabled is None else enabled
    conn.execute(
        """
        UPDATE watch_sources
        SET source = ?, path = ?, scope = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (next_source, str(next_path), next_scope, int(next_enabled), now_utc(), source_id),
    )
    conn.commit()
    return get_watch_source(conn, source_id)


def delete_watch_source(conn: sqlite3.Connection, source_id: int) -> bool:
    cursor = conn.execute("DELETE FROM watch_sources WHERE id = ?", (source_id,))
    conn.commit()
    return bool(cursor.rowcount)


def get_watch_source(conn: sqlite3.Connection, source_id: int) -> WatchSource | None:
    row = conn.execute("SELECT * FROM watch_sources WHERE id = ? LIMIT 1", (source_id,)).fetchone()
    return _watch_source_from_row(row) if row else None


def get_watch_source_by_path(conn: sqlite3.Connection, source: str, path: Path) -> WatchSource:
    row = conn.execute(
        "SELECT * FROM watch_sources WHERE source = ? AND path = ? LIMIT 1",
        (source, str(path)),
    ).fetchone()
    if row is None:
        raise RuntimeError("watch source was not persisted")
    return _watch_source_from_row(row)


def list_watch_sources(conn: sqlite3.Connection, *, enabled_only: bool = False) -> list[WatchSource]:
    sql = "SELECT * FROM watch_sources"
    params: list[object] = []
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    return [_watch_source_from_row(row) for row in conn.execute(sql, params).fetchall()]


def list_watch_events(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    limit: int = 5,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM watch_events
            WHERE watch_source_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (source_id, limit),
        ).fetchall()
    )


def run_watch_source(conn: sqlite3.Connection, source: WatchSource) -> ImportReport:
    importer = get_importer(source.source)
    try:
        report = sync_import_paths(conn, importer, [source.path])
    except Exception as exc:
        report = ImportReport(
            source=importer.name,
            scanned=0,
            imported=0,
            skipped=0,
            failed=1,
            errors=(f"{source.path}: {exc}",),
        )
    _record_watch_sync(conn, source.id, report)
    return report


def run_enabled_watch_sources(conn: sqlite3.Connection) -> list[tuple[WatchSource, ImportReport]]:
    results: list[tuple[WatchSource, ImportReport]] = []
    for source in list_watch_sources(conn, enabled_only=True):
        results.append((source, run_watch_source(conn, source)))
    return results


def _record_watch_sync(
    conn: sqlite3.Connection,
    source_id: int,
    report: ImportReport,
) -> None:
    now = now_utc()
    message = _watch_message(report)
    last_error = "\n".join(report.errors) if report.errors else None
    conn.execute(
        """
        UPDATE watch_sources
        SET last_sync_at = ?,
            last_imported = ?,
            last_skipped = ?,
            last_failed = ?,
            last_error = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            now,
            report.imported,
            report.skipped,
            report.failed,
            last_error,
            now,
            source_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO watch_events (
            watch_source_id, event_type, message, imported, skipped, failed, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            "sync",
            message,
            report.imported,
            report.skipped,
            report.failed,
            now,
        ),
    )
    conn.commit()


def _watch_message(report: ImportReport) -> str:
    base = (
        f"scanned={report.scanned} imported={report.imported} "
        f"skipped={report.skipped} failed={report.failed}"
    )
    if report.errors:
        return f"{base} error={report.errors[0]}"
    return base


def _watch_source_from_row(row: sqlite3.Row) -> WatchSource:
    return WatchSource(
        id=int(row["id"]),
        source=str(row["source"]),
        path=Path(str(row["path"])),
        scope=str(row["scope"]) if row["scope"] is not None else None,
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_sync_at=str(row["last_sync_at"]) if row["last_sync_at"] is not None else None,
        last_imported=int(row["last_imported"]),
        last_skipped=int(row["last_skipped"]),
        last_failed=int(row["last_failed"]),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
    )
