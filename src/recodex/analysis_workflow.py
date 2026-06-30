from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .analysis import ERROR_TERMS, SANDBOX_TERMS, TEST_TERMS, _is_signal_event, count_terms
from .llm import LLMAnalysisRequest, llm_token_usage_report
from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text
from .qualitative_coding import build_session_qualitative_analysis
from .report_contract import efficiency_report_contract
from .transcript_graph import GRAPH_SCHEMA_VERSION, build_transcript_graph
from .transcripts import extract_user_input_text

WORKFLOW_VERSION = "analysis_workflow_v4_qualitative_user_units"
WORKFLOW_PROMPT_VERSION = "analysis_workflow_v4_qualitative_user_units"
MAX_EVIDENCE_EXCERPT_CHARS = 520
MAX_EVENTS_PER_PACK = 14
MAX_LLM_EXTRACT_UNITS = 32
MAX_LLM_EXTRACT_PACKS = MAX_LLM_EXTRACT_UNITS
MAX_SEGMENTS_PER_QUALITATIVE_UNIT = 8

PHASES = (
    "context",
    "user_request",
    "planning",
    "tool_execution",
    "failure_retry",
    "patch",
    "verification",
    "user_correction",
    "final_response",
)

USER_CORRECTION_TERMS = (
    "不是",
    "不对",
    "错了",
    "我说的是",
    "我的意思",
    "重新",
    "偏题",
    "你漏",
    "你忘",
    "actually",
    "wrong",
    "not what",
    "i mean",
)
SCOPE_CORRECTION_TERMS = ("不要", "别")
PATCH_TERMS = ("apply_patch", "patched", "修改", "改动", "diff", "updated file", "write file")
NETWORK_TERMS = ("network", "dns", "connection", "proxy", "timeout", "registry", "pypi", "npm")
VERIFICATION_TERMS = ("验证", "测试", "构建", "检查", "跑测试", "test", "pytest", "unittest", "verification")
PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[./~]?[\w.-]+/)+(?:[\w.@-]+)(?:\.[A-Za-z0-9_+-]+)?")


@dataclass(frozen=True)
class NormalizedEvent:
    id: str
    turn_id: str
    session_id: str
    event_index: int
    phase: str
    role: str
    kind: str
    created_at: str | None
    source_ref: str
    excerpt: str
    user_input_text: str | None = None
    command: str | None = None
    status: str | None = None
    file_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    is_test: bool = False
    is_error: bool = False
    is_user_correction: bool = False
    byte_start: int | None = None
    byte_end: int | None = None


@dataclass(frozen=True)
class Episode:
    id: str
    phase: str
    title: str
    event_ids: tuple[str, ...]
    facts: dict[str, object]


@dataclass(frozen=True)
class EvidencePack:
    id: str
    episode_id: str
    phase: str
    summary: str
    facts: dict[str, object]
    user_inputs: tuple[dict[str, object], ...]
    source_refs: tuple[str, ...]
    raw_excerpts: tuple[dict[str, object], ...]
    commands: tuple[dict[str, object], ...]
    file_refs: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceWindow:
    window_id: str
    session_id: str
    episode_id: str
    center_event_id: str
    center_signal_type: str
    event_ids: tuple[str, ...]
    compact_text: str
    token_estimate: int
    signal_score: float


@dataclass(frozen=True)
class MicroClaim:
    claim_id: str
    window_id: str
    episode_id: str
    session_id: str
    claim_type: str
    claim: str
    supporting_event_ids: tuple[str, ...]
    quote: str
    confidence: float


@dataclass(frozen=True)
class AnalysisCard:
    card_id: str
    window_id: str
    episode_id: str
    session_id: str
    card_type: str
    title: str
    observed_fact: str
    inferred_problem: str
    candidate_destination: str
    secondary_destinations: tuple[str, ...]
    evidence_claim_ids: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    confidence: float
    quality_score: float
    artifact_readiness: str


@dataclass(frozen=True)
class CardVerification:
    verification_id: str
    card_id: str
    verdict: str
    problems: tuple[dict[str, str], ...]
    revised_confidence: float
    revised_destination: str
    reason: str


@dataclass(frozen=True)
class WorkflowLLMStage:
    stage: str
    payload: dict[str, object]
    system: str
    schema: dict[str, Any]
    metadata: dict[str, object]
    input_summary: dict[str, object]
    max_output_tokens: int


@dataclass(frozen=True)
class WorkflowStageOutput:
    output: dict[str, Any]
    warnings: tuple[str, ...] = ()
    cached: bool = False
    usage: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowStageResult:
    stage: str
    status: str
    input_summary: dict[str, object]
    output: dict[str, Any]
    warnings: tuple[str, ...] = ()
    cached: bool = False
    usage: dict[str, object] = field(default_factory=dict)


StageRunner = Callable[[WorkflowLLMStage], WorkflowStageOutput]


def run_analysis_workflow(
    session: SessionRecord,
    events: list[TranscriptEvent],
    *,
    stage_runner: StageRunner,
) -> dict[str, Any]:
    trace = normalize_trace(session, events)
    normalized_events = [
        _event_from_payload(item)
        for item in trace["events"]
        if isinstance(item, dict)
    ]
    user_intent = _user_intent_payload(normalized_events)
    episodes = segment_episodes(session, normalized_events)
    packs = build_evidence_packs(session, episodes, normalized_events)
    evidence_windows = build_evidence_windows(session, episodes, normalized_events)
    micro_claims = extract_micro_claims(session, evidence_windows, normalized_events)
    analysis_cards = build_analysis_cards(session, evidence_windows, micro_claims)
    card_verifications = verify_analysis_cards(analysis_cards, micro_claims)
    pattern_clusters = build_pattern_clusters(session, analysis_cards, card_verifications)
    pack_coverage = select_llm_evidence_packs(packs)[1]
    qualitative_analysis = _qualitative_analysis_with_normalized_refs(
        build_session_qualitative_analysis(session, events),
        normalized_events,
    )
    extract_units, coverage = select_llm_qualitative_units(
        qualitative_analysis,
        pack_coverage=pack_coverage,
    )
    stages: list[WorkflowStageResult] = []
    issues: list[dict[str, Any]] = []

    for unit in extract_units:
        stage = _workflow_stage(
            "extract",
            _extractor_payload(session, qualitative_analysis, unit),
            input_summary={
                "analysis_unit_id": unit["id"],
                "theme_id": unit.get("theme_id", ""),
                "source_ref_count": len(unit.get("source_refs", [])),
                "segment_count": len(unit.get("segments", [])),
                "code_ids": list(unit.get("codes", [])),
            },
        )
        try:
            result = _run_stage(stage_runner, stage)
            extracted, warnings = validate_qualitative_extract_output(result.output, unit)
            stages.append(_stage_result(stage, result, warnings=warnings))
            issues.extend(extracted)
        except RuntimeError as exc:
            fallback = _extract_fallback_output(unit, exc)
            stages.append(_fallback_stage_result(stage, fallback, exc))

    cluster_stage = _workflow_stage(
        "cluster",
        _cluster_payload(session, issues, coverage, qualitative_analysis),
        input_summary={"issue_count": len(issues), **coverage},
    )
    if issues:
        try:
            cluster_result = _run_stage(stage_runner, cluster_stage)
            clusters, cluster_warnings = validate_cluster_output(cluster_result.output, issues)
            stages.append(_stage_result(cluster_stage, cluster_result, warnings=cluster_warnings))
        except RuntimeError as exc:
            clusters = []
            stages.append(_fallback_stage_result(cluster_stage, {"clusters": [], "discarded_issue_ids": []}, exc))
    else:
        clusters = []
        stages.append(_skipped_stage_result(cluster_stage, {"clusters": [], "discarded_issue_ids": []}, reason="no_extracted_issues"))

    validation_stage = _workflow_stage(
        "validate",
        _validator_payload(session, issues, clusters, qualitative_analysis),
        input_summary={"issue_count": len(issues), "cluster_count": len(clusters)},
    )
    if clusters:
        try:
            validation_result = _run_stage(stage_runner, validation_stage)
            validation, validation_warnings = validate_validator_output(validation_result.output, _qualitative_source_refs(qualitative_analysis))
            stages.append(_stage_result(validation_stage, validation_result, warnings=validation_warnings))
        except RuntimeError as exc:
            validation = {"validated_issues": [], "validated_clusters": [], "human_queue": [], "rejected_ids": []}
            stages.append(_fallback_stage_result(validation_stage, validation, exc))
    else:
        validation = {"validated_issues": [], "validated_clusters": [], "human_queue": [], "rejected_ids": []}
        stages.append(_skipped_stage_result(validation_stage, validation, reason="no_llm_clusters"))

    validated_cluster_ids = {
        str(item.get("id"))
        for item in validation.get("validated_clusters", [])
        if isinstance(item, dict) and item.get("status") == "supported"
    }
    llm_validated_clusters = [
        cluster
        for cluster in clusters
        if cluster["id"] in validated_cluster_ids and _agent_scope_cluster(cluster)
    ]
    scope_rejected_ids = [
        cluster["id"]
        for cluster in clusters
        if cluster["id"] in validated_cluster_ids and not _agent_scope_cluster(cluster)
    ]
    if scope_rejected_ids:
        validation["rejected_ids"] = list(dict.fromkeys([*validation.get("rejected_ids", []), *scope_rejected_ids]))
        validation["human_queue"] = [
            *validation.get("human_queue", []),
            *[
                {
                    "id": cluster_id,
                    "reason": "Rejected by deterministic agent-workflow scope gate.",
                    "action": "ignore_business_product_cluster",
                }
                for cluster_id in scope_rejected_ids
            ],
        ]
    validated_clusters = [
        *llm_validated_clusters,
        *[
            cluster
            for cluster in pattern_clusters
            if cluster["id"] not in {item["id"] for item in llm_validated_clusters}
        ],
    ]
    skill_candidates = [
        candidate
        for candidate in (_skill_candidate_for_cluster(cluster, validation) for cluster in validated_clusters)
        if candidate is not None
    ]

    reporter_stage = _workflow_stage(
        "report",
        _reporter_payload(session, user_intent, qualitative_analysis, issues, validated_clusters, validation, skill_candidates, coverage),
        input_summary={
            "user_input_count": user_intent.get("user_input_count", 0),
            "qualitative_segment_count": len(_qualitative_segments(qualitative_analysis)),
            "qualitative_theme_count": len(_qualitative_themes(qualitative_analysis)),
            **coverage,
            "issue_count": len(issues),
            "cluster_count": len(clusters),
            "validated_cluster_count": len(validated_clusters),
            "human_queue_count": len(validation.get("human_queue", [])),
            "skill_candidate_count": len(skill_candidates),
        },
    )
    try:
        reporter_result = _run_stage(stage_runner, reporter_stage)
        report, report_warnings = validate_report_output(reporter_result.output, validated_clusters)
        stages.append(_stage_result(reporter_stage, reporter_result, warnings=report_warnings))
    except RuntimeError as exc:
        report = _fallback_report_output(user_intent, validated_clusters, validation, skill_candidates, exc)
        stages.append(_fallback_stage_result(reporter_stage, report, exc))

    return {
        "workflow_version": WORKFLOW_VERSION,
        "session": _session_payload(session),
        "user_intent": user_intent,
        "qualitative_analysis": qualitative_analysis,
        "normalized_trace": trace,
        "deterministic_facts": trace["deterministic_facts"],
        "episodes": [_episode_payload(episode) for episode in episodes],
        "evidence_packs": [_pack_payload(pack) for pack in packs],
        "evidence_windows": [_window_payload(window) for window in evidence_windows],
        "micro_claims": [_claim_payload(claim) for claim in micro_claims],
        "analysis_cards": [_card_payload(card) for card in analysis_cards],
        "card_verifications": [_card_verification_payload(verification) for verification in card_verifications],
        "pattern_clusters": pattern_clusters,
        "llm_coverage": coverage,
        "stages": [_stage_payload(stage) for stage in stages],
        "issues": issues,
        "clusters": [*clusters, *pattern_clusters],
        "validated_clusters": validated_clusters,
        "validation": validation,
        "skill_candidates": skill_candidates,
        "report": report,
    }


def normalize_trace(session: SessionRecord, events: list[TranscriptEvent]) -> dict[str, object]:
    graph = build_transcript_graph(session, events)
    normalized = _events_from_graph(graph.to_payload())
    turns: list[dict[str, object]] = []
    for turn in graph.turns:
        turn_events = [event for event in normalized if event.turn_id == turn["turn_id"]]
        turns.append({
            "id": turn["turn_id"],
            "phase": str(turn.get("phase_hint") or (turn_events[0].phase if turn_events else "")),
            "event_ids": [event.id for event in turn_events],
            "source_refs": [event.source_ref for event in turn_events],
        })
    return {
        "session": _session_payload(session),
        "user_intent": _user_intent_payload(normalized),
        "turns": turns,
        "events": [_normalized_event_payload(event) for event in normalized],
        "deterministic_facts": deterministic_prepass(session, normalized),
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
    }


def _events_from_graph(graph: dict[str, Any]) -> list[NormalizedEvent]:
    tool_by_event = {
        str(item.get("event_id")): item
        for item in graph.get("tool_calls", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    result_by_event = {
        str(item.get("event_id")): item
        for item in graph.get("tool_results", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    files_by_event: dict[str, list[str]] = {}
    for item in graph.get("file_refs", []):
        if not isinstance(item, dict) or not item.get("event_id") or not item.get("path"):
            continue
        files_by_event.setdefault(str(item["event_id"]), []).append(str(item["path"]))
    test_event_ids = {
        str(item.get("event_id"))
        for item in graph.get("test_refs", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    error_event_ids = {
        str(item.get("event_id"))
        for item in graph.get("error_refs", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    correction_event_ids = {
        str(item.get("event_id"))
        for item in graph.get("user_corrections", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    normalized: list[NormalizedEvent] = []
    for item in graph.get("events", []):
        if not isinstance(item, dict):
            continue
        event_id = str(item["event_id"])
        tool = tool_by_event.get(event_id, {})
        result = result_by_event.get(event_id, {})
        event_type = str(item.get("event_type") or "message")
        tags = [str(item.get("phase") or ""), event_type]
        if event_id in test_event_ids:
            tags.append("test_or_verification")
        if event_id in error_event_ids:
            tags.append("error")
        if event_id in correction_event_ids:
            tags.append("user_correction")
        command = tool.get("command")
        status = result.get("status") or tool.get("status")
        normalized.append(
            NormalizedEvent(
                id=event_id,
                turn_id=str(item["turn_id"]),
                session_id=str(item["session_id"]),
                event_index=int(item["event_index"]),
                phase=str(item.get("phase") or "planning"),
                role=str(item.get("role") or "unknown"),
                kind=str(item.get("kind") or event_type),
                created_at=str(item["created_at"]) if item.get("created_at") is not None else None,
                source_ref=str(item["source_ref"]),
                excerpt=str(item.get("text_excerpt") or ""),
                user_input_text=str(item["user_input_text"]) if item.get("user_input_text") else None,
                command=str(command) if command is not None else None,
                status=str(status) if status is not None else None,
                file_refs=tuple(files_by_event.get(event_id, [])),
                tags=tuple(tag for tag in dict.fromkeys(tags) if tag),
                is_test=event_id in test_event_ids,
                is_error=event_id in error_event_ids,
                is_user_correction=event_id in correction_event_ids,
                byte_start=_optional_int(item.get("byte_start")),
                byte_end=_optional_int(item.get("byte_end")),
            )
        )
    return normalized


def normalize_events(session: SessionRecord, events: list[TranscriptEvent]) -> list[NormalizedEvent]:
    normalized: list[NormalizedEvent] = []
    turn_number = 0
    for event in events:
        if not _is_signal_event(event):
            continue
        if event.role == "user":
            turn_number += 1
        command = _command_for_event(event)
        user_input = (
            str(event.metadata.get("user_input_text") or event.metadata.get("codex_prompt"))
            if event.role == "user" and (event.metadata.get("user_input_text") or event.metadata.get("codex_prompt"))
            else extract_user_input_text(event.text) if event.role == "user" else None
        )
        excerpt = _excerpt(redact_text(user_input or event.text), MAX_EVIDENCE_EXCERPT_CHARS)
        status = _status_for_event(event, command)
        is_test = _is_test_event(event, command)
        is_error = False if status == "ok" else status == "failed" or (_transcript_tool_like_event(event, command) and count_terms(event.text, ERROR_TERMS) > 0)
        is_user_correction = event.role == "user" and _is_user_correction_text(event.text)
        phase = _phase_for_event(event, command, is_test=is_test, is_error=is_error, is_user_correction=is_user_correction)
        normalized.append(
            NormalizedEvent(
                id=f"ev_{session.session_id}_{event.event_index}",
                turn_id=f"turn_{max(turn_number, 1)}",
                session_id=session.session_id,
                event_index=event.event_index,
                phase=phase,
                role=event.role,
                kind=event.kind,
                created_at=event.created_at,
                source_ref=f"{session.session_id}:turn_{max(turn_number, 1)}:event_{event.event_index}",
                excerpt=excerpt,
                user_input_text=_excerpt(redact_text(user_input), MAX_EVIDENCE_EXCERPT_CHARS) if user_input else None,
                command=command,
                status=status,
                file_refs=tuple(_file_refs(event.text, command)),
                tags=_event_tags(event, phase, command, is_test=is_test, is_error=is_error, is_user_correction=is_user_correction),
                is_test=is_test,
                is_error=is_error,
                is_user_correction=is_user_correction,
                byte_start=_optional_int(event.metadata.get("byte_start")),
                byte_end=_optional_int(event.metadata.get("byte_end")),
            )
        )
    if normalized:
        return normalized
    return [
        NormalizedEvent(
            id=f"ev_{session.session_id}_preview",
            turn_id="turn_1",
            session_id=session.session_id,
            event_index=0,
            phase="user_request",
            role="unknown",
            kind="session_preview",
            created_at=session.started_at,
            source_ref=f"{session.session_id}:turn_1:preview",
            excerpt=_excerpt(redact_text(session.raw_preview), MAX_EVIDENCE_EXCERPT_CHARS),
            tags=("preview",),
        )
    ]


def deterministic_prepass(session: SessionRecord, events: list[NormalizedEvent]) -> dict[str, object]:
    commands = [event for event in events if event.command]
    failed_commands = [event for event in commands if event.status == "failed"]
    file_reads = [
        file_ref
        for event in events
        for file_ref in event.file_refs
        if event.command and _looks_like_read_command(event.command)
    ]
    repeated_file_reads = sorted({path for path in file_reads if file_reads.count(path) > 1})
    all_text = "\n".join(event.excerpt for event in events)
    files_touched = sorted({file_ref for event in events for file_ref in event.file_refs})
    test_run_count = sum(1 for event in events if event.is_test)
    error_signals = session.error_count + sum(1 for event in events if event.is_error)
    user_input_count = sum(1 for event in events if event.user_input_text)
    context_event_count = sum(1 for event in events if event.phase == "context")
    return {
        "command_count": len(commands) or session.command_count,
        "command_failure_count": len(failed_commands),
        "test_run_count": test_run_count,
        "verification_present": test_run_count > 0 or count_terms(all_text, TEST_TERMS) > 0,
        "files_touched": files_touched,
        "sandbox_or_network_errors": count_terms(all_text, SANDBOX_TERMS) + count_terms(all_text, NETWORK_TERMS),
        "user_correction_count": sum(1 for event in events if event.is_user_correction),
        "user_input_count": user_input_count,
        "context_event_count": context_event_count,
        "user_focus_ratio": round(user_input_count / max(1, len(events)), 3),
        "repeated_file_reads": repeated_file_reads,
        "repeated_file_read_count": len(repeated_file_reads),
        "skipped_verification": (session.command_count > 0 or len(commands) > 0) and test_run_count == 0,
        "error_signals": error_signals,
    }


def segment_episodes(session: SessionRecord, events: list[NormalizedEvent]) -> list[Episode]:
    episodes: list[Episode] = []
    current: list[NormalizedEvent] = []
    context_buffer: list[NormalizedEvent] = []

    for event in events:
        if event.phase == "context":
            if current:
                episodes.append(_episode(session, len(episodes) + 1, _episode_phase(current), current))
                current = []
            context_buffer.append(event)
            continue
        if context_buffer:
            episodes.append(_episode(session, len(episodes) + 1, "context", context_buffer))
            context_buffer = []
        starts_new_goal = bool(event.user_input_text and current and any(item.user_input_text for item in current))
        too_large = len(current) >= MAX_EVENTS_PER_PACK and event.user_input_text
        if current and (starts_new_goal or too_large):
            episodes.append(_episode(session, len(episodes) + 1, _episode_phase(current), current))
            current = []
        current.append(event)
    if context_buffer:
        episodes.append(_episode(session, len(episodes) + 1, "context", context_buffer))
    if current:
        episodes.append(_episode(session, len(episodes) + 1, _episode_phase(current), current))
    return episodes or [_episode(session, 1, "user_request", events)]


def build_evidence_packs(
    session: SessionRecord,
    episodes: list[Episode],
    events: list[NormalizedEvent],
) -> list[EvidencePack]:
    by_id = {event.id: event for event in events}
    packs: list[EvidencePack] = []
    for episode in episodes:
        episode_events = tuple(by_id[event_id] for event_id in episode.event_ids if event_id in by_id)
        file_refs = tuple(sorted({file_ref for event in episode_events for file_ref in event.file_refs}))
        commands = tuple(
            {
                "event_id": event.id,
                "source_ref": event.source_ref,
                "command": event.command,
                "status": event.status or "unknown",
                "is_test": event.is_test,
            }
            for event in episode_events
            if event.command
        )
        user_inputs = _pack_user_inputs(episode_events, events)
        packs.append(
            EvidencePack(
                id=f"pack_{session.session_id}_{len(packs) + 1}",
                episode_id=episode.id,
                phase=episode.phase,
                summary=_pack_summary(episode, episode_events),
                facts=episode.facts,
                user_inputs=user_inputs,
                source_refs=tuple(event.source_ref for event in episode_events),
                raw_excerpts=tuple(_raw_excerpt_payload(event) for event in episode_events if _include_supporting_excerpt(event)),
                commands=commands,
                file_refs=file_refs,
            )
        )
    return packs


def build_evidence_windows(
    session: SessionRecord,
    episodes: list[Episode],
    events: list[NormalizedEvent],
) -> list[EvidenceWindow]:
    by_id = {event.id: event for event in events}
    episode_by_event = {
        event_id: episode
        for episode in episodes
        for event_id in episode.event_ids
    }
    windows: list[EvidenceWindow] = []
    seen_centers: set[str] = set()
    for event in events:
        episode = episode_by_event.get(event.id)
        if episode is None:
            continue
        signal_type, score = _event_signal(event, episode, by_id)
        if not signal_type or event.id in seen_centers:
            continue
        seen_centers.add(event.id)
        episode_events = [by_id[event_id] for event_id in episode.event_ids if event_id in by_id]
        index = next((idx for idx, item in enumerate(episode_events) if item.id == event.id), 0)
        selected = episode_events[max(0, index - 4) : index + 5]
        compact_text = _compact_window_text(selected)
        windows.append(
            EvidenceWindow(
                window_id=f"window_{len(windows) + 1:04d}",
                session_id=session.session_id,
                episode_id=episode.id,
                center_event_id=event.id,
                center_signal_type=signal_type,
                event_ids=tuple(item.id for item in selected),
                compact_text=compact_text,
                token_estimate=max(1, len(compact_text) // 4),
                signal_score=score,
            )
        )
    return _merge_evidence_windows(windows)


def extract_micro_claims(
    session: SessionRecord,
    windows: list[EvidenceWindow],
    events: list[NormalizedEvent],
) -> list[MicroClaim]:
    by_id = {event.id: event for event in events}
    claims: list[MicroClaim] = []
    for window in windows:
        window_events = [by_id[event_id] for event_id in window.event_ids if event_id in by_id]
        for event in window_events:
            claim_type = _claim_type_for_event(event)
            if not claim_type:
                continue
            claims.append(
                MicroClaim(
                    claim_id=f"claim_{len(claims) + 1:04d}",
                    window_id=window.window_id,
                    episode_id=window.episode_id,
                    session_id=session.session_id,
                    claim_type=claim_type,
                    claim=_claim_text(event, claim_type),
                    supporting_event_ids=(event.id,),
                    quote=_excerpt(event.user_input_text or event.excerpt or event.command or "", 180),
                    confidence=0.9 if event.user_input_text or event.command else 0.78,
                )
            )
        if window.center_signal_type == "validation_gap":
            supporting_ids = tuple(event.id for event in window_events if event.command) or window.event_ids[-1:]
            claims.append(
                MicroClaim(
                    claim_id=f"claim_{len(claims) + 1:04d}",
                    window_id=window.window_id,
                    episode_id=window.episode_id,
                    session_id=session.session_id,
                    claim_type="validation_missing",
                    claim="assistant 在该窗口内执行了代码相关命令，但没有出现测试、构建、lint 或等价验证命令。",
                    supporting_event_ids=supporting_ids,
                    quote="no validation command observed",
                    confidence=0.82,
                )
            )
    return claims


def build_analysis_cards(
    session: SessionRecord,
    windows: list[EvidenceWindow],
    claims: list[MicroClaim],
) -> list[AnalysisCard]:
    claims_by_window: dict[str, list[MicroClaim]] = {}
    for claim in claims:
        claims_by_window.setdefault(claim.window_id, []).append(claim)
    cards: list[AnalysisCard] = []
    for window in windows:
        window_claims = claims_by_window.get(window.window_id, [])
        if not window_claims:
            continue
        card_type = _card_type_for_signal(window.center_signal_type)
        claim_ids = tuple(claim.claim_id for claim in window_claims)
        event_ids = tuple(dict.fromkeys(event_id for claim in window_claims for event_id in claim.supporting_event_ids))
        title, observed_fact, inferred_problem, destination, secondary = _card_fields(card_type, window_claims)
        quality = _card_quality_score(window, window_claims, card_type)
        cards.append(
            AnalysisCard(
                card_id=f"card_{len(cards) + 1:04d}",
                window_id=window.window_id,
                episode_id=window.episode_id,
                session_id=session.session_id,
                card_type=card_type,
                title=title,
                observed_fact=observed_fact,
                inferred_problem=inferred_problem,
                candidate_destination=destination,
                secondary_destinations=secondary,
                evidence_claim_ids=claim_ids,
                evidence_event_ids=event_ids,
                confidence=min(0.95, 0.62 + quality / 20),
                quality_score=quality,
                artifact_readiness="ready_for_review" if quality >= 6 else "needs_more_evidence",
            )
        )
    return cards


def verify_analysis_cards(cards: list[AnalysisCard], claims: list[MicroClaim]) -> list[CardVerification]:
    claim_ids = {claim.claim_id for claim in claims}
    verifications: list[CardVerification] = []
    for card in cards:
        problems: list[dict[str, str]] = []
        if not card.evidence_claim_ids or any(claim_id not in claim_ids for claim_id in card.evidence_claim_ids):
            problems.append({"type": "missing_claim", "detail": "card 没有完整 claim 支持。"})
        if _privacy_risk_text(" ".join([card.title, card.observed_fact, card.inferred_problem])):
            problems.append({"type": "privacy_risk", "detail": "card 文本包含疑似隐私内容。"})
        if card.card_type == "ignore":
            problems.append({"type": "not_actionable", "detail": "card 不属于可沉淀的 agent workflow 信号。"})
        verdict = "reject" if any(item["type"] in {"missing_claim", "privacy_risk"} for item in problems) else "pass"
        verifications.append(
            CardVerification(
                verification_id=f"verification_{len(verifications) + 1:04d}",
                card_id=card.card_id,
                verdict=verdict,
                problems=tuple(problems),
                revised_confidence=0.25 if verdict == "reject" else card.confidence,
                revised_destination="ignore" if verdict == "reject" else card.candidate_destination,
                reason="; ".join(item["detail"] for item in problems) if problems else "card is claim-backed and in agent-workflow scope",
            )
        )
    return verifications


def build_pattern_clusters(
    session: SessionRecord,
    cards: list[AnalysisCard],
    verifications: list[CardVerification],
) -> list[dict[str, Any]]:
    passed = {verification.card_id for verification in verifications if verification.verdict == "pass"}
    grouped: dict[str, list[AnalysisCard]] = {}
    for card in cards:
        if card.card_id in passed:
            grouped.setdefault(card.card_type, []).append(card)
    clusters: list[dict[str, Any]] = []
    for card_type, group in grouped.items():
        title, pattern, recommendation, destinations, readiness = _cluster_fields(card_type, group)
        evidence_refs = list(dict.fromkeys(ref for card in group for ref in card.evidence_event_ids))
        clusters.append(
            {
                "id": f"cluster_{_safe_id(card_type)}",
                "cluster_id": f"cluster_{_safe_id(card_type)}",
                "title": title,
                "pattern": pattern,
                "common_pattern": pattern,
                "pattern_type": card_type,
                "cluster_type": card_type,
                "severity": "high" if card_type in {"validation_gap", "user_correction"} else "medium",
                "confidence": round(sum(card.confidence for card in group) / max(1, len(group)), 2),
                "issue_ids": [card.card_id for card in group],
                "card_ids": [card.card_id for card in group],
                "evidence_refs": evidence_refs,
                "evidence_event_ids": evidence_refs,
                "impact": _cluster_impact(card_type),
                "recommended_change": recommendation,
                "recommended_destinations": list(destinations),
                "skill_candidate_allowed": card_type in {"validation_gap", "user_correction"} and len(group) >= 2,
                "skill_gate_reason": "requires repeated verified cards before skill draft" if len(group) < 2 else "repeated verified workflow evidence",
                "frequency": len(group),
                "priority_score": _cluster_priority(card_type, group),
                "readiness": readiness,
                "affected_repos": [session.project_path] if session.project_path else [],
            }
        )
    return sorted(clusters, key=lambda item: (-float(item.get("priority_score", 0)), str(item.get("title", ""))))


def select_llm_evidence_packs(
    packs: list[EvidencePack],
    *,
    max_packs: int = MAX_LLM_EXTRACT_PACKS,
) -> tuple[list[EvidencePack], dict[str, object]]:
    eligible_packs = [pack for pack in packs if not _context_only_pack(pack)]
    candidate_packs = eligible_packs or packs
    skipped_context_packs = len(packs) - len(eligible_packs)
    total = len(candidate_packs)
    if max_packs <= 0 or total <= max_packs:
        return candidate_packs, _coverage_payload(
            packs,
            candidate_packs,
            max_packs=max_packs,
            strategy="user_input_context_filter" if skipped_context_packs else "all",
            skipped_context_packs=skipped_context_packs,
        )

    selected_indexes = {0, total - 1}
    ranked = sorted(
        range(total),
        key=lambda index: (-_pack_signal_score(candidate_packs[index]), index),
    )
    for index in ranked:
        if len(selected_indexes) >= max_packs:
            break
        selected_indexes.add(index)

    selected = [pack for index, pack in enumerate(candidate_packs) if index in selected_indexes]
    return selected, _coverage_payload(
        packs,
        selected,
        max_packs=max_packs,
        strategy="user_input_high_signal_budget" if skipped_context_packs else "high_signal_budget",
        skipped_context_packs=skipped_context_packs,
    )


def select_llm_qualitative_units(
    qualitative_analysis: dict[str, Any],
    *,
    pack_coverage: dict[str, object],
    max_units: int = MAX_LLM_EXTRACT_UNITS,
) -> tuple[list[dict[str, Any]], dict[str, object]]:
    units = build_qualitative_extract_units(qualitative_analysis)
    total = len(units)
    if max_units <= 0 or total <= max_units:
        selected = units
        strategy = "qualitative_theme_all"
    else:
        selected_indexes = {0, total - 1}
        ranked = sorted(
            range(total),
            key=lambda index: (-_qualitative_unit_signal_score(units[index]), index),
        )
        for index in ranked:
            if len(selected_indexes) >= max_units:
                break
            selected_indexes.add(index)
        selected = [unit for index, unit in enumerate(units) if index in selected_indexes]
        strategy = "qualitative_theme_signal_budget"
    return selected, _qualitative_coverage_payload(
        qualitative_analysis,
        units,
        selected,
        pack_coverage=pack_coverage,
        max_units=max_units,
        strategy=strategy,
    )


def build_qualitative_extract_units(qualitative_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    segments = _qualitative_segments(qualitative_analysis)
    by_ref = {str(segment.get("source_ref")): segment for segment in segments if segment.get("source_ref")}
    themes = sorted(
        _qualitative_themes(qualitative_analysis),
        key=lambda theme: (
            str(theme.get("theme_id") or "") == "task_intent",
            -len(theme.get("evidence_refs", []) if isinstance(theme.get("evidence_refs"), list) else []),
            str(theme.get("theme_id") or ""),
        ),
    )
    units: list[dict[str, Any]] = []
    assigned_refs: set[str] = set()
    for theme in themes:
        theme_id = str(theme.get("theme_id") or "theme")
        evidence_refs = [
            str(ref)
            for ref in theme.get("evidence_refs", [])
            if str(ref) in by_ref
        ] if isinstance(theme.get("evidence_refs"), list) else []
        if theme_id == "task_intent":
            evidence_refs = [ref for ref in evidence_refs if ref not in assigned_refs]
        if not evidence_refs:
            continue
        for chunk_index, refs in enumerate(_chunks(evidence_refs, MAX_SEGMENTS_PER_QUALITATIVE_UNIT), start=1):
            chunk_segments = [by_ref[ref] for ref in refs if ref in by_ref]
            if not chunk_segments:
                continue
            units.append(_qualitative_unit_payload(theme, chunk_segments, chunk_index))
            assigned_refs.update(str(segment.get("source_ref")) for segment in chunk_segments if segment.get("source_ref"))

    uncoded_segments = [
        segment
        for segment in segments
        if str(segment.get("source_ref") or "") not in assigned_refs
    ]
    if uncoded_segments:
        uncoded_theme = {
            "theme_id": "uncoded_user_input",
            "label": "Uncoded user input",
            "codes": ["uncoded_user_input"],
            "evidence_refs": [segment.get("source_ref") for segment in uncoded_segments],
            "representative_quotes": [_excerpt(str(segment.get("text") or ""), 180) for segment in uncoded_segments[:3]],
            "validation": {
                "status": "weak",
                "evidence_count": len(uncoded_segments),
                "rule": "uncoded pure-user-input segments are still preserved for LLM review",
            },
        }
        for chunk_index, chunk_segments in enumerate(_chunks(uncoded_segments, MAX_SEGMENTS_PER_QUALITATIVE_UNIT), start=1):
            units.append(_qualitative_unit_payload(uncoded_theme, chunk_segments, chunk_index))
    return units


def _coverage_payload(
    all_packs: list[EvidencePack],
    selected: list[EvidencePack],
    *,
    max_packs: int,
    strategy: str,
    skipped_context_packs: int = 0,
) -> dict[str, object]:
    selected_ids = {pack.episode_id for pack in selected}
    skipped = [pack for pack in all_packs if pack.episode_id not in selected_ids]
    return {
        "total_evidence_packs": len(all_packs),
        "llm_extract_packs": len(selected),
        "skipped_evidence_packs": len(skipped),
        "max_llm_extract_packs": max_packs,
        "coverage_strategy": strategy,
        "skipped_context_packs": skipped_context_packs,
        "selected_episode_ids": [pack.episode_id for pack in selected],
        "skipped_episode_ids": [pack.episode_id for pack in skipped],
    }


def _qualitative_coverage_payload(
    qualitative_analysis: dict[str, Any],
    units: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    *,
    pack_coverage: dict[str, object],
    max_units: int,
    strategy: str,
) -> dict[str, object]:
    selected_ids = {str(unit["id"]) for unit in selected}
    skipped = [unit for unit in units if str(unit["id"]) not in selected_ids]
    segments = _qualitative_segments(qualitative_analysis)
    themes = _qualitative_themes(qualitative_analysis)
    return {
        **pack_coverage,
        "analysis_basis": "qualitative_user_input_segments",
        "coverage_strategy": strategy,
        "total_qualitative_segments": len(segments),
        "total_qualitative_themes": len(themes),
        "total_extract_units": len(units),
        "llm_extract_units": len(selected),
        "llm_extract_packs": len(selected),
        "skipped_extract_units": len(skipped),
        "max_llm_extract_units": max_units,
        "selected_unit_ids": [str(unit["id"]) for unit in selected],
        "skipped_unit_ids": [str(unit["id"]) for unit in skipped],
        "selected_theme_ids": list(dict.fromkeys(str(unit.get("theme_id") or "") for unit in selected)),
        "skipped_theme_ids": list(dict.fromkeys(str(unit.get("theme_id") or "") for unit in skipped)),
    }


def _qualitative_unit_payload(
    theme: dict[str, Any],
    segments: list[dict[str, Any]],
    chunk_index: int,
) -> dict[str, Any]:
    theme_id = str(theme.get("theme_id") or "theme")
    source_refs = [str(segment.get("source_ref")) for segment in segments if segment.get("source_ref")]
    suffix = f"{chunk_index:03d}"
    return {
        "id": f"qunit_{_safe_id(theme_id)}_{suffix}",
        "theme_id": theme_id,
        "label": str(theme.get("label") or theme_id.replace("_", " ")),
        "codes": [str(code) for code in theme.get("codes", [])] if isinstance(theme.get("codes"), list) else [],
        "source_refs": source_refs,
        "segments": [_qualitative_segment_llm_payload(segment) for segment in segments],
        "representative_quotes": [
            str(item)
            for item in theme.get("representative_quotes", [])
            if str(item).strip()
        ][:3] if isinstance(theme.get("representative_quotes"), list) else [],
        "validation": theme.get("validation") if isinstance(theme.get("validation"), dict) else {},
    }


def _qualitative_unit_signal_score(unit: dict[str, Any]) -> int:
    text = "\n".join(str(segment.get("text") or "") for segment in unit.get("segments", []) if isinstance(segment, dict))
    code_ids = {str(code) for code in unit.get("codes", []) if str(code)}
    score = len(unit.get("source_refs", []) if isinstance(unit.get("source_refs"), list) else []) * 20
    if "user_correction" in code_ids or _contains_any(text, USER_CORRECTION_TERMS):
        score += 120
    if "llm_reliability" in code_ids or "json" in text.lower() or "llm" in text.lower():
        score += 90
    if "import_quality" in code_ids:
        score += 70
    if "reporting_experience" in code_ids or "artifact_workflow" in code_ids:
        score += 50
    if "task_request" in code_ids:
        score += 20
    return score


def _chunks(items: list[Any], chunk_size: int) -> list[list[Any]]:
    if chunk_size <= 0:
        return [items]
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _pack_signal_score(pack: EvidencePack) -> int:
    facts = pack.facts
    score = 0
    score += len(pack.user_inputs) * 90
    score += int(facts.get("user_corrections") or 0) * 120
    score += int(facts.get("failed_command_count") or 0) * 100
    score += int(facts.get("error_signals") or 0) * 80
    score += int(facts.get("verification_signals") or 0) * 50
    score += len(pack.commands) * 20
    score += min(len(pack.file_refs), 8) * 6
    if pack.phase in {"user_correction", "failure_retry", "verification"}:
        score += 40
    if pack.phase in {"user_request", "final_response"}:
        score += 10
    return score


def _context_only_pack(pack: EvidencePack) -> bool:
    return (
        pack.phase == "context"
        and not pack.user_inputs
        and not pack.commands
        and int(pack.facts.get("error_signals") or 0) == 0
        and int(pack.facts.get("verification_signals") or 0) == 0
    )


def build_workflow_llm_request(
    stage: WorkflowLLMStage,
    *,
    provider: str,
    model: str,
) -> LLMAnalysisRequest:
    serialized_payload = json.dumps(stage.payload, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(
        "\n".join(
            [
                WORKFLOW_PROMPT_VERSION,
                workflow_schema_version(stage.stage),
                stage.stage,
                model,
                serialized_payload,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return LLMAnalysisRequest(
        task_type=f"analysis_workflow_{stage.stage}",
        provider=provider,
        model=model,
        prompt_version=WORKFLOW_PROMPT_VERSION,
        schema_version=workflow_schema_version(stage.stage),
        input_hash=input_hash,
        system=stage.system,
        messages=[{"role": "user", "content": serialized_payload}],
        schema=stage.schema,
        metadata={**stage.metadata, "workflow_version": WORKFLOW_VERSION},
    )


def workflow_schema_version(stage: str) -> str:
    return f"analysis_workflow.{stage}.v1"


def validate_extract_output(output: dict[str, Any], pack: EvidencePack) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    warnings: list[str] = []
    user_refs = {
        str(item.get("source_ref"))
        for item in pack.user_inputs
        if isinstance(item, dict) and item.get("source_ref")
    }
    known_refs = user_refs or set(pack.source_refs)
    issues: list[dict[str, Any]] = []
    raw_issues = output.get("issues", output.get("findings", []))
    if not isinstance(raw_issues, list):
        warnings.append("extract.issues was not a list")
        raw_issues = []
    for index, raw in enumerate(raw_issues[:8]):
        if not isinstance(raw, dict):
            warnings.append("dropped non-object extracted issue")
            continue
        refs = [str(ref) for ref in raw.get("evidence_refs", []) if str(ref) in known_refs]
        if not refs:
            warnings.append(f"dropped issue without valid evidence_refs: {raw.get('issue_type', index)}")
            continue
        issues.append(
            {
                "id": str(raw.get("id") or f"{pack.episode_id}_issue_{index + 1}"),
                "episode_id": pack.episode_id,
                "phase": pack.phase,
                "issue_type": _clean(raw.get("issue_type") or raw.get("category"), "other"),
                "severity": _enum(raw.get("severity"), {"low", "medium", "high", "critical"}, "medium"),
                "evidence_refs": refs,
                "user_impact": _clean(raw.get("user_impact") or raw.get("impact"), ""),
                "root_cause_hypothesis": _clean(raw.get("root_cause_hypothesis") or raw.get("summary"), ""),
                "recommended_change": _clean(raw.get("recommended_change") or raw.get("recommendation"), ""),
                "confidence": _confidence(raw.get("confidence")),
                "missing_evidence": [
                    _clean(item, "")
                    for item in raw.get("missing_evidence", [])
                    if str(item).strip()
                ] if isinstance(raw.get("missing_evidence"), list) else [],
            }
        )
    return issues, tuple(warnings)


def validate_qualitative_extract_output(
    output: dict[str, Any],
    unit: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    warnings: list[str] = []
    known_refs = {str(ref) for ref in unit.get("source_refs", []) if str(ref)}
    unit_id = str(unit.get("id") or "qualitative_unit")
    theme_id = str(unit.get("theme_id") or "")
    issues: list[dict[str, Any]] = []
    raw_issues = output.get("issues", output.get("findings", []))
    if not isinstance(raw_issues, list):
        warnings.append("extract.issues was not a list")
        raw_issues = []
    for index, raw in enumerate(raw_issues[:8]):
        if not isinstance(raw, dict):
            warnings.append("dropped non-object extracted issue")
            continue
        refs = [str(ref) for ref in raw.get("evidence_refs", []) if str(ref) in known_refs]
        if not refs:
            warnings.append(f"dropped issue without valid qualitative evidence_refs: {raw.get('issue_type', index)}")
            continue
        issues.append(
            {
                "id": str(raw.get("id") or f"{unit_id}_issue_{index + 1}"),
                "episode_id": unit_id,
                "qualitative_unit_id": unit_id,
                "theme_id": theme_id,
                "phase": "user_input_analysis",
                "issue_type": _clean(raw.get("issue_type") or raw.get("category"), "other"),
                "severity": _enum(raw.get("severity"), {"low", "medium", "high", "critical"}, "medium"),
                "evidence_refs": refs,
                "user_impact": _clean(raw.get("user_impact") or raw.get("impact"), ""),
                "root_cause_hypothesis": _clean(raw.get("root_cause_hypothesis") or raw.get("summary"), ""),
                "recommended_change": _clean(raw.get("recommended_change") or raw.get("recommendation"), ""),
                "confidence": _confidence(raw.get("confidence")),
                "missing_evidence": [
                    _clean(item, "")
                    for item in raw.get("missing_evidence", [])
                    if str(item).strip()
                ] if isinstance(raw.get("missing_evidence"), list) else [],
            }
        )
    return issues, tuple(warnings)


def validate_cluster_output(
    output: dict[str, Any],
    issues: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    warnings: list[str] = []
    known_issue_ids = {str(issue["id"]) for issue in issues}
    known_refs = {str(ref) for issue in issues for ref in issue.get("evidence_refs", [])}
    clusters: list[dict[str, Any]] = []
    raw_clusters = output.get("clusters", [])
    if not isinstance(raw_clusters, list):
        warnings.append("cluster.clusters was not a list")
        raw_clusters = []
    for index, raw in enumerate(raw_clusters[:10]):
        if not isinstance(raw, dict):
            warnings.append("dropped non-object cluster")
            continue
        issue_ids = [str(item) for item in raw.get("issue_ids", raw.get("finding_ids", [])) if str(item) in known_issue_ids]
        refs = [str(item) for item in raw.get("evidence_refs", []) if str(item) in known_refs]
        if not issue_ids and not refs:
            warnings.append(f"dropped unsupported cluster: {raw.get('title', index)}")
            continue
        clusters.append(
            {
                "id": str(raw.get("id") or f"cluster_{index + 1}"),
                "title": _clean(raw.get("title") or raw.get("pattern"), "Untitled pattern"),
                "pattern": _clean(raw.get("pattern"), ""),
                "pattern_type": _clean(raw.get("pattern_type") or raw.get("category"), "workflow"),
                "severity": _enum(raw.get("severity"), {"low", "medium", "high", "critical"}, "medium"),
                "confidence": _confidence(raw.get("confidence")),
                "issue_ids": issue_ids,
                "evidence_refs": refs,
                "impact": _clean(raw.get("impact") or raw.get("user_impact"), ""),
                "recommended_change": _clean(raw.get("recommended_change") or raw.get("recommendation"), ""),
                "skill_candidate_allowed": bool(raw.get("skill_candidate_allowed", len(issue_ids) >= 2)),
                "skill_gate_reason": _clean(raw.get("skill_gate_reason"), "requires repeated supported evidence"),
            }
        )
    return clusters, tuple(warnings)


def validate_validator_output(
    output: dict[str, Any],
    validation_source: list[NormalizedEvent] | list[str] | set[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    warnings: list[str] = []
    known_refs = _known_validation_refs(validation_source)
    validated_issues = _validated_items(output.get("validated_issues", output.get("validated_findings", [])), known_refs, warnings)
    validated_clusters = _validated_items(output.get("validated_clusters", []), known_refs, warnings)
    human_queue = _list_of_dicts(output.get("human_queue"))[:20]
    raw_warnings = output.get("warnings", [])
    if isinstance(raw_warnings, list):
        warnings.extend(str(item) for item in raw_warnings[:8])
    return {
        "validated_issues": validated_issues,
        "validated_clusters": validated_clusters,
        "human_queue": human_queue,
        "rejected_ids": [str(item) for item in output.get("rejected_ids", [])[:30]]
        if isinstance(output.get("rejected_ids"), list)
        else [],
    }, tuple(warnings)


def validate_report_output(
    output: dict[str, Any],
    validated_clusters: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    warnings: list[str] = []
    clusters = _list_of_dicts(output.get("clusters"))[:12]
    if not clusters:
        clusters = validated_clusters[:12]
        if not clusters:
            warnings.append("report contains no validated clusters")
    return {
        "headline": _clean(output.get("headline"), "LLM workflow analysis completed"),
        "overall": _clean(output.get("overall"), ""),
        "user_intent_summary": _clean(output.get("user_intent_summary"), ""),
        "clusters": clusters,
        "suggestions": _list_of_dicts(output.get("suggestions"))[:12],
        "skill_drafts": _list_of_dicts(output.get("skill_drafts"))[:8],
        "verification": output.get("verification") if isinstance(output.get("verification"), dict) else {},
        "flow": _list_of_dicts(output.get("flow"))[:12],
    }, tuple(warnings)


def workflow_result_to_report_data(
    session: SessionRecord,
    workflow: dict[str, Any],
    *,
    report_id: str,
    generated_at: str,
) -> dict[str, Any]:
    report = workflow.get("report") if isinstance(workflow.get("report"), dict) else {}
    deterministic_facts = workflow.get("deterministic_facts") if isinstance(workflow.get("deterministic_facts"), dict) else {}
    clusters = report.get("clusters") if isinstance(report.get("clusters"), list) else workflow.get("validated_clusters", [])
    user_intent = workflow.get("user_intent") if isinstance(workflow.get("user_intent"), dict) else {}
    qualitative_analysis = workflow.get("qualitative_analysis") if isinstance(workflow.get("qualitative_analysis"), dict) else {}
    qualitative_segments = _qualitative_segments(qualitative_analysis)
    qualitative_themes = _qualitative_themes(qualitative_analysis)
    llm_coverage = workflow.get("llm_coverage") if isinstance(workflow.get("llm_coverage"), dict) else {}
    efficiency_analysis = (
        workflow.get("efficiency_analysis")
        if isinstance(workflow.get("efficiency_analysis"), dict)
        else {}
    )
    stage_usage = [
        stage.get("usage")
        for stage in workflow.get("stages", [])
        if isinstance(stage, dict) and isinstance(stage.get("usage"), dict)
    ]
    headline = _workflow_report_headline(report, workflow)
    overall = _workflow_report_overall(report, workflow, clusters)
    verification = report.get("verification") if isinstance(report.get("verification"), dict) else {}
    contract = efficiency_report_contract(
        efficiency_analysis=efficiency_analysis,
        summary={
            "headline": headline,
            "overall": overall,
            "completion_confidence": "medium",
            "primary_improvement": report.get("primary_recommendation")
            or report.get("next_best_action")
            or "",
        },
        verification=verification,
        outcome_scope="session",
    )
    payload = {
        "meta": {
            "report_id": report_id,
            "generated_at": generated_at,
            "analysis_mode": "llm-workflow",
            "workflow_version": workflow.get("workflow_version"),
            "session_id": session.session_id,
            "source": session.source or session.tool,
            "project": session.project_path or "",
        },
        "summary": {
            "headline": headline,
            "overall": overall,
            "user_intent": report.get("user_intent_summary", ""),
        },
        **contract,
        "metrics": {
            "user_inputs": deterministic_facts.get("user_input_count", 0),
            "context_events": deterministic_facts.get("context_event_count", 0),
            "user_focus_ratio": deterministic_facts.get("user_focus_ratio", 0),
            "episodes": len(workflow.get("episodes", [])),
            "evidence_packs": len(workflow.get("evidence_packs", [])),
            "evidence_windows": len(workflow.get("evidence_windows", [])),
            "micro_claims": len(workflow.get("micro_claims", [])),
            "analysis_cards": len(workflow.get("analysis_cards", [])),
            "card_verifications": len(workflow.get("card_verifications", [])),
            "pattern_clusters": len(workflow.get("pattern_clusters", [])),
            "qualitative_segments": len(qualitative_segments),
            "qualitative_themes": len(qualitative_themes),
            "llm_extract_units": llm_coverage.get("llm_extract_units", 0),
            "llm_extract_packs": llm_coverage.get("llm_extract_packs", 0),
            "skipped_extract_units": llm_coverage.get("skipped_extract_units", 0),
            "skipped_evidence_packs": llm_coverage.get("skipped_evidence_packs", 0),
            "llm_stages": len(workflow.get("stages", [])),
            "issues": len(workflow.get("issues", [])),
            "validated_clusters": len(workflow.get("validated_clusters", [])),
            "human_queue": len((workflow.get("validation") or {}).get("human_queue", []))
            if isinstance(workflow.get("validation"), dict)
            else 0,
            "commands": deterministic_facts.get("command_count", session.command_count),
            "failed_commands": deterministic_facts.get("command_failure_count", 0),
            "tests": deterministic_facts.get("test_run_count", 0),
            "errors": deterministic_facts.get("error_signals", session.error_count),
        },
        "flow": report.get("flow", []),
        "user_intent": user_intent,
        "issues": clusters,
        "suggestions": report.get("suggestions", []),
        "verification": verification,
        "evidence": _report_evidence(workflow),
        "efficiency_analysis": efficiency_analysis,
        "token_usage": llm_token_usage_report(stage_usage),
        "workflow": _strip_legacy_report_fields({
            "normalized_trace": workflow.get("normalized_trace", {}),
            "deterministic_facts": deterministic_facts,
            "qualitative_analysis": qualitative_analysis,
            "stages": workflow.get("stages", []),
            "episodes": workflow.get("episodes", []),
            "evidence_packs": workflow.get("evidence_packs", []),
            "evidence_windows": workflow.get("evidence_windows", []),
            "micro_claims": workflow.get("micro_claims", []),
            "analysis_cards": workflow.get("analysis_cards", []),
            "card_verifications": workflow.get("card_verifications", []),
            "pattern_clusters": workflow.get("pattern_clusters", []),
            "issues": workflow.get("issues", []),
            "clusters": workflow.get("clusters", []),
            "validated_clusters": workflow.get("validated_clusters", []),
            "validation": workflow.get("validation", {}),
            "skill_candidates": workflow.get("skill_candidates", []),
            "efficiency_analysis": efficiency_analysis,
            "llm_coverage": workflow.get("llm_coverage", {}),
            "user_intent": user_intent,
        }),
        "artifacts": [
            {
                "label": "Workflow JSON",
                "type": "json",
                "description": "Structured LLM workflow result with stage-level outputs.",
            }
        ],
    }
    return _redact_report_value(payload)


def _redact_report_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_report_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_report_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_report_value(item) for key, item in value.items()}
    return value


def _strip_legacy_report_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_legacy_report_fields(item)
            for key, item in value.items()
            if key not in {"category", "card_type"}
        }
    if isinstance(value, list):
        return [_strip_legacy_report_fields(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_legacy_report_fields(item) for item in value]
    return value


def _workflow_report_headline(report: dict[str, Any], workflow: dict[str, Any]) -> str:
    card_count = len(workflow.get("analysis_cards", []) if isinstance(workflow.get("analysis_cards"), list) else [])
    cluster_count = len(workflow.get("validated_clusters", []) if isinstance(workflow.get("validated_clusters"), list) else [])
    window_count = len(workflow.get("evidence_windows", []) if isinstance(workflow.get("evidence_windows"), list) else [])
    return f"生成 {card_count} 张可审计 workflow 证据卡，聚合 {cluster_count} 个候选模式（{window_count} 个证据窗口）"


def _workflow_report_overall(report: dict[str, Any], workflow: dict[str, Any], clusters: object) -> str:
    cluster_list = clusters if isinstance(clusters, list) else []
    titles = [
        str(cluster.get("title") or "").strip()
        for cluster in cluster_list
        if isinstance(cluster, dict) and str(cluster.get("title") or "").strip()
    ][:3]
    card_count = len(workflow.get("analysis_cards", []) if isinstance(workflow.get("analysis_cards"), list) else [])
    claim_count = len(workflow.get("micro_claims", []) if isinstance(workflow.get("micro_claims"), list) else [])
    if titles:
        return f"本次报告基于 {claim_count} 条 micro claim 和 {card_count} 张 analysis card；优先审核模式：{'；'.join(titles)}。"
    return f"本次报告基于 {claim_count} 条 micro claim 和 {card_count} 张 analysis card；暂无通过验证的模式，建议继续积累证据。"


def extract_schema() -> dict[str, Any]:
    issue = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "issue_type",
            "severity",
            "evidence_refs",
            "user_impact",
            "root_cause_hypothesis",
            "recommended_change",
            "confidence",
            "missing_evidence",
        ],
        "properties": {
            "id": {"type": "string", "maxLength": 80},
            "issue_type": {"type": "string", "maxLength": 80},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "evidence_refs": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 4},
            "user_impact": {"type": "string", "maxLength": 240},
            "root_cause_hypothesis": {"type": "string", "maxLength": 280},
            "recommended_change": {"type": "string", "maxLength": 320},
            "confidence": {"type": "number"},
            "missing_evidence": {"type": "array", "items": {"type": "string", "maxLength": 160}, "maxItems": 3},
        },
    }
    return _object_schema(
        ["analysis_unit_id", "issues", "observations"],
        {
            "analysis_unit_id": {"type": "string", "maxLength": 100},
            "issues": {"type": "array", "items": issue, "maxItems": 3},
            "observations": {"type": "array", "items": {"type": "string", "maxLength": 180}, "maxItems": 3},
        },
    )


def cluster_schema() -> dict[str, Any]:
    cluster = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "title",
            "pattern",
            "pattern_type",
            "severity",
            "confidence",
            "issue_ids",
            "evidence_refs",
            "impact",
            "recommended_change",
            "skill_candidate_allowed",
            "skill_gate_reason",
        ],
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "pattern": {"type": "string"},
            "pattern_type": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "confidence": {"type": "number"},
            "issue_ids": {"type": "array", "items": {"type": "string"}},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "impact": {"type": "string"},
            "recommended_change": {"type": "string"},
            "skill_candidate_allowed": {"type": "boolean"},
            "skill_gate_reason": {"type": "string"},
        },
    }
    return _object_schema(
        ["clusters", "discarded_issue_ids"],
        {
            "clusters": {"type": "array", "items": cluster},
            "discarded_issue_ids": {"type": "array", "items": {"type": "string"}},
        },
    )


def validator_schema() -> dict[str, Any]:
    validation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "status", "confidence", "reason", "evidence_refs"],
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["supported", "weak", "rejected"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    return _object_schema(
        ["validated_issues", "validated_clusters", "human_queue", "rejected_ids", "warnings"],
        {
            "validated_issues": {"type": "array", "items": validation},
            "validated_clusters": {"type": "array", "items": validation},
            "human_queue": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "rejected_ids": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    )


def report_schema() -> dict[str, Any]:
    free_object = {"type": "object", "additionalProperties": True}
    return _object_schema(
        ["headline", "overall", "user_intent_summary", "clusters", "suggestions", "skill_drafts", "verification", "flow"],
        {
            "headline": {"type": "string"},
            "overall": {"type": "string"},
            "user_intent_summary": {"type": "string"},
            "clusters": {"type": "array", "items": free_object},
            "suggestions": {"type": "array", "items": free_object},
            "skill_drafts": {"type": "array", "items": free_object},
            "verification": free_object,
            "flow": {"type": "array", "items": free_object},
        },
    )


def _workflow_stage(stage: str, payload: dict[str, object], *, input_summary: dict[str, object]) -> WorkflowLLMStage:
    return WorkflowLLMStage(
        stage=stage,
        payload=payload,
        system=_system_prompt(stage),
        schema=_schema_for_stage(stage),
        metadata={"task_type": f"analysis_workflow_{stage}", "stage": stage},
        input_summary=input_summary,
        max_output_tokens=_workflow_stage_max_output_tokens(stage),
    )


def _workflow_stage_max_output_tokens(stage: str) -> int:
    if stage == "extract":
        return 4000
    if stage == "cluster":
        return 7000
    if stage == "validate":
        return 5000
    if stage == "report":
        return 6000
    return 3000


def _run_stage(stage_runner: StageRunner, stage: WorkflowLLMStage) -> WorkflowStageOutput:
    try:
        return stage_runner(stage)
    except Exception as exc:
        message = str(exc)
        prefix = f"LLM workflow stage `{stage.stage}` failed:"
        if message.startswith(prefix):
            raise RuntimeError(message) from exc
        raise RuntimeError(f"{prefix} {message}") from exc


def _stage_result(
    stage: WorkflowLLMStage,
    output: WorkflowStageOutput,
    *,
    warnings: tuple[str, ...],
) -> WorkflowStageResult:
    merged_warnings = (*output.warnings, *warnings)
    return WorkflowStageResult(
        stage=stage.stage,
        status="ok" if not merged_warnings else "ok_with_warnings",
        input_summary=stage.input_summary,
        output=output.output,
        warnings=merged_warnings,
        cached=output.cached,
        usage=output.usage,
    )


def _fallback_stage_result(
    stage: WorkflowLLMStage,
    output: dict[str, Any],
    exc: Exception,
) -> WorkflowStageResult:
    return WorkflowStageResult(
        stage=stage.stage,
        status="fallback",
        input_summary=stage.input_summary,
        output=output,
        warnings=(f"{stage.stage}_fallback", _clean(str(exc), f"{stage.stage} stage failed")),
        cached=False,
    )


def _skipped_stage_result(
    stage: WorkflowLLMStage,
    output: dict[str, Any],
    *,
    reason: str,
) -> WorkflowStageResult:
    return WorkflowStageResult(
        stage=stage.stage,
        status="skipped",
        input_summary=stage.input_summary,
        output=output,
        warnings=(reason,),
        cached=False,
    )


def _extract_fallback_output(unit: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "analysis_unit_id": str(unit.get("id") or ""),
        "issues": [],
        "observations": [
            f"extract stage skipped after LLM JSON parsing failure: {_clean(str(exc), 'extract failed')}",
        ],
    }


def _fallback_report_output(
    user_intent: dict[str, object],
    validated_clusters: list[dict[str, Any]],
    validation: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    exc: Exception,
) -> dict[str, Any]:
    primary_request = str(user_intent.get("primary_request") or "")
    return {
        "headline": "LLM workflow analysis completed with report fallback",
        "overall": "Issue extraction, clustering, and validation completed. Report synthesis used a deterministic fallback because the report LLM stage failed.",
        "user_intent_summary": primary_request,
        "clusters": validated_clusters[:12],
        "suggestions": [
            {
                "title": "Review fallback report",
                "priority": "medium",
                "why": "The final report LLM stage failed after validated analysis was already available.",
                "recommendation": "Use the validated clusters and evidence refs below; retry report synthesis later if richer prose is needed.",
            }
        ],
        "skill_drafts": skill_candidates[:8],
        "verification": {
            "status": "fallback",
            "reason": _clean(str(exc), "report stage failed"),
            "validated_clusters": len(validated_clusters),
            "human_queue": len(validation.get("human_queue", [])) if isinstance(validation, dict) else 0,
        },
        "flow": [
            {
                "stage": "report_fallback",
                "title": "Deterministic report fallback",
                "description": "Validated clusters were preserved even though final LLM report synthesis failed.",
            }
        ],
    }


def _schema_for_stage(stage: str) -> dict[str, Any]:
    if stage == "extract":
        return extract_schema()
    if stage == "cluster":
        return cluster_schema()
    if stage == "validate":
        return validator_schema()
    if stage == "report":
        return report_schema()
    raise ValueError(f"Unsupported workflow stage: {stage}")


def _system_prompt(stage: str) -> str:
    shared = [
        "你是 recodex 的 AI coding 会话分析工作流。",
        "只能分析输入中的定性编码用户输入：user_intent、qualitative_analysis.segments、qualitative_segments、用户输入 source_refs。",
        "不要分析原始整段聊天、AGENTS、IDE context、环境上下文、工具输出、命令、文件路径或日志。",
        "如果输入中没有 qualitative_segments，不要输出用户诉求类 issue。",
        "所有问题、模式、建议都必须引用 evidence_refs/source_refs。",
        "不要把单次偶发问题直接升级为 skill；低置信度进入 human_queue。",
        "输出必须是符合 JSON schema 的 JSON 对象。",
    ]
    stage_lines = {
        "extract": [
            "当前阶段是 issue extractor。",
            "只阅读 qualitative_segments、qualitative_theme、codebook 和 audit_trail；不得要求或引用 command、raw_excerpts、file_refs。",
            "最多输出 3 个 issue。每个字段必须简短：影响、根因、建议都只写一句话。",
            "evidence_refs 最多 4 个，只使用 qualitative_segments 里的 source_ref。",
            "对单个定性分析单元输出 issue_type、severity、evidence_refs、user_impact、root_cause_hypothesis、recommended_change、confidence、missing_evidence。",
        ],
        "cluster": [
            "当前阶段是 pattern clusterer。",
            "把多个 issue 合并为模式，例如未先读代码就实现、测试失败后没有根因分析、LLM 输出 JSON 不稳定、导入器性能瓶颈。",
        ],
        "validate": [
            "当前阶段是 validation pass。",
            "只审查问题是否被用户输入证据支持，是否引用具体用户输入 source_ref，是否把偶发问题误判为通用 skill。",
        ],
        "report": [
            "当前阶段是 report synthesis。",
            "报告要先说明用户真实输入、用户纠正和任务意图，再展示经过验证的问题模式。",
            "只展示经过 validation pass 支持的 clusters：问题、证据、影响、建议、可生成产物。",
        ],
    }
    return "\n".join([*shared, *stage_lines[stage]])


def _extractor_payload(
    session: SessionRecord,
    qualitative_analysis: dict[str, Any],
    unit: dict[str, Any],
) -> dict[str, object]:
    return {
        "session": _llm_session_payload(session),
        "analysis_focus": {
            "primary": "qualitative_analysis.segments",
            "supporting": ["qualitative_theme", "codebook", "audit_trail"],
            "rule": "Only coded pure-user-input segments are available for LLM analysis. Evidence refs must be source_ref values from qualitative_segments.",
        },
        "analysis_unit": {
            "id": unit["id"],
            "theme_id": unit.get("theme_id", ""),
            "label": unit.get("label", ""),
            "source_refs": list(unit.get("source_refs", [])),
            "segment_count": len(unit.get("segments", [])),
        },
        "qualitative_analysis": _qualitative_analysis_llm_payload(qualitative_analysis, unit=unit),
        "qualitative_theme": _qualitative_theme_for_unit(unit),
        "qualitative_segments": list(unit.get("segments", [])),
        "codebook": _codebook_for_unit(qualitative_analysis, unit),
        "audit_trail": _qualitative_audit_trail(qualitative_analysis),
        "output_contract": {
            "analysis_unit_id": unit["id"],
            "evidence_refs": "Use only source_ref values from qualitative_segments.",
        },
        "response_limits": {
            "max_issues": 3,
            "max_evidence_refs_per_issue": 4,
            "style": "concise_json_only",
        },
    }


def _cluster_payload(
    session: SessionRecord,
    issues: list[dict[str, Any]],
    coverage: dict[str, object],
    qualitative_analysis: dict[str, Any],
) -> dict[str, object]:
    return {
        "session": _llm_session_payload(session),
        "issues": issues,
        "analysis_coverage": coverage,
        "qualitative_analysis": _qualitative_analysis_summary_payload(qualitative_analysis),
        "instruction": "Cluster per-qualitative-unit issues into durable patterns. Patterns must stay grounded in coded user intent and issue evidence.",
    }


def _validator_payload(
    session: SessionRecord,
    issues: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    qualitative_analysis: dict[str, Any],
) -> dict[str, object]:
    return {
        "session": _llm_session_payload(session),
        "issues": issues,
        "clusters": clusters,
        "qualitative_analysis": _qualitative_analysis_summary_payload(qualitative_analysis),
        "source_refs": _qualitative_source_ref_payloads(qualitative_analysis),
        "checks": [
            "is the problem supported by evidence?",
            "does it cite a concrete qualitative user-input source_ref?",
            "is a one-off issue being overgeneralized into a skill?",
        ],
    }


def _reporter_payload(
    session: SessionRecord,
    user_intent: dict[str, object],
    qualitative_analysis: dict[str, Any],
    issues: list[dict[str, Any]],
    validated_clusters: list[dict[str, Any]],
    validation: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    coverage: dict[str, object],
) -> dict[str, object]:
    return {
        "session": _llm_session_payload(session),
        "user_intent": user_intent,
        "analysis_coverage": coverage,
        "qualitative_analysis": _qualitative_analysis_summary_payload(qualitative_analysis),
        "qualitative_segments": _qualitative_source_ref_payloads(qualitative_analysis),
        "issues": issues,
        "validated_clusters": validated_clusters,
        "validation": validation,
        "skill_candidates": skill_candidates,
    }


def _user_intent_payload(events: list[NormalizedEvent]) -> dict[str, object]:
    inputs = [_user_input_payload(event) for event in events if event.user_input_text]
    corrections = [item for item in inputs if item.get("is_correction")]
    requests = [item for item in inputs if not item.get("is_correction")]
    primary = str(requests[0]["text"]) if requests else str(inputs[0]["text"]) if inputs else ""
    latest = str(requests[-1]["text"]) if requests else primary
    return {
        "primary_request": primary,
        "latest_request": latest,
        "user_input_count": len(inputs),
        "correction_count": len(corrections),
        "context_event_count": sum(1 for event in events if event.phase == "context"),
        "timeline": inputs[:40],
        "corrections": corrections[:20],
        "analysis_policy": "LLM report synthesis should start from this user_input timeline; context/tool evidence is supporting only.",
    }


def _user_input_payload(event: NormalizedEvent) -> dict[str, object]:
    return {
        "event_id": event.id,
        "source_ref": event.source_ref,
        "turn_id": event.turn_id,
        "phase": event.phase,
        "created_at": event.created_at,
        "text": redact_text(event.user_input_text or ""),
        "is_correction": event.is_user_correction,
        "file_refs": [redact_text(ref) for ref in event.file_refs],
    }


def _pack_user_inputs(
    episode_events: tuple[NormalizedEvent, ...],
    all_events: list[NormalizedEvent],
) -> tuple[dict[str, object], ...]:
    direct = tuple(_user_input_payload(event) for event in episode_events if event.user_input_text)
    if direct:
        return direct
    first_index = min((event.event_index for event in episode_events), default=0)
    prior = [event for event in all_events if event.user_input_text and event.event_index < first_index]
    if prior:
        payload = dict(_user_input_payload(prior[-1]))
        payload["relation"] = "nearest_prior_user_input"
        return (payload,)
    return ()


def _episode_phase(events: list[NormalizedEvent]) -> str:
    if any(event.is_user_correction for event in events):
        return "user_correction"
    first_user = next((event for event in events if event.user_input_text), None)
    if first_user:
        return first_user.phase if first_user.phase in PHASES else "user_request"
    return events[0].phase if events else "user_request"


def _event_signal(
    event: NormalizedEvent,
    episode: Episode,
    by_id: dict[str, NormalizedEvent],
) -> tuple[str | None, float]:
    if _event_is_context_noise(event):
        return None, 0.0
    if event.is_user_correction:
        return "user_correction", 9.0
    if event.user_input_text and _is_user_correction_text(event.user_input_text):
        return "user_correction", 8.6
    if (
        _episode_has_code_change(episode, by_id)
        and _episode_has_code_change_before_or_at(event, episode, by_id)
        and not _episode_has_validation(episode, by_id)
        and _can_center_validation_gap(event)
    ):
        return "validation_gap", 7.8
    if _tool_failure_signal(event):
        return "wrong_command", 7.2
    if event.user_input_text and _external_context_text(event.user_input_text):
        return "external_context", 6.4
    return None, 0.0


def _episode_has_code_change(episode: Episode, by_id: dict[str, NormalizedEvent]) -> bool:
    return any(
        _event_is_code_change(event)
        for event_id in episode.event_ids
        for event in [by_id.get(event_id)]
        if event is not None
    )


def _episode_has_validation(episode: Episode, by_id: dict[str, NormalizedEvent]) -> bool:
    return any(
        event.is_test or (event.command and _is_test_event_from_command(event.command))
        for event_id in episode.event_ids
        for event in [by_id.get(event_id)]
        if event is not None
    )


def _episode_has_code_change_before_or_at(
    center: NormalizedEvent,
    episode: Episode,
    by_id: dict[str, NormalizedEvent],
) -> bool:
    return any(
        event.event_index <= center.event_index and _event_is_code_change(event)
        for event_id in episode.event_ids
        for event in [by_id.get(event_id)]
        if event is not None
    )


def _can_center_validation_gap(event: NormalizedEvent) -> bool:
    if _event_is_context_noise(event):
        return False
    if _event_is_code_change(event):
        return True
    if event.phase == "final_response":
        return True
    lowered = " ".join([event.excerpt, event.command or ""]).lower()
    return event.role == "assistant" and any(term in lowered for term in ("完成", "done", "fixed", "已修改", "已完成"))


def _event_is_code_change(event: NormalizedEvent) -> bool:
    if "success. updated the following files" in event.excerpt.lower():
        return True
    if event.phase == "patch":
        return True
    if event.kind.lower() in {"patch", "file_write", "file_diff"}:
        return True
    command = (event.command or "").strip().lower()
    if command and _command_changes_files(command):
        return True
    if event.role == "assistant":
        lowered = event.excerpt.lower()
        return any(
            term in lowered
            for term in (
                "已修改",
                "已改",
                "改动已经",
                "修改已经",
                "补丁",
                "patched",
                "updated file",
                "落代码",
                "落完",
            )
        )
    return False


def _command_changes_files(command: str) -> bool:
    lowered = command.strip().lower()
    if _looks_like_read_command(lowered):
        return False
    return any(
        term in lowered
        for term in (
            "apply_patch",
            "git apply",
            "patch ",
            "sed -i",
            "perl -pi",
            "tee ",
            "write file",
        )
    )


def _tool_failure_signal(event: NormalizedEvent) -> bool:
    if event.status == "ok":
        return False
    if not _normalized_tool_like_event(event):
        return False
    return event.status == "failed" or event.is_error


def _normalized_tool_like_event(event: NormalizedEvent) -> bool:
    kind = event.kind.lower()
    lowered = event.excerpt.strip().lower()
    return (
        bool(event.command)
        or event.role == "tool"
        or "tool" in kind
        or "command" in kind
        or lowered.startswith("chunk id:")
        or "process exited with code" in lowered[:240]
        or "apply_patch verification failed" in lowered[:240]
    )


def _merge_evidence_windows(windows: list[EvidenceWindow]) -> list[EvidenceWindow]:
    merged: list[EvidenceWindow] = []
    for window in windows:
        if not merged:
            merged.append(window)
            continue
        previous = merged[-1]
        if previous.episode_id == window.episode_id and set(previous.event_ids).intersection(window.event_ids):
            event_ids = tuple(dict.fromkeys((*previous.event_ids, *window.event_ids)))
            merged[-1] = EvidenceWindow(
                window_id=previous.window_id,
                session_id=previous.session_id,
                episode_id=previous.episode_id,
                center_event_id=previous.center_event_id,
                center_signal_type=previous.center_signal_type,
                event_ids=event_ids,
                compact_text=previous.compact_text,
                token_estimate=previous.token_estimate,
                signal_score=max(previous.signal_score, window.signal_score),
            )
        else:
            merged.append(window)
    return merged


def _compact_window_text(events: list[NormalizedEvent]) -> str:
    lines: list[str] = []
    for event in events:
        label = event.phase.upper()
        body = event.user_input_text or event.command or event.excerpt
        command = f" command=`{redact_text(event.command)}`" if event.command else ""
        lines.append(f"[{label}] {event.source_ref}{command} {_excerpt(redact_text(body), 300)}")
    return "\n".join(lines)


def _claim_type_for_event(event: NormalizedEvent) -> str | None:
    if _event_is_context_noise(event):
        return None
    if event.user_input_text and event.is_user_correction:
        return "user_said"
    if event.user_input_text:
        return "user_said"
    if event.command:
        return "tool_ran"
    if event.role == "assistant" and event.excerpt:
        return "assistant_did"
    if event.is_error:
        return "tool_failed"
    return None


def _claim_text(event: NormalizedEvent, claim_type: str) -> str:
    if claim_type == "user_said":
        prefix = "用户提出了输入"
        if event.is_user_correction:
            prefix = "用户纠正了 assistant 的方向、范围或理解"
        return f"{prefix}：{_excerpt(event.user_input_text or event.excerpt, 220)}"
    if claim_type == "tool_ran":
        return f"assistant 运行了命令 `{redact_text(event.command or '')}`，状态为 {event.status or 'unknown'}。"
    if claim_type == "assistant_did":
        return f"assistant 回复：{_excerpt(event.excerpt, 220)}"
    if claim_type == "tool_failed":
        return f"工具或命令输出包含失败信号：{_excerpt(event.excerpt, 220)}"
    return _excerpt(event.excerpt, 220)


def _card_type_for_signal(signal_type: str) -> str:
    mapping = {
        "user_correction": "user_correction",
        "validation_gap": "validation_gap",
        "wrong_command": "wrong_command",
        "external_context": "external_context",
    }
    return mapping.get(signal_type, "ignore")


def _card_fields(card_type: str, claims: list[MicroClaim]) -> tuple[str, str, str, str, tuple[str, ...]]:
    observed = "；".join(_excerpt(claim.claim, 180) for claim in claims[:3])
    if card_type == "validation_gap":
        return (
            "执行后缺少可审计验证",
            observed,
            "agent workflow 缺少把修改、命令或调查结果绑定到目标测试/构建验证的步骤。",
            "eval",
            ("skill", "global_agents_md"),
        )
    if card_type == "user_correction":
        return (
            "用户纠正了任务方向或范围",
            observed,
            "assistant 需要在用户纠偏后重新确认目标，并把后续分析限制在修正后的目标上。",
            "skill",
            ("eval", "repo_agents_md"),
        )
    if card_type == "wrong_command":
        return (
            "命令或工具执行出现失败信号",
            observed,
            "agent workflow 需要在失败命令后先定位原因，再决定下一步修改。",
            "eval",
            ("skill",),
        )
    if card_type == "external_context":
        return (
            "用户提供了外部上下文",
            observed,
            "反复复制外部上下文时，应考虑 provider/MCP/引用入口，而不是让上下文散落在聊天里。",
            "mcp",
            ("skill",),
        )
    return ("低信号窗口", observed, "证据不足，暂不沉淀。", "ignore", ())


def _card_quality_score(window: EvidenceWindow, claims: list[MicroClaim], card_type: str) -> float:
    score = 0.0
    score += min(3.0, len(claims) * 0.8)
    score += 2.0 if any(claim.quote for claim in claims) else 0.5
    score += 2.0 if card_type in {"validation_gap", "user_correction", "wrong_command"} else 1.0
    score += 1.0 if window.signal_score >= 8 else 0.6
    score += 1.0 if len({event_id for claim in claims for event_id in claim.supporting_event_ids}) >= 2 else 0.4
    return round(score, 2)


def _cluster_fields(card_type: str, cards: list[AnalysisCard]) -> tuple[str, str, str, tuple[str, ...], str]:
    if card_type == "validation_gap":
        readiness = "ready_for_review" if len(cards) >= 1 else "needs_more_evidence"
        return (
            "代码或命令执行后的验证闭环不稳定",
            "assistant 在完成探索、修改或命令执行后，没有稳定产出目标测试、构建、lint 或等价验证证据。",
            "把目标验证作为完成前门禁：说明未验证原因，优先运行用户/CI 指定命令。",
            ("eval", "skill", "global_agents_md"),
            readiness,
        )
    if card_type == "user_correction":
        return (
            "用户纠偏后需要重新锁定任务目标",
            "用户指出方向、范围或理解偏差后，assistant 需要显式收敛到修正后的目标。",
            "在用户纠偏后先复述新的目标和边界，再继续执行；把偏题场景加入 eval。",
            ("skill", "eval", "repo_agents_md"),
            "ready_for_review",
        )
    if card_type == "wrong_command":
        return (
            "失败命令后的恢复流程需要证据化",
            "命令失败或工具错误后，需要先保留错误证据并定位根因。",
            "把失败命令、错误摘要、下一步验证写入 repair loop。",
            ("eval", "skill"),
            "ready_for_review",
        )
    return (
        "外部上下文复制成本较高",
        "用户反复提供外部上下文时，agent 缺少结构化接入点。",
        "评估是否需要 MCP/provider adapter 或上下文引用入口。",
        ("mcp", "skill"),
        "needs_more_evidence",
    )


def _cluster_impact(card_type: str) -> str:
    if card_type == "validation_gap":
        return "完成状态缺少可验证证据，容易导致返工或错误交付。"
    if card_type == "user_correction":
        return "纠偏后如果不重新锁定目标，后续分析和实现会继续偏离用户意图。"
    if card_type == "wrong_command":
        return "失败命令未被结构化处理会掩盖真实根因。"
    return "外部上下文散落在聊天里，后续复用和审阅成本较高。"


def _cluster_priority(card_type: str, cards: list[AnalysisCard]) -> float:
    base = {"validation_gap": 22, "user_correction": 20, "wrong_command": 18, "external_context": 14}.get(card_type, 8)
    return round(base + min(8, len(cards) * 2) + sum(card.quality_score for card in cards) / max(1, len(cards)), 2)


def _agent_scope_cluster(cluster: dict[str, Any]) -> bool:
    text = " ".join(
        str(cluster.get(key) or "")
        for key in ("title", "pattern", "pattern_type", "impact", "recommended_change")
    ).lower()
    blocked_types = {
        "product_functionality_gap",
        "business_requirement",
        "feature_request",
        "domain_requirement",
        "product_gap",
    }
    if str(cluster.get("pattern_type") or "").lower() in blocked_types:
        return False
    blocked_phrases = (
        "business reporting",
        "order report",
        "gift report",
        "report schedules",
        "report triggering",
        "订单报表",
        "礼包报表",
        "业务报表",
    )
    return not any(phrase in text for phrase in blocked_phrases)


def _privacy_risk_text(text: str) -> bool:
    lowered = text.lower()
    return "[redacted:url]" not in lowered and bool(re.search(r"https?://|token|secret|authorization", lowered))


def _external_context_text(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("jira", "linear", "slack", "notion", "figma", "ci 日志", "pr 评论"))


def _event_is_context_noise(event: NormalizedEvent) -> bool:
    lowered = event.excerpt.strip().lower()
    return (
        event.phase == "context"
        or event.role in {"system", "developer"}
        or lowered.startswith("<environment_context>")
        or lowered.startswith("<permissions")
        or lowered.startswith("<collaboration_mode>")
        or lowered.startswith("<skills_instructions>")
        or "knowledge cutoff" in lowered[:240]
        or "sandbox_mode" in lowered[:600]
    )


def _include_supporting_excerpt(event: NormalizedEvent) -> bool:
    if event.user_input_text:
        return True
    if event.phase == "context":
        return False
    return bool(event.command or event.is_test or event.is_error or event.excerpt)


def _episode(session: SessionRecord, number: int, phase: str, events: list[NormalizedEvent]) -> Episode:
    text = "\n".join(event.excerpt for event in events)
    title_event = next((event for event in events if event.user_input_text), next((event for event in events if event.role == "user"), events[0]))
    return Episode(
        id=f"episode_{number}",
        phase=phase if phase in PHASES else "planning",
        title=_excerpt(title_event.user_input_text or title_event.excerpt, 90) or session.title,
        event_ids=tuple(event.id for event in events),
        facts={
            "phase": phase,
            "event_count": len(events),
            "source_refs": [event.source_ref for event in events],
            "command_count": sum(1 for event in events if event.command),
            "failed_command_count": sum(1 for event in events if event.command and event.status == "failed"),
            "file_refs": sorted({file_ref for event in events for file_ref in event.file_refs}),
            "error_signals": sum(1 for event in events if event.is_error) + count_terms(text, ERROR_TERMS),
            "verification_signals": sum(1 for event in events if event.is_test) + count_terms(text, TEST_TERMS),
            "user_corrections": sum(1 for event in events if event.is_user_correction),
            "user_input_count": sum(1 for event in events if event.user_input_text),
        },
    )


def _pack_summary(episode: Episode, events: tuple[NormalizedEvent, ...]) -> str:
    refs = len(events)
    commands = sum(1 for event in events if event.command)
    files = sorted({file_ref for event in events for file_ref in event.file_refs})
    return (
        f"{episode.phase}: {episode.title} "
        f"(source_refs={refs}, commands={commands}, files={len(files)}, "
        f"errors={episode.facts.get('error_signals', 0)}, verification={episode.facts.get('verification_signals', 0)})"
    )


def _phase_for_event(
    event: TranscriptEvent,
    command: str | None,
    *,
    is_test: bool,
    is_error: bool,
    is_user_correction: bool,
) -> str:
    text = event.text.lower()
    kind = event.kind.lower()
    if is_user_correction:
        return "user_correction"
    if event.role == "user":
        return "user_request"
    if is_error:
        return "failure_retry"
    if is_test or count_terms(text, TEST_TERMS) > 0 or _contains_any(text, VERIFICATION_TERMS):
        return "verification"
    if command or "tool" in kind or "command" in kind:
        return "tool_execution"
    if _contains_any(text, PATCH_TERMS):
        return "patch"
    if event.role == "assistant" and _looks_like_final_response(text):
        return "final_response"
    return "planning"


def _command_for_event(event: TranscriptEvent) -> str | None:
    command = event.metadata.get("command") or event.metadata.get("cmd") if event.metadata else None
    if command:
        return str(command)
    match = re.search(r"(?:^|\n)command=([^\n]+)", event.text)
    if match:
        return match.group(1).strip()
    if event.kind.lower() in {"exec_command", "command", "tool_call"}:
        return _excerpt(event.text, 240)
    match = re.search(r"(?:cmd|command)=`([^`]+)`", event.text)
    if match:
        return match.group(1)
    return None


def _status_for_event(event: TranscriptEvent, command: str | None) -> str | None:
    exit_code = _exit_code(event)
    if exit_code == 0:
        return "ok"
    if exit_code is not None and exit_code != 0:
        return "failed"
    text = event.text.lower()
    if "process exited with code 0" in text or "exit code 0" in text:
        return "ok"
    if not command and "tool" not in event.kind.lower() and "command" not in event.kind.lower():
        return None
    if "failed" in text or "error" in text or "exception" in text:
        return "failed"
    return "unknown"


def _exit_code(event: TranscriptEvent) -> int | None:
    raw = event.metadata.get("exit_code") if event.metadata else None
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    match = re.search(r"(?:exit code|process exited with code)\s+(-?\d+)", event.text, re.I)
    if match:
        return int(match.group(1))
    return None


def _transcript_tool_like_event(event: TranscriptEvent, command: str | None) -> bool:
    kind = event.kind.lower()
    lowered = event.text.strip().lower()
    return (
        bool(command)
        or event.role == "tool"
        or "tool" in kind
        or "command" in kind
        or lowered.startswith("chunk id:")
        or "process exited with code" in lowered[:240]
        or "apply_patch verification failed" in lowered[:240]
    )


def _is_test_event(event: TranscriptEvent, command: str | None) -> bool:
    if not command:
        return False
    text = " ".join([command or "", event.text]).lower()
    return count_terms(text, TEST_TERMS) > 0 or _contains_any(text, VERIFICATION_TERMS)


def _file_refs(text: str, command: str | None) -> list[str]:
    refs = PATH_RE.findall(" ".join([command or "", text]))
    cleaned = []
    for ref in refs:
        if ref.startswith("http://") or ref.startswith("https://"):
            continue
        if _looks_like_url_path_ref(ref):
            continue
        if ref not in cleaned:
            cleaned.append(ref)
    return cleaned[:24]


def _looks_like_url_path_ref(ref: str) -> bool:
    head = ref.split("/", 1)[0]
    return head.isdigit() or bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,3}:?\d*", head))


def _event_tags(
    event: TranscriptEvent,
    phase: str,
    command: str | None,
    *,
    is_test: bool,
    is_error: bool,
    is_user_correction: bool,
) -> tuple[str, ...]:
    tags = [phase]
    if command:
        tags.append("tool_call")
    if is_error:
        tags.append("error")
    if is_test:
        tags.append("test_or_verification")
    if is_user_correction:
        tags.append("user_correction")
    if count_terms(event.text, SANDBOX_TERMS):
        tags.append("sandbox")
    if count_terms(event.text, NETWORK_TERMS):
        tags.append("network")
    return tuple(dict.fromkeys(tags))


def _skill_candidate_for_cluster(cluster: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any] | None:
    validations = [
        item for item in validation.get("validated_clusters", [])
        if isinstance(item, dict) and item.get("id") == cluster.get("id")
    ]
    supported = validations and validations[0].get("status") == "supported"
    repeated = len(cluster.get("issue_ids", [])) >= 2
    allowed = bool(cluster.get("skill_candidate_allowed")) and supported and repeated
    if not allowed:
        return None
    return {
        "cluster_id": cluster["id"],
        "title": cluster["title"],
        "status": "draftable_after_human_confirmation",
        "reason": "pattern is repeated, evidence-backed, executable, and still requires human confirmation",
        "evidence_refs": cluster.get("evidence_refs", []),
        "recommended_change": cluster.get("recommended_change", ""),
    }


def _validated_items(value: object, known_refs: set[str], warnings: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return items
    for raw in value[:30]:
        if not isinstance(raw, dict):
            warnings.append("dropped non-object validation result")
            continue
        refs = [str(ref) for ref in raw.get("evidence_refs", []) if str(ref) in known_refs]
        status = _enum(raw.get("status"), {"supported", "weak", "rejected"}, "weak")
        confidence = _confidence(raw.get("confidence"))
        items.append(
            {
                "id": str(raw.get("id") or raw.get("issue_id") or raw.get("cluster_id") or ""),
                "status": "weak" if confidence < 0.6 and status == "supported" else status,
                "confidence": confidence,
                "reason": _clean(raw.get("reason"), ""),
                "evidence_refs": refs,
            }
        )
    return items


def _known_validation_refs(validation_source: list[NormalizedEvent] | list[str] | set[str]) -> set[str]:
    refs: set[str] = set()
    for item in validation_source:
        if isinstance(item, NormalizedEvent):
            refs.add(item.source_ref)
        elif isinstance(item, dict):
            ref = str(item.get("source_ref") or "").strip()
            if ref:
                refs.add(ref)
        else:
            ref = str(item).strip()
            if ref:
                refs.add(ref)
    return refs


def _session_payload(session: SessionRecord) -> dict[str, object]:
    return {
        "id": session.session_id,
        "title": redact_text(session.title),
        "source": session.source or session.tool,
        "source_path": redact_text(session.source_path),
        "project_path": redact_text(session.project_path or ""),
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "message_count": session.message_count,
        "command_count": session.command_count,
        "error_count": session.error_count,
    }


def _llm_session_payload(session: SessionRecord) -> dict[str, object]:
    return {"session_id": session.session_id}


def _normalized_event_payload(event: NormalizedEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "turn_id": event.turn_id,
        "session_id": event.session_id,
        "event_index": event.event_index,
        "phase": event.phase,
        "role": event.role,
        "kind": event.kind,
        "created_at": event.created_at,
        "source_ref": event.source_ref,
        "excerpt": redact_text(event.excerpt),
        "user_input_text": redact_text(event.user_input_text) if event.user_input_text else None,
        "command": redact_text(event.command) if event.command else None,
        "status": event.status,
        "file_refs": [redact_text(ref) for ref in event.file_refs],
        "tags": list(event.tags),
        "is_test": event.is_test,
        "is_error": event.is_error,
        "is_user_correction": event.is_user_correction,
        "byte_start": event.byte_start,
        "byte_end": event.byte_end,
    }


def _event_from_payload(payload: dict[str, object]) -> NormalizedEvent:
    return NormalizedEvent(
        id=str(payload["id"]),
        turn_id=str(payload["turn_id"]),
        session_id=str(payload["session_id"]),
        event_index=int(payload["event_index"]),
        phase=str(payload["phase"]),
        role=str(payload["role"]),
        kind=str(payload["kind"]),
        created_at=str(payload["created_at"]) if payload.get("created_at") is not None else None,
        source_ref=str(payload["source_ref"]),
        excerpt=str(payload["excerpt"]),
        user_input_text=str(payload["user_input_text"]) if payload.get("user_input_text") else None,
        command=str(payload["command"]) if payload.get("command") is not None else None,
        status=str(payload["status"]) if payload.get("status") is not None else None,
        file_refs=tuple(str(item) for item in payload.get("file_refs", []) if item),
        tags=tuple(str(item) for item in payload.get("tags", []) if item),
        is_test=bool(payload.get("is_test")),
        is_error=bool(payload.get("is_error")),
        is_user_correction=bool(payload.get("is_user_correction")),
        byte_start=_optional_int(payload.get("byte_start")),
        byte_end=_optional_int(payload.get("byte_end")),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _raw_excerpt_payload(event: NormalizedEvent) -> dict[str, object]:
    return {
        "event_id": event.id,
        "source_ref": event.source_ref,
        "role": event.role,
        "phase": event.phase,
        "excerpt": redact_text(event.excerpt),
        "user_input_text": redact_text(event.user_input_text) if event.user_input_text else None,
        "tags": list(event.tags),
    }


def _source_ref_payload(event: NormalizedEvent) -> dict[str, object]:
    return {
        "source_ref": event.source_ref,
        "event_id": event.id,
        "turn_id": event.turn_id,
        "phase": event.phase,
        "command": redact_text(event.command) if event.command else None,
        "status": event.status,
        "file_refs": [redact_text(ref) for ref in event.file_refs],
    }


def _user_source_ref_payload(event: NormalizedEvent) -> dict[str, object]:
    return {
        "source_ref": event.source_ref,
        "event_id": event.id,
        "turn_id": event.turn_id,
        "phase": event.phase,
        "user_input_text": event.user_input_text or "",
        "is_correction": event.is_user_correction,
    }


def _qualitative_segments(qualitative_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    value = qualitative_analysis.get("segments")
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, dict) and item.get("role") == "user" and item.get("source_ref")
    ]


def _qualitative_analysis_with_normalized_refs(
    qualitative_analysis: dict[str, Any],
    events: list[NormalizedEvent],
) -> dict[str, Any]:
    base_refs = {event.event_index: event.source_ref for event in events if event.user_input_text}
    if not base_refs:
        return qualitative_analysis
    ref_map: dict[str, str] = {}
    aligned = dict(qualitative_analysis)
    segments: list[dict[str, Any]] = []
    for segment in _qualitative_segments(qualitative_analysis):
        updated = dict(segment)
        old_ref = str(updated.get("source_ref") or "")
        try:
            event_index = int(updated.get("event_index"))
            unit_index = int(updated.get("unit_index") or 1)
        except (TypeError, ValueError):
            segments.append(updated)
            continue
        base_ref = base_refs.get(event_index)
        if base_ref:
            new_ref = base_ref if unit_index == 1 else f"{base_ref}:unit_{unit_index}"
            updated["source_ref"] = new_ref
            if old_ref:
                ref_map[old_ref] = new_ref
        segments.append(updated)
    aligned["segments"] = segments

    themes: list[dict[str, Any]] = []
    for theme in _qualitative_themes(qualitative_analysis):
        updated_theme = dict(theme)
        if isinstance(theme.get("evidence_refs"), list):
            updated_theme["evidence_refs"] = [
                ref_map.get(str(ref), str(ref))
                for ref in theme["evidence_refs"]
            ]
        themes.append(updated_theme)
    aligned["themes"] = themes
    audit = dict(_qualitative_audit_trail(qualitative_analysis))
    audit["source_ref_alignment"] = "normalized_trace_source_ref"
    aligned["audit_trail"] = audit
    return aligned


def _qualitative_themes(qualitative_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    value = qualitative_analysis.get("themes")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _qualitative_audit_trail(qualitative_analysis: dict[str, Any]) -> dict[str, Any]:
    value = qualitative_analysis.get("audit_trail")
    return value if isinstance(value, dict) else {}


def _qualitative_analysis_llm_payload(
    qualitative_analysis: dict[str, Any],
    *,
    unit: dict[str, Any],
) -> dict[str, Any]:
    payload = _qualitative_analysis_summary_payload(qualitative_analysis)
    payload["selected_unit_id"] = unit["id"]
    payload["theme"] = _qualitative_theme_for_unit(unit)
    payload["segments"] = list(unit.get("segments", []))
    return payload


def _qualitative_analysis_summary_payload(qualitative_analysis: dict[str, Any]) -> dict[str, Any]:
    session = qualitative_analysis.get("session") if isinstance(qualitative_analysis.get("session"), dict) else {}
    segments = _qualitative_segments(qualitative_analysis)
    themes = _qualitative_themes(qualitative_analysis)
    return {
        "method": str(qualitative_analysis.get("method") or ""),
        "session": {"session_id": str(session.get("session_id") or "")},
        "unit_of_analysis": str(_qualitative_audit_trail(qualitative_analysis).get("unit_of_analysis") or "pure_user_input_segment"),
        "segment_count": len(segments),
        "theme_count": len(themes),
        "themes": [_qualitative_theme_summary_payload(theme) for theme in themes],
        "audit_trail": _qualitative_audit_trail(qualitative_analysis),
    }


def _qualitative_theme_summary_payload(theme: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = [
        str(ref)
        for ref in theme.get("evidence_refs", [])
        if str(ref).strip()
    ] if isinstance(theme.get("evidence_refs"), list) else []
    return {
        "theme_id": str(theme.get("theme_id") or ""),
        "label": str(theme.get("label") or ""),
        "codes": [str(code) for code in theme.get("codes", [])] if isinstance(theme.get("codes"), list) else [],
        "evidence_refs": evidence_refs,
        "evidence_count": len(evidence_refs),
        "representative_quotes": [
            _excerpt(str(item), 220)
            for item in theme.get("representative_quotes", [])
            if str(item).strip()
        ][:3] if isinstance(theme.get("representative_quotes"), list) else [],
        "validation": theme.get("validation") if isinstance(theme.get("validation"), dict) else {},
    }


def _qualitative_theme_for_unit(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "theme_id": str(unit.get("theme_id") or ""),
        "label": str(unit.get("label") or ""),
        "codes": [str(code) for code in unit.get("codes", [])] if isinstance(unit.get("codes"), list) else [],
        "source_refs": [str(ref) for ref in unit.get("source_refs", [])] if isinstance(unit.get("source_refs"), list) else [],
        "representative_quotes": [
            _excerpt(str(item), 220)
            for item in unit.get("representative_quotes", [])
            if str(item).strip()
        ][:3] if isinstance(unit.get("representative_quotes"), list) else [],
        "validation": unit.get("validation") if isinstance(unit.get("validation"), dict) else {},
    }


def _codebook_for_unit(qualitative_analysis: dict[str, Any], unit: dict[str, Any]) -> list[dict[str, Any]]:
    codes = {str(code) for code in unit.get("codes", []) if str(code)}
    theme_id = str(unit.get("theme_id") or "")
    codebook = qualitative_analysis.get("codebook")
    if not isinstance(codebook, list):
        return []
    selected: list[dict[str, Any]] = []
    for item in codebook:
        if not isinstance(item, dict):
            continue
        if str(item.get("code_id") or "") in codes or str(item.get("theme_id") or "") == theme_id:
            selected.append(
                {
                    "code_id": str(item.get("code_id") or ""),
                    "label": str(item.get("label") or ""),
                    "definition": str(item.get("definition") or ""),
                    "theme_id": str(item.get("theme_id") or ""),
                }
            )
    return selected


def _qualitative_segment_llm_payload(segment: dict[str, Any]) -> dict[str, Any]:
    codes = segment.get("codes") if isinstance(segment.get("codes"), list) else []
    return {
        "segment_id": str(segment.get("segment_id") or ""),
        "session_id": str(segment.get("session_id") or ""),
        "source_ref": str(segment.get("source_ref") or ""),
        "event_index": segment.get("event_index"),
        "unit_index": segment.get("unit_index"),
        "created_at": segment.get("created_at"),
        "role": "user",
        "text": str(segment.get("text") or ""),
        "codes": [
            {
                "code_id": str(code.get("code_id") or ""),
                "label": str(code.get("label") or ""),
                "theme_id": str(code.get("theme_id") or ""),
                "confidence": code.get("confidence", 0),
            }
            for code in codes
            if isinstance(code, dict)
        ],
    }


def _qualitative_source_refs(qualitative_analysis: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for segment in _qualitative_segments(qualitative_analysis):
        ref = str(segment.get("source_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _qualitative_source_ref_payloads(qualitative_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for segment in _qualitative_segments(qualitative_analysis):
        codes = segment.get("codes") if isinstance(segment.get("codes"), list) else []
        code_ids = [str(code.get("code_id") or "") for code in codes if isinstance(code, dict) and code.get("code_id")]
        theme_ids = list(dict.fromkeys(str(code.get("theme_id") or "") for code in codes if isinstance(code, dict) and code.get("theme_id")))
        payloads.append(
            {
                "source_ref": str(segment.get("source_ref") or ""),
                "segment_id": str(segment.get("segment_id") or ""),
                "event_index": segment.get("event_index"),
                "unit_index": segment.get("unit_index"),
                "created_at": segment.get("created_at"),
                "text": str(segment.get("text") or ""),
                "code_ids": code_ids,
                "theme_ids": theme_ids,
            }
        )
    return payloads


def _episode_payload(episode: Episode) -> dict[str, object]:
    return {
        "id": episode.id,
        "phase": episode.phase,
        "title": episode.title,
        "event_ids": list(episode.event_ids),
        "facts": episode.facts,
    }


def _user_only_episode_payload(episode: Episode) -> dict[str, object]:
    return {
        "id": episode.id,
        "phase": episode.phase,
        "title": episode.title,
        "facts": _user_only_facts(episode.facts),
    }


def _pack_payload(pack: EvidencePack) -> dict[str, object]:
    return {
        "id": pack.id,
        "episode_id": pack.episode_id,
        "phase": pack.phase,
        "summary": redact_text(pack.summary),
        "facts": pack.facts,
        "user_inputs": list(pack.user_inputs),
        "source_refs": list(pack.source_refs),
        "raw_excerpts": list(pack.raw_excerpts),
        "commands": [_redacted_mapping(command) for command in pack.commands],
        "file_refs": [redact_text(ref) for ref in pack.file_refs],
    }


def _redacted_mapping(value: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(item, str):
            redacted[key] = redact_text(item)
        elif isinstance(item, list):
            redacted[key] = [redact_text(child) if isinstance(child, str) else child for child in item]
        else:
            redacted[key] = item
    return redacted


def _window_payload(window: EvidenceWindow) -> dict[str, object]:
    return {
        "window_id": window.window_id,
        "session_id": window.session_id,
        "episode_id": window.episode_id,
        "center_event_id": window.center_event_id,
        "center_signal_type": window.center_signal_type,
        "event_ids": list(window.event_ids),
        "compact_text": redact_text(window.compact_text),
        "token_estimate": window.token_estimate,
        "signal_score": window.signal_score,
    }


def _claim_payload(claim: MicroClaim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "window_id": claim.window_id,
        "episode_id": claim.episode_id,
        "session_id": claim.session_id,
        "claim_type": claim.claim_type,
        "claim": redact_text(claim.claim),
        "supporting_event_ids": list(claim.supporting_event_ids),
        "quote": redact_text(claim.quote),
        "confidence": claim.confidence,
    }


def _card_payload(card: AnalysisCard) -> dict[str, object]:
    return {
        "card_id": card.card_id,
        "window_id": card.window_id,
        "episode_id": card.episode_id,
        "session_id": card.session_id,
        "card_type": card.card_type,
        "title": redact_text(card.title),
        "observed_fact": redact_text(card.observed_fact),
        "inferred_problem": redact_text(card.inferred_problem),
        "candidate_destination": card.candidate_destination,
        "secondary_destinations": list(card.secondary_destinations),
        "evidence_claim_ids": list(card.evidence_claim_ids),
        "evidence_event_ids": list(card.evidence_event_ids),
        "confidence": card.confidence,
        "quality_score": card.quality_score,
        "artifact_readiness": card.artifact_readiness,
    }


def _card_verification_payload(verification: CardVerification) -> dict[str, object]:
    return {
        "verification_id": verification.verification_id,
        "card_id": verification.card_id,
        "verdict": verification.verdict,
        "problems": list(verification.problems),
        "revised_confidence": verification.revised_confidence,
        "revised_destination": verification.revised_destination,
        "reason": verification.reason,
    }


def _user_only_pack_payload(pack: EvidencePack) -> dict[str, object]:
    return {
        "id": pack.id,
        "episode_id": pack.episode_id,
        "phase": pack.phase,
        "summary": _user_only_pack_summary(pack),
        "facts": _user_only_facts(pack.facts),
        "user_inputs": list(pack.user_inputs),
        "source_refs": _user_input_source_refs(pack),
    }


def _user_only_facts(facts: dict[str, object]) -> dict[str, object]:
    allowed = ("phase", "user_input_count", "user_corrections")
    return {key: facts[key] for key in allowed if key in facts}


def _user_only_pack_summary(pack: EvidencePack) -> str:
    return (
        f"{pack.phase}: user_inputs={len(pack.user_inputs)}, "
        f"corrections={pack.facts.get('user_corrections', 0)}, "
        f"source_refs={len(_user_input_source_refs(pack))}"
    )


def _user_input_source_refs(pack: EvidencePack) -> list[str]:
    refs: list[str] = []
    for item in pack.user_inputs:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("source_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _stage_payload(stage: WorkflowStageResult) -> dict[str, object]:
    output = dict(stage.output)
    return {
        "stage": stage.stage,
        "status": stage.status,
        "input_summary": stage.input_summary,
        "output_summary": _output_summary(stage.stage, output),
        "output": output,
        "warnings": list(stage.warnings),
        "cached": stage.cached,
        "usage": stage.usage,
    }


def _output_summary(stage: str, output: dict[str, Any]) -> dict[str, object]:
    if stage == "extract":
        return {"issues": len(output.get("issues", output.get("findings", [])) or [])}
    if stage == "cluster":
        return {"clusters": len(output.get("clusters", []) or [])}
    if stage == "validate":
        return {
            "validated_issues": len(output.get("validated_issues", output.get("validated_findings", [])) or []),
            "validated_clusters": len(output.get("validated_clusters", []) or []),
            "human_queue": len(output.get("human_queue", []) or []),
        }
    if stage == "report":
        return {"clusters": len(output.get("clusters", []) or []), "suggestions": len(output.get("suggestions", []) or [])}
    return {}


def _report_evidence(workflow: dict[str, Any]) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    user_intent = workflow.get("user_intent") if isinstance(workflow.get("user_intent"), dict) else {}
    timeline = user_intent.get("timeline") if isinstance(user_intent.get("timeline"), list) else []
    for index, item in enumerate(timeline, start=1):
        if not isinstance(item, dict):
            continue
        user_input = str(item.get("text") or "").strip()
        if not user_input:
            continue
        evidence.append(
            {
                "id": item.get("source_ref") or f"user_input_{index:03d}",
                "content": user_input,
                "role": "user",
                "phase": item.get("phase", "user_request"),
                "user_input_text": user_input,
                "is_correction": bool(item.get("is_correction")),
            }
        )
    if evidence:
        return evidence[:30]
    for pack in workflow.get("evidence_packs", []):
        if not isinstance(pack, dict):
            continue
        for item in pack.get("user_inputs", []):
            if isinstance(item, dict):
                user_input = str(item.get("text") or "").strip()
                if not user_input:
                    continue
                evidence.append(
                    {
                        "id": item.get("source_ref", ""),
                        "content": user_input,
                        "role": "user",
                        "phase": item.get("phase", "user_request"),
                        "user_input_text": user_input,
                        "is_correction": bool(item.get("is_correction")),
                    }
                )
    return evidence[:30]


def _object_schema(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clean(value: object, default: str) -> str:
    if value is None:
        return default
    text = redact_text(str(value)).strip()
    return text or default


def _enum(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _is_user_correction_text(text: str) -> bool:
    lowered = text.lower()
    if _contains_any(lowered, USER_CORRECTION_TERMS):
        return True
    if _contains_any(lowered, SCOPE_CORRECTION_TERMS):
        compact = re.sub(r"\s+", "", lowered)
        return any(term in compact for term in ("不是", "而是", "要跟", "单独", "范围", "我的意思"))
    return False


def _is_test_event_from_command(command: str) -> bool:
    lowered = command.lower()
    return count_terms(lowered, TEST_TERMS) > 0 or _contains_any(lowered, VERIFICATION_TERMS)


def _looks_like_final_response(text: str) -> bool:
    return any(term in text for term in ("完成", "已", "summary", "done", "final", "verification", "验证"))


def _looks_like_read_command(command: str) -> bool:
    lowered = command.lower()
    return lowered.startswith(("sed ", "cat ", "rg ", "grep ", "less ", "head ", "tail ", "nl "))


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return cleaned or "unit"


def _excerpt(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
