from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    source: str | None = None


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


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    session_id: str
    event_id: str
    source_file: str
    byte_start: int
    byte_end: int
    quote: str
    reason: str
    content_hash: str


@dataclass(frozen=True)
class CostLedger:
    total_duration_seconds: int | None
    extra_turns: int
    failed_commands: int
    repeated_commands: int
    repeated_file_reads: int
    user_corrections: int
    reverted_changes: int
    ignored_tool_results: int
    verification_followups: int
    clearly_avoidable_events: tuple[str, ...] = ()
    potentially_avoidable_events: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    category: str
    severity: str
    confidence: float
    observation: str
    observed_cost: dict[str, Any]
    cause: str
    responsibility_layers: tuple[str, ...]
    impact: str
    recommendation: str
    evidence_refs: tuple[str, ...]
    source_card_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImprovementOpportunity:
    id: str
    source_finding_ids: tuple[str, ...]
    title: str
    problem: str
    cause: str
    recurrence: str
    preventability: str
    impact: str
    confidence: float
    best_action: str
    recommended_mechanism: str
    routing_reason: str
    suggested_target: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactCandidate:
    id: str
    opportunity_id: str
    artifact_type: str
    target_path: str | None
    proposed_content: str
    scope: str
    rationale: str
    risks: tuple[str, ...]
    validation_plan: tuple[str, ...]
    status: str
    last_verified_at: datetime | None = None
