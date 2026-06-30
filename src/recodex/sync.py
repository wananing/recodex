from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from recodex.db import now_utc, save_transcript
from recodex.importers.base import SessionImporter
from recodex.models import CatalogEntry, ParsedTranscript
from recodex.transcripts import catalog_transcript_file


@dataclass(frozen=True)
class ImportReport:
    source: str
    scanned: int
    imported: int
    skipped: int
    failed: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogReport:
    source: str
    scanned: int
    cataloged: int
    failed: int
    errors: tuple[str, ...] = ()


def sync_import_paths(
    conn: sqlite3.Connection,
    importer: SessionImporter,
    roots: Iterable[Path],
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Import files through an importer, skipping unchanged files."""
    root_list = [Path(root) for root in roots]
    files = importer.discover(root_list)
    if limit is not None:
        files = files[:limit]

    imported = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    started_at = now_utc()

    for file in files:
        try:
            path = file.resolve()
            stat = path.stat()
            record = _sync_file_record(conn, importer.name, path)
            should_import, content_hash = _should_import(path, stat.st_mtime, record)
            if not should_import:
                skipped += 1
                if content_hash is not None and not dry_run:
                    _upsert_sync_file(conn, importer.name, path, stat.st_mtime, content_hash)
                continue

            parsed = importer.parse_file(path)
            if parsed.session.message_count <= 0:
                skipped += 1
            else:
                if not dry_run:
                    _save_imported_transcript(conn, parsed)
                imported += 1
            if not dry_run:
                _upsert_sync_file(conn, importer.name, path, stat.st_mtime, content_hash)
        except (OSError, ValueError, sqlite3.Error) as exc:
            failed += 1
            errors.append(f"{file}: {exc}")

    report = ImportReport(
        source=importer.name,
        scanned=len(files),
        imported=imported,
        skipped=skipped,
        failed=failed,
        errors=tuple(errors),
    )
    if not dry_run:
        _record_import_run(conn, importer.name, root_list, report, started_at, now_utc())
        conn.commit()
    return report


def sync_catalog_paths(
    conn: sqlite3.Connection,
    importer: SessionImporter,
    roots: Iterable[Path],
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> CatalogReport:
    """Build a lightweight session catalog without fully importing transcripts."""
    files = importer.discover([Path(root) for root in roots])
    if limit is not None:
        files = files[:limit]

    entries: list[CatalogEntry] = []
    failed = 0
    errors: list[str] = []
    catalog_file = getattr(importer, "catalog_file", catalog_transcript_file)
    for file in files:
        try:
            entry = catalog_file(file.resolve())
            if not entry.source:
                entry = replace(entry, source=importer.name)
            entries.append(entry)
        except (OSError, ValueError, sqlite3.Error) as exc:
            failed += 1
            errors.append(f"{file}: {exc}")

    if entries and not dry_run:
        from recodex.db import save_catalog_entries

        save_catalog_entries(conn, entries)
    return CatalogReport(
        source=importer.name,
        scanned=len(files),
        cataloged=len(entries),
        failed=failed,
        errors=tuple(errors),
    )


def _save_imported_transcript(conn: sqlite3.Connection, parsed: ParsedTranscript) -> None:
    conn.execute("SAVEPOINT transcript_import")
    try:
        save_transcript(conn, parsed, commit=False)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT transcript_import")
        conn.execute("RELEASE SAVEPOINT transcript_import")
        raise
    conn.execute("RELEASE SAVEPOINT transcript_import")


def _sync_file_record(
    conn: sqlite3.Connection,
    source: str,
    path: Path,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM sync_files
        WHERE source = ? AND path = ?
        LIMIT 1
        """,
        (source, str(path)),
    ).fetchone()


def _should_import(
    path: Path,
    mtime: float,
    record: sqlite3.Row | None,
) -> tuple[bool, str]:
    if record is not None and abs(float(record["mtime"]) - mtime) < 0.000001:
        return False, str(record["content_hash"])
    content_hash = file_content_hash(path)
    if record is not None and record["content_hash"] == content_hash:
        return False, content_hash
    return True, content_hash


def _upsert_sync_file(
    conn: sqlite3.Connection,
    source: str,
    path: Path,
    mtime: float,
    content_hash: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_files (source, path, mtime, content_hash, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source, path) DO UPDATE SET
            mtime = excluded.mtime,
            content_hash = excluded.content_hash,
            last_seen_at = excluded.last_seen_at
        """,
        (source, str(path), mtime, content_hash, now_utc()),
    )


def _record_import_run(
    conn: sqlite3.Connection,
    source: str,
    roots: list[Path],
    report: ImportReport,
    started_at: str,
    finished_at: str,
) -> None:
    root_path = ",".join(str(root.expanduser()) for root in roots)
    cursor = conn.execute(
        """
        INSERT INTO import_runs (
            source, root_path, scanned, imported, skipped, failed, started_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            root_path,
            report.scanned,
            report.imported,
            report.skipped,
            report.failed,
            started_at,
            finished_at,
        ),
    )
    run_id = int(cursor.lastrowid)
    conn.executemany(
        """
        INSERT INTO import_errors (run_id, path, message)
        VALUES (?, ?, ?)
        """,
        [
            (run_id, error.split(": ", 1)[0], error)
            for error in report.errors
        ],
    )


def file_content_hash(path: Path, chunk_size: int = 64 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
