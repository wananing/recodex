from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .analysis_workflow import NormalizedEvent
from .models import (
    ArtifactCandidate,
    CostLedger,
    EvidenceRef,
    Finding,
    ImprovementOpportunity,
    SessionRecord,
)
from .privacy import redact_text


@dataclass(frozen=True)
class DiagnosticBundle:
    cost_ledger: CostLedger
    evidence_refs: tuple[EvidenceRef, ...]
    findings: tuple[Finding, ...]
    improvement_opportunities: tuple[ImprovementOpportunity, ...]
    artifact_candidates: tuple[ArtifactCandidate, ...]


def build_diagnostic_bundle(
    sessions: list[SessionRecord],
    normalized_by_session: dict[str, list[NormalizedEvent]],
    cards: list[Any],
    verifications: list[Any],
    clusters: list[Any],
) -> DiagnosticBundle:
    session_by_id = {session.session_id: session for session in sessions}
    referenced_event_ids = {
        str(event_id)
        for card in cards
        for event_id in getattr(card, "evidence_event_ids", ())
        if event_id
    }
    evidence_refs = build_evidence_refs(
        session_by_id,
        normalized_by_session,
        referenced_event_ids=referenced_event_ids,
    )
    evidence_ref_by_event_id = {ref.event_id: ref.id for ref in evidence_refs}
    events_by_id = {
        event.id: event
        for events in normalized_by_session.values()
        for event in events
    }
    cost_ledger = build_cost_ledger(sessions, normalized_by_session, cards)
    findings = build_findings(
        cards,
        verifications,
        evidence_ref_by_event_id,
        events_by_id,
        cost_ledger,
    )
    opportunities = build_improvement_opportunities(findings, clusters)
    artifacts = build_artifact_candidates(opportunities)
    return DiagnosticBundle(
        cost_ledger=cost_ledger,
        evidence_refs=tuple(evidence_refs),
        findings=tuple(findings),
        improvement_opportunities=tuple(opportunities),
        artifact_candidates=tuple(artifacts),
    )


def build_evidence_refs(
    session_by_id: dict[str, SessionRecord],
    normalized_by_session: dict[str, list[NormalizedEvent]],
    *,
    referenced_event_ids: set[str],
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for session_id, events in sorted(normalized_by_session.items()):
        session = session_by_id.get(session_id)
        source_file = session.source_path if session is not None else session_id
        for event in events:
            if event.id not in referenced_event_ids:
                continue
            quote_source = event.user_input_text or event.excerpt or event.command or ""
            quote = _truncate(redact_text(quote_source), 260)
            refs.append(
                EvidenceRef(
                    id=_stable_id("evref", session_id, event.id, event.source_ref, quote),
                    session_id=session_id,
                    event_id=event.id,
                    source_file=redact_text(source_file),
                    byte_start=event.byte_start or 0,
                    byte_end=event.byte_end or 0,
                    quote=quote,
                    reason="Supports an audited workflow finding.",
                    content_hash=_hash_text(quote),
                )
            )
    return refs


def build_cost_ledger(
    sessions: list[SessionRecord],
    normalized_by_session: dict[str, list[NormalizedEvent]],
    cards: list[Any],
) -> CostLedger:
    events = [
        event
        for session_events in normalized_by_session.values()
        for event in session_events
    ]
    commands = [event for event in events if event.command]
    command_counts = Counter(redact_text(event.command or "") for event in commands)
    repeated_commands = sum(count - 1 for count in command_counts.values() if count > 1)
    read_paths = [
        path
        for event in commands
        if _looks_like_read_command(event.command or "")
        for path in event.file_refs
    ]
    path_counts = Counter(read_paths)
    repeated_file_reads = sum(count - 1 for count in path_counts.values() if count > 1)
    failed_commands = sum(1 for event in commands if event.status == "failed")
    user_corrections = sum(1 for event in events if event.is_user_correction)
    reverted_changes = sum(1 for event in events if _looks_like_rework(event))
    verification_followups = sum(
        1
        for event in events
        if (
            event.role == "user"
            and event.is_user_correction
            and _looks_like_verification_followup(event)
        )
    )
    ignored_tool_results = sum(
        1 for card in cards if getattr(card, "card_type", "") == "ignored_tool_result"
    )
    clear_types = {"validation_gap", "wrong_command", "user_correction"}
    clearly_avoidable_events = _card_event_ids(cards, clear_types)
    potentially_avoidable_events = _card_event_ids(
        cards,
        {
            "project_convention",
            "external_context",
            "safety_boundary",
            "successful_pattern",
        },
    )
    extra_turns = user_corrections + repeated_commands + verification_followups
    return CostLedger(
        total_duration_seconds=_total_duration_seconds(sessions),
        extra_turns=extra_turns,
        failed_commands=failed_commands,
        repeated_commands=repeated_commands,
        repeated_file_reads=repeated_file_reads,
        user_corrections=user_corrections,
        reverted_changes=reverted_changes,
        ignored_tool_results=ignored_tool_results,
        verification_followups=verification_followups,
        clearly_avoidable_events=tuple(clearly_avoidable_events),
        potentially_avoidable_events=tuple(potentially_avoidable_events),
    )


def build_findings(
    cards: list[Any],
    verifications: list[Any],
    evidence_ref_by_event_id: dict[str, str],
    events_by_id: dict[str, NormalizedEvent],
    cost_ledger: CostLedger,
) -> list[Finding]:
    verification_by_card = {
        str(getattr(verification, "card_id", "")): verification
        for verification in verifications
    }
    findings: list[Finding] = []
    for card in cards:
        card_type = str(getattr(card, "card_type", ""))
        if card_type in {"ignore", "successful_pattern"}:
            continue
        verification = verification_by_card.get(str(getattr(card, "card_id", "")))
        if verification is None or getattr(verification, "verdict", "") not in {"pass", "weaken"}:
            continue
        event_ids = tuple(str(event_id) for event_id in getattr(card, "evidence_event_ids", ()))
        evidence_refs = tuple(
            evidence_ref_by_event_id[event_id]
            for event_id in event_ids
            if event_id in evidence_ref_by_event_id
        )
        if not evidence_refs:
            continue
        confidence = min(
            float(getattr(card, "confidence", 0.0) or 0.0),
            float(getattr(verification, "revised_confidence", 0.0) or 0.0),
        )
        findings.append(
            Finding(
                id=_stable_id("finding", getattr(card, "card_id", ""), card_type),
                title=redact_text(str(getattr(card, "title", "") or _finding_title(card_type))),
                category=_finding_category(card_type),
                severity=_finding_severity(card_type),
                confidence=round(confidence, 2),
                observation=redact_text(str(getattr(card, "observed_fact", ""))),
                observed_cost=_observed_cost(card_type, event_ids, events_by_id, cost_ledger),
                cause=_finding_cause(card_type),
                responsibility_layers=_responsibility_layers(card_type),
                impact=redact_text(str(getattr(card, "inferred_problem", ""))),
                recommendation=_finding_recommendation(card_type),
                evidence_refs=evidence_refs,
                source_card_ids=(str(getattr(card, "card_id", "")),),
            )
        )
    return sorted(findings, key=_finding_sort_key)


def build_improvement_opportunities(
    findings: list[Finding],
    clusters: list[Any],
) -> list[ImprovementOpportunity]:
    cluster_by_type = {
        str(getattr(cluster, "cluster_type", "")): cluster
        for cluster in clusters
    }
    opportunities: list[ImprovementOpportunity] = []
    for finding in findings:
        card_type = _card_type_from_category(finding.category)
        cluster = cluster_by_type.get(card_type)
        frequency = int(getattr(cluster, "frequency", 1) or 1) if cluster is not None else 1
        readiness = str(
            getattr(cluster, "readiness", "needs_more_evidence") or "needs_more_evidence"
        )
        mechanism, routing_reason = _mechanism_route(card_type, frequency, readiness)
        best_action = _best_action(card_type, mechanism)
        opportunities.append(
            ImprovementOpportunity(
                id=_stable_id("opp", finding.id, mechanism),
                source_finding_ids=(finding.id,),
                title=_opportunity_title(card_type),
                problem=finding.observation,
                cause=finding.cause,
                recurrence=_recurrence_label(frequency),
                preventability=_preventability(card_type),
                impact=finding.impact,
                confidence=finding.confidence,
                best_action=best_action,
                recommended_mechanism=mechanism,
                routing_reason=routing_reason,
                suggested_target=_suggested_target(mechanism),
                evidence_refs=finding.evidence_refs,
            )
        )
    return sorted(opportunities, key=_opportunity_sort_key)


def build_artifact_candidates(
    opportunities: list[ImprovementOpportunity],
) -> list[ArtifactCandidate]:
    candidates: list[ArtifactCandidate] = []
    for opportunity in opportunities:
        mechanism = opportunity.recommended_mechanism
        if mechanism in {"user_advice", "ignore"}:
            continue
        candidates.append(
            ArtifactCandidate(
                id=_stable_id("artifact", opportunity.id, mechanism),
                opportunity_id=opportunity.id,
                artifact_type=mechanism,
                target_path=opportunity.suggested_target,
                proposed_content=_proposed_content(opportunity),
                scope=_artifact_scope(mechanism),
                rationale=opportunity.best_action,
                risks=_artifact_risks(mechanism),
                validation_plan=_artifact_validation_plan(mechanism),
                status="proposed",
            )
        )
    return candidates


def _observed_cost(
    card_type: str,
    event_ids: tuple[str, ...],
    events_by_id: dict[str, NormalizedEvent],
    cost_ledger: CostLedger,
) -> dict[str, Any]:
    events = [events_by_id[event_id] for event_id in event_ids if event_id in events_by_id]
    failed_commands = sum(1 for event in events if event.command and event.status == "failed")
    user_corrections = sum(1 for event in events if event.is_user_correction)
    command_count = sum(1 for event in events if event.command)
    cost = {
        "failed_commands": failed_commands,
        "user_corrections": user_corrections,
        "commands_in_evidence": command_count,
    }
    if card_type == "validation_gap":
        cost["verification_followups"] = max(
            1,
            user_corrections,
            cost_ledger.verification_followups,
        )
    if card_type == "wrong_command":
        cost["repeated_or_wrong_commands"] = max(1, command_count, cost_ledger.repeated_commands)
    return cost


def _finding_category(card_type: str) -> str:
    return {
        "validation_gap": "verification_cost_transferred_to_user",
        "wrong_command": "wrong_validation_entry",
        "user_correction": "high_value_user_intervention_late",
        "project_convention": "stable_project_context_late",
        "external_context": "dynamic_context_missing",
        "safety_boundary": "safety_boundary_missing",
    }.get(card_type, "workflow_friction")


def _card_type_from_category(category: str) -> str:
    return {
        "verification_cost_transferred_to_user": "validation_gap",
        "wrong_validation_entry": "wrong_command",
        "stable_project_context_late": "project_convention",
        "high_value_user_intervention_late": "user_correction",
        "dynamic_context_missing": "external_context",
        "safety_boundary_missing": "safety_boundary",
    }.get(category, "workflow_friction")


def _finding_title(card_type: str) -> str:
    return {
        "validation_gap": "完成缺少验证证据",
        "wrong_command": "验证入口发现或选择不稳定",
        "user_correction": "高价值用户纠正发生在执行后",
        "project_convention": "稳定项目约定发现过晚",
        "external_context": "动态外部信息缺少结构化入口",
        "safety_boundary": "高风险操作缺少确定性边界",
    }.get(card_type, "协作摩擦需要复查")


def _finding_severity(card_type: str) -> str:
    if card_type in {"validation_gap", "wrong_command", "safety_boundary"}:
        return "high"
    return "medium"


def _finding_cause(card_type: str) -> str:
    return {
        "validation_gap": "完成定义没有绑定目标验证、失败命令复现或 CI 日志证据。",
        "wrong_command": "验证入口没有从用户/CI 指定的失败命令收敛到标准命令。",
        "user_correction": "任务契约与执行假设没有在关键转折点同步。",
        "project_convention": "稳定项目知识没有进入默认可检索上下文。",
        "external_context": "动态外部状态依赖人工复制，缺少可调用集成。",
        "safety_boundary": "高风险操作还没有沉淀为确定性策略或拦截机制。",
    }.get(card_type, "证据显示执行轨迹中存在可避免摩擦。")


def _responsibility_layers(card_type: str) -> tuple[str, ...]:
    return {
        "validation_gap": ("Agent", "Harness", "Project"),
        "wrong_command": ("Agent", "Project"),
        "user_correction": ("Operator", "Agent"),
        "project_convention": ("Project", "Harness"),
        "external_context": ("Environment", "Harness"),
        "safety_boundary": ("Harness", "Project"),
    }.get(card_type, ("Agent",))


def _finding_recommendation(card_type: str) -> str:
    return {
        "validation_gap": "把目标验证命令和结果作为完成前 checklist，并在高频重复后再考虑 skill。",
        "wrong_command": "优先记录并复现用户或 CI 指定的失败命令，再运行更宽的验证。",
        "user_correction": "在用户纠正后更新任务契约，并用一段短结论确认后续路径。",
        "project_convention": "把稳定目录、命令、边界和约定写入 AGENTS.md 或项目文档。",
        "external_context": "把反复依赖的外部信息接入 MCP 或结构化导入流程。",
        "safety_boundary": "把高风险路径、命令或生产操作升级为 hook/CI/policy。",
    }.get(card_type, "保留证据并人工判断是否值得沉淀。")


def _mechanism_route(card_type: str, frequency: int, readiness: str) -> tuple[str, str]:
    if card_type == "validation_gap":
        if frequency >= 3 and readiness == "ready_for_draft":
            return (
                "skill",
                f"skill gate passed: {frequency} supporting validation-gap cards are ready_for_draft.",
            )
        if frequency < 3:
            return (
                "checklist",
                "skill gate held: validation gaps need at least 3 supporting cards before skill draft.",
            )
        return (
            "checklist",
            f"skill gate held: validation-gap readiness is {readiness}, not ready_for_draft.",
        )
    if card_type == "wrong_command":
        if frequency >= 2:
            return ("script", f"script route: {frequency} wrong-command cards repeat.")
        return ("checklist", "script gate held: wrong-command signal is still single-session.")
    if card_type == "project_convention":
        return ("agents_md", "project convention route: stable context belongs in AGENTS.md.")
    if card_type == "external_context":
        return ("mcp", "external context route: dynamic information needs structured integration.")
    if card_type == "safety_boundary":
        return ("hook_or_ci", "safety route: high-risk boundary needs enforced policy.")
    if card_type == "user_correction":
        return ("user_advice", "skill gate held: user correction is a one-off collaboration signal.")
    return ("checklist", "default route: keep the improvement reviewable before automation.")


def _recommended_mechanism(card_type: str, frequency: int, readiness: str) -> str:
    return _mechanism_route(card_type, frequency, readiness)[0]


def _best_action(card_type: str, mechanism: str) -> str:
    if mechanism == "skill":
        return "在人工 review 后生成多步骤 workflow skill，包含触发条件、输入、分支和验证。"
    if mechanism == "checklist":
        return "生成完成前 checklist，先覆盖当前可验证缺口。"
    if mechanism == "script":
        return "把重复验证或排查命令固化为脚本入口。"
    if mechanism == "agents_md":
        return "把稳定项目事实写入 AGENTS.md，作为后续默认上下文。"
    if mechanism == "mcp":
        return "评估把动态外部信息接入 MCP 或其他结构化集成。"
    if mechanism == "hook_or_ci":
        return "把必须强制执行的安全或验证约束升级为 hook/CI/policy。"
    if card_type == "user_correction":
        return "将本次纠正作为一次性协作建议，不直接沉淀为长期规则。"
    return "保留证据并人工 review。"


def _opportunity_title(card_type: str) -> str:
    return {
        "validation_gap": "降低完成验证转移成本",
        "wrong_command": "稳定失败命令定位入口",
        "user_correction": "提前同步任务契约变化",
        "project_convention": "前置稳定项目知识",
        "external_context": "结构化动态外部上下文",
        "safety_boundary": "强制化高风险边界",
    }.get(card_type, "减少可避免协作摩擦")


def _recurrence_label(frequency: int) -> str:
    if frequency <= 1:
        return "single_session_signal"
    return f"{frequency}_supporting_cards"


def _preventability(card_type: str) -> str:
    if card_type in {"validation_gap", "wrong_command", "project_convention", "safety_boundary"}:
        return "high"
    if card_type in {"external_context", "user_correction"}:
        return "medium"
    return "unknown"


def _suggested_target(mechanism: str) -> str | None:
    return {
        "checklist": "docs/ai-coding-checklist.md",
        "script": "scripts/ai-review.sh",
        "agents_md": "AGENTS.md",
        "skill": "skills/<reviewed-workflow>/SKILL.md",
        "hook_or_ci": ".github/workflows/ai-review.yml",
        "mcp": "mcp/<integration>",
    }.get(mechanism)


def _proposed_content(opportunity: ImprovementOpportunity) -> str:
    return "\n".join(
        [
            f"# {opportunity.title}",
            "",
            f"Problem: {opportunity.problem}",
            "",
            f"Cause: {opportunity.cause}",
            "",
        f"Action: {opportunity.best_action}",
        "",
        f"Routing: {opportunity.routing_reason}",
        "",
        f"Evidence refs: {', '.join(opportunity.evidence_refs)}",
    ]
    )


def _artifact_scope(mechanism: str) -> str:
    if mechanism in {"agents_md", "hook_or_ci", "script"}:
        return "project"
    if mechanism == "skill":
        return "reviewed_workflow"
    if mechanism == "mcp":
        return "integration"
    return "session_or_project"


def _artifact_risks(mechanism: str) -> tuple[str, ...]:
    if mechanism == "skill":
        return ("可能把一次性 workaround 固化为长期流程。", "需要确认跨 session 重复性。")
    if mechanism == "mcp":
        return ("外部系统权限和数据脱敏需要单独审查。",)
    if mechanism == "hook_or_ci":
        return ("强制规则可能阻塞正常开发，需要人工确认阈值。",)
    return ("建议内容需要人工 review 后再应用。",)


def _artifact_validation_plan(mechanism: str) -> tuple[str, ...]:
    if mechanism == "agents_md":
        return ("观察后续 session 中相同项目事实是否更早出现。",)
    if mechanism == "checklist":
        return ("观察后续完成前是否出现验证命令和结果。",)
    if mechanism == "script":
        return ("运行脚本并确认它覆盖原始失败/验证入口。",)
    if mechanism == "skill":
        return ("用至少一个后续 session 验证该 skill 是否减少同类摩擦。",)
    if mechanism == "hook_or_ci":
        return ("在 CI/hook dry-run 中确认误报率可接受。",)
    if mechanism == "mcp":
        return ("确认 MCP 返回的信息能替代人工复制粘贴。",)
    return ("人工 review 后观察后续效果。",)


def _card_event_ids(cards: list[Any], card_types: set[str]) -> list[str]:
    ids: list[str] = []
    for card in cards:
        if getattr(card, "card_type", "") not in card_types:
            continue
        for event_id in getattr(card, "evidence_event_ids", ()):
            if event_id and event_id not in ids:
                ids.append(str(event_id))
    return ids


def _finding_sort_key(finding: Finding) -> tuple[int, float, str]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
        finding.severity,
        9,
    )
    return (severity_rank, -finding.confidence, finding.title)


def _opportunity_sort_key(opportunity: ImprovementOpportunity) -> tuple[int, float, str]:
    preventability_rank = {"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(
        opportunity.preventability,
        9,
    )
    return (preventability_rank, -opportunity.confidence, opportunity.title)


def _looks_like_read_command(command: str) -> bool:
    lowered = command.lower()
    read_prefixes = ("cat ", "sed ", "rg ", "grep ", "nl ", "head ", "tail ")
    return any(lowered.startswith(prefix) for prefix in read_prefixes)


def _looks_like_rework(event: NormalizedEvent) -> bool:
    text = f"{event.excerpt}\n{event.user_input_text or ''}".lower()
    return any(term in text for term in ("revert", "rollback", "回退", "撤销", "重写"))


def _looks_like_verification_followup(event: NormalizedEvent) -> bool:
    text = f"{event.excerpt}\n{event.user_input_text or ''}".lower()
    terms = ("没跑", "test", "pytest", "ci", "lint", "typecheck", "验证")
    return any(term in text for term in terms)


def _total_duration_seconds(sessions: list[SessionRecord]) -> int | None:
    durations: list[int] = []
    for session in sessions:
        start = _parse_datetime(session.started_at)
        end = _parse_datetime(session.updated_at or session.ended_at)
        if start is None or end is None or end < start:
            continue
        durations.append(int((end - start).total_seconds()))
    return sum(durations) if durations else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return f"{prefix}_{digest.hexdigest()[:16]}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(redact_text(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
