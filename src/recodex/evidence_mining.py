from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis_workflow import NormalizedEvent, normalize_events, segment_episodes
from .diagnostics import build_diagnostic_bundle
from .models import (
    ArtifactCandidate,
    CostLedger,
    EvidenceRef,
    Finding,
    ImprovementOpportunity,
    SessionRecord,
    TranscriptEvent,
)
from .privacy import redact_text

WINDOW_RADIUS = 3
MIN_SIGNAL_SCORE = 6.0

USER_CORRECTION_WEIGHTS = {
    "不是": 8,
    "不对": 8,
    "错了": 8,
    "我说的是": 8,
    "你漏了": 8,
    "先别": 7,
    "重新": 6,
    "按这个格式": 6,
    "wrong": 8,
    "not what": 8,
    "i mean": 8,
}

VALIDATION_WEIGHTS = {
    "没跑测试": 9,
    "没跑": 8,
    "ci 还是失败": 8,
    "测试失败": 8,
    "lint": 7,
    "typecheck": 7,
    "failed": 7,
    "traceback": 7,
    "失败": 7,
    "test": 4,
    "pytest": 5,
}

SEDIMENTATION_WEIGHTS = {
    "以后": 8,
    "每次": 8,
    "固定": 7,
    "沉淀": 7,
    "skill": 7,
    "agents.md": 7,
    "流程": 6,
    "checklist": 6,
}

EXTERNAL_CONTEXT_WEIGHTS = {
    "pr 评论": 7,
    "jira": 7,
    "linear": 7,
    "slack": 7,
    "notion": 7,
    "figma": 7,
    "ci 日志": 7,
    "ci log": 7,
}

SAFETY_WEIGHTS = {
    "不要改 .env": 9,
    "secret": 9,
    "token": 9,
    "rm -rf": 9,
    "生产": 8,
    "deploy": 8,
}

PROJECT_CONVENTION_TERMS = (
    "约定",
    "规范",
    "目录",
    "命名",
    "不要改认证外",
    "project convention",
)


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
    quote: str | None
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
    revised_destination: str | None
    reason: str


@dataclass(frozen=True)
class PatternCluster:
    cluster_id: str
    cluster_type: str
    title: str
    common_pattern: str
    recommended_destinations: tuple[str, ...]
    frequency: int
    affected_repos: tuple[str, ...]
    time_range: dict[str, str | None]
    priority_score: float
    readiness: str
    confidence: float
    card_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceMiningResult:
    cost_ledger: CostLedger
    evidence_refs: tuple[EvidenceRef, ...]
    evidence_windows: tuple[EvidenceWindow, ...]
    micro_claims: tuple[MicroClaim, ...]
    analysis_cards: tuple[AnalysisCard, ...]
    card_verifications: tuple[CardVerification, ...]
    pattern_clusters: tuple[PatternCluster, ...]
    findings: tuple[Finding, ...]
    improvement_opportunities: tuple[ImprovementOpportunity, ...]
    artifact_candidates: tuple[ArtifactCandidate, ...]
    review_queue: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]


def run_evidence_mining(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    *,
    min_signal_score: float = MIN_SIGNAL_SCORE,
) -> EvidenceMiningResult:
    session_by_id = {session.session_id: session for session in sessions}
    all_windows: list[EvidenceWindow] = []
    all_claims: list[MicroClaim] = []
    all_cards: list[AnalysisCard] = []
    episode_count = 0
    high_signal_episode_ids: set[str] = set()
    normalized_by_session: dict[str, list[NormalizedEvent]] = {}

    for session in sessions:
        events = normalize_events(session, events_by_session.get(session.session_id, []))
        normalized_by_session[session.session_id] = events
        episodes = segment_episodes(session, events)
        episode_count += len(episodes)
        event_to_episode = {
            event_id: episode.id for episode in episodes for event_id in episode.event_ids
        }
        windows = build_evidence_windows(
            session,
            events,
            event_to_episode,
            min_signal_score=min_signal_score,
        )
        all_windows.extend(windows)
        high_signal_episode_ids.update(window.episode_id for window in windows)
        claims = build_micro_claims(windows, events)
        all_claims.extend(claims)
        all_cards.extend(build_analysis_cards(windows, claims))

    verifications = tuple(verify_analysis_card(card, all_claims) for card in all_cards)
    accepted_card_ids = {
        verification.card_id
        for verification in verifications
        if verification.verdict in {"pass", "weaken"}
    }
    clusters = build_pattern_clusters(
        [card for card in all_cards if card.card_id in accepted_card_ids],
        session_by_id,
    )
    review_queue = tuple(
        _review_item(cluster)
        for cluster in clusters
        if cluster.readiness in {"ready_for_review", "ready_for_draft"}
    )
    diagnostics = build_diagnostic_bundle(
        sessions,
        normalized_by_session,
        all_cards,
        list(verifications),
        list(clusters),
    )
    coverage = {
        "sessions": len(sessions),
        "episodes": episode_count,
        "high_signal_episodes": len(high_signal_episode_ids),
        "evidence_refs": len(diagnostics.evidence_refs),
        "evidence_windows": len(all_windows),
        "micro_claims": len(all_claims),
        "analysis_cards": len(all_cards),
        "verifier_rejected": sum(
            1 for verification in verifications if verification.verdict == "reject"
        ),
        "clusters": len(clusters),
        "findings": len(diagnostics.findings),
        "improvement_opportunities": len(diagnostics.improvement_opportunities),
        "artifact_candidates": len(diagnostics.artifact_candidates),
        "ready_for_review_clusters": sum(
            1 for cluster in clusters if cluster.readiness == "ready_for_review"
        ),
        "ready_for_draft_clusters": sum(
            1 for cluster in clusters if cluster.readiness == "ready_for_draft"
        ),
    }
    return EvidenceMiningResult(
        cost_ledger=diagnostics.cost_ledger,
        evidence_refs=diagnostics.evidence_refs,
        evidence_windows=tuple(all_windows),
        micro_claims=tuple(all_claims),
        analysis_cards=tuple(all_cards),
        card_verifications=verifications,
        pattern_clusters=tuple(clusters),
        findings=diagnostics.findings,
        improvement_opportunities=diagnostics.improvement_opportunities,
        artifact_candidates=diagnostics.artifact_candidates,
        review_queue=review_queue,
        coverage=coverage,
    )


def build_evidence_windows(
    session: SessionRecord,
    events: list[NormalizedEvent],
    event_to_episode: dict[str, str],
    *,
    min_signal_score: float = MIN_SIGNAL_SCORE,
) -> list[EvidenceWindow]:
    scored = [
        (index, signal_score(event), center_signal_type(event))
        for index, event in enumerate(events)
    ]
    ranges = [
        {
            "start": max(0, index - WINDOW_RADIUS),
            "end": min(len(events) - 1, index + WINDOW_RADIUS),
            "center": index,
            "score": score,
            "signal_type": signal_type,
        }
        for index, score, signal_type in scored
        if score >= min_signal_score
    ]
    if not ranges:
        return []

    merged: list[dict[str, Any]] = []
    for item in ranges:
        if not merged or int(item["start"]) > int(merged[-1]["end"]) + 1:
            merged.append(dict(item))
            continue
        previous = merged[-1]
        previous["end"] = max(int(previous["end"]), int(item["end"]))
        if float(item["score"]) > float(previous["score"]):
            previous["center"] = item["center"]
            previous["score"] = item["score"]
            previous["signal_type"] = item["signal_type"]

    windows: list[EvidenceWindow] = []
    for index, item in enumerate(merged, start=1):
        window_events = events[int(item["start"]) : int(item["end"]) + 1]
        center = events[int(item["center"])]
        compact_text = _compact_window_text(window_events)
        episode_id = event_to_episode.get(center.id, f"ep_{session.session_id}_unknown")
        windows.append(
            EvidenceWindow(
                window_id=_stable_id("window", session.session_id, index, center.id),
                session_id=session.session_id,
                episode_id=episode_id,
                center_event_id=center.id,
                center_signal_type=str(item["signal_type"]),
                event_ids=tuple(event.id for event in window_events),
                compact_text=compact_text,
                token_estimate=max(1, len(compact_text) // 4),
                signal_score=round(float(item["score"]), 2),
            )
        )
    return windows


def build_micro_claims(
    windows: list[EvidenceWindow],
    events: list[NormalizedEvent],
) -> list[MicroClaim]:
    by_id = {event.id: event for event in events}
    claims: list[MicroClaim] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for window in windows:
        for event_id in window.event_ids:
            event = by_id.get(event_id)
            if event is None:
                continue
            claim = _claim_for_event(window, event)
            if claim is None:
                continue
            key = (claim.claim_type, claim.claim, claim.supporting_event_ids)
            if key in seen:
                continue
            seen.add(key)
            claims.append(claim)
    return claims


def build_analysis_cards(
    windows: list[EvidenceWindow],
    claims: list[MicroClaim],
) -> list[AnalysisCard]:
    claims_by_window: dict[str, list[MicroClaim]] = defaultdict(list)
    for claim in claims:
        claims_by_window[claim.window_id].append(claim)

    cards: list[AnalysisCard] = []
    for window in windows:
        window_claims = claims_by_window.get(window.window_id, [])
        if not window_claims:
            continue
        card_type = _card_type_for_window(window, window_claims)
        title = _card_title(card_type)
        observed_fact = _observed_fact(window_claims)
        inferred_problem = _inferred_problem(card_type)
        candidate_destination, secondary = _destination_for_card_type(card_type)
        evidence_claim_ids = tuple(claim.claim_id for claim in window_claims)
        evidence_event_ids = tuple(
            dict.fromkeys(
                event_id for claim in window_claims for event_id in claim.supporting_event_ids
            )
        )
        confidence = round(sum(claim.confidence for claim in window_claims) / len(window_claims), 2)
        quality_score = _quality_score(window, window_claims, observed_fact, inferred_problem)
        cards.append(
            AnalysisCard(
                card_id=_stable_id("card", window.window_id, card_type, observed_fact),
                window_id=window.window_id,
                episode_id=window.episode_id,
                session_id=window.session_id,
                card_type=card_type,
                title=title,
                observed_fact=observed_fact,
                inferred_problem=inferred_problem,
                candidate_destination=candidate_destination,
                secondary_destinations=secondary,
                evidence_claim_ids=evidence_claim_ids,
                evidence_event_ids=evidence_event_ids,
                confidence=confidence,
                quality_score=quality_score,
                artifact_readiness="needs_more_evidence",
            )
        )
    return cards


def verify_analysis_card(card: AnalysisCard, claims: list[MicroClaim]) -> CardVerification:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    problems: list[dict[str, str]] = []
    if not card.evidence_claim_ids or not card.evidence_event_ids:
        problems.append(
            {"type": "missing_evidence", "detail": "card 没有 claim 或 event 证据引用。"}
        )
    missing_claims = [
        claim_id for claim_id in card.evidence_claim_ids if claim_id not in claims_by_id
    ]
    if missing_claims:
        problems.append({"type": "missing_claim", "detail": ", ".join(missing_claims[:3])})
    if _contains_secret_like_text(card.observed_fact) or _contains_secret_like_text(
        card.inferred_problem
    ):
        problems.append(
            {"type": "privacy_risk", "detail": "card 文本包含疑似 secret/token/private URL。"}
        )
    if "经常" in card.inferred_problem or "跨 repo" in card.inferred_problem:
        problems.append(
            {"type": "unsupported_inference", "detail": "card 不应从单个 window 推断频率或范围。"}
        )

    if any(problem["type"] in {"missing_evidence", "missing_claim"} for problem in problems):
        verdict = "reject"
        revised_confidence = min(card.confidence, 0.25)
        reason = "缺少可审计证据链。"
    elif problems:
        verdict = "weaken"
        revised_confidence = max(0.1, round(card.confidence - 0.2, 2))
        reason = "证据存在，但需要降低置信度或人工复查。"
    elif card.quality_score < 6:
        verdict = "weaken"
        revised_confidence = max(0.1, round(card.confidence - 0.1, 2))
        reason = "证据链存在，但质量分不足以直接升级。"
    else:
        verdict = "pass"
        revised_confidence = card.confidence
        reason = "card 被 micro claims 和 event ids 支持。"

    return CardVerification(
        verification_id=_stable_id("verification", card.card_id, verdict),
        card_id=card.card_id,
        verdict=verdict,
        problems=tuple(problems),
        revised_confidence=revised_confidence,
        revised_destination=None,
        reason=reason,
    )


def build_pattern_clusters(
    cards: list[AnalysisCard],
    session_by_id: dict[str, SessionRecord],
) -> list[PatternCluster]:
    buckets: dict[str, list[AnalysisCard]] = defaultdict(list)
    for card in cards:
        if card.card_type == "ignore" or card.candidate_destination == "ignore":
            continue
        buckets[_cluster_key(card)].append(card)

    clusters: list[PatternCluster] = []
    for key, bucket in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0])):
        first = bucket[0]
        destinations = tuple(
            dict.fromkeys(
                destination
                for card in bucket
                for destination in (card.candidate_destination, *card.secondary_destinations)
            )
        )
        sessions = [session_by_id.get(card.session_id) for card in bucket]
        affected_repos = tuple(
            sorted(
                {
                    str(session.project_path or session.cwd or "(unknown)")
                    for session in sessions
                    if session is not None
                }
            )
        )
        time_range = _cluster_time_range([session for session in sessions if session is not None])
        frequency = len(bucket)
        readiness = _cluster_readiness(first.card_type, frequency)
        confidence = round(sum(card.confidence for card in bucket) / frequency, 2)
        priority_score = _priority_score(first.card_type, frequency, affected_repos, readiness)
        clusters.append(
            PatternCluster(
                cluster_id=_stable_id("cluster", key),
                cluster_type=first.card_type,
                title=_cluster_title(first.card_type),
                common_pattern=_cluster_pattern(first.card_type),
                recommended_destinations=destinations,
                frequency=frequency,
                affected_repos=affected_repos,
                time_range=time_range,
                priority_score=priority_score,
                readiness=readiness,
                confidence=confidence,
                card_ids=tuple(card.card_id for card in bucket),
            )
        )
    return clusters


def write_mining_outputs(result: EvidenceMiningResult, directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cost_ledger_path = directory / "cost_ledger.json"
    evidence_refs_path = directory / "evidence_refs.jsonl"
    cards_path = directory / "cards.jsonl"
    clusters_path = directory / "clusters.json"
    findings_path = directory / "findings.json"
    opportunities_path = directory / "opportunities.json"
    artifact_candidates_path = directory / "artifact_candidates.json"
    review_queue_path = directory / "review_queue.json"
    coverage_path = directory / "coverage_report.md"

    cost_ledger_path.write_text(
        json.dumps(
            _payload(result.cost_ledger),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence_refs_path.write_text(
        "\n".join(
            json.dumps(_payload(ref), ensure_ascii=False, sort_keys=True)
            for ref in result.evidence_refs
        )
        + ("\n" if result.evidence_refs else ""),
        encoding="utf-8",
    )
    cards_path.write_text(
        "\n".join(
            json.dumps(_payload(card), ensure_ascii=False, sort_keys=True)
            for card in result.analysis_cards
        )
        + ("\n" if result.analysis_cards else ""),
        encoding="utf-8",
    )
    clusters_path.write_text(
        json.dumps(
            [_payload(cluster) for cluster in result.pattern_clusters],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    findings_path.write_text(
        json.dumps(
            [_payload(finding) for finding in result.findings],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    opportunities_path.write_text(
        json.dumps(
            [_payload(opportunity) for opportunity in result.improvement_opportunities],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    artifact_candidates_path.write_text(
        json.dumps(
            [_payload(candidate) for candidate in result.artifact_candidates],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    review_queue_path.write_text(
        json.dumps(list(result.review_queue), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    coverage_path.write_text(render_coverage_report(result), encoding="utf-8")
    return {
        "cost_ledger": cost_ledger_path,
        "evidence_refs": evidence_refs_path,
        "cards": cards_path,
        "clusters": clusters_path,
        "findings": findings_path,
        "opportunities": opportunities_path,
        "artifact_candidates": artifact_candidates_path,
        "review_queue": review_queue_path,
        "coverage_report": coverage_path,
    }


def render_coverage_report(result: EvidenceMiningResult) -> str:
    coverage = result.coverage
    lines = [
        "# Evidence Mining Coverage",
        "",
        f"- Generated: {_now_utc()}",
        f"- Sessions: {coverage['sessions']}",
        f"- Episodes: {coverage['episodes']}",
        f"- High-signal episodes: {coverage['high_signal_episodes']}",
        f"- Evidence refs: {coverage['evidence_refs']}",
        f"- Evidence windows: {coverage['evidence_windows']}",
        f"- Micro claims: {coverage['micro_claims']}",
        f"- Analysis cards: {coverage['analysis_cards']}",
        f"- Verifier rejected cards: {coverage['verifier_rejected']}",
        f"- Clusters: {coverage['clusters']}",
        f"- Findings: {coverage['findings']}",
        f"- Improvement opportunities: {coverage['improvement_opportunities']}",
        f"- Artifact candidates: {coverage['artifact_candidates']}",
        f"- Ready for review clusters: {coverage['ready_for_review_clusters']}",
        f"- Ready for draft clusters: {coverage['ready_for_draft_clusters']}",
        "",
        "## Cost Ledger",
        "",
        f"- Failed commands: {result.cost_ledger.failed_commands}",
        f"- Repeated commands: {result.cost_ledger.repeated_commands}",
        f"- Repeated file reads: {result.cost_ledger.repeated_file_reads}",
        f"- User corrections: {result.cost_ledger.user_corrections}",
        f"- Verification followups: {result.cost_ledger.verification_followups}",
        "",
        "## Top Opportunities",
        "",
    ]
    if not result.improvement_opportunities:
        lines.append("- No improvement opportunities generated.")
    else:
        for opportunity in result.improvement_opportunities[:8]:
            lines.append(
                f"- `{opportunity.id}` {opportunity.title} "
                f"-> `{opportunity.recommended_mechanism}` "
                f"(preventability={opportunity.preventability}, "
                f"recurrence={opportunity.recurrence})"
            )
    lines.extend([
        "",
        "## Top Clusters",
        "",
    ])
    if not result.pattern_clusters:
        lines.append("- No clusters generated.")
    else:
        for cluster in sorted(
            result.pattern_clusters,
            key=lambda item: (-item.priority_score, -item.frequency, item.title),
        )[:20]:
            lines.append(
                f"- `{cluster.cluster_id}` {cluster.title} "
                f"(frequency={cluster.frequency}, readiness={cluster.readiness}, "
                f"priority={cluster.priority_score})"
            )
    lines.append("")
    return "\n".join(lines)


def signal_score(event: NormalizedEvent) -> float:
    text = _event_text(event)
    score = 0.0
    score += _weighted_term_score(text, USER_CORRECTION_WEIGHTS)
    score += _weighted_term_score(text, VALIDATION_WEIGHTS)
    score += _weighted_term_score(text, SEDIMENTATION_WEIGHTS)
    score += _weighted_term_score(text, EXTERNAL_CONTEXT_WEIGHTS)
    score += _weighted_term_score(text, SAFETY_WEIGHTS)
    if event.is_user_correction:
        score += 8
    if event.is_error:
        score += 4
    if event.is_test:
        score += 2
    if event.command:
        score += 1
    return round(score, 2)


def center_signal_type(event: NormalizedEvent) -> str:
    text = _event_text(event)
    lowered = text.lower()
    if _contains_weighted(text, SAFETY_WEIGHTS):
        return "safety_boundary"
    if _looks_like_wrong_command(text):
        return "wrong_command"
    if _looks_like_validation_gap(text):
        return "validation_gap"
    if _contains_weighted(text, EXTERNAL_CONTEXT_WEIGHTS):
        return "external_context"
    if any(term in lowered for term in PROJECT_CONVENTION_TERMS):
        return "project_convention"
    if event.is_user_correction:
        return "user_correction"
    if _looks_like_successful_pattern(text):
        return "successful_pattern"
    return "unclear"


def _claim_for_event(window: EvidenceWindow, event: NormalizedEvent) -> MicroClaim | None:
    text = _event_text(event)
    if event.role == "user" and event.is_user_correction:
        if _looks_like_validation_gap(text):
            claim_type = "validation_missing"
            claim = "用户指出 assistant 在声明进展前缺少日志查看或目标测试复现。"
        else:
            claim_type = "user_said"
            claim = "用户对 assistant 的执行方向或结果提出了纠正。"
        return _claim(window, event, claim_type, claim, confidence=0.92)

    if event.command:
        status = event.status or "unknown"
        if status == "failed":
            claim_type = "tool_failed"
        elif event.is_test:
            claim_type = "validation_performed"
        else:
            claim_type = "tool_ran"
        claim = f"assistant 运行了命令 `{redact_text(event.command)}`，结果为 {status}。"
        return _claim(window, event, claim_type, claim, confidence=0.88)

    if event.role == "assistant" and _looks_like_completion_claim(text):
        claim = "assistant 表达了完成、修复或继续推进的状态。"
        return _claim(window, event, "assistant_did", claim, confidence=0.72)

    if event.is_error:
        claim = "窗口内出现失败、错误或异常信号。"
        return _claim(window, event, "tool_failed", claim, confidence=0.78)

    return None


def _claim(
    window: EvidenceWindow,
    event: NormalizedEvent,
    claim_type: str,
    claim: str,
    *,
    confidence: float,
) -> MicroClaim:
    quote = _short_quote(_event_text(event))
    return MicroClaim(
        claim_id=_stable_id("claim", window.window_id, event.id, claim_type, claim),
        window_id=window.window_id,
        episode_id=window.episode_id,
        session_id=window.session_id,
        claim_type=claim_type,
        claim=claim,
        supporting_event_ids=(event.id,),
        quote=quote,
        confidence=confidence,
    )


def _compact_window_text(events: list[NormalizedEvent]) -> str:
    lines = []
    for event in events:
        label = _compact_label(event)
        command = f" command=`{redact_text(event.command)}`" if event.command else ""
        lines.append(f"[{label}] {event.source_ref}{command} {redact_text(_event_text(event))}")
    return "\n".join(lines)


def _compact_label(event: NormalizedEvent) -> str:
    if event.role == "user" and event.is_user_correction:
        return "USER_CORRECTION"
    if event.command:
        return "COMMAND_RUN"
    if event.role == "user":
        return "USER"
    if event.role == "assistant":
        return "ASSISTANT"
    if event.role == "tool":
        return "TOOL"
    return event.role.upper() if event.role else "EVENT"


def _card_type_for_window(window: EvidenceWindow, claims: list[MicroClaim]) -> str:
    claim_types = {claim.claim_type for claim in claims}
    if "validation_missing" in claim_types:
        return "validation_gap"
    if window.center_signal_type != "unclear" and window.center_signal_type in {
        "wrong_command",
        "validation_gap",
        "safety_boundary",
        "project_convention",
        "external_context",
        "successful_pattern",
    }:
        return window.center_signal_type
    if "user_said" in claim_types:
        return "user_correction"
    return "ignore"


def _card_title(card_type: str) -> str:
    return {
        "validation_gap": "CI/测试修复缺少目标验证闭环",
        "wrong_command": "验证命令与用户指出的失败命令不一致",
        "safety_boundary": "高风险命令或敏感文件需要确定性边界",
        "project_convention": "项目约定没有被稳定遵守",
        "external_context": "外部上下文需要结构化接入",
        "successful_pattern": "成功流程可沉淀为复用工作流",
        "user_correction": "用户纠正暴露执行偏差",
        "ignore": "证据不足，暂不进入归并",
    }.get(card_type, "高信号交互需要复查")


def _observed_fact(claims: list[MicroClaim]) -> str:
    return _truncate("；".join(claim.claim for claim in claims), 520)


def _inferred_problem(card_type: str) -> str:
    return {
        "validation_gap": "修复或验证流程缺少先定位失败日志、失败命令和目标复现的固定步骤。",
        "wrong_command": "验证命令选择缺少对用户或 CI 指定失败命令的约束。",
        "safety_boundary": "危险命令、敏感文件或生产操作缺少可拦截的确定性规则。",
        "project_convention": "项目局部约定没有进入可检索的 repo 或目录级指导。",
        "external_context": "外部系统信息反复依赖人工复制，缺少结构化上下文入口。",
        "successful_pattern": "高质量成功流程还没有被拆成可复用 checklist/runbook。",
        "user_correction": "assistant 的执行路径和用户真实要求之间存在可复查偏差。",
        "ignore": "当前窗口缺少足够明确的用户纠正、验证缺口或可执行改进信号。",
    }.get(card_type, "窗口内存在需要人工复查的高信号行为。")


def _destination_for_card_type(card_type: str) -> tuple[str, tuple[str, ...]]:
    if card_type == "validation_gap":
        return "skill", ("eval", "repo_agents_md")
    if card_type == "wrong_command":
        return "eval", ("skill", "repo_agents_md")
    if card_type == "safety_boundary":
        return "hook", ("rule", "global_agents_md")
    if card_type == "project_convention":
        return "repo_agents_md", ("directory_agents_md", "eval")
    if card_type == "external_context":
        return "mcp", ("skill",)
    if card_type == "successful_pattern":
        return "skill", ("eval",)
    if card_type == "ignore":
        return "ignore", ()
    return "repo_agents_md", ("eval",)


def _quality_score(
    window: EvidenceWindow,
    claims: list[MicroClaim],
    observed_fact: str,
    inferred_problem: str,
) -> float:
    evidence_strength = 2 if any(claim.quote for claim in claims) else 1
    if len(claims) >= 2:
        evidence_strength += 1
    specificity = (
        2
        if any(
            "`" in claim.claim or "CI" in observed_fact or "test" in observed_fact.lower()
            for claim in claims
        )
        else 1
    )
    actionability = (
        2
        if window.center_signal_type
        in {"validation_gap", "wrong_command", "safety_boundary", "project_convention"}
        else 1
    )
    recurrence_potential = 1.5 if actionability >= 2 else 1
    confidence = sum(claim.confidence for claim in claims) / max(1, len(claims))
    unsupported_penalty = 1 if "经常" in inferred_problem else 0
    privacy_penalty = 2 if _contains_secret_like_text(observed_fact) else 0
    score = (
        3 * evidence_strength
        + 2 * specificity
        + 2 * actionability
        + 2 * recurrence_potential
        + confidence
        - 3 * unsupported_penalty
        - 2 * privacy_penalty
    )
    return round(score, 2)


def _cluster_key(card: AnalysisCard) -> str:
    return "|".join(
        [
            card.card_type,
            card.candidate_destination,
            _canonical_pattern_key(card),
        ]
    )


def _canonical_pattern_key(card: AnalysisCard) -> str:
    if card.card_type in {"validation_gap", "wrong_command"}:
        return "targeted_validation"
    if card.card_type == "safety_boundary":
        return "deterministic_safety"
    if card.card_type == "external_context":
        return "external_context_ingestion"
    if card.card_type == "project_convention":
        return "project_convention"
    return _slug(card.title)


def _cluster_title(card_type: str) -> str:
    return {
        "validation_gap": "改完代码后目标验证步骤不稳定",
        "wrong_command": "失败命令定位和复现不稳定",
        "safety_boundary": "高风险操作缺少确定性拦截",
        "project_convention": "项目约定需要进入本地指导",
        "external_context": "外部上下文复制粘贴成本高",
        "successful_pattern": "成功工作流可沉淀",
        "user_correction": "用户纠正集中出现",
    }.get(card_type, "高信号行为模式")


def _cluster_pattern(card_type: str) -> str:
    return {
        "validation_gap": (
            "assistant 在修复或收尾时没有稳定执行目标测试、CI 日志读取或失败命令复现。"
        ),
        "wrong_command": "assistant 运行了过宽或错误的验证命令，而不是用户或 CI 指出的失败命令。",
        "safety_boundary": "部分操作可由命令、路径或敏感关键词确定性识别，应优先进入 hook/rule。",
        "project_convention": "用户纠正指向项目局部规范，适合进入 repo/directory guidance。",
        "external_context": "用户反复粘贴来自外部系统的上下文，适合评估 MCP 或结构化导入。",
        "successful_pattern": "窗口展示了可复用的多步成功流程。",
        "user_correction": "用户纠正显示 assistant 没有完全跟随任务约束。",
    }.get(card_type, "多张 evidence card 指向同类可复查行为。")


def _cluster_readiness(card_type: str, frequency: int) -> str:
    if frequency >= 3 and card_type in {"validation_gap", "wrong_command", "successful_pattern"}:
        return "ready_for_draft"
    if frequency >= 2:
        return "ready_for_review"
    return "needs_more_evidence"


def _priority_score(
    card_type: str,
    frequency: int,
    affected_repos: tuple[str, ...],
    readiness: str,
) -> float:
    frequency_score = min(frequency, 5)
    time_saved_score = (
        3 if card_type in {"validation_gap", "wrong_command", "external_context"} else 2
    )
    risk_score = 3 if card_type in {"validation_gap", "wrong_command", "safety_boundary"} else 1
    automation_score = (
        3 if card_type in {"validation_gap", "wrong_command", "safety_boundary"} else 2
    )
    cross_repo_score = 2 if len(affected_repos) > 1 else 1
    recency_score = 1
    maintenance_cost = 1 if readiness != "ready_for_draft" else 2
    ambiguity_penalty = 1 if frequency == 1 else 0
    score = (
        4 * frequency_score
        + 3 * time_saved_score
        + 3 * risk_score
        + 2 * automation_score
        + 2 * cross_repo_score
        + recency_score
        - 2 * maintenance_cost
        - 3 * ambiguity_penalty
    )
    return round(float(score), 2)


def _cluster_time_range(sessions: list[SessionRecord]) -> dict[str, str | None]:
    starts = sorted(str(session.started_at) for session in sessions if session.started_at)
    ends = sorted(
        str(session.updated_at or session.ended_at)
        for session in sessions
        if session.updated_at or session.ended_at
    )
    return {
        "start": starts[0] if starts else None,
        "end": ends[-1] if ends else None,
    }


def _review_item(cluster: PatternCluster) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "title": cluster.title,
        "frequency": cluster.frequency,
        "readiness": cluster.readiness,
        "priority_score": cluster.priority_score,
        "recommended_destinations": list(cluster.recommended_destinations),
        "card_ids": list(cluster.card_ids),
    }


def _event_text(event: NormalizedEvent) -> str:
    return str(event.user_input_text or event.excerpt or "")


def _weighted_term_score(text: str, weights: dict[str, int]) -> int:
    lowered = text.lower()
    return sum(weight for term, weight in weights.items() if term.lower() in lowered)


def _contains_weighted(text: str, weights: dict[str, int]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in weights)


def _looks_like_wrong_command(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("不是", "wrong", "not ")) and any(
        term in lowered for term in ("test", "pytest", "npm", "pnpm", "ci")
    )


def _looks_like_validation_gap(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered for term in ("没跑", "未运行", "还没看 ci", "ci 失败", "failed", "测试失败")
    )


def _looks_like_successful_pattern(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered for term in ("这样就对了", "这次可以", "成功", "works well", "good pattern")
    )


def _looks_like_completion_claim(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("已修", "修好了", "完成", "done", "fixed", "我会"))


def _short_quote(text: str, limit: int = 96) -> str | None:
    cleaned = " ".join(redact_text(text).split())
    if not cleaned:
        return None
    return _truncate(cleaned, limit)


def _contains_secret_like_text(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in ("secret", "token", "api_key", "private key")):
        return True
    return bool(re.search(r"https://[^\s]+/(?:private|internal|token)[^\s]*", lowered))


def _payload(item: Any) -> dict[str, Any]:
    return asdict(item)


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return f"{prefix}_{digest.hexdigest()[:16]}"


def _slug(value: str) -> str:
    lowered = value.lower()
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", lowered).strip("-")
    return cleaned[:80] or "cluster"


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(redact_text(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
