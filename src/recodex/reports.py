from __future__ import annotations

import re
from pathlib import Path
from sqlite3 import Row

from .analysis import (
    ERROR_TERMS,
    SANDBOX_TERMS,
    TEST_TERMS,
    WORKFLOW_TERMS,
    mechanism_for_improvement_category,
    session_signals,
    top_terms,
)
from .db import now_utc
from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def retro_report_path(directory: Path, session: SessionRecord) -> Path:
    return directory / f"retro-{_slug(redact_text(session.title))}-{session.session_id[:10]}.md"


def patterns_report_path(directory: Path, since_label: str) -> Path:
    return directory / f"patterns-{_slug(since_label)}.md"


def improvements_report_path(directory: Path) -> Path:
    return directory / "improvements.md"


def render_retro(session: SessionRecord, events: list[TranscriptEvent]) -> str:
    signals = session_signals(events)
    goal = _task_goal(events, session)
    outcome = _outcome(session, signals)
    timeline = _timeline(events)
    went_well = _what_went_well(events, signals)
    went_wrong = _what_went_wrong(session, events, signals)
    interventions = _user_interventions(events)
    lessons = _reusable_lessons(session, signals)

    return "\n".join(
        [
            "# AI Dev Session Retrospective",
            "",
            f"Generated: {now_utc()}",
            "",
            f"Session: `{session.session_id}`",
            "",
            f"Source: `{redact_text(session.source_path)}`",
            "",
            f"Window: {session.started_at or 'unknown'} -> {session.updated_at or 'unknown'}",
            "",
            "## 1. Task Goal",
            "",
            goal,
            "",
            "## 2. Outcome",
            "",
            outcome,
            "",
            "## 3. Timeline",
            "",
            *timeline,
            "",
            "## 4. What Went Well",
            "",
            *went_well,
            "",
            "## 5. What Went Wrong",
            "",
            *went_wrong,
            "",
            "## 6. User Interventions",
            "",
            *interventions,
            "",
            "## 7. Reusable Lessons",
            "",
            *lessons,
            "",
            "## 8. Improvement Candidates",
            "",
            *_retro_candidates(session, signals),
            "",
        ]
    )


def render_retro_with_findings(
    session: SessionRecord,
    events: list[TranscriptEvent],
    analysis: dict[str, object],
) -> str:
    base = render_retro(session, events).rstrip()
    lines = [
        base,
        "",
        "## 9. 重点诊断",
        "",
        str(analysis.get("overall_assessment") or "没有生成额外诊断。"),
        "",
    ]
    findings = [item for item in analysis.get("main_findings", []) if isinstance(item, dict)]
    if findings:
        for finding in findings[:5]:
            lines.extend(
                [
                    f"### {redact_text(str(finding.get('title') or '未命名问题'))}",
                    "",
                    f"问题：{redact_text(str(finding.get('problem') or '未提供。'))}",
                    "",
                    f"影响：{redact_text(str(finding.get('impact') or '未提供。'))}",
                    "",
                    f"建议：{redact_text(str(finding.get('recommendation') or '未提供。'))}",
                    "",
                    "证据：",
                    "",
                ]
            )
            refs = [str(ref) for ref in finding.get("evidence_refs", [])]
            if refs:
                for ref in refs[:5]:
                    lines.append(f"- `{redact_text(ref)}`")
            else:
                lines.append("- 未提供证据引用。")
            lines.append("")
    else:
        lines.extend(["- 没有带证据的问题诊断。", ""])

    suggestions = [str(item) for item in analysis.get("next_time_suggestions", [])]
    if suggestions:
        lines.extend(["### 下次建议", ""])
        lines.extend(f"- {redact_text(item)}" for item in suggestions[:5])
        lines.append("")
    return "\n".join(lines)


def render_patterns(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    since_label: str,
) -> str:
    from .efficiency_analysis import run_efficiency_analysis

    efficiency = run_efficiency_analysis(sessions, events_by_session)
    all_events = [event for events in events_by_session.values() for event in events]
    total_messages = sum(session.message_count for session in sessions)
    total_commands = sum(session.command_count for session in sessions)
    total_errors = sum(session.error_count for session in sessions)
    high_friction = sorted(sessions, key=lambda item: (item.error_count, item.command_count), reverse=True)[:10]
    terms = (
        top_terms(all_events, ERROR_TERMS)
        + top_terms(all_events, SANDBOX_TERMS)
        + top_terms(all_events, TEST_TERMS)
        + top_terms(all_events, WORKFLOW_TERMS)
    )

    lines = [
        f"# AI Development Patterns Since {since_label}",
        "",
        f"- Generated: {now_utc()}",
        f"- Sessions: {len(sessions)}",
        f"- Messages: {total_messages}",
        f"- Command-like events: {total_commands}",
        f"- Error-like events: {total_errors}",
        "",
        "## Repeated Terms",
        "",
    ]
    if terms:
        for term, count in sorted(set(terms), key=lambda item: (-item[1], item[0]))[:20]:
            lines.append(f"- `{term}`: {count}")
    else:
        lines.append("- No repeated terms detected yet.")

    lines.extend(["", "## High-Friction Sessions", ""])
    if high_friction:
        for session in high_friction:
            lines.append(
                f"- `{session.session_id}`: {redact_text(session.title)} "
                f"(errors={session.error_count}, commands={session.command_count})"
            )
    else:
        lines.append("- No sessions found for this window.")

    lines.extend(["", "## Efficiency Findings", ""])
    if efficiency.findings:
        for finding in efficiency.findings:
            refs = ", ".join(finding.evidence_refs) or "none"
            lines.extend(
                [
                    f"### {redact_text(finding.title)}",
                    "",
                    f"- Problem type: `{finding.problem_type}`",
                    f"- Mechanism: `{finding.mechanism}`",
                    f"- Occurrences: {finding.occurrences}",
                    f"- Evidence refs: {refs}",
                    "",
                    redact_text(finding.observation),
                    "",
                ]
            )
    else:
        lines.append("- No v2 efficiency findings detected for this window.")

    lines.extend(["", "## Artifact Candidates", ""])
    if efficiency.artifact_candidates:
        for candidate in efficiency.artifact_candidates:
            source_ids = ", ".join(candidate.source_finding_ids)
            lines.extend(
                [
                    f"### {redact_text(candidate.title)}",
                    "",
                    f"- Mechanism: `{candidate.mechanism}`",
                    f"- Target: `{candidate.target_path or 'none'}`",
                    f"- Source findings: {source_ids}",
                    "",
                    redact_text(candidate.rationale),
                    "",
                ]
            )
    else:
        lines.append("- No artifact candidates detected for this window.")

    lines.extend(
        [
            "",
            "## Suggested Review Loop",
            "",
            "- Pick the highest-friction session and run `recodex retro latest` after rescanning.",
            "- Convert one repeated failure into a checklist item.",
            "- Convert one repeated command sequence into a script or Make target.",
            "- Export AGENTS.md suggestions after accepting the strongest improvement candidates.",
            "",
        ]
    )
    return "\n".join(lines)


def render_improvements(rows: list[Row]) -> str:
    lines = [
        "# Improvement Candidates",
        "",
        f"- Generated: {now_utc()}",
        f"- Candidates: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("No candidates yet. Run `recodex improvements propose` after scanning transcripts.")
        lines.append("")
        return "\n".join(lines)

    for row in rows:
        lines.extend(
            [
                f"## #{row['id']} {redact_text(row['title'])}",
                "",
                f"- Status: `{row['status']}`",
                f"- Mechanism: `{mechanism_for_improvement_category(row['category'])}`",
                f"- Session: `{row['session_id'] or 'aggregate'}`",
                "",
                "Evidence:",
                "",
                f"> {_escape_blockquote(redact_text(row['evidence']))}",
                "",
                "Recommendation:",
                "",
                redact_text(row["recommendation"]),
                "",
            ]
        )
    return "\n".join(lines)


def render_agents_patch(rows: list[Row]) -> str:
    bullets = []
    for row in rows[:8]:
        bullets.append(f"+- {redact_text(row['recommendation'])}")
    if not bullets:
        bullets.append("+- Run `recodex improvements propose` after scanning transcripts.")

    return "\n".join(
        [
            "# AGENTS.md Patch 建议",
            "",
            "应用到仓库 AGENTS.md 前请人工 review。",
            "",
            "```diff",
            "--- a/AGENTS.md",
            "+++ b/AGENTS.md",
            "@@",
            "+## recodex",
            "+",
            "+使用本地复盘减少重复 AI 开发失败。",
            "+",
            "+- 完成较大 AI 辅助开发后，运行 `recodex scan` 和 `recodex retro latest`。",
            "+- 调整团队工作流前，先用 `recodex patterns --since 30d` 复查近期模式。",
            "+- 将重复失败沉淀为 checklist、skill 或脚本。",
            *bullets,
            "```",
            "",
        ]
    )


def render_checklist_export(rows: list[Row]) -> str:
    lines = [
        "# AI Coding Completion Checklist",
        "",
        "Before saying a task is done:",
        "",
        "- [ ] I identified the files changed.",
        "- [ ] I ran the relevant tests.",
        "- [ ] I ran typecheck or build if available.",
        "- [ ] I checked for unrelated diffs.",
        "- [ ] I summarized verification commands.",
        "- [ ] I listed any remaining risk.",
        "",
        "## Improvement Candidates",
        "",
    ]
    if not rows:
        lines.extend(["No improvement candidates yet. Run `recodex improvements propose`.", ""])
        return "\n".join(lines)

    for row in rows:
        lines.extend(
            [
                f"- [ ] Review candidate #{_row(row, 'id')}: {redact_text(_row(row, 'title'))}",
                "",
                "Evidence:",
                "",
                f"> {_escape_blockquote(_short_evidence(_row(row, 'evidence')))}",
                "",
            ]
        )
    return "\n".join(lines)


def render_scripts_export(rows: list[Row]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# AI development review loop.",
        'TRANSCRIPTS="${RECODEX_TRANSCRIPTS:-$HOME/.codex/sessions}"',
        'recodex scan "$TRANSCRIPTS"',
        "recodex retro latest",
        "recodex patterns --since 30d",
        "recodex improvements propose --since 30d",
        "",
    ]
    if not rows:
        lines.extend(
            [
                "# No candidate-specific script suggestions yet.",
                "# Run `recodex improvements propose` after scanning transcripts.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.append("# Candidate-specific script suggestions:")
    for row in rows:
        lines.extend(
            [
                f"# - #{_row(row, 'id')} {redact_text(_row(row, 'title'))}",
                f"#   Recommendation: {_one_line(redact_text(_row(row, 'recommendation')))}",
                f"#   Evidence: {_one_line(_short_evidence(_row(row, 'evidence')))}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def render_ci_rule_export(rows: list[Row]) -> str:
    lines = [
        "name: recodex",
        "",
        "on:",
        "  workflow_dispatch:",
        "  pull_request:",
        "  push:",
        "    branches: [main]",
        "",
        "jobs:",
        "  review-loop:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Review recent AI development patterns",
        "        run: |",
        "          recodex patterns --since 30d",
        "          recodex improvements propose --since 30d",
        "",
    ]
    if not rows:
        lines.extend(["# No candidate-specific CI rules yet.", ""])
        return "\n".join(lines)

    lines.append("# Candidate-specific CI suggestions:")
    for row in rows:
        lines.extend(
            [
                f"# - #{_row(row, 'id')} {redact_text(_row(row, 'title'))}",
                f"#   Recommendation: {_one_line(redact_text(_row(row, 'recommendation')))}",
                f"#   Evidence: {_one_line(_short_evidence(_row(row, 'evidence')))}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def write_checklist_export(directory: Path, rows: list[Row]) -> Path:
    return write_text(directory / "checklists" / "recodex-checklist.md", render_checklist_export(rows))


def write_scripts_export(directory: Path, rows: list[Row]) -> Path:
    path = write_text(directory / "scripts" / "recodex-verify.sh", render_scripts_export(rows))
    path.chmod(0o755)
    return path


def write_ci_rule_export(directory: Path, rows: list[Row]) -> Path:
    return write_text(directory / "ci" / "verify.yml", render_ci_rule_export(rows))


def write_skill_exports(directory: Path, rows: list[Row]) -> list[Path]:
    skill_dir = directory / "skills" / "recodex-retro"
    checklist_dir = directory / "checklists"
    scripts_dir = directory / "scripts"
    skill = write_text(
        skill_dir / "SKILL.md",
        _skill_markdown(rows),
    )
    checklist = write_text(
        checklist_dir / "recodex-retro.md",
        _checklist_markdown(rows),
    )
    script = write_text(
        scripts_dir / "recodex-weekly.sh",
        _weekly_script(),
    )
    script.chmod(0o755)
    return [skill, checklist, script]


def _task_goal(events: list[TranscriptEvent], session: SessionRecord) -> str:
    for event in events:
        if event.role == "user" and _is_report_signal_event(event):
            return f"从第一条用户请求推断目标。证据：`{session.session_id}#{event.event_index}` {_safe_excerpt(event.text)}"
    return f"没有捕获到明确用户目标。Session 标题：{_safe_excerpt(session.title)}"


def _outcome(session: SessionRecord, signals: dict[str, int]) -> str:
    if session.error_count or signals["errors"]:
        return "partial_or_failed: 检测到错误类证据，需要人工复核。"
    if signals["tests"]:
        return "success_or_partial: 检测到验证类证据，但最终状态仍是推断。"
    return "unknown: 没有检测到明确验证或失败信号。"


def _timeline(events: list[TranscriptEvent]) -> list[str]:
    signal_events = [event for event in events if _is_report_signal_event(event)]
    if not signal_events:
        return ["- No timeline events captured."]
    return [
        f"{index}. `{event.session_id}#{event.event_index}` {event.role}/{event.kind}: {_safe_excerpt(event.text)}"
        for index, event in enumerate(signal_events[:12], start=1)
        if event.text
    ]


def _what_went_well(events: list[TranscriptEvent], signals: dict[str, int]) -> list[str]:
    items = []
    if signals["tests"]:
        items.append("- 会话中讨论或执行了验证 / 测试相关动作。")
    if any(event.kind.lower().endswith("command") or "command" in event.kind.lower() for event in events):
        items.append("- 命令执行证据已被捕获，可用于后续复盘。")
    return items or ["- 暂未检测到明显成功模式。"]


def _what_went_wrong(
    session: SessionRecord,
    events: list[TranscriptEvent],
    signals: dict[str, int],
) -> list[str]:
    items = []
    if session.error_count or signals["errors"]:
        evidence = next(
            (
                event for event in events
                if _is_report_signal_event(event) and event.role in {"tool", "assistant", "unknown"} and _has_error(event.text)
            ),
            None,
        )
        if evidence is None:
            evidence = next((event for event in events if _is_report_signal_event(event) and _has_error(event.text)), None)
        if evidence:
            items.append(
                f"- 错误类证据出现在 `{evidence.session_id}#{evidence.event_index}`："
                f"{_safe_excerpt(evidence.text)}"
            )
        else:
            items.append("- 会话摘要中出现错误类证据。")
    if signals["sandbox"]:
        items.append("- 出现 sandbox 或权限摩擦，应该沉淀为工作流说明。")
    return items or ["- 暂未检测到明显失败模式。"]


def _user_interventions(events: list[TranscriptEvent]) -> list[str]:
    corrections = [
        event for event in events
        if event.role == "user"
        and _is_report_signal_event(event)
        and re.search(r"不是这个|你忘了|刚才说过|not this|you forgot|as i said", event.text, re.I)
    ]
    if not corrections:
        return ["- 未检测到明确用户纠正信号。"]
    return [
        f"- `{event.session_id}#{event.event_index}` {_safe_excerpt(event.text)}"
        for event in corrections
    ]


def _reusable_lessons(session: SessionRecord, signals: dict[str, int]) -> list[str]:
    lessons = []
    if session.command_count:
        lessons.append("- 将重复命令序列沉淀为脚本或 Make target。")
    if signals["tests"] == 0:
        lessons.append("- 没有 test/build 命令时，补充明确的完成检查清单。")
    if signals["sandbox"]:
        lessons.append("- 相似任务开始前，先写清楚 sandbox 和授权升级预期。")
    return lessons or ["- 继续积累会话，直到出现可重复复用的经验。"]


def _notable_events(events: list[TranscriptEvent]) -> list[str]:
    selected = [event for event in events if _is_report_signal_event(event)]
    if not selected:
        return ["- No notable events captured."]
    return [
        f"- `{event.role}` `{event.kind}`: {_excerpt(event.text)}"
        for event in selected[:12]
        if event.text
    ]


def _retro_candidates(session: SessionRecord, signals: dict[str, int]) -> list[str]:
    candidates = []
    if session.error_count or signals["errors"]:
        candidates.append("- 为本次会话的主要失败模式创建 checklist。")
    if session.command_count >= 4 or signals["tests"] >= 2:
        candidates.append("- 将重复验证命令升级为脚本或 Make target。")
    if signals["sandbox"] >= 2:
        candidates.append("- 在 AGENTS.md 中补充 sandbox 和授权审批说明。")
    if signals["workflow"] >= 2:
        candidates.append("- 将可复用工作流沉淀为本地 skill。")
    return candidates or ["- 暂未检测到强改进候选。"]


def _skill_markdown(rows: list[Row]) -> str:
    recommendations = "\n".join(f"- {redact_text(row['recommendation'])}" for row in rows[:8])
    if not recommendations:
        recommendations = "- Run a retrospective and capture one concrete workflow improvement."
    return "\n".join(
        [
            "---",
            "name: recodex-retro",
            "description: Review AI development sessions and turn repeated issues into workflow assets.",
            "---",
            "",
            "# AI Development Retrospective",
            "",
            "Use this when a development session involved substantial AI assistance or repeated failures.",
            "",
            "## Steps",
            "",
            "- Run `recodex scan` against local transcripts.",
            "- Run `recodex retro latest` for the most recent session.",
            "- Run `recodex improvements propose` and review the candidates.",
            "- Export accepted guidance into AGENTS.md, skills, checklists, or scripts.",
            "",
            "## Current Recommendations",
            "",
            recommendations,
            "",
        ]
    )


def _checklist_markdown(rows: list[Row]) -> str:
    items = [
        "- [ ] Scan recent Codex transcripts.",
        "- [ ] Read the latest retrospective report.",
        "- [ ] Identify one repeated failure mode.",
        "- [ ] Add or update one workflow artifact.",
        "- [ ] Verify the artifact is actionable in a new session.",
    ]
    for row in rows[:5]:
        items.append(f"- [ ] Review candidate #{row['id']}: {redact_text(row['title'])}")
    return "# AI Review Checklist\n\n" + "\n".join(items) + "\n"


def _weekly_script() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
        'TRANSCRIPTS="${RECODEX_TRANSCRIPTS:-$HOME/.codex/sessions}"',
            'recodex scan "$TRANSCRIPTS"',
            "recodex patterns --since 30d",
            "recodex improvements propose",
            "recodex improvements review",
            "",
        ]
    )


def _escape_blockquote(text: str) -> str:
    return text.replace("\n", "\n> ")


def _safe_excerpt(text: str, limit: int = 220) -> str:
    return _excerpt(redact_text(text), limit=limit)


def _short_evidence(text: str) -> str:
    return _safe_excerpt(str(text), limit=500)


def _has_error(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ERROR_TERMS)


def _is_report_signal_event(event: TranscriptEvent) -> bool:
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
        "chunk id:",
    )
    noise_terms = (
        "you are codex",
        "knowledge cutoff",
        "sandbox_mode",
        "original token count",
    )
    if lowered.startswith(noise_prefixes):
        return False
    return not any(term in lowered for term in noise_terms)


def _row(row: Row | dict[str, object], name: str) -> str:
    try:
        value = row[name]  # type: ignore[index]
    except (KeyError, IndexError):
        value = ""
    return str(value or "")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _excerpt(text: str, limit: int = 220) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:60] or "report"
