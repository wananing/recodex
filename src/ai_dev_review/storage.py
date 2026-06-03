from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import now_utc
from .transcripts import catalog_transcript_file, default_transcript_roots

JSONL_EXTENSION = ".jsonl"
TEN_MIB = 10 * 1024 * 1024


@dataclass(frozen=True)
class StorageIndexResult:
    discovered: int
    created: int
    updated: int
    skipped: int
    missing: int
    failed: int


@dataclass(frozen=True)
class StorageStats:
    roots: tuple[Path, ...]
    file_count: int
    total_size: int
    largest_path: Path | None
    largest_size: int
    files_over_10mb: int
    files_older_than_30d: int
    indexed_sessions: int
    summaries: int
    archive_size: int
    hot_index_size: int


@dataclass(frozen=True)
class ArchiveResult:
    candidates: int
    moved: int
    failed: int
    dry_run: bool
    paths: tuple[tuple[Path, Path], ...]


@dataclass(frozen=True)
class RestoreResult:
    restored: bool
    message: str
    path: Path | None = None


def storage_roots(values: list[str] | None) -> list[Path]:
    if values:
        return [Path(value).expanduser().resolve() for value in values]
    return [path.expanduser().resolve() for path in default_transcript_roots()]


def storage_archive_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return (Path.home() / ".ai-dev-review" / "archive" / "codex").resolve()


def discover_storage_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        expanded = root.expanduser()
        if expanded.is_file() and expanded.suffix.lower() == JSONL_EXTENSION:
            files.append(expanded.resolve())
        elif expanded.is_dir():
            files.extend(path.resolve() for path in expanded.rglob("*.jsonl") if path.is_file())
    return sorted(set(files))


def index_raw_session_files(
    conn: sqlite3.Connection,
    roots: list[Path],
    *,
    incremental: bool = True,
) -> StorageIndexResult:
    files = discover_storage_files(roots)
    seen_hot_paths: set[str] = set()
    created = updated = skipped = failed = 0
    now = now_utc()

    for path in files:
        try:
            stat = path.stat()
            resolved = str(path.resolve())
            seen_hot_paths.add(resolved)
            existing_by_path = raw_file_by_path(conn, resolved)
            if incremental and _unchanged(existing_by_path, stat):
                skipped += 1
                conn.execute(
                    "UPDATE raw_session_files SET last_seen_at = ? WHERE session_id = ?",
                    (now, existing_by_path["session_id"]),
                )
                continue

            entry = catalog_transcript_file(path)
            existing_by_session = raw_file_by_session(conn, entry.session_id)
            sha_prefix = sha256_prefix(path)
            inode = inode_value(stat)
            conn.execute(
                """
                INSERT INTO raw_session_files (
                    session_id, source, hot_path, archive_path, size_bytes, mtime,
                    inode, sha256_prefix, parsed_until_offset, status,
                    first_seen_at, last_seen_at
                )
                VALUES (?, 'codex', ?, NULL, ?, ?, ?, ?, ?, 'hot', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    source = excluded.source,
                    hot_path = excluded.hot_path,
                    archive_path = NULL,
                    size_bytes = excluded.size_bytes,
                    mtime = excluded.mtime,
                    inode = excluded.inode,
                    sha256_prefix = excluded.sha256_prefix,
                    parsed_until_offset = excluded.parsed_until_offset,
                    status = 'hot',
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    entry.session_id,
                    resolved,
                    stat.st_size,
                    stat.st_mtime,
                    inode,
                    sha_prefix,
                    stat.st_size,
                    now,
                    now,
                ),
            )
            if existing_by_session is None and existing_by_path is None:
                created += 1
            else:
                updated += 1
        except OSError:
            failed += 1

    missing = mark_missing_hot_files(conn, roots, seen_hot_paths, now)
    conn.commit()
    return StorageIndexResult(
        discovered=len(files),
        created=created,
        updated=updated,
        skipped=skipped,
        missing=missing,
        failed=failed,
    )


def raw_file_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM raw_session_files
        WHERE hot_path = ? OR archive_path = ?
        LIMIT 1
        """,
        (path, path),
    ).fetchone()


def raw_file_by_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM raw_session_files WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()


def mark_missing_hot_files(
    conn: sqlite3.Connection,
    roots: list[Path],
    seen_hot_paths: set[str],
    now: str,
) -> int:
    if not roots:
        return 0
    changed = 0
    rows = conn.execute(
        """
        SELECT session_id, hot_path
        FROM raw_session_files
        WHERE status = 'hot' AND hot_path IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        path = Path(row["hot_path"]).expanduser()
        resolved = str(path.resolve())
        if resolved in seen_hot_paths or not path_under_any(path, roots):
            continue
        conn.execute(
            """
            UPDATE raw_session_files
            SET status = 'missing', last_seen_at = ?
            WHERE session_id = ?
            """,
            (now, row["session_id"]),
        )
        changed += 1
    return changed


def collect_storage_stats(
    conn: sqlite3.Connection,
    roots: list[Path],
    archive_dir: Path,
) -> StorageStats:
    files = discover_storage_files(roots)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    total_size = 0
    largest_path: Path | None = None
    largest_size = 0
    files_over_10mb = 0
    files_older_than_30d = 0
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total_size += stat.st_size
        if stat.st_size > largest_size:
            largest_path = path
            largest_size = stat.st_size
        if stat.st_size > TEN_MIB:
            files_over_10mb += 1
        if stat.st_mtime < cutoff:
            files_older_than_30d += 1

    indexed_sessions = conn.execute(
        "SELECT COUNT(*) AS count FROM raw_session_files"
    ).fetchone()["count"]
    summaries = conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()["count"]
    hot_index_size = conn.execute(
        """
        SELECT COALESCE(SUM(size_bytes), 0) AS total
        FROM raw_session_files
        WHERE status = 'hot'
        """
    ).fetchone()["total"]

    return StorageStats(
        roots=tuple(roots),
        file_count=len(files),
        total_size=int(total_size),
        largest_path=largest_path,
        largest_size=int(largest_size),
        files_over_10mb=files_over_10mb,
        files_older_than_30d=files_older_than_30d,
        indexed_sessions=int(indexed_sessions),
        summaries=int(summaries),
        archive_size=directory_size(archive_dir),
        hot_index_size=int(hot_index_size),
    )


def largest_storage_files(roots: list[Path], limit: int) -> list[tuple[Path, int]]:
    rows: list[tuple[Path, int]] = []
    for path in discover_storage_files(roots):
        try:
            rows.append((path, path.stat().st_size))
        except OSError:
            continue
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def recent_storage_files(roots: list[Path], since: datetime, limit: int) -> list[Path]:
    cutoff = since.timestamp()
    rows: list[tuple[Path, float]] = []
    for path in discover_storage_files(roots):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            rows.append((path, mtime))
    rows.sort(key=lambda item: item[1], reverse=True)
    return [path for path, _mtime in rows[:limit]]


def archive_candidates(
    conn: sqlite3.Connection,
    roots: list[Path],
    older_than_days: int,
    *,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).timestamp()
    rows = conn.execute(
        """
        SELECT *
        FROM raw_session_files
        WHERE status = 'hot'
          AND hot_path IS NOT NULL
          AND mtime < ?
        ORDER BY mtime ASC, size_bytes DESC
        """,
        (cutoff,),
    ).fetchall()
    filtered = [
        row
        for row in rows
        if Path(row["hot_path"]).exists() and path_under_any(Path(row["hot_path"]), roots)
    ]
    if limit is not None:
        return filtered[:limit]
    return filtered


def archive_raw_session_files(
    conn: sqlite3.Connection,
    roots: list[Path],
    archive_dir: Path,
    *,
    older_than_days: int,
    dry_run: bool,
    limit: int | None = None,
) -> ArchiveResult:
    rows = archive_candidates(conn, roots, older_than_days, limit=limit)
    planned: list[tuple[Path, Path]] = []
    moved = failed = 0
    now = now_utc()

    for row in rows:
        source = Path(row["hot_path"]).expanduser().resolve()
        target = unique_target(archive_dir / relative_to_any(source, roots), row["session_id"])
        planned.append((source, target))
        if dry_run:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            stat = target.stat()
            conn.execute(
                """
                UPDATE raw_session_files
                SET hot_path = NULL,
                    archive_path = ?,
                    size_bytes = ?,
                    mtime = ?,
                    inode = ?,
                    status = 'archived',
                    last_seen_at = ?
                WHERE session_id = ?
                """,
                (
                    str(target),
                    stat.st_size,
                    stat.st_mtime,
                    inode_value(stat),
                    now,
                    row["session_id"],
                ),
            )
            moved += 1
        except OSError:
            failed += 1
    if not dry_run:
        conn.commit()
    return ArchiveResult(
        candidates=len(rows),
        moved=moved,
        failed=failed,
        dry_run=dry_run,
        paths=tuple(planned),
    )


def restore_raw_session_file(
    conn: sqlite3.Connection,
    session_id: str,
    sessions_root: Path,
    archive_dir: Path,
) -> RestoreResult:
    row = raw_file_by_session(conn, session_id)
    if row is None:
        return RestoreResult(False, f"No raw session file found for `{session_id}`.")
    if row["status"] != "archived" or not row["archive_path"]:
        return RestoreResult(False, f"Session `{session_id}` is not archived.")

    source = Path(row["archive_path"]).expanduser().resolve()
    if not source.exists():
        return RestoreResult(False, f"Archived file is missing: {source}")

    target = sessions_root.expanduser().resolve() / relative_to_archive(source, archive_dir)
    if target.exists():
        return RestoreResult(False, f"Restore target already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    stat = target.stat()
    conn.execute(
        """
        UPDATE raw_session_files
        SET hot_path = ?,
            archive_path = NULL,
            size_bytes = ?,
            mtime = ?,
            inode = ?,
            status = 'hot',
            last_seen_at = ?
        WHERE session_id = ?
        """,
        (
            str(target),
            stat.st_size,
            stat.st_mtime,
            inode_value(stat),
            now_utc(),
            session_id,
        ),
    )
    conn.commit()
    return RestoreResult(True, f"Restored `{session_id}`.", target)


def vacuum_storage(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA optimize")
    conn.execute("VACUUM")


def parse_age_days(value: str) -> int:
    lowered = value.strip().lower()
    if lowered.endswith("d") and lowered[:-1].isdigit():
        return int(lowered[:-1])
    if lowered.endswith("w") and lowered[:-1].isdigit():
        return int(lowered[:-1]) * 7
    if lowered.isdigit():
        return int(lowered)
    raise ValueError(f"Unsupported age value: {value}")


def format_size(size: int) -> str:
    value = float(size)
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or suffix == "TB":
            if suffix == "B":
                return f"{int(value)}{suffix}"
            return f"{value:.1f}{suffix}"
        value /= 1024
    return f"{size}B"


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return int(total)


def sha256_prefix(path: Path, chunk_size: int = 64 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        digest.update(file.read(chunk_size))
    return digest.hexdigest()[:16]


def inode_value(stat_result) -> str:
    return f"{stat_result.st_dev}:{stat_result.st_ino}"


def _unchanged(row: sqlite3.Row | None, stat_result) -> bool:
    if row is None:
        return False
    return (
        row["status"] == "hot"
        and int(row["size_bytes"]) == int(stat_result.st_size)
        and abs(float(row["mtime"]) - float(stat_result.st_mtime)) < 0.000001
    )


def path_under_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.expanduser().resolve().relative_to(root.expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def relative_to_any(path: Path, roots: list[Path]) -> Path:
    resolved = path.expanduser().resolve()
    for root in roots:
        try:
            return resolved.relative_to(root.expanduser().resolve())
        except ValueError:
            continue
    return Path(resolved.name)


def relative_to_archive(path: Path, archive_dir: Path) -> Path:
    try:
        return path.expanduser().resolve().relative_to(archive_dir.expanduser().resolve())
    except ValueError:
        return Path(path.name)


def unique_target(path: Path, session_id: str) -> Path:
    if not path.exists():
        return path
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    return path.with_name(f"{stem}.{session_id[:8]}{suffix}")
