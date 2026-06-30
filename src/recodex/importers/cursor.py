from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from recodex.importers.base import retag_parsed_transcript
from recodex.models import ParsedTranscript
from recodex.transcripts import (
    SUPPORTED_EXTENSIONS,
    build_parsed_transcript,
    parse_transcript_file,
    parse_transcript_value,
)

CURSOR_SQLITE_NAMES = {"state.vscdb"}
CURSOR_JSON_KEYS = ("chat", "composer", "aichat", "aiService", "conversation")
MAX_CURSOR_VALUE_CHARS = 15 * 1024 * 1024


class CursorImporter:
    """Importer for Cursor JSON exports and workspaceStorage SQLite state."""

    name = "cursor"
    supported_extensions = tuple(sorted({*SUPPORTED_EXTENSIONS, ".vscdb"}))

    @property
    def default_roots(self) -> tuple[Path, ...]:
        home = Path.home()
        candidates: list[Path] = []
        for env_name in ("CURSOR_CHAT_DIR", "CURSOR_USER_DATA_DIR", "CURSOR_CONFIG_DIR"):
            raw = os.environ.get(env_name)
            if raw:
                candidates.extend(_split_env_paths(raw))
        xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
        candidates.extend(
            [
                xdg_config / "Cursor" / "User" / "workspaceStorage",
                xdg_config / "Cursor" / "User" / "globalStorage",
                home / ".cursor",
                home / ".config" / "Cursor" / "User" / "workspaceStorage",
            ]
        )
        roots: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.expanduser().resolve() if path.expanduser().exists() else path.expanduser()
            if path.expanduser().exists() and resolved not in seen:
                seen.add(resolved)
                roots.append(path.expanduser())
        return tuple(roots)

    def discover(self, roots: Iterable[Path]) -> list[Path]:
        files: list[Path] = []
        for root in roots:
            expanded = root.expanduser()
            if expanded.is_file() and _is_cursor_file(expanded):
                files.append(expanded.resolve())
            elif expanded.is_dir():
                for child in expanded.rglob("*"):
                    if child.is_file() and _is_cursor_file(child):
                        files.append(child.resolve())
        return sorted(set(files))

    def parse_file(self, path: Path) -> ParsedTranscript:
        if path.name in CURSOR_SQLITE_NAMES:
            return retag_parsed_transcript(_parse_cursor_sqlite(path), self.name)
        return retag_parsed_transcript(parse_transcript_file(path), self.name)


def _parse_cursor_sqlite(path: Path) -> ParsedTranscript:
    values = _cursor_json_values(path)
    if not values:
        return build_parsed_transcript(path, [], path.stem)
    if len(values) == 1:
        return parse_transcript_value(path, values[0], fallback_session_id=path.stem)
    return parse_transcript_value(
        path,
        {"session_id": path.stem, "cursor_items": values},
        fallback_session_id=path.stem,
    )


def _cursor_json_values(path: Path) -> list[Any]:
    values: list[Any] = []
    uri = f"file:{path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return values
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "ItemTable" not in tables:
            return values
        for key, raw in conn.execute("SELECT key, value FROM ItemTable"):
            if not _looks_like_cursor_key(str(key)):
                continue
            if not isinstance(raw, str) or len(raw) > MAX_CURSOR_VALUE_CHARS:
                continue
            parsed = _decode_cursor_value(raw)
            if parsed is not None:
                values.append(parsed)
    except sqlite3.Error:
        return values
    finally:
        conn.close()
    return values


def _decode_cursor_value(raw: str) -> Any | None:
    text = raw.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _is_cursor_file(path: Path) -> bool:
    return path.name in CURSOR_SQLITE_NAMES or path.suffix.lower() in SUPPORTED_EXTENSIONS


def _looks_like_cursor_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment.lower() in lowered for fragment in CURSOR_JSON_KEYS)


def _split_env_paths(raw: str) -> list[Path]:
    return [Path(part).expanduser() for part in raw.split(",") if part.strip()]
