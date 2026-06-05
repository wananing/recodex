from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptEvent:
    session_id: str
    event_index: int
    role: str
    kind: str
    text: str
    created_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    source_path: str
    started_at: str | None
    updated_at: str | None
    title: str
    tool: str
    message_count: int
    user_message_count: int
    assistant_message_count: int
    command_count: int
    error_count: int
    raw_preview: str
    id: str | None = None
    source: str | None = None
    project_path: str | None = None
    transcript_path: str | None = None
    ended_at: str | None = None
    model: str | None = None
    cwd: str | None = None
    status: str | None = None
    raw_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogEntry:
    source_path: str
    session_id: str
    project_path: str | None
    started_at: str | None
    updated_at: str | None
    model: str | None
    title: str
    file_size: int


@dataclass(frozen=True)
class ParsedTranscript:
    session: SessionRecord
    events: tuple[TranscriptEvent, ...]


@dataclass(frozen=True)
class ImprovementDraft:
    fingerprint: str
    session_id: str | None
    category: str
    title: str
    evidence: str
    recommendation: str
