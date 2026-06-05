from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .analysis import ERROR_TERMS, TEST_TERMS, session_signals
from .models import ImprovementDraft, SessionRecord, TranscriptEvent
from .privacy import redact_text

VERIFY_TERMS = (
    *TEST_TERMS,
    "typecheck",
    "tsc",
    "build",
    "lint",
    "ruff",
    "mypy",
    "pytest",
    "vitest",
    "jest",
    "health",
    "status",
    "journalctl",
    "smoke",
)


def build_session_report_data(
    session: SessionRecord,
    events: list[TranscriptEvent],
    analysis: dict[str, object] | None = None,
) -> dict[str, Any]:
    evidence = _session_evidence(events, session)
    signals = session_signals(events)
    verification = _verification_block(session, events)
    issues = _analysis_issues(analysis) or _session_issues(session, events, signals, verification, evidence)
    suggestions = _analysis_suggestions(analysis) or _suggestions_from_issues(issues)
    generated_at = _now()
    project = _session_project(session)

    return {
        "meta": {
            "report_id": _report_id("session", session.session_id, generated_at),
            "project": project,
            "source": session.tool or session.source or "codex",
            "session_id": session.session_id,
            "session_path": redact_text(session.source_path or session.transcript_path or ""),
            "session_time": session.started_at or session.updated_at or "unknown",
            "duration_minutes": _duration_minutes(session.started_at, session.updated_at),
            "privacy_mode": "redacted",
            "analysis_mode": "llm+rules" if analysis else "rules+heuristics",
            "generated_at": generated_at,
        },
        "summary": {
            "headline": _headline(issues, verification),
            "overall": _overall_summary(session, issues, verification),
            "completion_confidence": _completion_confidence(issues, verification),
            "top_focus": _top_focus(issues, verification),
        },
        "metrics": {
            "main_issue_count": len(issues),
            "context_items_late": _user_correction_count(events),
            "verification_found": verification["overall"] == "验证闭环存在",
            "user_corrections": _user_correction_count(events),
            "failed_commands": _failed_command_count(events),
            "files_changed": _file_change_count(events),
            "messages": session.message_count,
            "commands": session.command_count,
            "errors": session.error_count + signals["errors"],
        },
        "flow": _session_flow(session, events, verification),
        "issues": issues,
        "context_frontload": _context_frontload(session, events, verification),
        "intervention": _intervention(events),
        "verification": verification,
        "suggestions": suggestions,
        "artifacts": _artifacts(suggestions),
        "evidence": evidence,
    }


def build_project_report_data(
    project_key: str,
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    drafts: Iterable[ImprovementDraft],
    since_label: str,
) -> dict[str, Any]:
    draft_list = list(drafts)
    generated_at = _now()
    all_events = [event for session in sessions for event in events_by_session.get(session.session_id, [])]
    verification_found = any(_has_verification(events_by_session.get(session.session_id, [])) for session in sessions)
    issues = _project_issues(draft_list, sessions)
    suggestions = _project_suggestions(draft_list)

    return {
        "meta": {
            "report_id": _report_id("project", project_key, generated_at),
            "project": redact_text(project_key),
            "source": "codex",
            "session_id": "project-aggregate",
            "session_path": "",
            "session_time": since_label,
            "duration_minutes": None,
            "privacy_mode": "redacted",
            "analysis_mode": "rules+heuristics",
            "generated_at": generated_at,
        },
        "summary": {
            "headline": "近期 Codex 会话已按项目聚合成复盘报告。",
            "overall": _project_overall(project_key, sessions, issues),
            "completion_confidence": "medium" if verification_found else "medium_low",
            "top_focus": issues[0]["title"] if issues else "继续积累样本",
        },
        "metrics": {
            "main_issue_count": len(issues),
            "context_items_late": _user_correction_count(all_events),
            "verification_found": verification_found,
            "user_corrections": _user_correction_count(all_events),
            "failed_commands": _failed_command_count(all_events),
            "files_changed": _file_change_count(all_events),
            "sessions": len(sessions),
            "messages": sum(session.message_count for session in sessions),
            "commands": sum(session.command_count for session in sessions),
            "errors": sum(session.error_count for session in sessions),
        },
        "flow": _project_flow(sessions, draft_list),
        "issues": issues,
        "context_frontload": _project_context_frontload(draft_list),
        "intervention": {
            "observation": "该报告汇总最近窗口内同一项目的多个会话，用于发现重复问题和可沉淀动作。",
            "suggestions": [
                "优先处理高影响、低成本的改进候选。",
                "把稳定项目事实放入 AGENTS.md，把多步流程沉淀为 skill 或 checklist。",
            ],
        },
        "verification": _project_verification(sessions, events_by_session, verification_found),
        "suggestions": suggestions,
        "artifacts": _artifacts(suggestions),
        "evidence": _project_evidence(draft_list, sessions, events_by_session),
    }


def write_report_bundle(directory: Path, basename: str, report: dict[str, Any]) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = write_report_json(directory / f"{basename}.json", report)
    html_path = write_report_html(directory / f"{basename}.html", report)
    return json_path, html_path


def write_report_json(path: Path, report: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_report_html(path: Path, report: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report_html(report), encoding="utf-8")
    return path


def render_report_html(report: dict[str, Any]) -> str:
    meta = _dict(report.get("meta"))
    summary = _dict(report.get("summary"))
    metrics = _dict(report.get("metrics"))
    embedded_json = _json_for_script(report)
    title = f"{_h(str(meta.get('project') or 'recodex'))} - recodex"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #18202b;
      --muted: #687282;
      --line: #dde2ea;
      --soft: #eef2f6;
      --accent: #166534;
      --warn: #b45309;
      --danger: #b91c1c;
      --info: #1d4ed8;
      --shadow: 0 12px 32px rgba(24, 32, 43, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    a {{ color: inherit; }}
    .page {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 56px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 22px;
    }}
    .eyebrow {{ margin: 0 0 8px; color: var(--muted); font-size: 13px; }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.18; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 15px; letter-spacing: 0; }}
    p {{ margin: 0; }}
    .meta {{
      min-width: 280px;
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 6px 12px;
      color: var(--muted);
      font-size: 13px;
      text-align: left;
    }}
    .meta span:nth-child(odd) {{ color: #404b5a; font-weight: 650; }}
    .summary {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.8fr);
      gap: 18px;
      margin-bottom: 18px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 18px;
    }}
    .headline {{ font-size: 20px; line-height: 1.35; font-weight: 750; margin-bottom: 10px; }}
    .muted {{ color: var(--muted); }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
      min-height: 74px;
    }}
    .metric strong {{ display: block; font-size: 21px; line-height: 1.2; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.48fr); gap: 18px; align-items: start; }}
    section {{ margin-bottom: 18px; }}
    .flow {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }}
    .flow li, .issue, .suggestion, .evidence, .check {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 13px;
    }}
    .status, .severity, .priority {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 2px 8px;
      font-size: 12px;
      color: #344052;
      background: #fff;
    }}
    .severity-high, .severity-critical {{ border-color: #fecaca; color: var(--danger); background: #fff5f5; }}
    .severity-medium {{ border-color: #fed7aa; color: var(--warn); background: #fff7ed; }}
    .severity-low {{ border-color: #bfdbfe; color: var(--info); background: #eff6ff; }}
    .issue + .issue, .suggestion + .suggestion, .evidence + .evidence, .check + .check {{ margin-top: 10px; }}
    .issue-head, .suggestion-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .kv {{ display: grid; gap: 6px; color: var(--muted); font-size: 13px; }}
    .kv b {{ color: var(--ink); font-weight: 650; }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      background: #101827;
      color: #f8fafc;
      border-radius: 8px;
      padding: 14px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.5;
    }}
    .artifact-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin: 0 0 10px;
    }}
    button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 8px;
      padding: 7px 10px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }}
    button:hover {{ border-color: #aab4c2; background: var(--soft); }}
    .footer {{ color: var(--muted); font-size: 12px; padding-top: 10px; }}
    @media (max-width: 880px) {{
      .topbar, .summary, .grid {{ display: block; }}
      .meta {{ margin-top: 14px; min-width: 0; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      h1 {{ font-size: 24px; }}
    }}
    @media (max-width: 520px) {{
      .page {{ padding: 18px 12px 36px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .issue-head, .suggestion-head, .artifact-head {{ display: block; }}
      .severity, .priority, button {{ margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <p class="eyebrow">recodex 静态报告</p>
        <h1>{_h(str(meta.get("project") or "Unknown Project"))}</h1>
      </div>
      <div class="meta" aria-label="报告元数据">
        <span>来源</span><span>{_h(str(meta.get("source") or "unknown"))}</span>
        <span>会话</span><span>{_h(str(meta.get("session_id") or "unknown"))}</span>
        <span>时间</span><span>{_h(str(meta.get("session_time") or "unknown"))}</span>
        <span>模式</span><span>{_h(str(meta.get("analysis_mode") or "unknown"))}</span>
      </div>
    </header>

    <div class="summary">
      <section class="panel">
        <h2>概览</h2>
        <p class="headline">{_h(str(summary.get("headline") or "暂无概览。"))}</p>
        <p class="muted">{_h(str(summary.get("overall") or ""))}</p>
      </section>
      <section class="panel">
        <h2>指标</h2>
        {_render_metrics(metrics)}
      </section>
    </div>

    <div class="grid">
      <div>
        <section class="panel">
          <h2>流程路径</h2>
          {_render_flow(report.get("flow"))}
        </section>
        <section class="panel">
          <h2>主要问题</h2>
          {_render_issues(report.get("issues"))}
        </section>
        <section class="panel">
          <h2>改进建议</h2>
          {_render_suggestions(report.get("suggestions"))}
        </section>
        <section class="panel">
          <h2>证据附录</h2>
          {_render_evidence(report.get("evidence"))}
        </section>
      </div>
      <aside>
        <section class="panel">
          <h2>上下文前置</h2>
          {_render_context_frontload(report.get("context_frontload"))}
        </section>
        <section class="panel">
          <h2>过程干预</h2>
          {_render_intervention(report.get("intervention"))}
        </section>
        <section class="panel">
          <h2>验收与验证</h2>
          {_render_verification(report.get("verification"))}
        </section>
        <section class="panel">
          <h2>输出片段</h2>
          {_render_artifacts(report.get("artifacts"))}
        </section>
      </aside>
    </div>
    <p class="footer">本报告由 CLI 生成；页面只展示嵌入数据，不读取 Codex session，不扫描本地目录。</p>
  </main>
  <script id="report-data" type="application/json">
{embedded_json}
  </script>
  <script>
    document.querySelectorAll("[data-copy-target]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const target = document.getElementById(button.dataset.copyTarget);
        if (!target) return;
        const text = target.textContent || "";
        try {{
          await navigator.clipboard.writeText(text);
          button.textContent = "已复制";
          setTimeout(() => {{ button.textContent = "复制"; }}, 1200);
        }} catch (_error) {{
          const area = document.createElement("textarea");
          area.value = text;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
        }}
      }});
    }});
  </script>
</body>
</html>
"""


def _session_issues(
    session: SessionRecord,
    events: list[TranscriptEvent],
    signals: dict[str, int],
    verification: dict[str, Any],
    evidence: list[dict[str, str]],
) -> list[dict[str, Any]]:
    refs = [item["id"] for item in evidence[:2]] or ["ev_001"]
    issues: list[dict[str, Any]] = []
    if verification["overall"] != "验证闭环存在" and _has_work_signal(session, events):
        issues.append(
            {
                "title": "验收证据不足",
                "severity": "high",
                "observation": "会话中存在代码修改、实现声明或命令执行，但未发现清晰的测试、构建、typecheck 或手动验收结果。",
                "impact": "完成可信度不足，用户仍需要自行判断任务是否真的完成。",
                "suggestion": "把运行最小相关验证并汇报命令结果作为收尾要求。",
                "evidence_refs": refs,
            }
        )
    if session.error_count or signals["errors"]:
        issues.append(
            {
                "title": "失败信号需要明确分诊",
                "severity": "medium",
                "observation": "会话中检测到错误、失败或异常相关文本。",
                "impact": "如果没有基于错误输出继续定位，后续修改可能变成猜测式修复。",
                "suggestion": "失败命令后先总结错误关键信息和当前假设，再决定下一步修改。",
                "evidence_refs": refs,
            }
        )
    if _user_correction_count(events):
        issues.append(
            {
                "title": "用户纠正应沉淀为项目上下文",
                "severity": "medium",
                "observation": "会话中出现用户纠正或重新说明方向的信号。",
                "impact": "相同信息下次仍可能被遗漏，造成重复解释和无效探索。",
                "suggestion": "把稳定项目事实写入 AGENTS.md；多步流程则沉淀为 checklist 或 skill。",
                "evidence_refs": refs,
            }
        )
    if not issues:
        issues.append(
            {
                "title": "暂无高优先级问题",
                "severity": "low",
                "observation": "当前样本没有暴露明显的失败、验证缺口或用户纠正。",
                "impact": "继续积累会话后再观察重复模式。",
                "suggestion": "保留本次报告作为基线，后续对比命令失败、用户纠正和验证完整度。",
                "evidence_refs": refs,
            }
        )
    return issues[:5]


def _analysis_issues(analysis: dict[str, object] | None) -> list[dict[str, Any]]:
    if not analysis:
        return []
    issues: list[dict[str, Any]] = []
    for item in analysis.get("main_findings", []) or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "title": redact_text(str(item.get("title") or "未命名问题")),
                "severity": str(item.get("severity") or "medium"),
                "observation": redact_text(str(item.get("problem") or "")),
                "impact": redact_text(str(item.get("impact") or "")),
                "suggestion": redact_text(str(item.get("recommendation") or "")),
                "evidence_refs": [str(ref) for ref in item.get("evidence_refs", [])][:6],
            }
        )
    return issues[:5]


def _analysis_suggestions(analysis: dict[str, object] | None) -> list[dict[str, str]]:
    if not analysis:
        return []
    suggestions: list[dict[str, str]] = []
    for item in analysis.get("improvement_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        suggestions.append(
            {
                "title": redact_text(str(item.get("title") or "未命名建议")),
                "priority": str(item.get("priority") or "medium"),
                "why": redact_text(str(item.get("why") or "")),
                "action": _artifact_action(str(item.get("artifact_type") or "checklist")),
                "target": str(item.get("artifact_type") or "checklist"),
            }
        )
    for text in analysis.get("next_time_suggestions", []) or []:
        suggestions.append(
            {
                "title": "下次会话建议",
                "priority": "medium",
                "why": redact_text(str(text)),
                "action": redact_text(str(text)),
                "target": "workflow",
            }
        )
    return suggestions[:5]


def _suggestions_from_issues(issues: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions = []
    for issue in issues[:5]:
        target = "Checklist"
        title = str(issue.get("title") or "改进建议")
        if "上下文" in title or "项目" in title:
            target = "AGENTS.md"
        elif "失败" in title:
            target = "Checklist"
        suggestions.append(
            {
                "title": str(issue.get("suggestion") or title)[:80],
                "priority": _priority_for_severity(str(issue.get("severity") or "medium")),
                "why": str(issue.get("impact") or "该建议来自本次会话证据。"),
                "action": str(issue.get("suggestion") or "把该问题沉淀为下一次会话的检查项。"),
                "target": target,
            }
        )
    return suggestions


def _project_issues(drafts: list[ImprovementDraft], sessions: list[SessionRecord]) -> list[dict[str, Any]]:
    issues = []
    for index, draft in enumerate(drafts[:6], start=1):
        issues.append(
            {
                "title": redact_text(draft.title),
                "severity": _severity_for_category(draft.category),
                "observation": redact_text(draft.evidence),
                "impact": "该问题在近期会话中有可追溯证据，适合优先进入人工 review。",
                "suggestion": redact_text(draft.recommendation),
                "evidence_refs": [f"ev_{index:03d}"],
            }
        )
    if not issues:
        issues.append(
            {
                "title": "近期样本暂未形成强改进候选",
                "severity": "low",
                "observation": f"已扫描 {len(sessions)} 个会话，但没有足够强的重复失败或沉淀信号。",
                "impact": "可以继续积累更多会话后再做跨会话模式分析。",
                "suggestion": "下次运行默认快速启动时保留按项目报告，用于对比趋势。",
                "evidence_refs": ["ev_001"],
            }
        )
    return issues[:6]


def _project_suggestions(drafts: list[ImprovementDraft]) -> list[dict[str, str]]:
    suggestions = []
    for draft in drafts[:6]:
        suggestions.append(
            {
                "title": redact_text(draft.title),
                "priority": _priority_for_severity(_severity_for_category(draft.category)),
                "why": redact_text(draft.evidence)[:260],
                "action": redact_text(draft.recommendation),
                "target": _target_for_category(draft.category),
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "title": "保留项目级复盘基线",
                "priority": "low",
                "why": "当前窗口暂未检测到强改进候选。",
                "action": "继续积累会话，后续用相同窗口对比验证缺口、失败命令和用户纠正数量。",
                "target": "Workflow",
            }
        )
    return suggestions[:6]


def _session_evidence(events: list[TranscriptEvent], session: SessionRecord) -> list[dict[str, str]]:
    selected: list[TranscriptEvent] = []
    for candidate in (
        next((event for event in events if event.role == "user" and _is_signal_event(event)), None),
        next((event for event in events if _looks_failed(event.text) and _is_signal_event(event)), None),
        next((event for event in reversed(events) if event.role == "assistant" and _is_signal_event(event)), None),
        next((event for event in events if _is_command_event(event) and _is_signal_event(event)), None),
    ):
        if candidate is not None and candidate not in selected:
            selected.append(candidate)
    if not selected:
        return [
            {
                "id": "ev_001",
                "title": "会话摘要",
                "content": _excerpt(redact_text(session.raw_preview or session.title), 700),
                "analysis": "没有捕获到更具体的事件片段，暂用会话摘要作为证据。",
            }
        ]
    evidence = []
    for index, event in enumerate(selected[:6], start=1):
        evidence.append(
            {
                "id": f"ev_{index:03d}",
                "title": f"{event.role}/{event.kind} #{event.event_index}",
                "content": _excerpt(redact_text(event.text), 900),
                "analysis": _evidence_analysis(event),
            }
        )
    return evidence


def _project_evidence(
    drafts: list[ImprovementDraft],
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[dict[str, str]]:
    evidence = []
    for index, draft in enumerate(drafts[:8], start=1):
        evidence.append(
            {
                "id": f"ev_{index:03d}",
                "title": redact_text(draft.title),
                "content": _excerpt(redact_text(draft.evidence), 900),
                "analysis": "该证据来自近期同项目会话聚合后的改进候选。",
            }
        )
    if evidence:
        return evidence
    for index, session in enumerate(sessions[:3], start=1):
        session_evidence = _session_evidence(events_by_session.get(session.session_id, []), session)[0]
        session_evidence["id"] = f"ev_{index:03d}"
        evidence.append(session_evidence)
    return evidence or [{"id": "ev_001", "title": "暂无证据", "content": "暂无可展示证据。", "analysis": ""}]


def _verification_block(session: SessionRecord, events: list[TranscriptEvent]) -> dict[str, Any]:
    checks = [
        _check("测试", _contains_any(events, ("test", "pytest", "unittest", "vitest", "jest")), "检测测试命令或测试输出。"),
        _check("构建", _contains_any(events, ("build", "compile", "package")), "检测构建或打包命令。"),
        _check("Typecheck", _contains_any(events, ("typecheck", "tsc", "mypy", "pyright")), "检测类型检查。"),
        _check("最终回答命令结果", _final_mentions_verification(events), "最终回答是否包含验证命令和结果。"),
    ]
    found = any(check["status"] == "found" for check in checks)
    return {
        "overall": "验证闭环存在" if found else "验证闭环不足",
        "checks": checks,
        "recommended_closing_format": [
            "修改了哪些文件",
            "运行了哪些验证命令",
            "命令结果如何",
            "哪些验证没有运行",
            "还剩什么风险",
        ],
    }


def _project_verification(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    verification_found: bool,
) -> dict[str, Any]:
    verified_sessions = sum(1 for session in sessions if _has_verification(events_by_session.get(session.session_id, [])))
    return {
        "overall": "验证闭环存在" if verification_found else "验证闭环不足",
        "checks": [
            {
                "name": "会话验证覆盖",
                "status": "found" if verification_found else "not_found",
                "detail": f"{verified_sessions}/{len(sessions)} 个会话检测到测试、构建、typecheck 或健康检查信号。",
            },
            {
                "name": "失败命令",
                "status": "partial",
                "detail": "项目级报告仅统计失败信号，具体命令请查看单次 retro 报告。",
            },
        ],
        "recommended_closing_format": [
            "本次窗口处理了哪些会话",
            "哪些会话有验证证据",
            "最高优先级改进候选是什么",
            "哪些建议需要人工 review",
        ],
    }


def _session_flow(
    session: SessionRecord,
    events: list[TranscriptEvent],
    verification: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "stage": "提出任务",
            "status": "已捕获" if any(event.role == "user" for event in events) else "信息不足",
            "description": _first_user_text(events) or f"会话标题：{redact_text(session.title)}",
        },
        {
            "stage": "探索定位",
            "status": "有命令证据" if session.command_count else "命令较少",
            "description": f"捕获到 {session.command_count} 个命令类事件，{session.error_count} 个错误类信号。",
        },
        {
            "stage": "实现修改",
            "status": "有进展" if _has_work_signal(session, events) else "未明确",
            "description": "根据会话文本判断是否存在实现、修改、修复或导出动作。",
        },
        {
            "stage": "验收收尾",
            "status": verification["overall"],
            "description": "检查最终阶段是否有测试、构建、typecheck、健康检查或手动验收结果。",
        },
    ]


def _project_flow(sessions: list[SessionRecord], drafts: list[ImprovementDraft]) -> list[dict[str, str]]:
    return [
        {"stage": "采集会话", "status": "完成", "description": f"本次窗口纳入 {len(sessions)} 个可解析 Codex 会话。"},
        {"stage": "按项目聚合", "status": "完成", "description": "报告只展示当前项目聚合结果，避免跨项目建议混杂。"},
        {"stage": "复盘诊断", "status": "完成", "description": "基于结构化事件、失败信号、验证信号和用户纠正生成诊断。"},
        {"stage": "候选改进", "status": "待 review", "description": f"生成 {len(drafts)} 个候选动作，落地前需要人工确认。"},
    ]


def _context_frontload(
    session: SessionRecord,
    events: list[TranscriptEvent],
    verification: dict[str, Any],
) -> list[dict[str, str]]:
    items = []
    if not _has_verification(events):
        items.append(
            {
                "item": "项目验证命令",
                "appeared_when": "全会话未明确出现",
                "why_important": "没有标准验证入口时，AI 容易在收尾阶段只给出完成声明。",
                "suggested_location": "AGENTS.md / 完成标准",
            }
        )
    if _session_project(session) != "(unknown)":
        items.append(
            {
                "item": _session_project(session),
                "appeared_when": "会话元数据",
                "why_important": "项目路径可用于按项目聚合，避免跨项目经验混用。",
                "suggested_location": "report.json meta.project",
            }
        )
    return items or [
        {
            "item": "暂无明显缺失上下文",
            "appeared_when": "当前样本",
            "why_important": "继续观察更多会话后再判断是否需要沉淀。",
            "suggested_location": "后续复盘",
        }
    ]


def _project_context_frontload(drafts: list[ImprovementDraft]) -> list[dict[str, str]]:
    items = []
    for draft in drafts[:4]:
        items.append(
            {
                "item": redact_text(draft.title),
                "appeared_when": "近期会话聚合后",
                "why_important": redact_text(draft.evidence)[:220],
                "suggested_location": _target_for_category(draft.category),
            }
        )
    return items or [
        {
            "item": "项目命令和完成标准",
            "appeared_when": "建议持续维护",
            "why_important": "这些信息越早进入会话，越能减少无效探索。",
            "suggested_location": "AGENTS.md",
        }
    ]


def _intervention(events: list[TranscriptEvent]) -> dict[str, Any]:
    corrections = [
        _excerpt(redact_text(event.text), 260)
        for event in events
        if event.role == "user" and _looks_like_correction(event.text)
    ]
    if corrections:
        return {
            "observation": "会话中出现用户纠正，说明目标、目录、命令或约束可能没有提前进入上下文。",
            "suggestions": [
                "当 AI 连续沿错误方向推进时，让它暂停并复述当前假设。",
                "把重复纠正的稳定项目事实沉淀到项目说明或 checklist。",
            ],
            "examples": corrections[:3],
        }
    return {
        "observation": "未检测到明确用户纠正信号。",
        "suggestions": [
            "如果下次出现两轮以上方向偏差，应要求 AI 暂停并总结当前假设。",
            "复杂任务可以先要求定位结论，再允许修改。",
        ],
    }


def _artifacts(suggestions: list[dict[str, str]]) -> dict[str, str]:
    agents_lines = [
        "## AI Development Completion",
        "- Do not mark code changes complete until relevant verification has been run or explicitly waived.",
        "- Final responses must include commands run, results, and remaining risks.",
    ]
    checklist_lines = [
        "- [ ] 已确认任务目标和完成标准",
        "- [ ] 已运行最小相关验证",
        "- [ ] 最终回答包含命令和结果",
        "- [ ] 未运行的验证已说明原因",
    ]
    for suggestion in suggestions[:3]:
        agents_lines.append(f"- {suggestion['action']}")
        checklist_lines.append(f"- [ ] {suggestion['title']}")
    return {
        "agents_md_suggestion": "\n".join(agents_lines) + "\n",
        "checklist_suggestion": "\n".join(checklist_lines) + "\n",
    }


def _headline(issues: list[dict[str, Any]], verification: dict[str, Any]) -> str:
    if verification["overall"] != "验证闭环存在":
        return "本次会话有推进，但完成可信度需要补强。"
    if issues and issues[0].get("severity") in {"high", "critical"}:
        return "本次会话存在高优先级流程风险。"
    return "本次会话已形成可复盘的结构化报告。"


def _overall_summary(
    session: SessionRecord,
    issues: list[dict[str, Any]],
    verification: dict[str, Any],
) -> str:
    focus = "、".join(str(issue.get("title")) for issue in issues[:3])
    return (
        f"会话 `{redact_text(session.session_id)}` 共捕获 {session.message_count} 条消息、"
        f"{session.command_count} 个命令类事件。主要关注点：{focus or '暂无'}。"
        f"验收判断：{verification['overall']}。"
    )


def _project_overall(project_key: str, sessions: list[SessionRecord], issues: list[dict[str, Any]]) -> str:
    focus = "、".join(str(issue.get("title")) for issue in issues[:3])
    return f"`{redact_text(project_key)}` 最近窗口纳入 {len(sessions)} 个会话。主要建议聚焦：{focus or '继续积累样本'}。"


def _completion_confidence(issues: list[dict[str, Any]], verification: dict[str, Any]) -> str:
    if verification["overall"] != "验证闭环存在":
        return "medium_low"
    if any(issue.get("severity") in {"critical", "high"} for issue in issues):
        return "medium"
    return "medium_high"


def _top_focus(issues: list[dict[str, Any]], verification: dict[str, Any]) -> str:
    if verification["overall"] != "验证闭环存在":
        return "verification_gap"
    return str(issues[0].get("title") if issues else "baseline")


def _duration_minutes(started_at: str | None, updated_at: str | None) -> int | None:
    if not started_at or not updated_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() // 60))


def _check(name: str, found: bool, detail: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "found" if found else "not_found",
        "detail": detail if found else f"未发现。{detail}",
    }


def _contains_any(events: list[TranscriptEvent], terms: tuple[str, ...]) -> bool:
    text = "\n".join(event.text.lower() for event in events if _is_signal_event(event))
    return any(term.lower() in text for term in terms)


def _final_mentions_verification(events: list[TranscriptEvent]) -> bool:
    final = next((event for event in reversed(events) if event.role == "assistant" and _is_signal_event(event)), None)
    return bool(final and any(term in final.text.lower() for term in VERIFY_TERMS))


def _has_verification(events: list[TranscriptEvent]) -> bool:
    return _contains_any(events, tuple(str(term) for term in VERIFY_TERMS))


def _has_work_signal(session: SessionRecord, events: list[TranscriptEvent]) -> bool:
    if session.command_count:
        return True
    text = "\n".join(event.text.lower() for event in events if _is_signal_event(event))
    return any(term in text for term in ("修改", "修复", "实现", "完成", "changed", "fixed", "implemented", "updated"))


def _failed_command_count(events: list[TranscriptEvent]) -> int:
    return sum(1 for event in events if _is_command_event(event) and _looks_failed(event.text))


def _file_change_count(events: list[TranscriptEvent]) -> int:
    text = "\n".join(event.text for event in events if _is_signal_event(event))
    paths = set(re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|java|go|rs|md|toml|yml|yaml|json|sh)", text))
    if paths:
        return min(len(paths), 999)
    return 1 if re.search(r"修改|changed|updated|created|deleted|file", text, re.I) else 0


def _user_correction_count(events: list[TranscriptEvent]) -> int:
    return sum(1 for event in events if event.role == "user" and _looks_like_correction(event.text))


def _first_user_text(events: list[TranscriptEvent]) -> str:
    event = next((item for item in events if item.role == "user" and _is_signal_event(item)), None)
    return _excerpt(redact_text(event.text), 260) if event else ""


def _looks_like_correction(text: str) -> bool:
    return bool(re.search(r"不是这个|你忘了|刚才说过|不对|应该是|not this|you forgot|as i said|wrong", text, re.I))


def _looks_failed(text: str) -> bool:
    lowered = text.lower()
    if "process exited with code 0" in lowered:
        return False
    return any(term in lowered for term in ERROR_TERMS)


def _is_command_event(event: TranscriptEvent) -> bool:
    return "command" in event.kind.lower() or "command" in event.metadata or "cmd" in event.metadata


def _is_signal_event(event: TranscriptEvent) -> bool:
    if not event.text.strip():
        return False
    lowered = event.text.strip().lower()
    if lowered.startswith(
        (
            "<environment_context>",
            "<permissions",
            "<collaboration_mode>",
            "<skills_instructions>",
            "chunk id:",
            "cwd=",
            "model=",
        )
    ):
        return False
    return "you are codex" not in lowered and "original token count" not in lowered


def _evidence_analysis(event: TranscriptEvent) -> str:
    if event.role == "user":
        return "用于判断用户原始目标、约束或纠正信息。"
    if _looks_failed(event.text):
        return "用于判断失败信号和后续分诊是否充分。"
    if _is_command_event(event):
        return "用于判断会话中的命令执行和验证情况。"
    return "用于判断最终说明、进展声明或收尾信息。"


def _severity_for_category(category: str) -> str:
    if category in {"agents", "checklist", "ci"}:
        return "high"
    if category in {"script", "skill"}:
        return "medium"
    return "medium"


def _priority_for_severity(severity: str) -> str:
    return "high" if severity in {"critical", "high"} else "medium" if severity == "medium" else "low"


def _target_for_category(category: str) -> str:
    mapping = {
        "agents": "AGENTS.md",
        "checklist": "Checklist",
        "script": "Script",
        "skill": "Skill",
        "ci": "CI",
        "patterns": "Workflow",
    }
    return mapping.get(category, category or "Workflow")


def _artifact_action(artifact_type: str) -> str:
    return {
        "agents_md": "把该要求写入项目 AGENTS.md。",
        "checklist": "把该要求加入完成前 checklist。",
        "skill": "把该流程沉淀为可复用 skill。",
        "script": "把重复命令沉淀为脚本。",
        "ci": "把重复验证升级为 CI 检查。",
        "hook": "用 hook 强制执行关键检查。",
        "prompt_template": "把该要求加入任务 prompt 模板。",
    }.get(artifact_type, "把该建议沉淀为下一次会话可复用的工作流资产。")


def _session_project(session: SessionRecord) -> str:
    return redact_text(session.project_path or session.cwd or "(unknown)")


def _report_id(kind: str, value: str, generated_at: str) -> str:
    digest = hashlib.sha256(f"{kind}\n{value}\n{generated_at}".encode("utf-8")).hexdigest()[:10]
    return f"rep_{kind}_{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _excerpt(text: str, limit: int = 220) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _json_for_script(report: dict[str, Any]) -> str:
    raw = json.dumps(report, ensure_ascii=False, indent=2)
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _render_metrics(metrics: dict[str, Any]) -> str:
    labels = [
        ("main_issue_count", "主要问题"),
        ("verification_found", "发现验证"),
        ("user_corrections", "用户纠正"),
        ("failed_commands", "失败命令"),
        ("sessions", "会话数"),
        ("commands", "命令数"),
    ]
    parts = []
    for key, label in labels:
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, bool):
            value = "是" if value else "否"
        parts.append(f'<div class="metric"><strong>{_h(str(value))}</strong><span>{_h(label)}</span></div>')
    return '<div class="metrics">' + "".join(parts) + "</div>"


def _render_flow(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无流程数据。</p>'
    return "<ol class=\"flow\">" + "".join(
        f"<li><h3>{_h(str(item.get('stage') or '阶段'))} "
        f"<span class=\"status\">{_h(str(item.get('status') or 'unknown'))}</span></h3>"
        f"<p class=\"muted\">{_h(str(item.get('description') or ''))}</p></li>"
        for item in items
    ) + "</ol>"


def _render_issues(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无主要问题。</p>'
    parts = []
    for item in items:
        severity = _class_token(str(item.get("severity") or "medium"))
        refs = ", ".join(str(ref) for ref in _list(item.get("evidence_refs"))) or "无"
        parts.append(
            f'<article class="issue"><div class="issue-head"><h3>{_h(str(item.get("title") or "问题"))}</h3>'
            f'<span class="severity severity-{severity}">{_h(str(item.get("severity") or "medium"))}</span></div>'
            f'<div class="kv"><p><b>观察：</b>{_h(str(item.get("observation") or ""))}</p>'
            f'<p><b>影响：</b>{_h(str(item.get("impact") or ""))}</p>'
            f'<p><b>建议：</b>{_h(str(item.get("suggestion") or ""))}</p>'
            f'<p><b>证据：</b>{_h(refs)}</p></div></article>'
        )
    return "".join(parts)


def _render_suggestions(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无改进建议。</p>'
    parts = []
    for item in items:
        priority = _class_token(str(item.get("priority") or "medium"))
        parts.append(
            f'<article class="suggestion"><div class="suggestion-head"><h3>{_h(str(item.get("title") or "建议"))}</h3>'
            f'<span class="priority severity-{priority}">{_h(str(item.get("priority") or "medium"))}</span></div>'
            f'<div class="kv"><p><b>原因：</b>{_h(str(item.get("why") or ""))}</p>'
            f'<p><b>动作：</b>{_h(str(item.get("action") or ""))}</p>'
            f'<p><b>载体：</b>{_h(str(item.get("target") or "Workflow"))}</p></div></article>'
        )
    return "".join(parts)


def _render_evidence(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无证据。</p>'
    return "".join(
        f'<article class="evidence"><h3>{_h(str(item.get("id") or ""))} {_h(str(item.get("title") or ""))}</h3>'
        f'<p>{_h(str(item.get("content") or ""))}</p>'
        f'<p class="muted">{_h(str(item.get("analysis") or ""))}</p></article>'
        for item in items
    )


def _render_context_frontload(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无上下文前置建议。</p>'
    return "".join(
        f'<article class="check"><h3>{_h(str(item.get("item") or ""))}</h3>'
        f'<div class="kv"><p><b>出现时机：</b>{_h(str(item.get("appeared_when") or ""))}</p>'
        f'<p><b>价值：</b>{_h(str(item.get("why_important") or ""))}</p>'
        f'<p><b>建议位置：</b>{_h(str(item.get("suggested_location") or ""))}</p></div></article>'
        for item in items
    )


def _render_intervention(value: object) -> str:
    item = _dict(value)
    suggestions = "".join(f"<li>{_h(str(suggestion))}</li>" for suggestion in _list(item.get("suggestions")))
    examples = "".join(f"<li>{_h(str(example))}</li>" for example in _list(item.get("examples")))
    examples_html = f"<h3>示例</h3><ul>{examples}</ul>" if examples else ""
    return (
        f'<p class="muted">{_h(str(item.get("observation") or "暂无过程干预数据。"))}</p>'
        f"<ul>{suggestions}</ul>{examples_html}"
    )


def _render_verification(value: object) -> str:
    block = _dict(value)
    checks = [_dict(item) for item in _list(block.get("checks"))]
    checks_html = "".join(
        f'<article class="check"><h3>{_h(str(item.get("name") or ""))} '
        f'<span class="status">{_h(str(item.get("status") or ""))}</span></h3>'
        f'<p class="muted">{_h(str(item.get("detail") or ""))}</p></article>'
        for item in checks
    )
    closing = "".join(f"<li>{_h(str(item))}</li>" for item in _list(block.get("recommended_closing_format")))
    return (
        f'<p class="headline">{_h(str(block.get("overall") or "未知"))}</p>'
        f"{checks_html}<h3>推荐收尾格式</h3><ul>{closing}</ul>"
    )


def _render_artifacts(value: object) -> str:
    artifacts = _dict(value)
    agents = str(artifacts.get("agents_md_suggestion") or "")
    checklist = str(artifacts.get("checklist_suggestion") or "")
    return (
        '<div class="artifact-head"><h3>AGENTS.md 建议</h3><button type="button" data-copy-target="agents-md">复制</button></div>'
        f'<pre id="agents-md">{_h(agents)}</pre>'
        '<div class="artifact-head" style="margin-top:14px"><h3>Checklist 建议</h3><button type="button" data-copy-target="checklist">复制</button></div>'
        f'<pre id="checklist">{_h(checklist)}</pre>'
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _h(value: str) -> str:
    return html.escape(value, quote=True)


def _class_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return cleaned or "medium"
