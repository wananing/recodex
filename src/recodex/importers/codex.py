from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from recodex.models import ParsedTranscript
from recodex.transcripts import (
    SUPPORTED_EXTENSIONS,
    parse_transcript_file,
)


class CodexImporter:
    """Importer for Codex JSONL, hook JSON, and text transcript files."""

    name = "codex"
    supported_extensions = tuple(sorted(SUPPORTED_EXTENSIONS))

    @property
    def default_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        seen: set[Path] = set()
        if os.environ.get("CODEX_SESSIONS_DIR"):
            for path in _split_env_paths(os.environ["CODEX_SESSIONS_DIR"]):
                _append_existing(path, roots, seen)
        for home in _codex_home_paths():
            sessions = home / "sessions"
            archived = home / "archived_sessions"
            found = False
            for path in (sessions, archived):
                if _append_existing(path, roots, seen):
                    found = True
            if not found:
                for path in (home / "transcripts", home / "history"):
                    _append_existing(path, roots, seen)
        return tuple(roots)

    def discover(self, roots: Iterable[Path]) -> list[Path]:
        return _discover_codex_files(roots)

    def parse_file(self, path: Path) -> ParsedTranscript:
        return parse_transcript_file(path)


CODEX_SESSION_SUFFIXES = {".jsonl"}
CODEX_SESSION_DIRS = {"sessions", "archived_sessions"}


def _discover_codex_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    direct_seen: set[Path] = set()
    dedupe_seen: set[tuple[Path, Path]] = set()

    for root in roots:
        expanded = root.expanduser()
        if expanded.is_file():
            if expanded.suffix.lower() in SUPPORTED_EXTENSIONS:
                resolved = expanded.resolve()
                if resolved not in direct_seen:
                    direct_seen.add(resolved)
                    files.append(resolved)
            continue

        for source_dir, dedupe_scope in _codex_usage_sources(expanded):
            for file in _collect_session_files(source_dir):
                try:
                    relative = file.relative_to(source_dir)
                except ValueError:
                    relative = Path(file.name)
                key = (dedupe_scope, relative)
                if key in dedupe_seen:
                    continue
                dedupe_seen.add(key)
                files.append(file.resolve())

    ordered: list[Path] = []
    seen_files: set[Path] = set()
    for file in files:
        if file in seen_files:
            continue
        seen_files.add(file)
        ordered.append(file)
    return ordered


def _codex_usage_sources(path: Path) -> list[tuple[Path, Path]]:
    if not path.is_dir():
        return []

    sessions = path / "sessions"
    archived = path / "archived_sessions"
    sources: list[tuple[Path, Path]] = []
    seen: set[Path] = set()

    for candidate in (sessions, archived):
        _append_source(candidate, path.resolve(), sources, seen)
    if sources:
        return sources

    if path.name in CODEX_SESSION_DIRS:
        return [(path, path.parent.resolve())]
    return [(path, path.resolve())]


def _append_source(
    path: Path,
    dedupe_scope: Path,
    sources: list[tuple[Path, Path]],
    seen: set[Path],
) -> None:
    if not path.is_dir():
        return
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    sources.append((path, dedupe_scope))


def _collect_session_files(root: Path) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif (
                        entry.is_file(follow_symlinks=False)
                        and Path(entry.name).suffix.lower() in CODEX_SESSION_SUFFIXES
                    ):
                        files.append(Path(entry.path))
        except OSError:
            continue
    return sorted(files)


def _codex_home_paths() -> list[Path]:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return _split_env_paths(raw)
    return [Path.home() / ".codex"]


def _split_env_paths(raw: str) -> list[Path]:
    return [Path(part).expanduser() for part in raw.split(",") if part.strip()]


def _append_existing(path: Path, roots: list[Path], seen: set[Path]) -> bool:
    expanded = path.expanduser()
    if not expanded.exists():
        return False
    resolved = expanded.resolve()
    if resolved in seen:
        return True
    seen.add(resolved)
    roots.append(expanded)
    return True
