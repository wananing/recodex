from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from recodex.importers.base import retag_parsed_transcript
from recodex.models import CatalogEntry, ParsedTranscript
from recodex.transcripts import catalog_transcript_file, parse_transcript_file


class ClaudeCodeImporter:
    """Importer for Claude Code transcript files."""

    name = "claude-code"
    supported_extensions = (".jsonl", ".json")

    @property
    def default_roots(self) -> tuple[Path, ...]:
        home = Path.home()
        candidates: list[Path] = []
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            candidates.extend(_claude_config_paths(os.environ["CLAUDE_CONFIG_DIR"]))
        if os.environ.get("CLAUDE_HOME"):
            candidates.extend(_claude_config_paths(os.environ["CLAUDE_HOME"]))
        if os.environ.get("CLAUDE_CODE_SESSIONS_DIR"):
            candidates.extend(_split_env_paths(os.environ["CLAUDE_CODE_SESSIONS_DIR"]))
        if os.environ.get("CLAUDE_CODE_PROJECTS_DIR"):
            candidates.extend(_split_env_paths(os.environ["CLAUDE_CODE_PROJECTS_DIR"]))
        if os.environ.get("CLAUDE_CODE_HISTORY_FILE"):
            candidates.extend(_split_env_paths(os.environ["CLAUDE_CODE_HISTORY_FILE"]))
        xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
        candidates.extend(
            [
                xdg_config / "claude",
                home / ".claude",
            ]
        )
        roots: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            for source_path in _claude_session_sources(path):
                resolved = source_path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    roots.append(source_path)
        return tuple(roots)

    def discover(self, roots: Iterable[Path]) -> list[Path]:
        files: list[Path] = []
        for root in roots:
            for source_path in _claude_session_sources(root):
                if source_path.is_file() and source_path.suffix.lower() in self.supported_extensions:
                    files.append(source_path.resolve())
                elif source_path.is_dir():
                    for child in source_path.rglob("*"):
                        if _is_importable_claude_file(child):
                            files.append(child.resolve())
        return sorted(set(files))

    def parse_file(self, path: Path) -> ParsedTranscript:
        parsed = retag_parsed_transcript(parse_transcript_file(path), self.name)
        return _with_claude_project_metadata(parsed, path)

    def catalog_file(self, path: Path) -> CatalogEntry:
        entry = catalog_transcript_file(path)
        project_path = entry.project_path or _project_path_from_claude_path(path)
        return replace(entry, project_path=project_path)


def _claude_session_sources(path: Path) -> list[Path]:
    expanded = path.expanduser()
    if expanded.is_file():
        return [expanded] if _is_importable_claude_file(expanded) else []
    if not expanded.is_dir():
        return []
    if expanded.name == "projects":
        return [expanded]

    sources: list[Path] = []
    for child_name in ("projects",):
        child = expanded / child_name
        if child.exists():
            sources.append(child)
    return sources or [expanded]


def _is_importable_claude_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in ClaudeCodeImporter.supported_extensions:
        return False
    if path.suffix.lower() == ".json" and path.name.endswith(".meta.json"):
        return False
    return True


def _with_claude_project_metadata(parsed: ParsedTranscript, path: Path) -> ParsedTranscript:
    project_path = parsed.session.project_path or _project_path_from_claude_path(path)
    metadata = dict(parsed.session.metadata)
    if project_path:
        metadata.setdefault("claude_project_path", project_path)
    return ParsedTranscript(
        session=replace(
            parsed.session,
            project_path=project_path,
            cwd=parsed.session.cwd or project_path,
            metadata=metadata,
        ),
        events=parsed.events,
    )


def _project_path_from_claude_path(path: Path) -> str | None:
    parts = path.expanduser().parts
    try:
        projects_index = parts.index("projects")
    except ValueError:
        return None
    if projects_index + 1 >= len(parts):
        return None
    encoded = parts[projects_index + 1]
    if not encoded.startswith("-"):
        return None
    return str(_decode_existing_absolute_path(encoded))


def _decode_existing_absolute_path(encoded: str) -> Path:
    tokens = [token for token in encoded.lstrip("-").split("-") if token]
    if not tokens:
        return Path("/")

    current = Path("/")
    index = 0
    while index < len(tokens):
        best: Path | None = None
        best_end = index + 1
        for end in range(len(tokens), index, -1):
            candidate = current / "-".join(tokens[index:end])
            if candidate.exists():
                best = candidate
                best_end = end
                break
        if best is None:
            best = current / tokens[index]
        current = best
        index = best_end
    return current


def _claude_config_paths(raw: str) -> list[Path]:
    paths = []
    for path in _split_env_paths(raw):
        paths.append(path.parent if path.name == "projects" else path)
    return paths


def _split_env_paths(raw: str) -> list[Path]:
    return [Path(part).expanduser() for part in raw.split(",") if part.strip()]
