from __future__ import annotations

import hashlib
from collections import Counter

from .models import ImprovementDraft, SessionRecord, TranscriptEvent
from .privacy import redact_text

SANDBOX_TERMS = ("sandbox", "permission", "escalat", "network", "approval", "restricted")
TEST_TERMS = ("test", "pytest", "unittest", "npm run build", "ruff", "lint", "verification")
WORKFLOW_TERMS = ("agents.md", "skill", "checklist", "workflow", "retro", "review")
ERROR_TERMS = ("error", "failed", "exception", "traceback", "timeout", "not found", "失败", "报错")


def count_terms(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def session_signals(events: list[TranscriptEvent]) -> Counter[str]:
    signal_events = [event for event in events if _is_signal_event(event)]
    text = "\n".join(event.text for event in signal_events)
    signals: Counter[str] = Counter()
    signals["errors"] = count_terms(text, ERROR_TERMS)
    signals["sandbox"] = count_terms(text, SANDBOX_TERMS)
    signals["tests"] = count_terms(text, TEST_TERMS)
    signals["workflow"] = count_terms(text, WORKFLOW_TERMS)
    signals["commands"] = sum(1 for event in signal_events if "command" in event.kind.lower())
    return signals


def top_terms(events: list[TranscriptEvent], terms: tuple[str, ...]) -> list[tuple[str, int]]:
    text = "\n".join(event.text for event in events)
    counts = Counter({term: count_terms(text, (term,)) for term in terms})
    return [(term, count) for term, count in counts.most_common() if count > 0]


def propose_improvements(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[ImprovementDraft]:
    drafts: list[ImprovementDraft] = []
    signals_by_session = {
        session.session_id: session_signals(events_by_session.get(session.session_id, []))
        for session in sessions
    }

    drafts.extend(_project_level_drafts(sessions, events_by_session, signals_by_session))
    drafts.extend(_rulebase_improvement_drafts(sessions, events_by_session))
    return _dedupe(drafts)


def _project_level_drafts(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    signals_by_session: dict[str, Counter[str]],
) -> list[ImprovementDraft]:
    drafts: list[ImprovementDraft] = []
    failure_sessions = [
        session
        for session in sessions
        if session.error_count or signals_by_session[session.session_id]["errors"] >= 2
    ]
    sandbox_sessions = [
        session
        for session in sessions
        if signals_by_session[session.session_id]["sandbox"] >= 2
    ]
    command_sessions = [
        session
        for session in sessions
        if session.command_count >= 4 or signals_by_session[session.session_id]["tests"] >= 3
    ]
    workflow_sessions = [
        session
        for session in sessions
        if signals_by_session[session.session_id]["workflow"] >= 2
    ]
    total_errors = sum(session.error_count for session in sessions)

    if failure_sessions:
        drafts.append(
            _draft(
                None,
                "checklist",
                "建立失败分诊 checklist",
                _aggregate_evidence(failure_sessions, events_by_session),
                "把失败处理固化为 checklist：记录失败信号、暴露失败的命令、根因假设、修复动作、验证命令和验证结果。",
            )
        )
    if sandbox_sessions:
        drafts.append(
            _draft(
                None,
                "agents",
                "补充 sandbox / escalation 执行约束",
                _aggregate_evidence(sandbox_sessions, events_by_session),
                "在 AGENTS.md 中写清楚：哪些命令可直接运行，哪些需要申请授权，生成状态写到哪里，以及失败后如何继续。",
            )
        )
    if command_sessions:
        drafts.append(
            _draft(
                None,
                "script",
                "沉淀高频验证和排查命令为脚本",
                _aggregate_evidence(command_sessions, events_by_session),
                "把反复出现的测试、构建、日志、健康检查命令做成脚本或 Make target，让后续 AI 会话直接运行标准入口。",
            )
        )
    if workflow_sessions:
        drafts.append(
            _draft(
                None,
                "skill",
                "沉淀可复用 AI 工作流为 skill",
                _aggregate_evidence(workflow_sessions, events_by_session),
                "把反复出现的多步流程做成本地 skill，包含触发条件、必要上下文、执行步骤、验证方式和常见错误。",
            )
        )
    if len(sessions) >= 2 and total_errors >= 2:
        drafts.append(
            _draft(
                None,
                "patterns",
                "定期复查近期重复失败主题",
                f"涉及 {len(sessions)} 个会话，共检测到 {total_errors} 个错误类信号。",
                "每周运行 `recodex patterns --since 30d`，只选择最高频的 1 个失败主题落地为 checklist、AGENTS.md 规则、脚本或 CI/eval。",
            )
        )
    return drafts


def _rulebase_improvement_drafts(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[ImprovementDraft]:
    from .rulebase import evaluate_session_rules

    hits: dict[str, dict[str, object]] = {}
    for session in sessions:
        events = events_by_session.get(session.session_id, [])
        for result in evaluate_session_rules(session, events, limit=4):
            if result.status not in {"violated", "partial"} or not result.suggestions:
                continue
            hit = hits.setdefault(
                result.rule.id,
                {
                    "result": result,
                    "sessions": [],
                },
            )
            hit["sessions"].append(session)  # type: ignore[index,union-attr]

    drafts: list[ImprovementDraft] = []
    for rule_id in sorted(hits, key=lambda item: _rule_sort_key(hits[item]["result"])):  # type: ignore[index]
        result = hits[rule_id]["result"]  # type: ignore[index,assignment]
        affected_sessions = hits[rule_id]["sessions"]  # type: ignore[index,assignment]
        drafts.append(
            _draft(
                None,
                _improvement_category_for_rule(result.rule.category),
                f"改进工作流：{result.rule.name}",
                _aggregate_evidence(affected_sessions, events_by_session),
                result.suggestions[0],
            )
        )
    return drafts


def _rule_sort_key(result) -> tuple[int, str]:
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(result.severity, 9)
    return (rank, result.rule.id)


def _improvement_category_for_rule(rule_category: str) -> str:
    if rule_category in {"project_memory", "safety", "verification"}:
        return "agents"
    if rule_category in {"automation", "tool_usage"}:
        return "script"
    if rule_category in {"bugfix_workflow", "task_planning"}:
        return "checklist"
    if rule_category in {"context_management", "collaboration"}:
        return "skill"
    return "checklist"


def _draft(
    session_id: str | None,
    category: str,
    title: str,
    evidence: str,
    recommendation: str,
) -> ImprovementDraft:
    fingerprint_source = "\n".join([category, title])
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:32]
    return ImprovementDraft(
        fingerprint=fingerprint,
        session_id=session_id,
        category=category,
        title=redact_text(title)[:180],
        evidence=redact_text(evidence)[:1_000],
        recommendation=redact_text(recommendation)[:1_000],
    )


def _dedupe(drafts: list[ImprovementDraft]) -> list[ImprovementDraft]:
    seen: set[str] = set()
    unique: list[ImprovementDraft] = []
    for draft in drafts:
        key = _draft_key(draft)
        if key in seen:
            continue
        seen.add(key)
        unique.append(draft)
    return unique


def _session_evidence(session: SessionRecord, events: list[TranscriptEvent]) -> str:
    snippets = [_event_evidence(event) for event in _select_evidence_events(events)]
    joined = " | ".join(snippets) if snippets else _excerpt(redact_text(session.raw_preview))
    return (
        f"Session `{session.session_id}` from `{redact_text(session.source_path)}`. "
        f"Messages={session.message_count}, commands={session.command_count}, "
        f"errors={session.error_count}. Evidence: {joined}"
    )


def _aggregate_evidence(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    *,
    limit: int = 4,
) -> str:
    parts = [f"涉及 {len(sessions)} 个会话。"]
    for session in sessions[:limit]:
        parts.append(
            f"`{session.session_id}` {redact_text(session.title)} "
            f"(commands={session.command_count}, errors={session.error_count}): "
            f"{_session_evidence_summary(session, events_by_session.get(session.session_id, []))}"
        )
    if len(sessions) > limit:
        parts.append(f"另有 {len(sessions) - limit} 个类似会话。")
    return " ".join(parts)


def _session_evidence_summary(session: SessionRecord, events: list[TranscriptEvent]) -> str:
    selected = _select_evidence_events(events)
    if not selected:
        return _excerpt(redact_text(session.raw_preview), 120)
    return " | ".join(_event_evidence(event, limit=120) for event in selected[:3])


def _select_evidence_events(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    selected: list[TranscriptEvent] = []
    user_goal = next((event for event in events if event.role == "user" and _is_signal_event(event)), None)
    failed_tool = next(
        (
            event for event in events
            if event.role in {"tool", "unknown"} and _is_signal_event(event) and _looks_failed(event.text)
        ),
        None,
    )
    command_event = next(
        (
            event for event in events
            if event.role in {"tool", "unknown"} and _is_signal_event(event) and event.metadata.get("command")
        ),
        None,
    )
    final_answer = next(
        (event for event in reversed(events) if event.role == "assistant" and _is_signal_event(event)),
        None,
    )
    for event in (user_goal, failed_tool or command_event, final_answer):
        if event is not None and event not in selected:
            selected.append(event)
    if selected:
        return selected
    return [event for event in events if _is_signal_event(event)][:3]


def _is_signal_event(event: TranscriptEvent) -> bool:
    if event.role not in {"user", "assistant", "tool", "unknown"} or not event.text.strip():
        return False
    lowered = event.text.strip().lower()
    noise_prefixes = (
        "<environment_context>",
        "<permissions",
        "<collaboration_mode>",
        "<skills_instructions>",
        "cwd=",
        "model=",
    )
    noise_terms = (
        "you are codex",
        "knowledge cutoff",
        "sandbox_mode",
    )
    if lowered.startswith(noise_prefixes):
        return False
    if any(term in lowered for term in noise_terms):
        return False
    if not event.metadata.get("command") and lowered.startswith("chunk id:"):
        return False
    return True


def _event_evidence(event: TranscriptEvent, *, limit: int = 180) -> str:
    command = event.metadata.get("command")
    prefix = f"{event.role}#{event.event_index}"
    if command:
        return f"{prefix} command=`{redact_text(str(command))}` output={_excerpt(redact_text(event.text), limit)}"
    return f"{prefix}: {_excerpt(redact_text(event.text), limit)}"


def _looks_failed(text: str) -> bool:
    lowered = text.lower()
    if "process exited with code 0" in lowered:
        return False
    return count_terms(text, ERROR_TERMS) > 0


def _draft_key(draft: ImprovementDraft) -> str:
    normalized_title = " ".join(draft.title.lower().split())
    return f"{draft.category}:{normalized_title}"


def _excerpt(text: str, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
