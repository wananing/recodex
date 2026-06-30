from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CatalogEntry, ParsedTranscript, SessionRecord, TranscriptEvent

SUPPORTED_EXTENSIONS = {".jsonl", ".json", ".txt", ".md", ".log"}
TEXT_KEYS = (
    "text",
    "message",
    "content",
    "command",
    "cmd",
    "arguments",
    "output",
    "stdout",
    "stderr",
    "error",
    "summary",
)
SESSION_ID_KEYS = (
    "session_id",
    "sessionId",
    "conversation_id",
    "conversationId",
    "rollout_id",
    "rolloutId",
    "chat_id",
    "chatId",
    "thread_id",
    "threadId",
    "composer_id",
    "composerId",
)
TIMESTAMP_KEYS = ("timestamp", "created_at", "createdAt", "created", "updated_at", "updatedAt", "time", "ts")
ROW_KEYS = (
    "messages",
    "items",
    "events",
    "turns",
    "entries",
    "conversation",
    "conversations",
    "history",
    "chatData",
    "composerData",
)
ROLE_VALUES = {"user", "assistant", "system", "tool", "developer"}
ERROR_TERMS = (
    "error",
    "failed",
    "failure",
    "exception",
    "traceback",
    "permission denied",
    "not found",
    "timeout",
    "报错",
    "失败",
)
COMMAND_RE = re.compile(
    r"(^|\b)(exec_command|apply_patch|shell|bash|zsh|git|uv|npm|pnpm|python|pytest|rg|sed|curl|make)(\b|$)",
    re.IGNORECASE,
)
ROLE_PREFIX_RE = re.compile(r"^\s*(user|assistant|system|tool|developer)\s*[:：]\s*", re.I)
USER_CORRECTION_RE = re.compile(
    r"不是这个|不对|错了|偏题|我说的是|我的意思|你忘了|你漏了|刚才说过|not this|wrong|not what|you forgot|as i said",
    re.I,
)
CODEX_IDE_CONTEXT_PREFIX = "# Context from my IDE setup:"
CODEX_REQUEST_MARKER = "my request for codex"
MAX_JSONL_LINE_CHARS = 5 * 1024 * 1024


def default_transcript_roots() -> list[Path]:
    home = Path.home()
    codex_homes = _env_paths("CODEX_HOME") or [home / ".codex"]
    candidates: list[Path] = []
    if os.environ.get("CODEX_SESSIONS_DIR"):
        candidates.extend(_env_paths("CODEX_SESSIONS_DIR"))
    for codex_home in codex_homes:
        candidates.extend([
            codex_home / "sessions",
            codex_home / "archived_sessions",
            codex_home / "transcripts",
            codex_home / "history",
        ])
    roots: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve() if path.exists() else path
        if path.exists() and resolved not in seen:
            seen.add(resolved)
            roots.append(path)
    return roots


def _env_paths(name: str) -> list[Path]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [Path(part).expanduser() for part in raw.split(",") if part.strip()]


def discover_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if expanded.is_file() and expanded.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(expanded.resolve())
        elif expanded.is_dir():
            for child in expanded.rglob("*"):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(child.resolve())
    return sorted(set(files))


def parse_transcript_file(path: Path) -> ParsedTranscript:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        events, discovered_session_id = _parse_jsonl(path)
    elif suffix == ".json":
        events, discovered_session_id = _parse_json(path)
    else:
        events, discovered_session_id = _parse_plain_text(path)

    return build_parsed_transcript(path, events, discovered_session_id)


def parse_transcript_value(
    path: Path,
    value: Any,
    *,
    fallback_session_id: str | None = None,
) -> ParsedTranscript:
    """Parse an already-decoded transcript-like JSON value."""
    events, discovered_session_id = _parse_json_value(value)
    return build_parsed_transcript(path, events, discovered_session_id or fallback_session_id)


def build_parsed_transcript(
    path: Path,
    events: list[TranscriptEvent],
    discovered_session_id: str | None,
) -> ParsedTranscript:
    session_id = _stable_session_id(path, discovered_session_id)
    fixed_events = tuple(
        TranscriptEvent(
            session_id=session_id,
            event_index=index,
            role=event.role,
            kind=event.kind,
            text=event.text,
            created_at=event.created_at,
            metadata=event.metadata,
        )
        for index, event in enumerate(events)
    )
    session = _summarize_session(path, session_id, fixed_events)
    return ParsedTranscript(session=session, events=fixed_events)


def catalog_transcript_file(path: Path, *, max_lines: int = 80) -> CatalogEntry:
    session_id: str | None = None
    project_path: str | None = None
    model: str | None = None
    title = ""
    timestamps: list[str] = []

    with path.open(encoding="utf-8", errors="replace") as file:
        for index, line in enumerate(file):
            if index >= max_lines:
                break
            if not line.strip():
                continue
            if len(line) > MAX_JSONL_LINE_CHARS:
                if not title:
                    title = f"{path.stem} (huge first line)"
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                if not title:
                    title = _clean_text(line)[:96]
                continue
            if not isinstance(value, dict):
                continue
            session_id = session_id or _session_id_from_json(value)
            project_path = project_path or _find_value(value, ("cwd", "workdir", "working_dir"))
            model = model or _find_value(value, ("model",))
            timestamp = _find_value(value, TIMESTAMP_KEYS)
            if timestamp:
                timestamps.append(timestamp)
            if not title and _json_role(value) == "user":
                candidate = _clean_text("\n".join(_collect_text(value)))
                title_candidate = _title_candidate_from_user_text(candidate)
                if title_candidate:
                    title = title_candidate.splitlines()[0][:96]

    file_updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    discovered_session_id = _stable_session_id(path, session_id)
    return CatalogEntry(
        source_path=str(path.resolve()),
        session_id=discovered_session_id,
        project_path=project_path,
        started_at=min(timestamps) if timestamps else file_updated_at,
        updated_at=max(timestamps) if timestamps else file_updated_at,
        model=model,
        title=title or path.stem,
        file_size=path.stat().st_size,
    )


def _parse_jsonl(path: Path) -> tuple[list[TranscriptEvent], str | None]:
    events: list[TranscriptEvent] = []
    session_id: str | None = None
    byte_offset = 0
    with path.open("rb") as file:
        for physical_index, line_bytes in enumerate(file):
            byte_start = byte_offset
            byte_offset += len(line_bytes)
            byte_end = byte_offset
            line = line_bytes.decode("utf-8", errors="replace")
            if not line.strip():
                continue
            if len(line_bytes) > MAX_JSONL_LINE_CHARS:
                events.append(
                    TranscriptEvent(
                        "",
                        len(events),
                        "tool",
                        "huge_line",
                        f"[huge JSONL line omitted: physical line {physical_index + 1}]",
                        None,
                        {
                            "physical_line": physical_index + 1,
                            "byte_start": byte_start,
                            "byte_end": byte_end,
                            "omitted": "huge_line",
                        },
                    )
                )
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                events.append(
                    _with_source_location(
                        _plain_event("", len(events), line),
                        physical_line=physical_index + 1,
                        byte_start=byte_start,
                        byte_end=byte_end,
                    )
                )
                continue
            event, row_session_id = _event_from_json("", len(events), value)
            session_id = session_id or row_session_id
            if event is not None:
                events.append(
                    _with_source_location(
                        event,
                        physical_line=physical_index + 1,
                        byte_start=byte_start,
                        byte_end=byte_end,
                    )
                )
    return events, session_id


def _parse_json(path: Path) -> tuple[list[TranscriptEvent], str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return _parse_plain_text(path)

    return _parse_json_value(value)


def _parse_json_value(value: Any) -> tuple[list[TranscriptEvent], str | None]:
    rows = _json_rows(value)
    events: list[TranscriptEvent] = []
    session_id: str | None = None
    for row in rows:
        event, row_session_id = _event_from_json("", len(events), row)
        session_id = session_id or row_session_id
        if event is not None:
            events.append(event)
    return events, session_id


def _parse_plain_text(path: Path) -> tuple[list[TranscriptEvent], str | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
    if not blocks and text.strip():
        blocks = [text.strip()]
    return [_plain_event("", index, block) for index, block in enumerate(blocks)], None


def _json_rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ROW_KEYS:
            child = value.get(key)
            if isinstance(child, list):
                return child
            if isinstance(child, dict):
                nested = _json_rows(child)
                if nested != [child]:
                    return nested
        nested_rows: list[Any] = []
        _collect_message_like_rows(value, nested_rows)
        if nested_rows:
            return nested_rows
    return [value]


def _collect_message_like_rows(value: Any, rows: list[Any], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(value, list):
        for item in value:
            _collect_message_like_rows(item, rows, depth + 1)
        return
    if not isinstance(value, dict):
        return
    role = _json_role(value)
    if role in ROLE_VALUES and _collect_text(value):
        rows.append(value)
        return
    for key in ROW_KEYS:
        child = value.get(key)
        if isinstance(child, (dict, list)):
            _collect_message_like_rows(child, rows, depth + 1)
    for child in value.values():
        if isinstance(child, (dict, list)):
            _collect_message_like_rows(child, rows, depth + 1)


def _event_from_json(
    session_id: str,
    index: int,
    value: Any,
) -> tuple[TranscriptEvent | None, str | None]:
    if not isinstance(value, dict):
        text = str(value).strip()
        if not text:
            return None, None
        return TranscriptEvent(session_id, index, "unknown", "json", text, None), None

    discovered_session_id = _session_id_from_json(value)
    metadata = _metadata_from_json(value)
    role = _json_role(value) or "unknown"
    top_type = _string_value(value.get("type"))
    item_type = _direct_or_nested_value(value.get("item"), "type")
    kind = (
        (item_type if top_type == "response_item" and item_type else top_type)
        or _string_value(value.get("kind"))
        or _string_value(value.get("event"))
        or _string_value(value.get("hook_event_name"))
        or _string_value(value.get("tool_name"))
        or _direct_or_nested_value(value.get("item"), "name")
        or "json"
    )
    if role == "unknown" and item_type == "reasoning":
        role = "assistant"
    if role == "unknown" and item_type in {"exec", "patch", "exploration"}:
        role = "tool"
    if role == "unknown" and (metadata.get("command") or metadata.get("hook_event_name")):
        role = "tool"
    created_at = _find_value(value, TIMESTAMP_KEYS)
    collected_text = _clean_text("\n".join(_collect_text(value)))
    if role == "user":
        user_input = _user_input_text_from_json(value, collected_text)
        if user_input:
            metadata["user_input_text"] = user_input
        prompt = user_input or _title_candidate_from_user_text(collected_text)
        if prompt and prompt != collected_text:
            metadata["codex_prompt"] = _clean_text(prompt)
    text = _clean_text("\n".join(piece for piece in (*_metadata_text(metadata), collected_text) if piece))
    if not text:
        return None, discovered_session_id
    return TranscriptEvent(session_id, index, role, kind, text, created_at, metadata), discovered_session_id


def _plain_event(session_id: str, index: int, text: str) -> TranscriptEvent:
    match = ROLE_PREFIX_RE.match(text)
    role = match.group(1).lower() if match else "unknown"
    if match:
        text = text[match.end() :].strip()
    return TranscriptEvent(session_id, index, role, "text", _clean_text(text), None, {})


def _with_source_location(
    event: TranscriptEvent,
    *,
    physical_line: int,
    byte_start: int,
    byte_end: int,
) -> TranscriptEvent:
    metadata = {
        **event.metadata,
        "physical_line": physical_line,
        "byte_start": byte_start,
        "byte_end": byte_end,
    }
    return TranscriptEvent(
        event.session_id,
        event.event_index,
        event.role,
        event.kind,
        event.text,
        event.created_at,
        metadata,
    )


def extract_user_input_text(text: str) -> str | None:
    """Return the human-authored request without Codex IDE/context wrappers."""
    stripped = text.strip()
    if _context_only_user_text(stripped):
        return None
    candidate = _title_candidate_from_user_text(stripped)
    return _clean_text(candidate) if candidate else _clean_text(stripped) if stripped else None


def _user_input_text_from_json(value: dict[str, Any], fallback_text: str) -> str | None:
    explicit = _clean_text("\n".join(_collect_user_input_text(value)))
    return extract_user_input_text(explicit or fallback_text)


def _collect_user_input_text(value: Any, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        pieces: list[str] = []
        for child in value:
            pieces.extend(_collect_user_input_text(child, depth + 1))
        return pieces
    if not isinstance(value, dict):
        return []

    item_type = _string_value(value.get("type"))
    if item_type in {"input_text", "text"}:
        direct_text = _string_value(value.get("text"))
        return [direct_text] if direct_text else []
    if item_type == "output_text":
        return []

    content = value.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        pieces: list[str] = []
        for child in content:
            pieces.extend(_collect_user_input_text(child, depth + 1))
        if pieces:
            return pieces

    pieces: list[str] = []
    for key in ("item", "payload", "message", "request"):
        child = value.get(key)
        if isinstance(child, (dict, list)):
            pieces.extend(_collect_user_input_text(child, depth + 1))
    return pieces


def _json_role(value: Any) -> str | None:
    role = _direct_or_nested_value(value, "role")
    if role:
        return role.lower()
    if isinstance(value, dict):
        entry_type = _string_value(value.get("type"))
        if entry_type and entry_type.lower() in ROLE_VALUES:
            return entry_type.lower()
    return None


def _collect_text(value: Any, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        pieces: list[str] = []
        for child in value:
            pieces.extend(_collect_text(child, depth + 1))
        return pieces
    if isinstance(value, dict):
        pieces = []
        for key in TEXT_KEYS:
            if key in value:
                pieces.extend(_collect_text(_decode_json_string(value[key]), depth + 1))
        if pieces:
            return pieces
        for child in value.values():
            if isinstance(child, (dict, list)):
                pieces.extend(_collect_text(child, depth + 1))
        return pieces
    return []


def _direct_or_nested_value(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        direct = _string_value(value.get(key))
        if direct:
            return direct
        for child in value.values():
            nested = _direct_or_nested_value(child, key)
            if nested:
                return nested
    if isinstance(value, list):
        for child in value:
            nested = _direct_or_nested_value(child, key)
            if nested:
                return nested
    return None


def _metadata_from_json(value: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    top_type = _string_value(value.get("type"))
    item_type = _direct_or_nested_value(value.get("item"), "type")
    parsed_cmd_type = _direct_or_nested_value(value.get("parsedCmd") or _nested_value(value.get("item"), "parsedCmd"), "type")
    if top_type:
        metadata["provider_type"] = top_type
    if item_type:
        metadata["codex_item_type"] = item_type
    if parsed_cmd_type:
        metadata["parsed_cmd_type"] = parsed_cmd_type
    for key in (
        "transcript_path",
        "cwd",
        "model",
        "model_provider",
        "originator",
        "hook_event_name",
        "tool_name",
        "uuid",
        "parentUuid",
        "requestId",
        "version",
        "isSidechain",
    ):
        found = _find_value(value, (key,))
        if found:
            metadata[key] = found

    command = _find_command(value)
    if command:
        metadata["command"] = command

    for key in ("exit_code", "stdout", "stderr"):
        found = _find_value(value, (key,))
        if found is not None:
            metadata[key] = found

    workdir = _find_value(value, ("workdir", "working_dir"))
    if workdir and "cwd" not in metadata:
        metadata["cwd"] = workdir
    if _is_codex_subagent_source(value):
        metadata["codex_source"] = "subagent"
    return metadata


def _nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _nested_value(child, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _nested_value(child, key)
            if found is not None:
                return found
    return None


def _metadata_text(metadata: dict[str, Any]) -> list[str]:
    pieces = []
    for key in ("hook_event_name", "model", "cwd", "transcript_path", "command", "exit_code", "stdout", "stderr"):
        if key in metadata and metadata[key] not in (None, ""):
            pieces.append(f"{key}={metadata[key]}")
    return pieces


def _find_command(value: Any) -> str | None:
    if isinstance(value, str):
        decoded = _decode_json_string(value)
        if decoded is not value:
            return _find_command(decoded)
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_command(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("cmd", "command"):
            direct = _string_value(value.get(key))
            if direct:
                return direct
        for key in ("arguments", "tool_input", "input"):
            if key in value:
                found = _find_command(_decode_json_string(value[key]))
                if found:
                    return found
        for child in value.values():
            if isinstance(child, (dict, list)):
                found = _find_command(child)
                if found:
                    return found
    return None


def _decode_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _find_value(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            direct = _string_value(value.get(key))
            if direct:
                return direct
        for child in value.values():
            nested = _find_value(child, keys)
            if nested:
                return nested
    if isinstance(value, list):
        for child in value:
            nested = _find_value(child, keys)
            if nested:
                return nested
    return None


def _session_id_from_json(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    top_type = _string_value(value.get("type"))
    if top_type == "session_meta":
        for container in (value, value.get("payload")):
            if not isinstance(container, dict):
                continue
            direct = _direct_string_value(container, (*SESSION_ID_KEYS, "id"))
            if direct:
                return direct
    return _find_value(value, SESSION_ID_KEYS)


def _direct_string_value(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        direct = _string_value(value.get(key))
        if direct:
            return direct
    return None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int | float):
        return str(value)
    return None


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"[ \t]+", " ", text).strip()
    return cleaned[:20_000]


def _stable_session_id(path: Path, discovered_session_id: str | None) -> str:
    if discovered_session_id:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", discovered_session_id).strip("-")
        if cleaned:
            return cleaned[:96]
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return f"file-{digest[:24]}"


def _summarize_session(
    path: Path,
    session_id: str,
    events: tuple[TranscriptEvent, ...],
) -> SessionRecord:
    message_events = [event for event in events if event.text]
    user_events = [event for event in events if event.role == "user"]
    assistant_events = [event for event in events if event.role == "assistant"]
    text = "\n".join(event.text for event in events)
    metadata = _session_metadata(events)
    title = _title_from_events(user_events, events, path)
    timestamps = [event.created_at for event in events if event.created_at]
    file_updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    raw_preview = "\n".join(
        piece for piece in (
            f"model={metadata.get('model')}" if metadata.get("model") else "",
            f"cwd={metadata.get('cwd')}" if metadata.get("cwd") else "",
            text,
        ) if piece
    )[:500]
    return SessionRecord(
        session_id=session_id,
        source_path=str(path),
        started_at=min(timestamps) if timestamps else file_updated_at,
        updated_at=max(timestamps) if timestamps else file_updated_at,
        title=title,
        tool="codex",
        message_count=len(message_events),
        user_message_count=len(user_events),
        assistant_message_count=len(assistant_events),
        command_count=sum(_looks_like_command(event) for event in events),
        error_count=sum(_looks_like_error(event.text) for event in events),
        raw_preview=raw_preview,
        id=session_id,
        source="codex",
        project_path=metadata.get("cwd"),
        transcript_path=metadata.get("transcript_path") or str(path),
        ended_at=max(timestamps) if timestamps else file_updated_at,
        model=metadata.get("model"),
        cwd=metadata.get("cwd"),
        status="unknown",
        metadata=metadata,
    )


def _title_from_events(
    user_events: list[TranscriptEvent],
    events: tuple[TranscriptEvent, ...],
    path: Path,
) -> str:
    source = ""
    for event in user_events:
        candidate = _title_candidate_from_user_text(event.text)
        if candidate:
            source = candidate
            break
    if not source:
        source = user_events[0].text if user_events else events[0].text if events else path.stem
    first_line = source.strip().splitlines()[0] if source.strip() else path.stem
    return first_line[:96]


def _looks_like_command(event: TranscriptEvent) -> bool:
    return bool(event.metadata.get("command") or COMMAND_RE.search(event.kind) or COMMAND_RE.search(event.text))


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ERROR_TERMS)


def _looks_like_real_user_goal(text: str) -> bool:
    return _title_candidate_from_user_text(text) is not None


def _title_candidate_from_user_text(text: str) -> str | None:
    stripped = text.strip()
    lowered = stripped.lower()
    if not stripped or _context_only_user_text(stripped):
        return None
    if stripped.startswith(CODEX_IDE_CONTEXT_PREFIX):
        return _extract_codex_prompt_from_ide_context(stripped)
    return stripped


def _context_only_user_text(text: str) -> bool:
    lowered = text.lower()
    return (
        lowered.startswith("<environment_context>")
        or lowered.startswith("<permissions")
        or lowered.startswith("<collaboration_mode>")
        or lowered.startswith("<skills_instructions>")
        or "knowledge cutoff" in lowered[:240]
        or "sandbox_mode" in lowered[:600]
        or text.startswith("# AGENTS.md")
    )


def _extract_codex_prompt_from_ide_context(text: str) -> str | None:
    lines = text.replace("\r\n", "\n").splitlines()
    prompt: str | None = None
    for index, line in enumerate(lines):
        inline_prompt = _codex_request_heading_payload(line)
        if inline_prompt is None:
            continue
        if inline_prompt:
            prompt = inline_prompt
            continue
        following = "\n".join(lines[index + 1 :]).strip()
        prompt = following or None
    return prompt


def _codex_request_heading_payload(line: str) -> str | None:
    trimmed = line.strip()
    if not trimmed.startswith("#"):
        return None
    heading = trimmed.lstrip("#").lstrip()
    lowered = heading.lower()
    if not lowered.startswith(CODEX_REQUEST_MARKER):
        return None
    suffix = heading[len(CODEX_REQUEST_MARKER) :].lstrip()
    if not suffix:
        return ""
    separator = suffix[0]
    if separator not in {":", "：", "-", "—"}:
        return None
    return suffix.lstrip(" \t:：-—").strip()


def _is_codex_subagent_source(value: dict[str, Any]) -> bool:
    source = _nested_value(value.get("payload") or value, "source")
    return isinstance(source, dict) and "subagent" in source


def _session_metadata(events: tuple[TranscriptEvent, ...]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for event in events:
        for key in ("model", "cwd", "transcript_path", "model_provider", "originator", "codex_source"):
            value = event.metadata.get(key)
            if value and key not in metadata:
                metadata[key] = str(value)
    return metadata


def looks_like_user_correction(text: str) -> bool:
    return bool(USER_CORRECTION_RE.search(text))
