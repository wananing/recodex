from __future__ import annotations

from recodex.importers.base import SessionImporter
from recodex.importers.claude_code import ClaudeCodeImporter
from recodex.importers.codex import CodexImporter
from recodex.importers.cursor import CursorImporter

_CLAUDE_CODE_IMPORTER = ClaudeCodeImporter()
_CODEX_IMPORTER = CodexImporter()
_CURSOR_IMPORTER = CursorImporter()

_IMPORTERS: dict[str, SessionImporter] = {
    "auto": _CODEX_IMPORTER,
    "claude": _CLAUDE_CODE_IMPORTER,
    "claude-code": _CLAUDE_CODE_IMPORTER,
    "codex": _CODEX_IMPORTER,
    "cursor": _CURSOR_IMPORTER,
}


def get_importer(source: str | None = None) -> SessionImporter:
    """Return a registered session importer by source name."""
    key = (source or "auto").strip().lower().replace("_", "-")
    try:
        return _IMPORTERS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_IMPORTERS))
        raise ValueError(f"Unsupported import source `{source}`. Supported: {supported}.") from exc


def importer_names() -> tuple[str, ...]:
    return tuple(sorted({importer.name for name, importer in _IMPORTERS.items() if name != "auto"}))


__all__ = ["ClaudeCodeImporter", "CodexImporter", "CursorImporter", "SessionImporter", "get_importer", "importer_names"]
