from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from .analysis_workflow import NormalizedEvent, normalize_events
from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text

EFFICIENCY_SCHEMA_VERSION = "recodex_efficiency_analysis_v2"

EfficiencyProblemType = Literal[
    "repeated_user_requirement",
    "project_knowledge_rediscovery",
    "repeated_workflow_orchestration",
    "repeated_command_sequence",
    "redundant_exploration",
    "hypothesis_stagnation",
    "ignored_tool_evidence",
    "scope_drift",
    "intervention_mismatch",
    "context_handoff_loss",
    "verification_debt",
    "environment_integration_friction",
]

ImprovementMechanism = Literal[
    "coaching",
    "global_instruction",
    "agents_md",
    "path_rule",
    "project_doc",
    "task_template",
    "checklist",
    "script",
    "hook",
    "ci",
    "skill",
    "mcp_integration",
    "environment_config",
    "none",
]

ResponsibilityLayer = Literal["operator", "agent", "project", "harness", "environment"]
FindingScope = Literal["within_session", "cross_session", "project", "global"]

MVP_PROBLEM_TYPES: tuple[EfficiencyProblemType, ...] = (
    "repeated_user_requirement",
    "project_knowledge_rediscovery",
    "repeated_workflow_orchestration",
    "repeated_command_sequence",
    "hypothesis_stagnation",
    "verification_debt",
)

READ_PREFIXES = ("cat ", "sed ", "rg ", "grep ", "nl ", "head ", "tail ", "less ")
PROJECT_KNOWLEDGE_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "Makefile",
    "package.json",
    "pyproject.toml",
    "README.md",
    "dashboard/package.json",
)
COMMAND_PATTERN = re.compile(
    r"\b(?:pnpm|npm|yarn|uv|python3?|pytest|make|cargo|go|mvn|gradle|ruff|tsc)"
    r"(?:\s+[\w:./=@+-]+){1,5}",
    re.I,
)


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    session_id: str
    event_id: str
    source_file: str
    line_number: int | None
    byte_start: int | None
    byte_end: int | None
    timestamp: str | None
    quote: str
    reason: str
    content_hash: str


@dataclass(frozen=True)
class ObservedCost:
    extra_turns: int | None = None
    repeated_commands: int | None = None
    failed_commands: int | None = None
    discarded_changes: int | None = None
    repeated_file_reads: int | None = None
    user_corrections: int | None = None
    tool_output_bytes: int | None = None
    validation_shifted_to_user: bool = False
    wall_time_seconds: int | None = None
    cost_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EfficiencyFinding:
    id: str
    problem_type: EfficiencyProblemType
    subtype: str | None
    scope: FindingScope
    title: str
    observation: str
    evidence_refs: tuple[str, ...]
    occurrences: int
    affected_sessions: tuple[str, ...]
    observed_cost: ObservedCost
    root_cause: str
    alternative_explanations: tuple[str, ...]
    responsibility_layers: tuple[ResponsibilityLayer, ...]
    recommendation: str
    mechanism: ImprovementMechanism
    confidence: float
    promotion_confidence: float


@dataclass(frozen=True)
class ArtifactCandidate:
    id: str
    source_finding_ids: tuple[str, ...]
    mechanism: ImprovementMechanism
    target_path: str | None
    title: str
    rationale: str
    proposed_content: str | None
    recurrence: int
    expected_benefit: str
    risks: tuple[str, ...]
    confidence: float
    status: Literal["proposed", "accepted", "rejected", "applied", "deprecated"]


@dataclass(frozen=True)
class EfficiencyAnalysisResult:
    schema_version: str
    mode: str
    sessions: tuple[str, ...]
    mvp_problem_types: tuple[EfficiencyProblemType, ...]
    findings: tuple[EfficiencyFinding, ...]
    artifact_candidates: tuple[ArtifactCandidate, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    cost_ledger: ObservedCost
    mechanism_counts: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass
class _AnalysisContext:
    sessions: list[SessionRecord]
    normalized_by_session: dict[str, list[NormalizedEvent]]
    session_by_id: dict[str, SessionRecord]
    evidence_refs_by_event_id: dict[str, EvidenceRef] = field(default_factory=dict)

    @property
    def events(self) -> list[NormalizedEvent]:
        return [
            event
            for session_events in self.normalized_by_session.values()
            for event in session_events
        ]

    def evidence_ids(self, event_ids: list[str], *, reason: str) -> tuple[str, ...]:
        ids: list[str] = []
        events_by_id = {event.id: event for event in self.events}
        for event_id in event_ids:
            event = events_by_id.get(event_id)
            if event is None:
                continue
            ref = self.evidence_refs_by_event_id.get(event_id)
            if ref is None:
                ref = _evidence_ref(self.session_by_id, event, reason=reason)
                self.evidence_refs_by_event_id[event_id] = ref
            if ref.id not in ids:
                ids.append(ref.id)
        return tuple(ids)


def run_efficiency_analysis(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    *,
    mode: Literal["quick", "deep"] = "quick",
) -> EfficiencyAnalysisResult:
    session_by_id = {session.session_id: session for session in sessions}
    normalized_by_session = {
        session.session_id: normalize_events(session, events_by_session.get(session.session_id, []))
        for session in sessions
    }
    context = _AnalysisContext(sessions, normalized_by_session, session_by_id)
    findings = [
        finding
        for finding in (
            _detect_repeated_user_requirement(context),
            _detect_project_knowledge_rediscovery(context),
            _detect_repeated_workflow_orchestration(context),
            _detect_repeated_command_sequence(context),
            _detect_hypothesis_stagnation(context),
            _detect_verification_debt(context),
        )
        if finding is not None
    ]
    findings = sorted(findings, key=_finding_sort_key)[:8]
    artifacts = tuple(
        candidate
        for candidate in (_artifact_candidate(finding) for finding in findings)
        if candidate is not None
    )
    mechanism_counts = Counter(finding.mechanism for finding in findings)
    return EfficiencyAnalysisResult(
        schema_version=EFFICIENCY_SCHEMA_VERSION,
        mode=mode,
        sessions=tuple(session.session_id for session in sessions),
        mvp_problem_types=MVP_PROBLEM_TYPES,
        findings=tuple(findings),
        artifact_candidates=artifacts,
        evidence_refs=tuple(context.evidence_refs_by_event_id.values()),
        cost_ledger=_cost_ledger(context),
        mechanism_counts=dict(mechanism_counts),
    )


def _detect_repeated_user_requirement(context: _AnalysisContext) -> EfficiencyFinding | None:
    buckets: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in context.events:
        if event.role != "user":
            continue
        key = _requirement_key(event.user_input_text or event.excerpt)
        if key:
            buckets[key].append(event)
    key, events = _largest_bucket(buckets)
    if not key or len(events) < 2:
        return None
    return _finding(
        context,
        problem_type="repeated_user_requirement",
        subtype=key,
        scope=_scope_for_events(events),
        title="相同项目要求被反复说明",
        observation="用户多次重复同一要求，说明该约束适合进入默认项目上下文。",
        events=events,
        observed_cost=ObservedCost(
            extra_turns=max(0, len(events) - 1),
            user_corrections=sum(1 for event in events if event.is_user_correction),
            cost_notes=("重复要求增加了用户监督和纠偏轮次。",),
        ),
        root_cause="稳定项目约束没有进入 AGENTS.md 或等效 Harness 上下文。",
        alternative_explanations=("用户可能是在强调高风险约束，而不是要求长期沉淀。",),
        responsibility_layers=("project", "harness"),
        recommendation="把该稳定要求沉淀到 AGENTS.md；如果只适用于目录，再拆成路径规则。",
        mechanism="agents_md",
        confidence=0.84,
        promotion_confidence=0.72,
    )


def _detect_project_knowledge_rediscovery(
    context: _AnalysisContext,
) -> EfficiencyFinding | None:
    buckets: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in context.events:
        if not event.command or not _looks_like_read_command(event.command):
            continue
        for target in _read_targets(event.command):
            buckets[target].append(event)
    target, events = _largest_bucket(buckets)
    if not target or len(events) < 2:
        return None
    return _finding(
        context,
        problem_type="project_knowledge_rediscovery",
        subtype=target,
        scope=_scope_for_events(events),
        title="稳定项目知识被反复重新发现",
        observation=f"`{target}` 被多次读取来恢复项目入口或规则。",
        events=events,
        observed_cost=ObservedCost(
            repeated_file_reads=max(1, len(events) - 1),
            extra_turns=max(0, len({event.session_id for event in events}) - 1),
            cost_notes=("测试入口、项目规则或工作区结构适合前置到项目上下文。",),
        ),
        root_cause="稳定项目事实依赖每次会话重新探索，而不是由项目指南或元数据提供。",
        alternative_explanations=("项目最近变化时，重新读取配置可能是必要探索。",),
        responsibility_layers=("project", "harness"),
        recommendation="把标准入口、常用命令和关键目录写入 AGENTS.md 或项目开发文档。",
        mechanism="agents_md",
        confidence=0.78,
        promotion_confidence=0.68,
    )


def _detect_repeated_workflow_orchestration(
    context: _AnalysisContext,
) -> EfficiencyFinding | None:
    buckets: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in context.events:
        if event.role != "user":
            continue
        tokens = _workflow_tokens(event.user_input_text or event.excerpt)
        if len(tokens) >= 3:
            buckets["->".join(tokens)].append(event)
    key, events = _largest_bucket(buckets)
    if not key or len(events) < 2:
        return None
    mechanism: ImprovementMechanism = "skill" if _workflow_needs_judgment(events) else "checklist"
    return _finding(
        context,
        problem_type="repeated_workflow_orchestration",
        subtype=key,
        scope=_scope_for_events(events),
        title="固定多步骤流程被反复手工编排",
        observation="相同任务流程在多个会话中由用户重新说明。",
        events=events,
        observed_cost=ObservedCost(
            extra_turns=max(1, len(events) - 1),
            cost_notes=("重复流程编排会增加漏步骤和执行不一致风险。",),
        ),
        root_cause="稳定工作流没有沉淀成可复用流程说明、Checklist 或 Skill。",
        alternative_explanations=("事故处理流程如果只出现一次，不应直接升级成长期机制。",),
        responsibility_layers=("operator", "project", "harness"),
        recommendation="将触发条件、步骤、分支判断和验证方式沉淀成 Checklist 或 Skill。",
        mechanism=mechanism,
        confidence=0.76,
        promotion_confidence=0.7 if mechanism == "skill" else 0.55,
    )


def _detect_repeated_command_sequence(context: _AnalysisContext) -> EfficiencyFinding | None:
    sequence_events: dict[tuple[str, str], list[NormalizedEvent]] = defaultdict(list)
    for events in context.normalized_by_session.values():
        command_events = [
            event
            for event in events
            if event.command and not _looks_like_read_command(event.command)
        ]
        tokens = [_command_token(event.command or "") for event in command_events]
        for index in range(len(tokens) - 1):
            pair = (tokens[index], tokens[index + 1])
            if pair[0] == "OTHER" or pair[1] == "OTHER":
                continue
            sequence_events[pair].extend(command_events[index : index + 2])
    sequence, events = _largest_bucket(sequence_events)
    if not sequence or len({event.session_id for event in events}) < 2:
        return None
    return _finding(
        context,
        problem_type="repeated_command_sequence",
        subtype=" -> ".join(sequence),
        scope=_scope_for_events(events),
        title="固定命令序列被重复执行",
        observation=f"命令序列 `{sequence[0]} -> {sequence[1]}` 在多个会话中重复出现。",
        events=events,
        observed_cost=ObservedCost(
            repeated_commands=max(1, len(events) - len({event.session_id for event in events})),
            extra_turns=max(1, len({event.session_id for event in events}) - 1),
            cost_notes=("确定性命令组合更适合脚本或任务入口。",),
        ),
        root_cause="固定命令组合没有脚本化，导致每次会话重新生成和确认。",
        alternative_explanations=("命令参数若每次显著不同，脚本化收益会降低。",),
        responsibility_layers=("project", "harness"),
        recommendation="为该序列增加 Makefile、package script 或 shell script 入口。",
        mechanism="script",
        confidence=0.8,
        promotion_confidence=0.74,
    )


def _detect_hypothesis_stagnation(context: _AnalysisContext) -> EfficiencyFinding | None:
    repeated_failures: list[NormalizedEvent] = []
    for events in context.normalized_by_session.values():
        failed_by_command: dict[str, list[NormalizedEvent]] = defaultdict(list)
        for event in events:
            if event.command and event.status == "failed":
                failed_by_command[_normalize_command(event.command)].append(event)
        for failures in failed_by_command.values():
            if len(failures) >= 2:
                repeated_failures.extend(failures)
    if len(repeated_failures) < 2:
        return None
    return _finding(
        context,
        problem_type="hypothesis_stagnation",
        subtype="repeated_failed_command",
        scope=_scope_for_events(repeated_failures),
        title="失败后假设没有实质更新",
        observation="同一失败命令被重复执行，后续尝试没有体现新的诊断方向。",
        events=repeated_failures,
        observed_cost=ObservedCost(
            failed_commands=len(repeated_failures),
            repeated_commands=max(1, len(repeated_failures) - 1),
            cost_notes=("重复失败应触发诊断 checkpoint，而不是继续近似重试。",),
        ),
        root_cause="失败结果没有被转化为新的假设、排除项或下一步诊断。",
        alternative_explanations=("某些 flaky 测试需要重跑确认，但应在证据中说明。",),
        responsibility_layers=("agent", "harness"),
        recommendation="增加失败两次后的诊断检查：说明错误指纹、已排除假设和下一次尝试差异。",
        mechanism="checklist",
        confidence=0.82,
        promotion_confidence=0.62,
    )


def _detect_verification_debt(context: _AnalysisContext) -> EfficiencyFinding | None:
    commands = [
        _normalize_command(event.command or "")
        for event in context.events
        if event.command
    ]
    expected = _expected_verification_commands(context.events)
    missing = sorted(command for command in expected if command not in commands)
    shifted_events = [
        event
        for event in context.events
        if event.role == "user"
        and _mentions_missing_verification(event.user_input_text or event.excerpt)
    ]
    if not missing and not shifted_events:
        return None
    evidence_events = shifted_events or [
        event
        for event in context.events
        if event.role == "assistant" and _looks_like_completion(event.excerpt)
    ]
    if not evidence_events:
        return None
    missing_label = ", ".join(missing[:3]) if missing else "required verification"
    return _finding(
        context,
        problem_type="verification_debt",
        subtype="missing_expected_verification",
        scope=_scope_for_events(evidence_events),
        title="验证债务转移给用户",
        observation=f"会话完成前缺少可观察的 `{missing_label}` 验证结果。",
        events=evidence_events,
        observed_cost=ObservedCost(
            extra_turns=len(shifted_events) or 1,
            user_corrections=len(shifted_events) or None,
            validation_shifted_to_user=bool(shifted_events),
            cost_notes=("缺少验证证据会让用户在会话后补做检查。",),
        ),
        root_cause="项目完成标准和必跑验证没有作为结束条件被强制检查。",
        alternative_explanations=("文档或纯研究任务可能不需要测试；环境不可用时应显式说明。",),
        responsibility_layers=("agent", "project", "harness"),
        recommendation="把必跑验证命令写入 AGENTS.md 或 Checklist；强制场景再升级为 Hook/CI。",
        mechanism="checklist",
        confidence=0.86,
        promotion_confidence=0.66,
    )


def _finding(
    context: _AnalysisContext,
    *,
    problem_type: EfficiencyProblemType,
    subtype: str | None,
    scope: FindingScope,
    title: str,
    observation: str,
    events: list[NormalizedEvent],
    observed_cost: ObservedCost,
    root_cause: str,
    alternative_explanations: tuple[str, ...],
    responsibility_layers: tuple[ResponsibilityLayer, ...],
    recommendation: str,
    mechanism: ImprovementMechanism,
    confidence: float,
    promotion_confidence: float,
) -> EfficiencyFinding:
    event_ids = [event.id for event in events]
    evidence_refs = context.evidence_ids(event_ids, reason=f"Supports {problem_type}.")
    return EfficiencyFinding(
        id=_stable_id("eff", problem_type, subtype or "", *evidence_refs),
        problem_type=problem_type,
        subtype=subtype,
        scope=scope,
        title=title,
        observation=observation,
        evidence_refs=evidence_refs,
        occurrences=len(events),
        affected_sessions=tuple(sorted({event.session_id for event in events})),
        observed_cost=observed_cost,
        root_cause=root_cause,
        alternative_explanations=alternative_explanations,
        responsibility_layers=responsibility_layers,
        recommendation=recommendation,
        mechanism=mechanism,
        confidence=confidence,
        promotion_confidence=promotion_confidence,
    )


def _artifact_candidate(finding: EfficiencyFinding) -> ArtifactCandidate | None:
    if finding.mechanism == "none":
        return None
    target_path = _target_path(finding.mechanism)
    return ArtifactCandidate(
        id=_stable_id("art", finding.id, finding.mechanism, target_path or ""),
        source_finding_ids=(finding.id,),
        mechanism=finding.mechanism,
        target_path=target_path,
        title=_artifact_title(finding),
        rationale=finding.recommendation,
        proposed_content=_artifact_content(finding),
        recurrence=finding.occurrences,
        expected_benefit=_expected_benefit(finding.problem_type),
        risks=_artifact_risks(finding.mechanism),
        confidence=finding.promotion_confidence,
        status="proposed",
    )


def _cost_ledger(context: _AnalysisContext) -> ObservedCost:
    events = context.events
    commands = [event for event in events if event.command]
    command_counts = Counter(_normalize_command(event.command or "") for event in commands)
    failed_commands = sum(1 for event in commands if event.status == "failed")
    repeated_commands = sum(count - 1 for count in command_counts.values() if count > 1)
    read_targets = [
        target
        for event in commands
        if _looks_like_read_command(event.command or "")
        for target in _read_targets(event.command or "")
    ]
    target_counts = Counter(read_targets)
    repeated_file_reads = sum(count - 1 for count in target_counts.values() if count > 1)
    user_corrections = sum(1 for event in events if event.is_user_correction)
    return ObservedCost(
        extra_turns=user_corrections + repeated_commands,
        repeated_commands=repeated_commands,
        failed_commands=failed_commands,
        repeated_file_reads=repeated_file_reads,
        user_corrections=user_corrections,
        validation_shifted_to_user=any(
            _mentions_missing_verification(event.user_input_text or event.excerpt)
            for event in events
            if event.role == "user"
        ),
        wall_time_seconds=_total_duration_seconds(context.sessions),
        cost_notes=("聚合成本只填入可由事件直接观察的计数。",),
    )


def _evidence_ref(
    session_by_id: dict[str, SessionRecord],
    event: NormalizedEvent,
    *,
    reason: str,
) -> EvidenceRef:
    session = session_by_id.get(event.session_id)
    quote = _truncate(
        redact_text(event.user_input_text or event.excerpt or event.command or ""),
        280,
    )
    source_file = session.source_path if session is not None else event.session_id
    return EvidenceRef(
        id=_stable_id("eref", event.session_id, event.id, quote),
        session_id=event.session_id,
        event_id=event.id,
        source_file=redact_text(source_file),
        line_number=event.event_index + 1,
        byte_start=event.byte_start,
        byte_end=event.byte_end,
        timestamp=event.created_at,
        quote=quote,
        reason=reason,
        content_hash=_hash_text(quote),
    )


def _requirement_key(text: str) -> str | None:
    lowered = text.lower()
    if "pnpm" in lowered and ("npm" in lowered or "package manager" in lowered):
        return "package_manager:use_pnpm"
    if "agents.md" in lowered and any(term in lowered for term in ("先读", "read", "先看")):
        return "project_context:read_agents_md"
    if "不要" in lowered and "最小" in lowered:
        return "scope:minimal_change"
    return None


def _workflow_tokens(text: str) -> list[str]:
    lowered = text.lower()
    sequence_markers = ("先", "再", "然后", "最后", "->", "→", "if", "如果")
    if not any(marker in lowered for marker in sequence_markers):
        return []
    checks = (
        ("read_context", ("读", "看", "read", "inspect")),
        ("build", ("构建", "build")),
        ("backup", ("备份", "backup")),
        ("restart", ("重启", "restart")),
        ("logs", ("日志", "log")),
        ("health_check", ("健康检查", "health")),
        ("test", ("测试", "test")),
        ("deploy", ("部署", "deploy")),
    )
    tokens = [
        token
        for token, terms in checks
        if any(term in lowered for term in terms)
    ]
    return tokens


def _workflow_needs_judgment(events: list[NormalizedEvent]) -> bool:
    return any(
        any(
            term in (event.user_input_text or event.excerpt).lower()
            for term in ("如果", "if", "失败")
        )
        for event in events
    )


def _expected_verification_commands(events: list[NormalizedEvent]) -> set[str]:
    commands: set[str] = set()
    for event in events:
        if event.role != "user":
            continue
        text = event.user_input_text or event.excerpt
        for match in COMMAND_PATTERN.findall(text):
            normalized = _normalize_command(match)
            if _is_verification_command(normalized):
                commands.add(normalized)
    return commands


def _mentions_missing_verification(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered
        for term in (
            "没跑",
            "没有运行",
            "没有验证",
            "未验证",
            "验证成本",
            "run the test",
            "did not run",
        )
    )


def _looks_like_completion(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("完成", "已修改", "done", "fixed"))


def _is_verification_command(command: str) -> bool:
    return any(
        term in command
        for term in ("test", "pytest", "lint", "typecheck", "tsc", "build", "check")
    )


def _read_targets(command: str) -> list[str]:
    targets: list[str] = []
    for target in PROJECT_KNOWLEDGE_FILES:
        if target.lower() in command.lower():
            targets.append(target)
    return targets


def _looks_like_read_command(command: str) -> bool:
    return command.lower().startswith(READ_PREFIXES)


def _command_token(command: str) -> str:
    normalized = _normalize_command(command)
    if "install" in normalized:
        return "INSTALL_DEPS"
    if "test:auth" in normalized:
        return "RUN_AUTH_TESTS"
    if "test" in normalized or "pytest" in normalized:
        return "RUN_TESTS"
    if "typecheck" in normalized or "tsc" in normalized:
        return "TYPECHECK"
    if "lint" in normalized or "ruff" in normalized:
        return "LINT"
    if "build" in normalized:
        return "BUILD"
    if "restart" in normalized or "systemctl" in normalized:
        return "RESTART_SERVICE"
    return "OTHER"


def _normalize_command(command: str) -> str:
    return " ".join(redact_text(command).strip().lower().split())


def _scope_for_events(events: list[NormalizedEvent]) -> FindingScope:
    session_count = len({event.session_id for event in events})
    if session_count >= 2:
        return "cross_session"
    return "within_session"


def _largest_bucket(
    buckets: dict[Any, list[NormalizedEvent]],
) -> tuple[Any | None, list[NormalizedEvent]]:
    if not buckets:
        return None, []
    key = max(buckets, key=lambda item: (len(buckets[item]), str(item)))
    return key, buckets[key]


def _finding_sort_key(finding: EfficiencyFinding) -> tuple[int, float, str]:
    priority = {
        "verification_debt": 0,
        "repeated_user_requirement": 1,
        "hypothesis_stagnation": 2,
        "project_knowledge_rediscovery": 3,
        "repeated_command_sequence": 4,
        "repeated_workflow_orchestration": 5,
    }.get(finding.problem_type, 9)
    return (priority, -finding.confidence, finding.title)


def _target_path(mechanism: ImprovementMechanism) -> str | None:
    return {
        "agents_md": "AGENTS.md",
        "project_doc": "docs/agent-workflow.md",
        "checklist": "docs/ai-workflow-checklist.md",
        "script": "scripts/",
        "skill": ".codex/skills/",
        "hook": ".codex/hooks/",
        "ci": ".github/workflows/",
    }.get(mechanism)


def _artifact_title(finding: EfficiencyFinding) -> str:
    return {
        "agents_md": "更新 AGENTS.md 项目协作规则",
        "checklist": "新增 AI 协作检查清单",
        "script": "脚本化重复命令序列",
        "skill": "沉淀可复用工作流 Skill",
    }.get(finding.mechanism, finding.title)


def _artifact_content(finding: EfficiencyFinding) -> str | None:
    evidence_line = "Evidence refs: " + ", ".join(finding.evidence_refs)
    if finding.mechanism == "agents_md":
        return f"## {finding.title}\n\n- {finding.recommendation}\n\n{evidence_line}\n"
    if finding.mechanism == "checklist":
        return f"# {finding.title}\n\n- [ ] {finding.recommendation}\n\n{evidence_line}\n"
    if finding.mechanism == "script":
        return (
            "# Add a deterministic command entry for this repeated sequence.\n\n"
            f"{evidence_line}\n"
        )
    if finding.mechanism == "skill":
        return f"# {finding.title}\n\nUse when: {finding.observation}\n\n{evidence_line}\n"
    return f"{finding.recommendation}\n\n{evidence_line}"


def _expected_benefit(problem_type: EfficiencyProblemType) -> str:
    return {
        "repeated_user_requirement": "减少用户重复纠偏和约束说明。",
        "project_knowledge_rediscovery": "缩短会话启动探索时间。",
        "repeated_workflow_orchestration": "减少漏步骤和手工编排成本。",
        "repeated_command_sequence": "减少命令拼写、路径和顺序错误。",
        "hypothesis_stagnation": "缩短重复失败循环。",
        "verification_debt": "降低用户补做验证的监督成本。",
    }.get(problem_type, "降低同类协作成本。")


def _artifact_risks(mechanism: ImprovementMechanism) -> tuple[str, ...]:
    if mechanism == "agents_md":
        return ("规则过宽会增加上下文噪声。",)
    if mechanism == "skill":
        return ("触发场景不稳定时维护成本较高。",)
    if mechanism == "script":
        return ("命令参数变化大时脚本可能过早固化。",)
    return ("需要后续观察是否真正减少同类成本。",)


def _total_duration_seconds(sessions: list[SessionRecord]) -> int | None:
    durations: list[int] = []
    for session in sessions:
        start = _parse_datetime(session.started_at)
        end = _parse_datetime(session.updated_at or session.ended_at)
        if start is not None and end is not None and end >= start:
            durations.append(int((end - start).total_seconds()))
    return sum(durations) if durations else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    return value


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return f"{prefix}_{digest.hexdigest()[:12]}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
