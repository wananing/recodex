from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis import ERROR_TERMS, TEST_TERMS, session_signals
from .efficiency_analysis import run_efficiency_analysis
from .evidence_auditor import audit_report_evidence
from .evidence_mining import run_evidence_mining
from .models import ImprovementDraft, SessionRecord, TranscriptEvent
from .privacy import redact_text
from .report_contract import efficiency_report_contract
from .transcripts import extract_user_input_text

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

EFFICIENCY_SIGNAL_TAXONOMY: tuple[dict[str, Any], ...] = (
    {
        "id": "completeness_tracking",
        "label": "完整度/验收确认",
        "title": "需求完整度没有被持续追踪",
        "terms": ("完整", "没完成", "占位", "临时", "示例", "构建了吗", "测试了吗", "完成了吗", "对接上了吗", "整体实现"),
        "priority_weight": 4.5,
        "problem": "大需求缺少可持续更新的完成度账本，用户需要反复追问实现状态、占位和验收口径。",
        "why_slows_work": "上下文会被长会话稀释，agent 容易把局部完成误判为整体完成，用户必须用额外轮次重新拉齐。",
        "recommended_action": "建立需求完成度账本（实现矩阵）",
        "suggested_artifact": "implementation_ledger",
        "suggested_target": "docs/implementation-ledger.md",
        "trigger": "当任务包含多个能力、跨端链路或用户强调完整实现时。",
        "next_action": "下次先维护实现矩阵：功能点、状态、模块、是否占位、验证方式、真机/线上结果；每次收尾都更新它。",
        "gain": "减少反复确认完整度、占位和验收状态的额外轮次。",
    },
    {
        "id": "real_environment_validation",
        "label": "真机/线上/运维",
        "title": "真实环境验证晚于本地实现",
        "terms": ("启动", "重启", "服务", "局域网", "手机", "safari", "部署", "线上", "日志", "prod", "数据库", "配置", "nginx", "上传", "gps", "定位", "坐标"),
        "priority_weight": 1.2,
        "problem": "本地实现和构建通过不能覆盖手机、线上、网关、数据库、多服务联动等真实使用路径。",
        "why_slows_work": "问题会延迟到用户试用阶段才暴露，导致实现、排障、重启和日志追查反复交替。",
        "recommended_action": "建立真机/线上冒烟链路",
        "suggested_artifact": "smoke_checklist",
        "suggested_target": "docs/real-device-smoke-checklist.md",
        "trigger": "当任务涉及手机端、线上部署、上传、地图、外部服务、数据库或多服务启动时。",
        "next_action": "下次把真实链路列成冒烟清单，并在报告里标明哪些只本地验证、哪些已经真机或线上验证。",
        "gain": "减少“本地好了但实际不可用”的返工和反复启动排障。",
    },
    {
        "id": "contract_coupling",
        "label": "通道/契约/耦合",
        "title": "共享通道改动缺少影响面说明",
        "terms": ("ws", "websocket", "通道", "设备", "小程序", "协议", "影响", "耦合", "agent"),
        "priority_weight": 1.15,
        "problem": "共享通道、协议或认证链路改动前没有稳定说明影响范围和不影响范围。",
        "why_slows_work": "用户需要反复确认是否误伤设备、小程序或 agent 链路，agent 也容易在共享模块里局部修补。",
        "recommended_action": "建立影响面说明和契约回归",
        "suggested_artifact": "contract_checklist",
        "suggested_target": "docs/channel-contract-checklist.md",
        "trigger": "当改动涉及 websocket、设备通道、小程序、认证、协议或共享服务时。",
        "next_action": "下次改动前先写影响面：改哪些入口、不改哪些通道、契约如何回归，再开始实现。",
        "gain": "减少误伤共享链路和反复确认影响面的沟通成本。",
    },
    {
        "id": "correction_drift",
        "label": "用户纠正/方向偏差",
        "title": "开工前目标和边界没有充分复述",
        "terms": ("不对", "不是", "不要", "不应该", "后端干才有用", "只针对", "只盯"),
        "priority_weight": 2.0,
        "problem": "用户纠正集中出现时，说明 agent 的当前假设和用户目标已经偏离。",
        "why_slows_work": "偏离后继续实现会扩大返工范围，用户需要用更多消息把 agent 拉回正确方向。",
        "recommended_action": "开工前复述目标和边界",
        "suggested_artifact": "task_brief",
        "suggested_target": "docs/ai-task-brief.md",
        "trigger": "当用户纠正目标、实现层、范围或验收口径时。",
        "next_action": "下次出现纠正后先暂停编码，复述目标、非目标、验收方式和当前假设，再继续。",
        "gain": "减少方向偏差导致的无效实现和返工。",
    },
    {
        "id": "product_rule_system",
        "label": "场景/产品规则",
        "title": "产品规则被当成单点 bug 修复",
        "terms": ("场景", "策略", "answer_lead", "天气", "普通qa", "普通 qa", "先播词", "规划路线", "意图", "工具"),
        "priority_weight": 1.5,
        "problem": "分类、场景、话术或策略类问题容易被修成单个 case，而不是可回归的规则体系。",
        "why_slows_work": "每个新边界都会再次暴露类似问题，用户会反复提醒不要只修当前窄场景。",
        "recommended_action": "建立场景分类 eval 集",
        "suggested_artifact": "eval_suite",
        "suggested_target": "evals/scene-classification-cases.yml",
        "trigger": "当任务涉及场景识别、意图分类、话术、工具调用或策略分流时。",
        "next_action": "下次先列现有场景和反例，补 eval case，再改分类或策略代码。",
        "gain": "减少单点修补带来的同类规则回归。",
    },
    {
        "id": "release_hygiene",
        "label": "提交/发布/合并",
        "title": "提交发布信息没有持续结构化",
        "terms": ("commit", "commt", "提交", "合并", "拉", "git", "发布"),
        "priority_weight": 1.0,
        "problem": "长会话中改动跨模块积累后，提交、合并、发布状态容易变成额外排查工作。",
        "why_slows_work": "用户需要不断确认哪些改动已提交、能否拉取、线上是否已经发布。",
        "recommended_action": "固定提交发布检查清单",
        "suggested_artifact": "release_checklist",
        "suggested_target": "docs/release-checklist.md",
        "trigger": "当会话跨多个模块或需要上线、合并、部署时。",
        "next_action": "下次按变更组提交，并在收尾报告里列出 commit、未提交文件、部署状态和回滚点。",
        "gain": "减少提交状态、合并状态和发布状态的反复确认。",
    },
)

AI_ACTOR_RECOMMENDATION_MARKERS = (
    "主动向用户",
    "邀请用户",
    "引导用户",
    "展示效果",
    "展示功能效果",
    "最终回答",
    "助手",
)


def _user_oriented_analysis(analysis: dict[str, object] | None) -> dict[str, object] | None:
    if not analysis:
        return None
    oriented = dict(analysis)
    oriented["chat_findings"] = [
        _user_oriented_chat_finding(item)
        for item in _dict_items(analysis.get("chat_findings"))
    ]
    return oriented


def _user_oriented_chat_finding(finding: dict[str, Any]) -> dict[str, Any]:
    item = dict(finding)
    if str(item.get("opportunity_title") or "") in {
        "前置验收方式和完成边界",
        "建立需求完成度账本",
        "建立重构任务开工清单",
        "先明确重构验收边界",
    }:
        return _reader_facing_chat_finding(item)
    if str(item.get("title") or "") in {
        "验收方式和完成边界没有前置",
        "任务拆解没有变成持续账本",
        "重构任务缺少开工前清单",
        "重构验收边界没有提前固定",
    }:
        return _reader_facing_chat_finding(item)
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "problem", "cause", "impact", "recommendation")
    )
    if _is_refactor_start_checklist_finding(haystack):
        item.update(
            {
                "title": "重构任务缺少开工前清单",
                "problem": "发起重构任务时，范围、非目标、交付物、验收标准和优先级没有先固定成清单。",
                "cause": "重构类需求没有稳定的开工输入格式，长会话中容易边做边补条件。",
                "impact": "需求边界会在实现过程中反复变化，需要额外轮次重新拉齐方向。",
                "recommendation": (
                    "下次发起重构任务前，先要求输出重构任务开工清单：范围、非目标、交付物、"
                    "验收方式、优先级、占位/mock 边界和主要风险。"
                ),
                "artifact_type": "checklist",
                "artifact_title": "重构任务开工清单",
                "artifact_target_path": "recodex/docs/refactor_task_start_checklist.md",
                "opportunity_title": "建立重构任务开工清单",
            }
        )
    elif _is_refactor_acceptance_boundary_finding(haystack):
        item.update(
            {
                "title": "重构验收边界没有提前固定",
                "problem": "完成度、占位/mock、功能验证和新架构是否生效，到了后段才零散追问。",
                "cause": "重构完成的判断标准没有在开工前写清楚，也没有在收尾时逐项对照。",
                "impact": "验收口径需要后补，重构是否真正完成不够清楚。",
                "recommendation": (
                    "开工前先约定重构验收边界：完成度清单、占位/mock 边界、功能验证、"
                    "真实场景验证和未覆盖风险；收尾按清单逐项检查。"
                ),
                "artifact_type": "checklist",
                "artifact_title": "重构验收边界清单",
                "artifact_target_path": "recodex/docs/refactor_acceptance_checklist.md",
                "opportunity_title": "先明确重构验收边界",
            }
        )
    elif _is_ai_actor_delivery_finding(haystack):
        item.update(
            {
                "title": "验收方式和完成边界没有前置",
                "problem": (
                    "聊天记录显示，长会话后才开始追问任务列表、完成度、占位/mock "
                    "以及报告是否体现新架构，说明验收标准和完成边界没有在开工前持续外显。"
                ),
                "cause": "任务启动时没有先形成可更新的阶段、完成标准、验证路径和未完成项账本。",
                "impact": "需要用额外轮次重新确认方向、完整度和可验证结果，重构越长越容易返工。",
                "recommendation": (
                    "下次发起重构或报告类任务前，先要求输出任务列表、验收路径、前后差异、"
                    "未覆盖风险和占位/mock 边界；每轮收尾按清单更新完成状态。"
                ),
                "artifact_type": "checklist",
                "artifact_title": "重构任务验收与完成度清单",
                "artifact_target_path": "recodex/docs/checklists/refactor_delivery_checklist.md",
                "opportunity_title": "前置验收方式和完成边界",
            }
        )
    elif "任务拆解" in haystack or "任务列表" in haystack:
        item.update(
            {
                "title": "任务拆解没有变成持续账本",
                "problem": (
                    "任务开始时要求先列完整任务列表再逐项实现，但会话推进中没有把任务状态、占位和验收方式持续更新。"
                ),
                "cause": "任务列表只作为一次性沟通内容，没有变成实现过程中的共享账本。",
                "impact": "后续需要反复追问哪些已完成、哪些仍是占位或 mock。",
                "recommendation": (
                    "启动大任务时，先要求维护实现矩阵，并在每轮收尾更新状态、验证证据和未完成项。"
                ),
                "artifact_type": "checklist",
                "artifact_title": "需求完成度账本",
                "artifact_target_path": "docs/implementation-ledger.md",
                "opportunity_title": "建立需求完成度账本",
            }
        )
    return _reader_facing_chat_finding(item)


def _reader_facing_chat_finding(finding: dict[str, Any]) -> dict[str, Any]:
    item = dict(finding)
    for key in ("problem", "cause", "impact", "recommendation"):
        if key in item:
            item[key] = _reader_facing_report_text(str(item.get(key) or ""))
    return item


def _reader_facing_report_text(value: str) -> str:
    replacements = (
        ("用户发起重构任务前", "下次发起重构任务前"),
        ("用户发起重构任务时", "发起重构任务时"),
        ("用户在发起重构或报告类任务前", "下次发起重构或报告类任务前"),
        ("用户在开工前", "开工前"),
        ("用户启动大任务时应要求", "启动大任务时，先要求"),
        ("用户要求先列完整任务列表再逐项实现", "任务开始时要求先列完整任务列表再逐项实现"),
        ("用户只能在后段", "后段才"),
        ("用户需要自己补验收口径", "验收口径需要后补"),
        ("用户需要用额外轮次", "需要用额外轮次"),
        ("用户需要反复追问", "后续需要反复追问"),
        ("用户需要额外轮次", "需要额外轮次"),
        ("同一个用户动作", "同一个下次动作"),
        ("用户可执行动作", "可执行动作"),
    )
    result = value
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def _is_ai_actor_delivery_finding(text: str) -> bool:
    if any(marker in text for marker in AI_ACTOR_RECOMMENDATION_MARKERS):
        return True
    return "重构成果用户验证缺失" in text or ("用户验证" in text and "缺失" in text)


def _is_refactor_start_checklist_finding(text: str) -> bool:
    return any(token in text for token in ("提报checklist", "提报检查", "前置检查项", "需求提报"))


def _is_refactor_acceptance_boundary_finding(text: str) -> bool:
    return any(token in text for token in ("重构完成度验证标准缺失", "重构验收prompt", "完成度验证标准"))


def build_session_report_data(
    session: SessionRecord,
    events: list[TranscriptEvent],
    analysis: dict[str, object] | None = None,
    *,
    deep: bool = False,
) -> dict[str, Any]:
    analysis = _user_oriented_analysis(analysis)
    evidence = _session_evidence(events, session)
    signals = session_signals(events)
    verification = _verification_block(session, events)
    user_intent = _user_intent(events)
    core_diagnostics = _core_diagnostics_for_sessions(
        [session],
        {session.session_id: events},
    )
    efficiency_analysis = run_efficiency_analysis(
        [session],
        {session.session_id: events},
    ).to_payload()
    efficiency_diagnosis = _efficiency_diagnosis(events)
    chat_transcript_analysis = _chat_transcript_analysis(events, analysis)
    efficiency_analysis = _augmented_efficiency_analysis(
        efficiency_analysis,
        efficiency_diagnosis,
        analysis,
        chat_transcript_analysis,
    )
    issues = (
        _efficiency_issues(efficiency_analysis)
        or _analysis_issues(analysis)
        or _session_issues(session, events, signals, verification, evidence)
    )
    suggestions = (
        _efficiency_suggestions(efficiency_analysis)
        or _analysis_suggestions(analysis)
        or _suggestions_from_issues(issues)
    )
    generated_at = _now()
    project = _session_project(session)
    primary_improvement = _primary_improvement(efficiency_analysis, suggestions)
    base_summary = {
        "headline": _headline(issues, verification),
        "overall": _overall_summary(session, issues, verification),
        "completion_confidence": _completion_confidence(issues, verification),
        "top_focus": _top_focus(issues, verification),
        "user_intent": user_intent["primary_request"],
        "max_avoidable_cost": _max_avoidable_cost(efficiency_analysis),
        "primary_cause": _primary_cause(efficiency_analysis),
        "primary_improvement": primary_improvement,
    }
    report_focus = _report_focus(analysis, chat_transcript_analysis, base_summary)
    summary = _summary_with_report_focus(base_summary, report_focus)
    core_contract = _core_report_contract(
        core_diagnostics,
        summary=summary,
        verification=verification,
        outcome_scope="session",
        efficiency_analysis=efficiency_analysis,
    )
    core_contract = _core_contract_with_report_focus(core_contract, report_focus)
    conversation_analysis = _conversation_analysis(core_diagnostics, analysis, events)
    efficiency_actions = _efficiency_actions(
        core_contract,
        conversation_analysis,
        efficiency_diagnosis,
    )
    user_efficiency_analysis = _user_efficiency_analysis(
        chat_transcript_analysis,
        efficiency_analysis,
        efficiency_actions,
    )
    metrics = _session_metrics(
        issues=issues,
        user_intent=user_intent,
        verification=verification,
        events=events,
        session=session,
        signals=signals,
        efficiency_analysis=efficiency_analysis,
    )

    report = {
        "schema_version": "recodex_core_report_v1",
        **core_contract,
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
        "summary": summary,
        "llm_retro": _llm_retro_payload(analysis),
        "token_usage": _token_usage_payload(analysis),
        "metrics": metrics,
        "core_diagnostics": _report_safe_payload(core_diagnostics),
        "efficiency_analysis": efficiency_analysis,
        "efficiency_diagnosis": efficiency_diagnosis,
        "report_focus": report_focus,
        "chat_transcript_analysis": chat_transcript_analysis,
        "user_efficiency_analysis": user_efficiency_analysis,
        "conversation_analysis": conversation_analysis,
        "efficiency_actions": efficiency_actions,
        "flow": _session_flow(session, events, verification),
        "user_intent": user_intent,
        "issues": issues,
        "context_frontload": _context_frontload(session, events, verification),
        "intervention": _intervention(events),
        "verification": verification,
        "suggestions": suggestions,
        "artifacts": _artifacts(suggestions),
        "evidence": evidence,
    }
    _enrich_effect_observation(report)
    return _with_evidence_audit(report, deep=deep)


def build_project_report_data(
    project_key: str,
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
    drafts: Iterable[ImprovementDraft],
    since_label: str,
    *,
    deep: bool = False,
) -> dict[str, Any]:
    draft_list = list(drafts)
    generated_at = _now()
    all_events = [
        event
        for session in sessions
        for event in events_by_session.get(session.session_id, [])
    ]
    verification_found = any(
        _has_verification(events_by_session.get(session.session_id, []))
        for session in sessions
    )
    core_diagnostics = _core_diagnostics_for_sessions(sessions, events_by_session)
    efficiency_analysis = run_efficiency_analysis(sessions, events_by_session).to_payload()
    issues = _efficiency_issues(efficiency_analysis) or _project_issues(draft_list, sessions)
    suggestions = _efficiency_suggestions(efficiency_analysis) or _project_suggestions(draft_list)
    primary_improvement = _primary_improvement(efficiency_analysis, suggestions)
    verification = _project_verification(sessions, events_by_session, verification_found)
    summary = {
        "headline": "近期 Codex 会话已按项目聚合成复盘报告。",
        "overall": _project_overall(project_key, sessions, issues),
        "completion_confidence": "medium" if verification_found else "medium_low",
        "top_focus": issues[0]["title"] if issues else "继续积累样本",
        "max_avoidable_cost": _max_avoidable_cost(efficiency_analysis),
        "primary_cause": _primary_cause(efficiency_analysis),
        "primary_improvement": primary_improvement,
    }
    core_contract = _core_report_contract(
        core_diagnostics,
        summary=summary,
        verification=verification,
        outcome_scope="project",
        efficiency_analysis=efficiency_analysis,
    )
    efficiency_diagnosis = _efficiency_diagnosis(all_events)
    conversation_analysis = _conversation_analysis(core_diagnostics, None, all_events)
    chat_transcript_analysis = _chat_transcript_analysis(all_events, None)
    efficiency_actions = _efficiency_actions(
        core_contract,
        conversation_analysis,
        efficiency_diagnosis,
    )

    report = {
        "schema_version": "recodex_core_report_v1",
        **core_contract,
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
        "summary": summary,
        "llm_retro": {},
        "token_usage": _empty_token_usage(),
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
            "efficiency_findings": len(_list(efficiency_analysis.get("findings"))),
            "artifact_candidates": len(_list(efficiency_analysis.get("artifact_candidates"))),
        },
        "core_diagnostics": _report_safe_payload(core_diagnostics),
        "efficiency_analysis": efficiency_analysis,
        "efficiency_diagnosis": efficiency_diagnosis,
        "chat_transcript_analysis": chat_transcript_analysis,
        "conversation_analysis": conversation_analysis,
        "efficiency_actions": efficiency_actions,
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
        "verification": verification,
        "suggestions": suggestions,
        "artifacts": _artifacts(suggestions),
        "evidence": _project_evidence(draft_list, sessions, events_by_session),
    }
    return _with_evidence_audit(report, deep=deep)


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
    .token-usage {{ display: grid; gap: 12px; }}
    .token-totals {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }}
    .token-totals div, .token-call {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
    }}
    .token-totals strong {{ display: block; color: #142033; font-size: 18px; line-height: 1.2; }}
    .token-totals span, .token-call span {{ color: var(--muted); font-size: 12px; }}
    .token-calls {{ display: grid; gap: 8px; }}
    .token-call strong {{ display: block; margin: 4px 0; color: #142033; font-size: 13px; }}
    .core-chain {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .core-column {{
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .core-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 13px;
    }}
    .core-card .label {{
      display: inline-flex;
      margin-bottom: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: #344052;
      background: #fff;
      font-size: 12px;
    }}
    .core-card strong {{ display: block; margin-bottom: 6px; font-size: 14px; line-height: 1.35; }}
    .core-card p {{ color: var(--muted); font-size: 13px; }}
    .chat-analysis {{ display: grid; gap: 12px; }}
    .chat-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 14px;
    }}
    .chat-card header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .chat-card header strong {{ display: block; font-size: 15px; line-height: 1.35; }}
    .chat-card .basis {{ color: var(--muted); font-size: 13px; margin: 0 0 10px; }}
    .chat-evidence-list {{ display: grid; gap: 8px; margin-top: 10px; }}
    .chat-evidence {{
      border-left: 3px solid #bfdbfe;
      background: #f8fafc;
      padding: 8px 10px;
      border-radius: 6px;
    }}
    .chat-evidence code {{ color: #475569; font-size: 12px; }}
    .chat-evidence blockquote {{
      margin: 5px 0 0;
      color: #1f2937;
      font-size: 13px;
    }}
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
      .topbar, .summary, .grid, .core-chain {{ display: block; }}
      .meta {{ margin-top: 14px; min-width: 0; }}
      .metrics, .token-totals {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      h1 {{ font-size: 24px; }}
      .core-column + .core-column {{ margin-top: 12px; }}
    }}
    @media (max-width: 520px) {{
      .page {{ padding: 18px 12px 36px; }}
      .metrics, .token-totals {{ grid-template-columns: 1fr; }}
      .issue-head, .suggestion-head, .artifact-head {{ display: block; }}
      .severity, .priority, button {{ margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <p class="eyebrow">recodex AI 编程效率剖析报告</p>
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
        <h2>核心判断</h2>
        <p class="headline">{_h(str(summary.get("headline") or "暂无概览。"))}</p>
        <p class="muted">{_h(str(summary.get("overall") or ""))}</p>
      </section>
      <section class="panel">
        <h2>可节省成本</h2>
        {_render_core_diagnostics(report.get("core_diagnostics"), summary)}
      </section>
      <section class="panel">
        <h2>关键问题回答</h2>
        {_render_core_answers(report)}
      </section>
      {_render_evidence_audit(report.get("evidence_audit"))}
      <section class="panel">
        <h2>指标</h2>
        {_render_metrics(metrics)}
      </section>
      <section class="panel">
        <h2>Token 消耗</h2>
        {_render_token_usage(report.get("token_usage"))}
      </section>
    </div>

    <section class="panel">
      <h2>问题 → 改进 → 沉淀建议</h2>
      {_render_core_chain(report.get("core_diagnostics"))}
    </section>

    <section class="panel">
      <h2>效率诊断过程</h2>
      {_render_efficiency_diagnosis(report.get("efficiency_diagnosis"))}
    </section>

    <section class="panel">
      <h2>聊天与提效分析</h2>
      {_render_user_efficiency_analysis(report.get("user_efficiency_analysis"), report.get("chat_transcript_analysis"))}
    </section>

    <section class="panel">
      <h2>证据中的聊天片段</h2>
      {_render_conversation_analysis(report.get("conversation_analysis"))}
    </section>

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
          <h2>可复制建议</h2>
          {_render_artifacts(report.get("artifacts"))}
        </section>
      </aside>
    </div>
    <p class="footer">本报告由本地命令生成；页面只展示嵌入数据，不重新读取会话文件，不扫描本地目录。</p>
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


def _core_diagnostics_for_sessions(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> dict[str, Any]:
    result = run_evidence_mining(sessions, events_by_session)
    return _normalize_core_diagnostics({
        "cost_ledger": _json_ready(result.cost_ledger),
        "evidence_refs": [_json_ready(ref) for ref in result.evidence_refs],
        "findings": [_json_ready(finding) for finding in result.findings],
        "improvement_opportunities": [
            _json_ready(opportunity)
            for opportunity in result.improvement_opportunities
        ],
        "artifact_candidates": [
            _json_ready(candidate)
            for candidate in result.artifact_candidates
        ],
        "coverage": dict(result.coverage),
    })


def _normalize_core_diagnostics(core: dict[str, Any]) -> dict[str, Any]:
    raw_findings = _dict_items(core.get("findings"))
    findings, finding_id_map = _merge_findings_with_id_map(raw_findings, limit=3)
    kept_finding_ids = {str(item.get("id") or "") for item in findings}
    raw_opportunities = [
        _rewrite_opportunity_finding_refs(item, finding_id_map, kept_finding_ids)
        for item in _dict_items(core.get("improvement_opportunities"))
    ]
    opportunities = _merge_core_items(
        raw_opportunities,
        key_fields=("title",),
        limit=3,
    )
    artifacts = _merge_core_items(
        _dict_items(core.get("artifact_candidates")),
        key_fields=("artifact_type", "target_path"),
        limit=3,
    )
    normalized = dict(core)
    normalized["findings"] = findings
    normalized["improvement_opportunities"] = opportunities
    normalized["artifact_candidates"] = artifacts
    coverage = dict(_dict(core.get("coverage")))
    coverage.update(
        {
            "reported_findings": len(findings),
            "reported_improvement_opportunities": len(opportunities),
            "reported_artifact_candidates": len(artifacts),
        }
    )
    normalized["coverage"] = coverage
    return normalized


def _merge_findings_with_id_map(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    id_map: dict[str, str] = {}
    for item in items:
        key = _core_item_key(item, ("title",))
        item_id = str(item.get("id") or "")
        if key in by_key:
            existing = by_key[key]
            existing_id = str(existing.get("id") or item_id)
            if item_id:
                id_map[item_id] = existing_id
            _merge_sequence_field(existing, item, "evidence_refs")
            _merge_sequence_field(existing, item, "source_card_ids")
            continue
        if len(merged) >= limit:
            if item_id:
                id_map[item_id] = ""
            continue
        clone = dict(item)
        if item_id:
            id_map[item_id] = item_id
        by_key[key] = clone
        merged.append(clone)
    return merged, id_map


def _rewrite_opportunity_finding_refs(
    opportunity: dict[str, Any],
    finding_id_map: dict[str, str],
    kept_finding_ids: set[str],
) -> dict[str, Any]:
    rewritten = dict(opportunity)
    refs = []
    for raw in _list(opportunity.get("source_finding_ids")):
        ref = finding_id_map.get(str(raw), str(raw))
        if ref not in kept_finding_ids:
            continue
        if ref and ref not in refs:
            refs.append(ref)
    rewritten["source_finding_ids"] = refs
    return rewritten


def _merge_core_items(
    items: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        key = _core_item_key(item, key_fields)
        if key in by_key:
            existing = by_key[key]
            _merge_sequence_field(existing, item, "evidence_refs")
            _merge_sequence_field(existing, item, "source_finding_ids")
            _merge_sequence_field(existing, item, "source_card_ids")
            continue
        clone = dict(item)
        by_key[key] = clone
        merged.append(clone)
        if len(merged) >= limit:
            break
    return merged


def _core_item_key(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    values = [str(item.get(field) or "").strip().lower() for field in fields]
    key = "|".join(values).strip("|")
    return key or str(item.get("id") or len(item))


def _merge_sequence_field(target: dict[str, Any], source: dict[str, Any], field: str) -> None:
    values: list[str] = []
    for raw in (*_list(target.get(field)), *_list(source.get(field))):
        value = str(raw)
        if value and value not in values:
            values.append(value)
    if values:
        target[field] = values


def _core_report_contract(
    core: dict[str, Any],
    *,
    summary: dict[str, Any],
    verification: dict[str, Any],
    outcome_scope: str,
    efficiency_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = core
    return efficiency_report_contract(
        efficiency_analysis=efficiency_analysis,
        summary=summary,
        verification=verification,
        outcome_scope=outcome_scope,
    )


def _llm_retro_payload(analysis: dict[str, object] | None) -> dict[str, Any]:
    if not analysis:
        return {}
    return {
        "overall_assessment": redact_text(str(analysis.get("overall_assessment") or "")),
        "main_findings": _json_ready(analysis.get("main_findings") or []),
        "chat_findings": _json_ready(
            [_user_oriented_chat_finding(item) for item in _dict_items(analysis.get("chat_findings"))]
        ),
        "improvement_candidates": _json_ready(
            [_user_oriented_improvement_candidate(item) for item in _dict_items(analysis.get("improvement_candidates"))]
        ),
        "next_time_suggestions": _json_ready(analysis.get("next_time_suggestions") or []),
        "what_went_well": _json_ready(analysis.get("what_went_well") or []),
    }


def _user_oriented_improvement_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    haystack = " ".join(str(item.get(key) or "") for key in ("title", "why"))
    if _is_refactor_start_checklist_finding(haystack):
        item.update(
            {
                "title": "重构任务开工清单",
                "artifact_type": "checklist",
                "why": "用于在重构开始前固定范围、非目标、交付物、验收方式、优先级和主要风险，减少后续补充和返工。",
            }
        )
    elif _is_refactor_acceptance_boundary_finding(haystack) or "验收提问模板" in haystack:
        item.update(
            {
                "title": "重构验收边界清单",
                "artifact_type": "checklist",
                "why": "用于在开工前约定完成度、占位/mock、功能验证、真实场景验证和未覆盖风险，收尾按清单检查。",
            }
        )
    return item


def _report_focus(
    analysis: dict[str, object] | None,
    chat_analysis: dict[str, Any],
    fallback_summary: dict[str, Any],
) -> dict[str, Any]:
    if not analysis or chat_analysis.get("source") != "llm":
        return {}
    findings = _ranked_chat_findings(analysis) or _ranked_llm_findings(analysis)
    primary_finding = findings[0] if findings else {}
    artifacts = _focus_artifacts(analysis, findings, primary_finding, chat_analysis)
    title = _focus_title(primary_finding, chat_analysis, fallback_summary)
    primary_cause = _focus_primary_cause(primary_finding, chat_analysis, fallback_summary)
    primary_improvement = _focus_primary_improvement(primary_finding, artifacts, fallback_summary)
    evidence_refs = _dedup_strings([
        *_list(primary_finding.get("evidence_refs")),
        *_list(chat_analysis.get("evidence_refs")),
    ])
    return {
        "source": "llm_chat_transcript",
        "source_finding_id": str(primary_finding.get("id") or ""),
        "title": title,
        "primary_problem": title,
        "primary_cause": primary_cause,
        "primary_improvement": primary_improvement,
        "summary": redact_text(str(chat_analysis.get("summary") or "")),
        "evidence_refs": evidence_refs[:8],
        "key_observations": _focus_key_observations(chat_analysis),
        "friction_points": _redacted_string_list(chat_analysis.get("friction_points"))[:5],
        "recommended_artifacts": artifacts,
    }


def _summary_with_report_focus(
    summary: dict[str, Any],
    report_focus: dict[str, Any],
) -> dict[str, Any]:
    if not report_focus:
        return summary
    focused = dict(summary)
    title = str(report_focus.get("title") or "")
    if title:
        focused["headline"] = f"聊天与提效分析显示：{title}"
        focused["top_focus"] = title
    focused["primary_cause"] = str(
        report_focus.get("primary_cause")
        or focused.get("primary_cause")
        or ""
    )
    focused["primary_improvement"] = str(
        report_focus.get("primary_improvement")
        or focused.get("primary_improvement")
        or ""
    )
    focused["report_focus_source"] = str(report_focus.get("source") or "")
    return focused


def _core_contract_with_report_focus(
    contract: dict[str, Any],
    report_focus: dict[str, Any],
) -> dict[str, Any]:
    if not report_focus:
        return contract
    focused = dict(contract)
    core_answers = dict(_dict(focused.get("core_answers")))
    core_answers["why_it_happened"] = str(
        report_focus.get("primary_cause")
        or core_answers.get("why_it_happened")
        or ""
    )
    core_answers["highest_leverage_change"] = str(
        report_focus.get("primary_improvement")
        or core_answers.get("highest_leverage_change")
        or ""
    )
    artifact = _first_dict(report_focus.get("recommended_artifacts"))
    if artifact:
        core_answers["what_should_be_preserved_as_artifact"] = str(
            artifact.get("mechanism") or artifact.get("target_path") or ""
        )
    focused["core_answers"] = core_answers
    return focused


def _ranked_llm_findings(analysis: dict[str, object]) -> list[dict[str, Any]]:
    findings = _dict_items(analysis.get("main_findings"))
    return sorted(
        findings,
        key=lambda item: (
            _severity_rank(str(item.get("severity") or "")),
            _float(item.get("confidence")),
        ),
        reverse=True,
    )


def _ranked_chat_findings(analysis: dict[str, object]) -> list[dict[str, Any]]:
    findings = []
    for index, finding in enumerate(_dict_items(analysis.get("chat_findings")), start=1):
        payload = _user_oriented_chat_finding(finding)
        payload.setdefault("id", f"chat_finding_{index}")
        findings.append(payload)
    return sorted(
        findings,
        key=lambda item: (
            _severity_rank(str(item.get("severity") or "")),
            _float(item.get("confidence")),
        ),
        reverse=True,
    )


def _focus_title(
    finding: dict[str, Any],
    chat_analysis: dict[str, Any],
    fallback_summary: dict[str, Any],
) -> str:
    for raw in (
        finding.get("title"),
        _first_string(chat_analysis.get("friction_points")),
        fallback_summary.get("top_focus"),
    ):
        value = redact_text(str(raw or "").strip())
        if value:
            return value
    return "聊天原文揭示的主要协作摩擦"


def _focus_primary_cause(
    finding: dict[str, Any],
    chat_analysis: dict[str, Any],
    fallback_summary: dict[str, Any],
) -> str:
    for raw in (
        finding.get("cause"),
        finding.get("problem"),
        _first_string(chat_analysis.get("friction_points")),
        chat_analysis.get("summary"),
        fallback_summary.get("primary_cause"),
    ):
        value = redact_text(str(raw or "").strip())
        if value:
            return value
    return "证据不足，暂不推断根因。"


def _focus_primary_improvement(
    finding: dict[str, Any],
    artifacts: list[dict[str, Any]],
    fallback_summary: dict[str, Any],
) -> str:
    artifact = artifacts[0] if artifacts else {}
    for raw in (
        finding.get("recommendation"),
        artifact.get("title"),
        artifact.get("rationale"),
        fallback_summary.get("primary_improvement"),
    ):
        value = redact_text(str(raw or "").strip())
        if value:
            return value
    return "继续积累报告证据后再沉淀改进。"


def _focus_artifacts(
    analysis: dict[str, object],
    findings: list[dict[str, Any]],
    primary_finding: dict[str, Any],
    chat_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    artifact_findings = _ranked_focus_artifact_findings([
        item
        for item in findings
        if item.get("artifact_type") and item.get("artifact_type") != "none"
    ], primary_finding)
    chat_finding_artifacts = [
        _focus_chat_finding_artifact(finding, chat_analysis, index)
        for index, finding in enumerate(artifact_findings[:3], start=1)
    ]
    if chat_finding_artifacts:
        return chat_finding_artifacts
    candidates = _ranked_llm_artifact_candidates(analysis, primary_finding)
    artifacts = [
        _focus_artifact_payload(candidate, primary_finding, chat_analysis, index)
        for index, candidate in enumerate(candidates[:3], start=1)
    ]
    if artifacts:
        return artifacts
    if not primary_finding:
        return []
    fallback = {
        "title": "报告展示验收 checklist",
        "artifact_type": "checklist",
        "priority": "high",
        "why": str(primary_finding.get("recommendation") or primary_finding.get("problem") or ""),
        "evidence_refs": _list(primary_finding.get("evidence_refs")),
    }
    return [_focus_artifact_payload(fallback, primary_finding, chat_analysis, 1)]


def _focus_chat_finding_artifact(
    finding: dict[str, Any],
    chat_analysis: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    mechanism = _focus_mechanism(str(finding.get("artifact_type") or "checklist"))
    title = redact_text(
        str(finding.get("artifact_title") or finding.get("title") or "会话改进产物")
    )
    evidence_refs = _dedup_strings([
        *_list(finding.get("evidence_refs")),
        *_list(chat_analysis.get("evidence_refs")),
    ])
    target_path = _normalized_focus_target_path(
        mechanism,
        str(finding.get("artifact_target_path") or ""),
        title,
    )
    return {
        "id": _focus_artifact_id(title, mechanism, index),
        "source": "llm_chat_transcript",
        "source_finding_id": str(finding.get("id") or ""),
        "mechanism": mechanism,
        "target_path": target_path,
        "title": title,
        "rationale": redact_text(
            str(finding.get("recommendation") or finding.get("impact") or "")
        ),
        "proposed_content": _focus_artifact_content(
            {
                "title": title,
                "why": finding.get("recommendation"),
                "mechanism": mechanism,
                "target_path": target_path,
            },
            finding,
            evidence_refs,
        ),
        "status": "proposed",
        "priority": (
            "high"
            if str(finding.get("severity") or "") in {"high", "critical"}
            else "medium"
        ),
        "evidence_refs": evidence_refs[:8],
    }


def _ranked_focus_artifact_findings(
    findings: list[dict[str, Any]],
    primary_finding: dict[str, Any],
) -> list[dict[str, Any]]:
    primary_id = str(primary_finding.get("id") or "")
    return sorted(
        findings,
        key=lambda item: (
            str(item.get("id") or "") == primary_id,
            _focus_artifact_business_rank(item),
            _severity_rank(str(item.get("severity") or "")),
            _float(item.get("confidence")),
        ),
        reverse=True,
    )


def _focus_artifact_business_rank(item: dict[str, Any]) -> int:
    theme = _focus_artifact_theme(item, item)
    return {
        "milestone": 50,
        "validation": 45,
        "delivery": 45,
        "environment": 20,
    }.get(theme, 30)


def _ranked_llm_artifact_candidates(
    analysis: dict[str, object],
    primary_finding: dict[str, Any],
) -> list[dict[str, Any]]:
    finding_refs = {str(ref) for ref in _list(primary_finding.get("evidence_refs"))}
    candidates = [
        _user_oriented_improvement_candidate(item)
        for item in _dict_items(analysis.get("improvement_candidates"))
    ]
    return sorted(
        candidates,
        key=lambda item: (
            bool(finding_refs.intersection(str(ref) for ref in _list(item.get("evidence_refs")))),
            _priority_rank(str(item.get("priority") or "")),
        ),
        reverse=True,
    )


def _focus_artifact_payload(
    candidate: dict[str, Any],
    primary_finding: dict[str, Any],
    chat_analysis: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    mechanism = _focus_mechanism(str(candidate.get("artifact_type") or "checklist"))
    title = redact_text(str(candidate.get("title") or "报告验收改进"))
    evidence_refs = _dedup_strings([
        *_list(candidate.get("evidence_refs")),
        *_list(primary_finding.get("evidence_refs")),
        *_list(chat_analysis.get("evidence_refs")),
    ])
    return {
        "id": _focus_artifact_id(title, mechanism, index),
        "source": "llm_chat_transcript",
        "mechanism": mechanism,
        "target_path": _normalized_focus_target_path(mechanism, "", title),
        "title": title,
        "rationale": redact_text(
            str(candidate.get("why") or primary_finding.get("recommendation") or "")
        ),
        "proposed_content": _focus_artifact_content(candidate, primary_finding, evidence_refs),
        "status": "proposed",
        "priority": str(candidate.get("priority") or "medium"),
        "evidence_refs": evidence_refs[:8],
    }


def _focus_artifact_content(
    candidate: dict[str, Any],
    primary_finding: dict[str, Any],
    evidence_refs: list[str],
) -> str:
    title = str(candidate.get("title") or "报告验收改进")
    problem = str(primary_finding.get("problem") or candidate.get("why") or "")
    recommendation = str(primary_finding.get("recommendation") or candidate.get("why") or "")
    theme = _focus_artifact_theme(candidate, primary_finding)
    if theme == "milestone":
        return _focus_milestone_prompt_content(title, problem, recommendation, evidence_refs)
    if theme == "validation":
        return _focus_validation_checklist_content(title, problem, recommendation, evidence_refs)
    if theme == "delivery":
        return _focus_delivery_checklist_content(title, problem, recommendation, evidence_refs)
    if theme == "environment":
        return _focus_environment_script_content(title, problem, recommendation, evidence_refs)
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            f"- 问题：{redact_text(problem)}",
            f"- 动作：{redact_text(recommendation)}",
            f"- 验收：{_focus_artifact_acceptance(candidate, primary_finding)}",
            f"- 证据：{', '.join(evidence_refs[:6]) or '待补充'}",
            "",
        ]
    )


def _focus_artifact_theme(candidate: dict[str, Any], finding: dict[str, Any]) -> str:
    haystack = " ".join(
        str(value or "")
        for value in (
            candidate.get("title"),
            candidate.get("target_path"),
            candidate.get("mechanism"),
            candidate.get("artifact_type"),
            finding.get("title"),
            finding.get("problem"),
            finding.get("recommendation"),
            finding.get("artifact_target_path"),
        )
    ).lower()
    if any(token in haystack for token in ("任务列表", "完成度账本", "implementation-ledger", "实现矩阵")):
        return "milestone"
    if any(token in haystack for token in ("里程碑", "milestone", "拆分", "阶段")):
        return "milestone"
    if any(token in haystack for token in ("真实场景", "验收", "验证", "测试", "test")):
        return "validation"
    if any(token in haystack for token in ("交付", "完成", "占位", "mock", "真实实现")):
        return "delivery"
    if any(token in haystack for token in ("环境", "java", "依赖", "precheck", "预检")):
        return "environment"
    return "general"


def _focus_milestone_prompt_content(
    title: str,
    problem: str,
    recommendation: str,
    evidence_refs: list[str],
) -> str:
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            "### 触发条件",
            "- 用户一次性给出多阶段需求、完整实现要求、第三方对接或后续追加功能。",
            "- 会话已经从调研/分析扩展到实现/联调/验收。",
            "",
            "### 开工前提问",
            "- 本轮只交付哪个阶段？",
            "- 哪些能力必须真实实现，哪些可以先保留 mock 或占位？",
            "- 每个阶段的验收方式是什么？",
            "",
            "### 阶段拆分模板",
            "| 阶段 | 目标 | 非目标 | 交付物 | 验收方式 | 是否进入下一阶段 |",
            "| --- | --- | --- | --- | --- | --- |",
            "| 1 | 调研/方案 | 不改代码 | 结论和风险 | 用户确认 | 待确认 |",
            "| 2 | 最小闭环实现 | 不扩展体验优化 | 可运行链路 | 构建+场景验证 | 待确认 |",
            "| 3 | 完整体验和边界 | 不新增无关功能 | 完整实现清单 | 真实场景验收 | 待确认 |",
            "",
            "### 进入实现前确认",
            f"- 问题：{redact_text(problem)}",
            f"- 建议动作：{redact_text(recommendation)}",
            "- 确认后再进入下一阶段，未确认时只做当前阶段的必要工作。",
            f"- 证据：{', '.join(evidence_refs[:6]) or '待补充'}",
            "",
        ]
    )


def _focus_validation_checklist_content(
    title: str,
    problem: str,
    recommendation: str,
    evidence_refs: list[str],
) -> str:
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            "### 适用场景",
            "- 新增页面、接口、移动端交互、第三方服务或跨端链路。",
            "",
            "### 验收清单",
            "- [ ] 覆盖真实用户路径，不只停留在编译/构建通过。",
            "- [ ] 记录验证入口、测试账号/数据、操作步骤和实际结果。",
            "- [ ] 标明未覆盖的设备、浏览器、第三方服务或线上环境。",
            "- [ ] 若使用 mock，说明替换真实 provider 的条件。",
            "- [ ] 报告中附上失败截图、日志或用户可复核的证据。",
            "",
            f"- 问题：{redact_text(problem)}",
            f"- 动作：{redact_text(recommendation)}",
            f"- 证据：{', '.join(evidence_refs[:6]) or '待补充'}",
            "",
        ]
    )


def _focus_delivery_checklist_content(
    title: str,
    problem: str,
    recommendation: str,
    evidence_refs: list[str],
) -> str:
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            "### 交付状态",
            "- [ ] 已完成：列出已真实实现并验证的功能。",
            "- [ ] 未完成：列出未实现、待联调或待用户确认的功能。",
            "- [ ] 占位：列出临时实现、示例数据和需要删除的 fallback。",
            "- [ ] mock：列出 mock provider、mock 数据和替换真实实现的条件。",
            "- [ ] 风险：列出真实场景、权限、外部服务或移动端适配风险。",
            "",
            "### 收尾格式",
            "- 本轮完成：",
            "- 本轮未完成：",
            "- mock/占位：",
            "- 已验证：",
            "- 仍需用户确认：",
            "",
            f"- 问题：{redact_text(problem)}",
            f"- 动作：{redact_text(recommendation)}",
            f"- 证据：{', '.join(evidence_refs[:6]) or '待补充'}",
            "",
        ]
    )


def _focus_environment_script_content(
    title: str,
    problem: str,
    recommendation: str,
    evidence_refs: list[str],
) -> str:
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            "```bash",
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "java -version",
            "test -x ./gradlew || test -x ./gradlew21",
            "```",
            "",
            f"- 问题：{redact_text(problem)}",
            f"- 动作：{redact_text(recommendation)}",
            f"- 证据：{', '.join(evidence_refs[:6]) or '待补充'}",
            "",
        ]
    )


def _focus_artifact_acceptance(
    candidate: dict[str, Any],
    primary_finding: dict[str, Any],
) -> str:
    target_path = str(candidate.get("target_path") or "")
    title = str(candidate.get("title") or primary_finding.get("title") or "")
    if "report" in target_path or "报告" in title or "dashboard" in title.lower():
        return "报告首屏必须展示聊天内容分析的主问题、主要卡点和首要动作。"
    if str(primary_finding.get("artifact_type") or "") == "checklist":
        return "下一次同类会话开始前使用该清单确认阶段目标、非目标和验收标准。"
    return "下次同类任务能直接复用该沉淀建议，并能通过引用证据检查是否生效。"


def _focus_key_observations(chat_analysis: dict[str, Any]) -> list[str]:
    observations = _redacted_string_list(chat_analysis.get("key_observations"))[:5]
    frictions = _redacted_string_list(chat_analysis.get("friction_points"))[:5]
    has_compile_positive = any(
        "编译" in item and ("充分" in item or "通过" in item)
        for item in observations
    )
    has_scenario_gap = any(
        ("真实场景" in item or "使用场景" in item) and ("验证" in item or "测试" in item)
        for item in [*observations, *frictions]
    )
    if not has_compile_positive or not has_scenario_gap:
        return observations
    normalized = "编译/构建验证较充分，但真实场景验证仍不足。"
    filtered = [
        item
        for item in observations
        if not ("编译" in item and ("充分" in item or "通过" in item))
    ]
    return _dedup_strings([normalized, *filtered])[:5]


def _focus_mechanism(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"agents_md", "checklist", "skill", "script", "ci", "hook", "prompt_template"}:
        return normalized
    return "checklist"


def _normalized_focus_target_path(mechanism: str, raw_target: str, title: str) -> str:
    raw = raw_target.strip().replace("\\", "/")
    if raw == "AGENTS.md":
        return raw
    if not raw:
        raw = _focus_target_path(mechanism, title)
    if mechanism == "prompt_template" and raw.startswith("prompts/"):
        raw = f"docs/{raw}"
    if mechanism == "checklist" and raw.startswith("checklists/"):
        raw = f"docs/{raw}"
    if raw.startswith("docs/"):
        return _hyphenated_markdown_path(raw)
    return raw


def _hyphenated_markdown_path(path: str) -> str:
    if not path.endswith(".md"):
        return path
    parts = path.split("/")
    filename = parts[-1]
    stem = filename[:-3]
    stem = stem.replace("_", "-").replace(" ", "-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    parts[-1] = f"{stem}.md"
    return "/".join(parts)


def _focus_target_path(mechanism: str, title: str) -> str:
    lowered = title.lower()
    if mechanism == "agents_md":
        return "AGENTS.md"
    if mechanism == "skill":
        return "skills/refactor-report-review/SKILL.md"
    if mechanism == "script":
        return "scripts/report-acceptance-check.sh"
    if mechanism in {"hook", "ci"}:
        return ".github/workflows/report-acceptance.yml"
    if mechanism == "prompt_template":
        return "docs/prompts/refactor-status-template.md"
    if "dashboard" in lowered:
        return "docs/dashboard-report-acceptance-checklist.md"
    if "重构" in title:
        return "docs/refactor-completion-checklist.md"
    return "docs/report-focus-checklist.md"


def _focus_artifact_id(title: str, mechanism: str, index: int) -> str:
    digest = hashlib.sha256(f"{title}\0{mechanism}\0{index}".encode("utf-8")).hexdigest()
    return f"focus_art_{digest[:12]}"


def _first_string(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    return ""


def _severity_rank(value: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(value.lower(), 0)


def _priority_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value.lower(), 0)


def _token_usage_payload(analysis: dict[str, object] | None) -> dict[str, Any]:
    if not analysis:
        return _empty_token_usage()
    usage = analysis.get("_recodex_token_usage")
    if not isinstance(usage, dict):
        return _empty_token_usage()
    calls = [_dict(item) for item in _list(usage.get("calls"))]
    totals = _dict(usage.get("totals"))
    return {
        "calls": [_json_ready(item) for item in calls],
        "totals": _json_ready(totals),
    }


def _empty_token_usage() -> dict[str, Any]:
    return {
        "calls": [],
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "current_run_total_tokens": 0,
            "cached_calls": 0,
            "estimated_calls": 0,
            "provider_reported_calls": 0,
        },
    }


def _efficiency_diagnosis(events: list[TranscriptEvent]) -> dict[str, Any]:
    messages = _pure_user_messages(events)
    signal_summary = [_efficiency_signal_summary_item(rule, messages) for rule in EFFICIENCY_SIGNAL_TAXONOMY]
    ranked_signals = sorted(
        [item for item in signal_summary if int(item.get("count") or 0) > 0],
        key=lambda item: (float(item.get("score") or 0), int(item.get("count") or 0)),
        reverse=True,
    )
    problems = [
        _efficiency_problem_from_signal(item, index)
        for index, item in enumerate(ranked_signals[:5], start=1)
    ]
    return {
        "method": {
            "version": "user_message_efficiency_v1",
            "scope": "pure_user_messages",
            "ranking": "score = signal_count * preventability_or_impact_weight",
            "evidence_policy": "每个信号保留最多 5 条用户原话作为代表证据；具体项目内容只作为证据，不作为主结论。",
        },
        "message_count": len(messages),
        "analysis_summary": _efficiency_diagnosis_summary(messages, ranked_signals),
        "process": _efficiency_diagnosis_process(len(events), len(messages), signal_summary, problems),
        "signal_summary": signal_summary,
        "efficiency_problems": problems,
    }


def _pure_user_messages(events: list[TranscriptEvent]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for event in events:
        if event.role != "user":
            continue
        text = extract_user_input_text(event.text) or event.text
        if _is_non_chat_user_context(text):
            continue
        cleaned = redact_text(text.strip())
        if not cleaned:
            continue
        messages.append(
            {
                "event_id": f"event_{event.event_index}",
                "event_index": event.event_index,
                "text": cleaned,
            }
        )
    return messages


def _pure_chat_messages(events: list[TranscriptEvent]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for event in events:
        if event.role not in {"user", "assistant"}:
            continue
        if _is_tool_like_chat_event(event):
            continue
        text = extract_user_input_text(event.text) if event.role == "user" else event.text
        if not text or _is_non_chat_user_context(text):
            continue
        cleaned = redact_text(text.strip())
        if not cleaned:
            continue
        messages.append(
            {
                "event_id": f"event_{event.event_index}",
                "event_index": event.event_index,
                "role": event.role,
                "kind": event.kind,
                "created_at": event.created_at,
                "text": cleaned,
            }
        )
    return messages


def _is_tool_like_chat_event(event: TranscriptEvent) -> bool:
    kind = event.kind.lower()
    if any(token in kind for token in ("tool", "command", "exec", "function_call")):
        return True
    if any(key in event.metadata for key in ("command", "cmd", "tool_call_id", "exit_code")):
        return True
    lowered = event.text.strip().lower()
    return lowered.startswith(("command=", "cmd=", "tool output:", "process exited with code"))


def _is_non_chat_user_context(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return (
        stripped.startswith("<environment_context>")
        or stripped.startswith("<turn_aborted>")
        or stripped.startswith("<permissions")
        or stripped.startswith("<collaboration_mode>")
    )


def _efficiency_signal_summary_item(
    rule: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_terms = rule.get("terms")
    terms = [str(term) for term in raw_terms] if isinstance(raw_terms, (list, tuple)) else []
    matched_messages: list[dict[str, Any]] = []
    matched_terms: list[str] = []
    for message in messages:
        text = str(message.get("text") or "")
        terms_for_message = _matched_terms(text, terms)
        if not terms_for_message:
            continue
        for term in terms_for_message:
            if term not in matched_terms:
                matched_terms.append(term)
        if len(matched_messages) < 5:
            matched_messages.append(
                {
                    "event_id": str(message.get("event_id") or ""),
                    "event_index": message.get("event_index"),
                    "quote": _excerpt(text, 220),
                    "matched_terms": terms_for_message,
                }
            )
    count = sum(1 for message in messages if _matched_terms(str(message.get("text") or ""), terms))
    weight = float(rule.get("priority_weight") or 1.0)
    return {
        "id": str(rule.get("id") or ""),
        "label": str(rule.get("label") or ""),
        "title": str(rule.get("title") or ""),
        "count": count,
        "score": round(count * weight, 2),
        "priority_weight": weight,
        "matched_terms": matched_terms[:12],
        "evidence": matched_messages,
        "problem": str(rule.get("problem") or ""),
        "why_slows_work": str(rule.get("why_slows_work") or ""),
        "recommended_action": str(rule.get("recommended_action") or ""),
        "suggested_artifact": str(rule.get("suggested_artifact") or ""),
        "suggested_target": str(rule.get("suggested_target") or ""),
        "trigger": str(rule.get("trigger") or ""),
        "next_action": str(rule.get("next_action") or ""),
        "expected_efficiency_gain": str(rule.get("gain") or ""),
    }


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term and term.lower() in lowered]


def _efficiency_problem_from_signal(signal: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "id": f"eff_problem_{signal.get('id')}",
        "source_signal_id": str(signal.get("id") or ""),
        "rank": rank,
        "title": str(signal.get("title") or signal.get("label") or "效率问题"),
        "problem": str(signal.get("problem") or ""),
        "why_it_slows_work": str(signal.get("why_slows_work") or ""),
        "recommended_action": str(signal.get("recommended_action") or ""),
        "suggested_artifact": str(signal.get("suggested_artifact") or ""),
        "suggested_target": str(signal.get("suggested_target") or ""),
        "trigger": str(signal.get("trigger") or ""),
        "next_action": str(signal.get("next_action") or ""),
        "expected_efficiency_gain": str(signal.get("expected_efficiency_gain") or ""),
        "signal_count": int(signal.get("count") or 0),
        "score": float(signal.get("score") or 0),
        "matched_terms": _list(signal.get("matched_terms")),
        "evidence": _list(signal.get("evidence"))[:5],
    }


def _efficiency_diagnosis_summary(
    messages: list[dict[str, Any]],
    ranked_signals: list[dict[str, Any]],
) -> str:
    if not messages:
        return "未提取到纯用户消息，暂不能从聊天记录复刻效率分析过程。"
    if not ranked_signals:
        return f"已提取 {len(messages)} 条纯用户消息，但没有命中当前效率信号分类。"
    top = "、".join(
        f"{item.get('label')} {int(item.get('count') or 0)} 次"
        for item in ranked_signals[:3]
    )
    return f"已提取 {len(messages)} 条纯用户消息；最高频/最高杠杆信号为：{top}。"


def _efficiency_diagnosis_process(
    event_count: int,
    message_count: int,
    signal_summary: list[dict[str, Any]],
    problems: list[dict[str, Any]],
) -> list[dict[str, str]]:
    active_signals = [item for item in signal_summary if int(item.get("count") or 0) > 0]
    top_signal = active_signals[0] if active_signals else {}
    return [
        {
            "step": "extract_user_messages",
            "title": "提取纯用户消息",
            "description": f"从 {event_count} 个事件中只保留用户真实输入，过滤环境上下文和中断标记，得到 {message_count} 条消息。",
            "output": f"{message_count} 条纯用户消息",
        },
        {
            "step": "classify_efficiency_signals",
            "title": "归类效率信号",
            "description": "按完整度、真实环境、通道契约、用户纠正、产品规则、提交发布六类信号扫描用户消息。",
            "output": f"{len(active_signals)} 类信号命中",
        },
        {
            "step": "rank_efficiency_problems",
            "title": "排序效率问题",
            "description": "按命中次数乘以可预防性/影响权重排序，避免只把最高频的低层操作当主结论。",
            "output": str(top_signal.get("label") or "暂无主信号"),
        },
        {
            "step": "select_representative_evidence",
            "title": "选择代表证据",
            "description": "每类信号最多保留 5 条用户原话，具体项目内容只作为证据，不直接成为报告主结论。",
            "output": f"{sum(len(_list(item.get('evidence'))) for item in signal_summary)} 条代表证据",
        },
        {
            "step": "route_to_reusable_actions",
            "title": "路由到提效动作",
            "description": "把效率问题映射到实现账本、冒烟清单、契约回归、任务 brief、eval 或发布 checklist。",
            "output": f"{len(problems)} 个候选提效动作",
        },
    ]


def _efficiency_actions_from_diagnosis(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for index, problem in enumerate(_dict_items(diagnosis.get("efficiency_problems"))[:3], start=1):
        evidence = [_dict(item) for item in _list(problem.get("evidence"))]
        quote = str(evidence[0].get("quote") or "") if evidence else ""
        evidence_refs = [str(item.get("event_id") or "") for item in evidence if item.get("event_id")]
        signal_count = int(problem.get("signal_count") or 0)
        evidence_summary = (
            f"{signal_count} 条用户消息命中；代表证据：{_excerpt(quote, 160)}"
            if quote
            else f"{signal_count} 条用户消息命中。"
        )
        actions.append(
            {
                "id": f"efficiency_action_{problem.get('source_signal_id') or index}",
                "rank": index,
                "title": str(problem.get("recommended_action") or problem.get("title") or "提效动作"),
                "source_finding": str(problem.get("title") or "效率问题"),
                "trigger": str(problem.get("trigger") or "当同类效率信号再次出现时。"),
                "next_action": _ensure_next_time_action(str(problem.get("next_action") or "先执行该提效动作。")),
                "expected_efficiency_gain": str(problem.get("expected_efficiency_gain") or problem.get("why_it_slows_work") or ""),
                "suggested_artifact": str(problem.get("suggested_artifact") or "review"),
                "suggested_target": str(problem.get("suggested_target") or "人工确认"),
                "evidence_summary": evidence_summary,
                "evidence_refs": evidence_refs[:8],
            }
        )
    return actions


def _ensure_next_time_action(action: str) -> str:
    return action if action.startswith("下次") else f"下次{action}"


def _efficiency_actions(
    core: dict[str, Any],
    conversation_analysis: list[dict[str, Any]],
    efficiency_diagnosis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    diagnosis_actions = _efficiency_actions_from_diagnosis(_dict(efficiency_diagnosis))
    findings = [_dict(item) for item in _list(core.get("findings"))]
    opportunities = [_dict(item) for item in _list(core.get("improvement_opportunities"))]
    if not opportunities:
        opportunities = [
            {
                "id": str(finding.get("id") or f"finding_{index}"),
                "title": str(finding.get("recommendation") or finding.get("title") or "改进动作"),
                "best_action": str(finding.get("recommendation") or ""),
                "recommended_mechanism": _mechanism_for_finding(finding),
                "suggested_target": _target_for_mechanism(_mechanism_for_finding(finding)),
                "source_finding_ids": [str(finding.get("id") or "")],
                "evidence_refs": _list(finding.get("evidence_refs")),
            }
            for index, finding in enumerate(findings[:3])
        ]
    core_actions: list[dict[str, Any]] = []
    for index, opportunity in enumerate(opportunities[:3], start=1):
        finding = _source_finding_for_opportunity(opportunity, findings)
        mechanism = str(opportunity.get("recommended_mechanism") or _mechanism_for_finding(finding) or "review")
        source_title = str(finding.get("title") or opportunity.get("cause") or opportunity.get("title") or "流程问题")
        evidence_refs = _dedup_strings([*_list(opportunity.get("evidence_refs")), *_list(finding.get("evidence_refs"))])
        evidence = _conversation_card_for_item(opportunity, finding, conversation_analysis)
        action = {
            "id": str(opportunity.get("id") or f"efficiency_action_{index}"),
            "rank": index,
            "title": _efficiency_action_title(mechanism, opportunity, finding),
            "source_finding": source_title,
            "trigger": _efficiency_trigger(mechanism, opportunity, finding),
            "next_action": _efficiency_next_action(mechanism, opportunity),
            "expected_efficiency_gain": _efficiency_gain(mechanism, _dict(core.get("cost_ledger")), opportunity, finding),
            "suggested_artifact": mechanism,
            "suggested_target": str(opportunity.get("suggested_target") or _target_for_mechanism(mechanism)),
            "evidence_summary": _efficiency_evidence_summary(evidence, opportunity, finding),
            "evidence_refs": evidence_refs[:8],
        }
        core_actions.append(action)
    return _merge_efficiency_actions([*core_actions, *diagnosis_actions])[:3]


def _user_efficiency_analysis(
    chat_analysis: dict[str, Any],
    efficiency_analysis: dict[str, Any],
    efficiency_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    findings = _dict_items(efficiency_analysis.get("findings"))
    guidance = [
        _user_efficiency_guidance_item(action, findings)
        for action in efficiency_actions[:3]
    ]
    guidance = [item for item in guidance if item]
    top_title = str(guidance[0].get("title") or "") if guidance else ""
    if top_title:
        summary = (
            f"聊天记录和效率诊断指向同一个下次动作：{top_title}。"
            "下次先把目标、阶段、验收方式、占位/mock 边界和验证证据写成可更新清单，再进入实现。"
        )
    else:
        summary = (
            "聊天记录和效率诊断会合并为可执行动作；当前样本证据不足，暂不生成固定建议。"
        )
    return {
        "version": "user_efficiency_guidance_v1",
        "subject": "user_developer_workflow",
        "summary": summary,
        "top_guidance": guidance,
        "chat_evidence_refs": _dedup_strings(_list(chat_analysis.get("evidence_refs")))[:8],
        "efficiency_evidence_refs": _dedup_strings(
            [
                ref
                for action in efficiency_actions
                for ref in _list(action.get("evidence_refs"))
            ]
        )[:8],
        "method": {
            "merged_sources": [
                "raw_user_and_assistant_chat_text",
                "user_message_efficiency_signals",
                "avoidable_cost_findings",
            ],
            "excluded": [
                "tool_outputs_as_chat_conclusions",
                "assistant_success_claims_as_primary_subject",
            ],
        },
    }


def _user_efficiency_guidance_item(
    action: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    source_title = str(action.get("source_finding") or "")
    source = next(
        (
            finding
            for finding in findings
            if str(finding.get("title") or "") == source_title
            or source_title in str(finding.get("title") or "")
        ),
        {},
    )
    title = str(action.get("title") or source.get("opportunity_title") or source.get("title") or "")
    if not title:
        return {}
    return {
        "title": title,
        "why": redact_text(
            _reader_facing_report_text(str(
                source.get("observation")
                or source.get("root_cause")
                or action.get("evidence_summary")
                or source_title
                or "该动作由聊天证据和效率成本共同支持。"
            ))
        ),
        "next_action": redact_text(
            _reader_facing_report_text(str(action.get("next_action") or source.get("recommendation") or ""))
        ),
        "expected_efficiency_gain": redact_text(str(action.get("expected_efficiency_gain") or "")),
        "suggested_target": str(action.get("suggested_target") or source.get("suggested_target") or ""),
        "source_finding": source_title,
        "evidence_refs": _dedup_strings([
            *_list(action.get("evidence_refs")),
            *_list(source.get("evidence_refs")),
        ])[:8],
    }


def _merge_efficiency_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for action in actions:
        target = str(action.get("suggested_target") or "")
        title = str(action.get("title") or "")
        key = (
            "target" if target else "title",
            target or title,
        )
        if key in by_key:
            existing = by_key[key]
            _merge_sequence_field(existing, action, "evidence_refs")
            new_summary = str(action.get("evidence_summary") or "")
            old_summary = str(existing.get("evidence_summary") or "")
            if "代表证据" in new_summary and "代表证据" not in old_summary:
                existing["evidence_summary"] = new_summary
            continue
        item = dict(action)
        item["rank"] = len(merged) + 1
        by_key[key] = item
        merged.append(item)
    return merged


def _source_finding_for_opportunity(
    opportunity: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    source_ids = {str(item) for item in _list(opportunity.get("source_finding_ids")) if item}
    for finding in findings:
        if str(finding.get("id") or "") in source_ids:
            return finding
    return {}


def _conversation_card_for_item(
    opportunity: dict[str, Any],
    finding: dict[str, Any],
    conversation_analysis: list[dict[str, Any]],
) -> dict[str, Any]:
    ids = {str(opportunity.get("id") or ""), str(finding.get("id") or "")}
    titles = {str(opportunity.get("title") or ""), str(finding.get("title") or "")}
    for card in conversation_analysis:
        if str(card.get("id") or "") in ids or str(card.get("title") or "") in titles:
            return card
    return {}


def _efficiency_action_title(
    mechanism: str,
    opportunity: dict[str, Any],
    finding: dict[str, Any],
) -> str:
    title = str(opportunity.get("title") or "")
    target = str(opportunity.get("suggested_target") or "")
    if "implementation-ledger" in target or "完成度账本" in title:
        return "建立需求完成度账本"
    if "验收方式" in title or "重构验收" in title:
        return title
    if mechanism == "agents_md":
        return "把稳定项目事实前置到 AGENTS.md"
    if mechanism == "hook_or_ci":
        return "把高风险边界变成自动检查"
    if mechanism == "script":
        return "固定失败命令定位入口"
    if mechanism == "checklist":
        if title and title not in {"降低完成验证转移成本"}:
            return title
        return "下次先复现目标失败命令"
    if mechanism == "skill":
        return "把高频多步流程沉淀为固定流程"
    if mechanism == "prompt_template":
        return "把任务开场问题固化为对话模板"
    return str(opportunity.get("best_action") or finding.get("recommendation") or opportunity.get("title") or "执行一个可复用提效动作")


def _efficiency_trigger(
    mechanism: str,
    opportunity: dict[str, Any],
    finding: dict[str, Any],
) -> str:
    title = str(opportunity.get("title") or "")
    target = str(opportunity.get("suggested_target") or "")
    if "开工清单" in title or "refactor_task_start" in target:
        return "当准备发起重构、大改版或跨模块任务，需要先把范围和验收口径固定下来时。"
    if "implementation-ledger" in target or "完成度账本" in title:
        return "当任务要求完整实现、任务列表、占位/mock 状态或阶段完成度时。"
    if "验收方式" in title or "重构验收" in title or "delivery-checklist" in target:
        return "当准备发起重构、页面或报告改版，并且需要判断是否真正完成时。"
    if "ai-task-brief" in target or "开工前复述" in title:
        return "当目标、范围、实现层或验收口径被纠正时。"
    if "evals/" in target or "eval" in title.lower() or "场景分类" in title:
        return "当任务涉及分类、场景、话术、策略分流或容易被修成单个 case 时。"
    if mechanism == "agents_md":
        return "当下一次任务再次需要先解释项目目录、模块边界、启动方式或常用命令时。"
    if mechanism == "hook_or_ci":
        return "当任务涉及生产风险、敏感文件、高风险命令、必须验证的边界条件时。"
    if mechanism == "script":
        return "当用户、CI 或日志已经给出具体失败命令，但会话仍在跑泛化验证时。"
    if mechanism == "checklist":
        return "当 assistant 准备说“修好了/完成了”之前，尤其是用户已经指出失败命令或验收口径时。"
    if mechanism == "skill":
        return "当同一类多步骤问题在多个会话反复出现，并且已有足够证据支持固定流程时。"
    return str(opportunity.get("problem") or finding.get("impact") or "当同类成本再次出现时。")


def _efficiency_next_action(mechanism: str, opportunity: dict[str, Any]) -> str:
    action = str(opportunity.get("best_action") or "把该改进沉淀为下一次可直接执行的流程。")
    target = str(opportunity.get("suggested_target") or _target_for_mechanism(mechanism))
    title = str(opportunity.get("title") or "")
    if "开工清单" in title or "refactor_task_start" in target:
        return f"下次发起重构前先填写或要求生成开工清单：范围、非目标、交付物、验收方式、优先级、占位/mock 边界和主要风险；落点：{target}。"
    if "implementation-ledger" in target or "完成度账本" in title:
        return f"下次先维护实现矩阵：任务、状态、是否占位/mock、验证方式和证据；落点：{target}。"
    if "验收方式" in title or "重构验收" in title or "delivery-checklist" in target:
        return f"下次开工前先要求列出验收入口、前后差异、真实场景验证方式和未覆盖风险；收尾按清单对照，落点：{target}。"
    if "ai-task-brief" in target or "开工前复述" in title:
        return f"下次先复述目标、非目标、验收方式和当前假设；用户确认后再进入实现，落点：{target}。"
    if "evals/" in target or "eval" in title.lower() or "场景分类" in title:
        return f"下次先列现有场景、反例、预期结果和回归样例，再修改分类或策略代码；落点：{target}。"
    if mechanism == "checklist":
        return f"下次先把用户/CI 指定的失败命令写进 checklist，并在复现前不改跑泛化验证；落点：{target}。"
    if mechanism == "script":
        return f"下次直接运行或补齐统一入口脚本，脚本先打印目标失败命令、复现步骤和标准验证；落点：{target}。"
    if mechanism == "agents_md":
        return f"下次会话开始前先读取项目默认事实；把模块边界、常用命令、启动方式和验收标准写进 {target}。"
    if mechanism == "hook_or_ci":
        return f"下次不要靠口头提醒；把必须执行的验证或高风险边界放进自动检查，落点：{target}。"
    if mechanism == "skill":
        return f"下次同类任务直接触发可复用流程；先人工确认证据，再写入 {target}。"
    return f"下次先执行这个动作：{action}；落点：{target}。"


def _efficiency_gain(
    mechanism: str,
    ledger: dict[str, Any],
    opportunity: dict[str, Any],
    finding: dict[str, Any],
) -> str:
    cost = _dict(finding.get("observed_cost"))
    parts = []
    for key, label in (
        ("repeated_file_reads", "重复读文件"),
        ("repeated_commands", "重复命令"),
        ("failed_commands", "失败命令"),
        ("user_corrections", "用户纠正"),
        ("verification_followups", "验证追问"),
        ("extra_turns", "额外轮次"),
    ):
        value = int(cost.get(key) or ledger.get(key) or 0)
        if value:
            parts.append(f"{label} {value} 次")
    if parts:
        return f"优先减少 {'，'.join(parts[:3])}。"
    if mechanism == "agents_md":
        return "减少开局探索、重复解释项目事实和上下文补充轮次。"
    if mechanism == "script":
        return "减少错误验证命令和无新信息的重复排查。"
    if mechanism == "hook_or_ci":
        return "减少靠人工记忆维护安全边界导致的返工。"
    return str(opportunity.get("impact") or finding.get("impact") or "减少同类返工成本。")


def _efficiency_evidence_summary(
    evidence: dict[str, Any],
    opportunity: dict[str, Any],
    finding: dict[str, Any],
) -> str:
    quotes = [
        str(item.get("quote") or "")
        for item in [_dict(raw) for raw in _list(evidence.get("evidence"))]
        if str(item.get("quote") or "").strip()
    ]
    quote = quotes[0] if quotes else ""
    basis = str(evidence.get("basis") or finding.get("impact") or opportunity.get("impact") or "")
    if quote:
        return f"证据片段：{_excerpt(quote, 160)}；判断依据：{_excerpt(basis, 180)}"
    return _excerpt(str(opportunity.get("problem") or finding.get("observation") or basis), 260)


def _mechanism_for_finding(finding: dict[str, Any]) -> str:
    category = str(finding.get("category") or "")
    title = str(finding.get("title") or "")
    if "验证" in title or "verification" in category:
        return "checklist"
    if "命令" in title or "command" in category:
        return "script"
    if "风险" in title or "safety" in category:
        return "hook_or_ci"
    if "项目" in title or "context" in category:
        return "agents_md"
    return "checklist"


def _target_for_mechanism(mechanism: str) -> str:
    return {
        "agents_md": "AGENTS.md",
        "hook_or_ci": ".github/workflows/ai-review.yml",
        "script": "scripts/ai-review.sh",
        "checklist": "docs/ai-coding-checklist.md",
        "skill": "skills/<reviewed-workflow>/SKILL.md",
        "prompt_template": "docs/prompts/ai-task-template.md",
    }.get(mechanism, "人工确认")


def _dedup_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw)
        if value and value not in result:
            result.append(value)
    return result


def _conversation_analysis(
    core: dict[str, Any],
    analysis: dict[str, object] | None,
    events: list[TranscriptEvent],
) -> list[dict[str, Any]]:
    evidence_by_id = {
        str(item.get("id") or ""): _dict(item)
        for item in _dict_items(core.get("evidence_refs"))
        if item.get("id")
    }
    cards: list[dict[str, Any]] = []
    for kind, items in (
        ("finding", _dict_items(core.get("findings"))),
        ("opportunity", _dict_items(core.get("improvement_opportunities"))),
    ):
        for item in items[:3]:
            refs = [str(ref) for ref in _list(item.get("evidence_refs")) if str(ref) in evidence_by_id]
            core_evidence = [_conversation_evidence_payload(evidence_by_id[ref]) for ref in refs[:5]]
            chat_evidence = _chat_context_for_refs(events, [evidence_by_id[ref] for ref in refs])
            evidence = _merge_chat_and_core_evidence(chat_evidence, core_evidence)
            if not evidence:
                continue
            title = str(item.get("title") or ("核心结论" if kind == "finding" else "改进机会"))
            cards.append(
                {
                    "id": str(item.get("id") or f"{kind}_{len(cards) + 1}"),
                    "kind": kind,
                    "title": title,
                    "evidence_label": "聊天证据",
                    "analysis": _conversation_analysis_sentence(kind, item, evidence),
                    "basis": _conversation_basis(item),
                    "evidence_refs": refs,
                    "evidence": evidence,
                    "llm_notes": _llm_notes_for_refs(analysis, refs),
                }
            )
    return cards[:6]


def _chat_transcript_analysis(
    events: list[TranscriptEvent],
    analysis: dict[str, object] | None,
) -> dict[str, Any]:
    messages = _pure_chat_messages(events)
    llm_analysis = _dict(analysis.get("chat_transcript_analysis")) if analysis else {}
    evidence_refs = _chat_analysis_refs(analysis or {}, llm_analysis, messages)
    transcript_sample = _chat_transcript_sample(messages, evidence_refs)
    source = "llm" if llm_analysis else "rules"
    summary = str(llm_analysis.get("summary") or "").strip()
    if not summary:
        summary = (
            f"已提取 {len(messages)} 条用户/助手聊天文字；未启用模型聊天内容分析。"
            if messages
            else "未提取到可用于聊天原文分析的用户/助手文字。"
        )
    return {
        "method": {
            "version": "raw_chat_transcript_v1",
            "scope": "raw_user_and_assistant_chat_text",
            "privacy": "redacted",
            "excluded": [
                "tool_calls",
                "tool_outputs",
                "command_results",
                "environment_context",
                "system_or_developer_instructions",
            ],
        },
        "source": source,
        "message_count": len(messages),
        "included_message_count": len(transcript_sample),
        "summary": redact_text(summary),
        "key_observations": _redacted_string_list(llm_analysis.get("key_observations"))[:5],
        "friction_points": _redacted_string_list(llm_analysis.get("friction_points"))[:5],
        "evidence_refs": evidence_refs,
        "transcript_sample": transcript_sample,
    }


def _chat_transcript_sample(
    messages: list[dict[str, Any]],
    evidence_refs: list[str],
) -> list[dict[str, Any]]:
    message_by_ref = {str(item.get("event_id") or ""): item for item in messages}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any] | None) -> None:
        if not item:
            return
        event_id = str(item.get("event_id") or "")
        if not event_id or event_id in seen:
            return
        seen.add(event_id)
        selected.append(
            {
                "event_id": event_id,
                "event_index": item.get("event_index"),
                "role": str(item.get("role") or ""),
                "quote": _excerpt(str(item.get("text") or ""), 420),
            }
        )

    for ref in evidence_refs:
        add(message_by_ref.get(ref))
    for item in messages[:12]:
        add(item)
    return selected[:16]


def _chat_analysis_refs(
    analysis: dict[str, object],
    llm_analysis: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[str]:
    message_refs = {str(item.get("event_id") or "") for item in messages}
    candidate_refs = list(_list(llm_analysis.get("evidence_refs")))
    for section in ("chat_findings", "main_findings", "improvement_candidates"):
        for item in _dict_items(analysis.get(section)):
            candidate_refs.extend(_list(item.get("evidence_refs")))
    refs = [str(ref) for ref in candidate_refs if str(ref) in message_refs]
    if refs:
        return _dedup_strings(refs)[:24]
    return [str(item.get("event_id") or "") for item in messages[:5] if item.get("event_id")]


def _redacted_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [redact_text(str(item)) for item in value if str(item).strip()]


def _conversation_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref_id": str(item.get("id") or ""),
        "event_id": str(item.get("event_id") or ""),
        "source_file": str(item.get("source_file") or ""),
        "role": str(item.get("role") or "evidence"),
        "quote": redact_text(str(item.get("quote") or "")),
        "reason": str(item.get("reason") or "Supports this report conclusion."),
    }


def _chat_context_for_refs(
    events: list[TranscriptEvent],
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_indices = [
        index
        for ref in refs
        if (index := _event_index_from_ref(str(ref.get("event_id") or ""))) is not None
    ]
    nearby: list[TranscriptEvent] = []
    for event in events:
        if event.role not in {"user", "assistant"} or not _is_signal_event(event):
            continue
        if target_indices and not any(0 <= target - event.event_index <= 5 or abs(target - event.event_index) <= 2 for target in target_indices):
            continue
        nearby.append(event)
    if not nearby:
        nearby = _session_chat_signal_events(events)
    return [_chat_event_payload(event) for event in nearby[:4]]


def _session_chat_signal_events(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    corrections = [
        event
        for event in events
        if event.role == "user" and _is_signal_event(event) and _looks_like_correction(extract_user_input_text(event.text) or event.text)
    ]
    user_requests = [
        event
        for event in events
        if event.role == "user" and _is_signal_event(event) and event not in corrections
    ]
    assistant_turns = [
        event
        for event in events
        if event.role == "assistant" and _is_signal_event(event)
    ]
    selected: list[TranscriptEvent] = []
    for event in [*corrections[:3], *(user_requests[:1]), *(assistant_turns[-1:])]:
        if event not in selected:
            selected.append(event)
    return selected


def _chat_event_payload(event: TranscriptEvent) -> dict[str, Any]:
    user_input = extract_user_input_text(event.text) if event.role == "user" else None
    quote = _excerpt(redact_text(user_input or event.text), 520)
    return {
        "ref_id": f"event_{event.event_index}",
        "event_id": f"event_{event.event_index}",
        "source_file": "",
        "role": event.role,
        "quote": quote,
        "reason": "Conversation turn near the referenced evidence.",
    }


def _merge_chat_and_core_evidence(
    chat_evidence: list[dict[str, Any]],
    core_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in [*chat_evidence, *core_evidence]:
        key = str(item.get("ref_id") or item.get("event_id") or item.get("quote") or "")
        if not key or any(str(existing.get("ref_id") or existing.get("event_id") or existing.get("quote") or "") == key for existing in merged):
            continue
        merged.append(item)
        if len(merged) >= 5:
            break
    return merged


def _event_index_from_ref(value: str) -> int | None:
    match = re.search(r"_(\d+)$", value)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _conversation_analysis_sentence(
    kind: str,
    item: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    title = str(item.get("title") or "这条结论")
    lead_quote = str(evidence[0].get("quote") or "")
    if kind == "opportunity":
        action = str(item.get("best_action") or item.get("recommended_change") or "")
        route = str(item.get("routing_reason") or "")
        return (
            f"这条改进不是从统计项直接跳出来的，而是由 {len(evidence)} 条聊天证据支撑："
            f"代表片段是“{lead_quote}”。建议动作：{action}；路由依据：{route}"
        )
    cause = str(item.get("cause") or "")
    recommendation = str(item.get("recommendation") or "")
    return (
        f"这条结论“{title}”由 {len(evidence)} 条聊天证据支撑："
        f"代表片段是“{lead_quote}”。根因判断：{cause}；建议：{recommendation}"
    )


def _conversation_basis(item: dict[str, Any]) -> str:
    for key in ("impact", "cause", "problem", "observation", "best_action", "recommendation"):
        value = str(item.get(key) or "").strip()
        if value:
            return redact_text(value)
    return "报告当前只有证据引用，缺少可读解释。"


def _llm_notes_for_refs(
    analysis: dict[str, object] | None,
    refs: list[str],
) -> list[dict[str, str]]:
    if not analysis or not refs:
        return []
    ref_set = set(refs)
    notes: list[dict[str, str]] = []
    for key, title_key, body_key in (
        ("main_findings", "title", "problem"),
        ("improvement_candidates", "title", "why"),
    ):
        for item in _dict_items(analysis.get(key)):
            item_refs = {str(ref) for ref in _list(item.get("evidence_refs"))}
            if not item_refs.intersection(ref_set):
                continue
            notes.append(
                {
                    "title": redact_text(str(item.get(title_key) or "模型结论")),
                    "body": redact_text(str(item.get(body_key) or "")),
                }
            )
    return notes[:3]


def _augmented_efficiency_analysis(
    efficiency_analysis: dict[str, Any],
    efficiency_diagnosis: dict[str, Any],
    analysis: dict[str, object] | None,
    chat_analysis: dict[str, Any],
) -> dict[str, Any]:
    augmented = dict(_json_ready(efficiency_analysis))
    base_findings = _dict_items(augmented.get("findings"))
    extra_findings = [
        *_chat_efficiency_findings(analysis),
        *_diagnosis_efficiency_findings(efficiency_diagnosis),
    ]
    findings = _merge_augmented_efficiency_findings([*extra_findings, *base_findings])
    augmented["findings"] = findings
    augmented["artifact_candidates"] = _augmented_efficiency_artifacts(
        findings,
        _dict_items(augmented.get("artifact_candidates")),
    )
    augmented["mechanism_counts"] = _mechanism_counts(findings)
    if chat_analysis.get("source") == "llm":
        augmented["mode"] = str(augmented.get("mode") or "quick") + "+chat"
    return augmented


def _chat_efficiency_findings(analysis: dict[str, object] | None) -> list[dict[str, Any]]:
    if not analysis:
        return []
    findings: list[dict[str, Any]] = []
    for finding in _ranked_chat_findings(analysis):
        theme = _focus_artifact_theme(finding, finding)
        if theme not in {"validation", "delivery", "milestone"}:
            continue
        title = redact_text(str(finding.get("title") or "聊天效率问题"))
        mechanism = _focus_mechanism(str(finding.get("artifact_type") or "checklist"))
        target_path = str(finding.get("artifact_target_path") or "").strip()
        if target_path:
            target_path = _normalized_focus_target_path(mechanism, target_path, title)
        else:
            target_path = _augmented_target_for_theme(theme, mechanism)
        findings.append(
            {
                "id": _augmented_id("eff_chat", title, finding.get("id")),
                "problem_type": _problem_type_for_theme(theme),
                "subtype": theme,
                "scope": "within_session",
                "title": title,
                "observation": redact_text(
                    str(finding.get("problem") or finding.get("impact") or title)
                ),
                "evidence_refs": _list(finding.get("evidence_refs")),
                "occurrences": max(1, len(_list(finding.get("evidence_refs")))),
                "affected_sessions": [],
                "observed_cost": {
                    "extra_turns": 1,
                    "repeated_commands": None,
                    "failed_commands": None,
                    "discarded_changes": None,
                    "repeated_file_reads": None,
                    "user_corrections": None,
                    "tool_output_bytes": None,
                    "validation_shifted_to_user": theme == "validation",
                    "wall_time_seconds": None,
                    "cost_notes": (_cost_note_for_theme(theme),),
                },
                "root_cause": redact_text(
                    str(finding.get("cause") or finding.get("problem") or "")
                ),
                "alternative_explanations": ["该判断来自模型对聊天内容的分析，需要结合证据复核。"],
                "responsibility_layers": ["user_workflow", "project"],
                "recommendation": redact_text(str(finding.get("recommendation") or "")),
                "mechanism": mechanism,
                "confidence": _float(finding.get("confidence")) or 0.75,
                "promotion_confidence": min(0.9, (_float(finding.get("confidence")) or 0.75) - 0.05),
                "opportunity_title": str(
                    finding.get("opportunity_title") or _opportunity_title_for_theme(theme, title)
                ),
                "suggested_target": target_path,
                "artifact_title": redact_text(
                    str(finding.get("artifact_title") or title)
                ),
                "artifact_theme": theme,
                "source": "llm_chat_transcript",
            }
        )
    return findings


def _diagnosis_efficiency_findings(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for problem in _dict_items(diagnosis.get("efficiency_problems")):
        signal_id = str(problem.get("source_signal_id") or "user_signal")
        evidence = [_dict(item) for item in _list(problem.get("evidence"))]
        evidence_refs = [
            str(item.get("event_id") or "")
            for item in evidence
            if item.get("event_id")
        ]
        title = redact_text(str(problem.get("title") or "用户文字效率问题"))
        target_path = str(problem.get("suggested_target") or "docs/implementation-ledger.md")
        artifact_theme = _diagnosis_artifact_theme(signal_id)
        findings.append(
            {
                "id": _augmented_id("eff_user", signal_id, title),
                "problem_type": _diagnosis_problem_type(signal_id),
                "subtype": signal_id,
                "scope": "within_session",
                "title": title,
                "observation": redact_text(str(problem.get("problem") or "")),
                "evidence_refs": evidence_refs,
                "occurrences": int(problem.get("signal_count") or len(evidence_refs) or 1),
                "affected_sessions": [],
                "observed_cost": {
                    "extra_turns": int(problem.get("signal_count") or len(evidence_refs) or 1),
                    "repeated_commands": None,
                    "failed_commands": None,
                    "discarded_changes": None,
                    "repeated_file_reads": None,
                    "user_corrections": None,
                    "tool_output_bytes": None,
                    "validation_shifted_to_user": False,
                    "wall_time_seconds": None,
                    "cost_notes": [str(problem.get("why_it_slows_work") or "")],
                },
                "root_cause": redact_text(str(problem.get("why_it_slows_work") or "")),
                "alternative_explanations": ["该信号来自用户文字频次，需避免把一次性偏好过度固化。"],
                "responsibility_layers": ["agent", "project"],
                "recommendation": redact_text(str(problem.get("next_action") or "")),
                "mechanism": "checklist",
                "confidence": 0.84,
                "promotion_confidence": 0.74,
                "opportunity_title": _diagnosis_opportunity_title(problem),
                "suggested_target": target_path,
                "artifact_title": _diagnosis_artifact_title(problem, artifact_theme),
                "artifact_theme": artifact_theme,
                "source": "user_message_efficiency",
            }
        )
    return findings


def _diagnosis_problem_type(signal_id: str) -> str:
    if signal_id == "completeness_tracking":
        return "repeated_user_requirement"
    if signal_id == "real_environment_validation":
        return "verification_debt"
    if signal_id == "release_hygiene":
        return "repeated_command_sequence"
    return "repeated_workflow_orchestration"


def _diagnosis_opportunity_title(problem: dict[str, Any]) -> str:
    signal_id = str(problem.get("source_signal_id") or "")
    if signal_id == "completeness_tracking":
        return "建立需求完成度账本"
    return redact_text(str(problem.get("recommended_action") or problem.get("title") or "沉淀复用流程"))


def _diagnosis_artifact_theme(signal_id: str) -> str:
    if signal_id == "completeness_tracking":
        return "implementation_ledger"
    if signal_id == "real_environment_validation":
        return "validation"
    if signal_id == "correction_drift":
        return "task_brief"
    if signal_id == "release_hygiene":
        return "release"
    return "workflow"


def _diagnosis_artifact_title(problem: dict[str, Any], artifact_theme: str) -> str:
    if artifact_theme == "implementation_ledger":
        return "需求完成度账本"
    return redact_text(str(problem.get("recommended_action") or problem.get("title") or "提效检查清单"))


def _merge_augmented_efficiency_findings(
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in sorted(findings, key=_augmented_finding_sort_key):
        key = (
            str(finding.get("problem_type") or ""),
            str(finding.get("title") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)
    return merged[:8]


def _augmented_finding_sort_key(finding: dict[str, Any]) -> tuple[int, int, int, float]:
    source_rank = {
        "llm_chat_transcript": 3,
        "user_message_efficiency": 2,
    }.get(str(finding.get("source") or ""), 1)
    subtype_rank = {
        "completeness_tracking": 60,
        "real_environment_validation": 45,
        "correction_drift": 40,
        "contract_coupling": 35,
        "product_rule_system": 32,
        "release_hygiene": 25,
    }.get(str(finding.get("subtype") or ""), 0)
    business_rank = {
        "verification_debt": 50,
        "repeated_user_requirement": 45,
        "project_knowledge_rediscovery": 35,
        "hypothesis_stagnation": 30,
    }.get(str(finding.get("problem_type") or ""), 20)
    return (-source_rank, -subtype_rank, -business_rank, -_float(finding.get("confidence")))


def _augmented_efficiency_artifacts(
    findings: list[dict[str, Any]],
    existing_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for finding in findings:
        artifact = _augmented_artifact_for_finding(finding)
        if artifact:
            artifacts.append(artifact)
    artifacts.extend(existing_artifacts)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    known_finding_ids = {str(finding.get("id") or "") for finding in findings}
    for artifact in artifacts:
        source_ids = [
            str(item)
            for item in _list(artifact.get("source_finding_ids"))
            if str(item) in known_finding_ids
        ]
        if not source_ids:
            continue
        item = dict(artifact)
        item["source_finding_ids"] = source_ids
        key = (str(item.get("mechanism") or ""), str(item.get("target_path") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:8]


def _augmented_artifact_for_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
    mechanism = str(finding.get("mechanism") or "checklist")
    target_path = str(finding.get("suggested_target") or "").strip()
    if not target_path:
        return None
    title = str(finding.get("artifact_title") or finding.get("title") or "提效产物")
    evidence_refs = _list(finding.get("evidence_refs"))
    proposed_content = _augmented_artifact_content(finding, title, evidence_refs)
    return {
        "id": _augmented_id("art", finding.get("id"), mechanism, target_path),
        "source_finding_ids": [str(finding.get("id") or "")],
        "mechanism": mechanism,
        "target_path": target_path,
        "title": title,
        "rationale": str(finding.get("recommendation") or ""),
        "proposed_content": proposed_content,
        "recurrence": int(finding.get("occurrences") or 1),
        "expected_benefit": _augmented_expected_benefit(finding),
        "risks": ["需要人工确认，避免把一次性会话偏好固化为长期噪声。"],
        "confidence": finding.get("promotion_confidence") or 0.65,
        "status": "proposed",
    }


def _augmented_artifact_content(
    finding: dict[str, Any],
    title: str,
    evidence_refs: list[Any],
) -> str:
    theme = str(finding.get("artifact_theme") or "")
    problem = str(finding.get("observation") or "")
    recommendation = str(finding.get("recommendation") or "")
    refs = [str(ref) for ref in evidence_refs]
    if theme == "implementation_ledger":
        return "\n".join(
            [
                f"## {redact_text(title)}",
                "",
                "### 使用时机",
                "- 用户要求完整实现、列任务列表、确认占位/mock/未完成项时。",
                "",
                "### 实现矩阵",
                "| 任务 | 状态 | 是否占位 | 是否 mock | 验证方式 | 证据 |",
                "| --- | --- | --- | --- | --- | --- |",
                "|  | todo / doing / done | 是/否 | 是/否 | 构建/测试/真实场景 |  |",
                "",
                "### 收尾要求",
                "- [ ] 任务列表逐项更新状态。",
                "- [ ] 标出未完成、占位、mock 和临时实现。",
                "- [ ] 每个 done 项都有验证方式和证据。",
                "",
                f"- 问题：{redact_text(problem)}",
                f"- 动作：{redact_text(recommendation)}",
                f"- 证据：{', '.join(refs[:6]) or '待补充'}",
                "",
            ]
        )
    if theme in {"validation", "delivery"}:
        return _focus_validation_checklist_content(title, problem, recommendation, refs)
    return "\n".join(
        [
            f"## {redact_text(title)}",
            "",
            f"- 问题：{redact_text(problem)}",
            f"- 动作：{redact_text(recommendation)}",
            f"- 证据：{', '.join(refs[:6]) or '待补充'}",
            "",
        ]
    )


def _problem_type_for_theme(theme: str) -> str:
    if theme in {"validation", "delivery"}:
        return "verification_debt"
    if theme == "milestone":
        return "repeated_user_requirement"
    return "repeated_workflow_orchestration"


def _opportunity_title_for_theme(theme: str, title: str) -> str:
    if theme in {"validation", "delivery"}:
        if "验收方式" in title or "完成边界" in title:
            return "前置验收方式和完成边界"
        if "重构验收" in title:
            return "先明确重构验收边界"
        if "重构" in title:
            return "先明确重构验收边界"
        return "补齐交付验证闭环"
    if theme == "milestone":
        return "先拆阶段再实现"
    return "沉淀复用流程"


def _augmented_target_for_theme(theme: str, mechanism: str) -> str:
    if theme in {"validation", "delivery"}:
        return "docs/checklists/refactor-delivery-checklist.md"
    if theme == "milestone":
        return "docs/implementation-ledger.md"
    if mechanism == "skill":
        return "skills/user-demand-priority-handling.md"
    return "docs/ai-workflow-checklist.md"


def _cost_note_for_theme(theme: str) -> str:
    if theme in {"validation", "delivery"}:
        return "验收方式没有前置会增加用户后置确认和返工成本。"
    if theme == "milestone":
        return "未维护任务列表会增加用户追问完整度和占位状态的轮次。"
    return "该聊天信号可能增加协作成本。"


def _augmented_expected_benefit(finding: dict[str, Any]) -> str:
    problem_type = str(finding.get("problem_type") or "")
    if problem_type == "verification_debt":
        return "减少用户在交付后再补验收口径和反复确认的成本。"
    if str(finding.get("artifact_theme") or "") == "implementation_ledger":
        return "减少完整度、占位、mock 和未完成项的反复追问。"
    if problem_type == "project_knowledge_rediscovery":
        return "缩短会话启动探索时间。"
    return "降低同类任务的重复协作成本。"


def _mechanism_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        mechanism = str(finding.get("mechanism") or "unknown")
        counts[mechanism] = counts.get(mechanism, 0) + 1
    return counts


def _augmented_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return f"{prefix}_{digest.hexdigest()[:12]}"


def _with_evidence_audit(report: dict[str, Any], *, deep: bool) -> dict[str, Any]:
    meta = _dict(report.get("meta"))
    analysis_mode = str(meta.get("analysis_mode") or "rules+heuristics")
    audit_label = "deep-audit" if deep else "audit"
    if "audit" not in analysis_mode:
        meta["analysis_mode"] = f"{analysis_mode}+{audit_label}"
    report["meta"] = meta
    report["evidence_audit"] = audit_report_evidence(
        report,
        mode="deep" if deep else "light",
    )
    return report


def _session_metrics(
    *,
    issues: list[dict[str, Any]],
    user_intent: dict[str, Any],
    verification: dict[str, Any],
    events: list[TranscriptEvent],
    session: SessionRecord,
    signals: dict[str, int],
    efficiency_analysis: dict[str, Any],
) -> dict[str, Any]:
    ledger = _dict(efficiency_analysis.get("cost_ledger"))
    metrics = {
        "main_issue_count": len(issues),
        "user_inputs": user_intent["user_input_count"],
        "context_events": user_intent["context_event_count"],
        "context_items_late": _user_correction_count(events),
        "verification_found": verification["overall"] == "验证闭环存在",
        "files_changed": _file_change_count(events),
        "messages": session.message_count,
        "commands": session.command_count,
        "errors": session.error_count + signals["errors"],
        "efficiency_findings": len(_list(efficiency_analysis.get("findings"))),
        "artifact_candidates": len(_list(efficiency_analysis.get("artifact_candidates"))),
    }
    for key in (
        "extra_turns",
        "failed_commands",
        "repeated_commands",
        "repeated_file_reads",
        "user_corrections",
    ):
        metrics[key] = int(ledger.get(key) or 0)
    return metrics


def _enrich_effect_observation(report: dict[str, Any]) -> None:
    effect = dict(_dict(report.get("effect_observation")))
    indicators = _dedup_strings(
        [
            *_effect_success_indicators(_dict(report.get("report_focus"))),
            *_artifact_success_indicators(_dict_items(report.get("artifact_candidates"))),
            *_artifact_success_indicators(
                _dict_items(_dict(report.get("efficiency_analysis")).get("artifact_candidates"))
            ),
        ]
    )
    if indicators:
        effect["success_indicators"] = indicators[:5]
    report["effect_observation"] = effect


def _effect_success_indicators(report_focus: dict[str, Any]) -> list[str]:
    artifacts = [_dict(item) for item in _list(report_focus.get("recommended_artifacts"))]
    indicators: list[str] = []
    for artifact in artifacts:
        theme = _focus_artifact_theme(artifact, artifact)
        if theme == "milestone":
            indicators.append("下次多阶段需求先拆里程碑，并在每个阶段确认后再进入实现。")
        elif theme == "validation":
            indicators.append("交付前完成真实场景验证，并在报告中明确验证路径和结果。")
        elif theme == "delivery":
            indicators.append("交付状态主动列出已完成、未完成、占位、mock 和真实实现边界。")
        elif theme == "environment":
            indicators.append("开发前完成环境预检查，避免版本或依赖问题消耗首轮实现时间。")
    return _dedup_strings(indicators)[:4]


def _artifact_success_indicators(artifacts: list[dict[str, Any]]) -> list[str]:
    indicators: list[str] = []
    for artifact in artifacts:
        text = " ".join(
            str(artifact.get(key) or "")
            for key in ("title", "target_path", "mechanism", "proposed_content")
        )
        if any(term in text for term in ("implementation-ledger", "实现矩阵", "任务列表", "完成度账本")):
            indicators.append("下次用户要求完整任务列表时，先维护任务列表/实现矩阵，再逐项推进并标出占位、mock 和未完成项。")
        if "AGENTS.md" in text:
            indicators.append("下次会话启动时先命中 AGENTS.md 中的标准入口和常用命令，减少重复读取 README。")
        if any(term in text for term in ("delivery-checklist", "交付验证", "真实场景验证")):
            indicators.append("交付前完成真实场景验证，并在报告中明确验证路径和结果。")
    return _dedup_strings(indicators)


def _efficiency_issues(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for finding in _list(analysis.get("findings")):
        item = _dict(finding)
        title = str(item.get("title") or "核心诊断")
        if title in seen_titles:
            continue
        seen_titles.add(title)
        issues.append(
            {
                "title": title,
                "severity": _severity_for_efficiency_finding(item),
                "observation": str(item.get("observation") or ""),
                "impact": _efficiency_impact(item),
                "suggestion": str(item.get("recommendation") or ""),
                "evidence_refs": [str(ref) for ref in _list(item.get("evidence_refs"))],
            }
        )
        if len(issues) >= 3:
            break
    return issues


def _efficiency_suggestions(analysis: dict[str, Any]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for artifact in _list(analysis.get("artifact_candidates")):
        item = _dict(artifact)
        title = str(item.get("title") or "改进机会")
        if title in seen_titles:
            continue
        seen_titles.add(title)
        suggestions.append(
            {
                "title": title,
                "priority": _priority_for_confidence(item.get("confidence")),
                "why": str(item.get("rationale") or item.get("expected_benefit") or ""),
                "action": str(item.get("proposed_content") or item.get("rationale") or ""),
                "target": str(item.get("target_path") or item.get("mechanism") or "review"),
            }
        )
        if len(suggestions) >= 3:
            break
    if suggestions:
        return suggestions

    for finding in _list(analysis.get("findings")):
        item = _dict(finding)
        title = _opportunity_title_for_problem_type(str(item.get("problem_type") or ""))
        if title in seen_titles:
            continue
        seen_titles.add(title)
        suggestions.append(
            {
                "title": title,
                "priority": _priority_for_confidence(item.get("confidence")),
                "why": str(item.get("observation") or item.get("root_cause") or ""),
                "action": str(item.get("recommendation") or ""),
                "target": str(item.get("mechanism") or "review"),
            }
        )
        if len(suggestions) >= 3:
            break
    return suggestions


def _max_avoidable_cost(analysis: dict[str, Any]) -> str:
    ledger = _dict(analysis.get("cost_ledger"))
    parts = []
    for key, label in (
        ("extra_turns", "额外轮次"),
        ("failed_commands", "失败命令"),
        ("repeated_commands", "重复命令"),
        ("repeated_file_reads", "重复读文件"),
        ("user_corrections", "用户纠正"),
    ):
        value = int(ledger.get(key) or 0)
        if value:
            parts.append(f"{label} {value} 次")
    if ledger.get("validation_shifted_to_user"):
        parts.append("验证成本转移给用户")
    return "，".join(parts) if parts else "未发现明确可避免成本"


def _primary_cause(analysis: dict[str, Any]) -> str:
    finding = _first_dict(analysis.get("findings"))
    return str(finding.get("root_cause") or "暂无足够证据判断主要根因。")


def _primary_improvement(
    analysis: dict[str, Any],
    suggestions: list[dict[str, str]],
) -> str:
    finding = _first_dict(analysis.get("findings"))
    if finding:
        if finding.get("opportunity_title"):
            return str(finding.get("opportunity_title"))
        return _opportunity_title_for_problem_type(str(finding.get("problem_type") or ""))
    return suggestions[0]["title"] if suggestions else "继续积累证据"


def _severity_for_efficiency_finding(finding: dict[str, Any]) -> str:
    problem_type = str(finding.get("problem_type") or "")
    confidence = _float(finding.get("confidence"))
    if problem_type in {"verification_debt", "hypothesis_stagnation"} or confidence >= 0.85:
        return "high"
    if confidence >= 0.65:
        return "medium"
    return "low"


def _efficiency_impact(finding: dict[str, Any]) -> str:
    if finding.get("root_cause"):
        return str(finding.get("root_cause"))
    observed_cost = _dict(finding.get("observed_cost"))
    notes = [str(note) for note in _list(observed_cost.get("cost_notes")) if note]
    return notes[0] if notes else ""


def _priority_for_confidence(value: object) -> str:
    confidence = _float(value)
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _opportunity_title_for_problem_type(problem_type: str) -> str:
    return {
        "verification_debt": "降低完成验证转移成本",
        "repeated_user_requirement": "沉淀重复项目要求",
        "project_knowledge_rediscovery": "前置稳定项目知识",
        "repeated_workflow_orchestration": "沉淀固定协作流程",
        "repeated_command_sequence": "脚本化重复命令序列",
        "hypothesis_stagnation": "缩短重复失败循环",
    }.get(problem_type, "降低重复协作成本")


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
                "impact": "该问题在近期会话中有可追溯证据，适合优先进入人工确认。",
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
                "target": "流程",
            }
        )
    return suggestions[:6]


def _session_evidence(events: list[TranscriptEvent], session: SessionRecord) -> list[dict[str, str]]:
    selected: list[tuple[TranscriptEvent, str]] = []
    for event in events:
        if event.role != "user" or not _is_signal_event(event):
            continue
        user_input = extract_user_input_text(event.text)
        if user_input:
            selected.append((event, user_input))
    if not selected:
        return [
            {
                "id": "ev_001",
                "event_id": "event_0",
                "source_ref": "event_0",
                "title": "纯用户输入",
                "content": "未抽取到纯用户输入。",
                "analysis": "报告页不再展示工具输出、助手回复或环境上下文作为正文证据。",
            }
        ]
    evidence = []
    for index, (event, user_input) in enumerate(selected[:8], start=1):
        is_correction = _looks_like_correction(user_input)
        evidence.append(
            {
                "id": f"ev_{index:03d}",
                "event_id": f"event_{event.event_index}",
                "source_ref": f"event_{event.event_index}",
                "title": f"{'用户纠正' if is_correction else '用户输入'} #{event.event_index}",
                "content": _excerpt(redact_text(user_input), 900),
                "analysis": "该证据来自纯用户输入，工具输出和上下文只用于后端验证。",
            }
        )
    return evidence


def _project_evidence(
    drafts: list[ImprovementDraft],
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[dict[str, str]]:
    evidence = []
    for index, session in enumerate(sessions[:3], start=1):
        session_evidence = _session_evidence(events_by_session.get(session.session_id, []), session)[0]
        session_evidence["id"] = f"ev_{index:03d}"
        evidence.append(session_evidence)
    return evidence or [{"id": "ev_001", "title": "纯用户输入", "content": "未抽取到纯用户输入。", "analysis": ""}]


def _verification_block(session: SessionRecord, events: list[TranscriptEvent]) -> dict[str, Any]:
    checks = [
        _check("测试", _contains_command_any(events, ("test", "pytest", "unittest", "vitest", "jest")), "检测测试命令或测试输出。"),
        _check("构建", _contains_command_any(events, ("build", "compile", "package")), "检测构建或打包命令。"),
        _check("Typecheck", _contains_command_any(events, ("typecheck", "tsc", "mypy", "pyright")), "检测类型检查。"),
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
            "哪些建议需要人工确认",
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
        {"stage": "候选改进", "status": "待确认", "description": f"生成 {len(drafts)} 个候选动作，落地前需要人工确认。"},
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


def _contains_command_any(events: list[TranscriptEvent], terms: tuple[str, ...]) -> bool:
    text = "\n".join(_command_event_text(event).lower() for event in events if _is_command_event(event))
    return any(term.lower() in text for term in terms)


def _final_mentions_verification(events: list[TranscriptEvent]) -> bool:
    final = next((event for event in reversed(events) if event.role == "assistant" and _is_signal_event(event)), None)
    if not final:
        return False
    lowered = final.text.lower()
    if any(term in lowered for term in ("没有运行", "没跑", "未运行", "not run", "did not run", "without running")):
        return False
    success_terms = (
        "passed",
        "pass",
        "通过",
        "成功",
        "exit code 0",
        "process exited with code 0",
        "0 failed",
        "ok",
    )
    return any(term in lowered for term in VERIFY_TERMS) and any(
        term in lowered for term in success_terms
    )


def _has_verification(events: list[TranscriptEvent]) -> bool:
    return _contains_command_any(events, tuple(str(term) for term in VERIFY_TERMS)) or _final_mentions_verification(events)


def _command_event_text(event: TranscriptEvent) -> str:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    parts = [
        event.text,
        str(metadata.get("command") or ""),
        str(metadata.get("cmd") or ""),
        str(metadata.get("stdout") or ""),
        str(metadata.get("stderr") or ""),
    ]
    return "\n".join(part for part in parts if part)


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
    if not event:
        return ""
    user_input = extract_user_input_text(event.text) if event.role == "user" else None
    return _excerpt(redact_text(user_input or event.text), 260)


def _user_intent(events: list[TranscriptEvent]) -> dict[str, object]:
    timeline: list[dict[str, object]] = []
    context_count = 0
    for event in events:
        if event.role != "user":
            continue
        user_input = extract_user_input_text(event.text)
        if not user_input:
            context_count += 1
            continue
        timeline.append(
            {
                "event_id": f"event_{event.event_index}",
                "source_ref": f"event_{event.event_index}",
                "phase": "user_correction" if _looks_like_correction(user_input) else "user_request",
                "created_at": event.created_at,
                "text": _excerpt(redact_text(user_input), 700),
                "is_correction": _looks_like_correction(user_input),
            }
        )
    corrections = [item for item in timeline if item["is_correction"]]
    requests = [item for item in timeline if not item["is_correction"]]
    primary = str(requests[0]["text"]) if requests else str(timeline[0]["text"]) if timeline else ""
    latest = str(requests[-1]["text"]) if requests else primary
    return {
        "primary_request": primary,
        "latest_request": latest,
        "user_input_count": len(timeline),
        "correction_count": len(corrections),
        "context_event_count": context_count,
        "timeline": timeline[:40],
        "corrections": corrections[:20],
        "analysis_policy": "Report generation starts from pure user inputs; context and tool output are supporting evidence.",
    }


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
        "checklist": "检查清单",
        "script": "脚本",
        "skill": "固定流程",
        "ci": "CI",
        "patterns": "流程",
    }
    return mapping.get(category, category or "流程")


def _artifact_action(artifact_type: str) -> str:
    return {
        "agents_md": "把该要求写入项目 AGENTS.md。",
        "checklist": "把该要求加入完成前检查清单。",
        "skill": "把该流程沉淀为可复用固定流程。",
        "script": "把重复命令沉淀为脚本。",
        "ci": "把重复验证升级为 CI 检查。",
        "hook": "用 hook 强制执行关键检查。",
        "prompt_template": "把该要求加入任务对话模板。",
    }.get(artifact_type, "把该建议沉淀为下一次会话可复用的资料或流程。")


def _session_project(session: SessionRecord) -> str:
    return redact_text(session.project_path or session.cwd or "(unknown)")


def _report_id(kind: str, value: str, generated_at: str) -> str:
    digest = hashlib.sha256(f"{kind}\n{value}\n{generated_at}".encode()).hexdigest()[:10]
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
    raw = raw.replace("</", "<\\/")
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _render_metrics(metrics: dict[str, Any]) -> str:
    labels = [
        ("main_issue_count", "主要问题"),
        ("verification_found", "发现验证"),
        ("user_corrections", "用户纠正"),
        ("failed_commands", "失败命令"),
        ("efficiency_findings", "效率问题"),
        ("artifact_candidates", "沉淀候选"),
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


def _render_token_usage(value: object) -> str:
    usage = _dict(value)
    totals = _dict(usage.get("totals"))
    calls = [_dict(item) for item in _list(usage.get("calls"))]
    if not calls:
        return '<p class="muted">该报告没有模型调用 token 消耗。</p>'
    total_items = [
        ("current_run_total_tokens", "本次新增"),
        ("total_tokens", "原始总量"),
        ("input_tokens", "输入 token"),
        ("output_tokens", "输出 token"),
    ]
    totals_html = "".join(
        '<div>'
        f'<strong>{_h(_format_int(totals.get(key)))}</strong>'
        f'<span>{_h(label)}</span>'
        "</div>"
        for key, label in total_items
    )
    calls_html = "".join(
        '<article class="token-call">'
        f'<span>{_h(_token_call_label(call))}</span>'
        f'<strong>{_h(_format_int(call.get("current_run_total_tokens")))} 本次 / {_h(_format_int(call.get("total_tokens")))} 原始</strong>'
        f'<span>{_h(_token_call_note(call))}</span>'
        "</article>"
        for call in calls[:5]
    )
    return f'<div class="token-usage"><div class="token-totals">{totals_html}</div><div class="token-calls">{calls_html}</div></div>'


def _token_call_label(call: dict[str, Any]) -> str:
    stage = str(call.get("stage") or call.get("task_type") or "llm")
    provider = str(call.get("provider") or "provider")
    model = str(call.get("model") or "model")
    return f"{stage} / {provider} / {model}"


def _token_call_note(call: dict[str, Any]) -> str:
    flags = []
    if call.get("cached"):
        flags.append("cache hit")
    if call.get("estimated"):
        flags.append("estimated")
    if call.get("missing_usage"):
        flags.append("missing usage")
    if call.get("retried"):
        flags.append("retried")
    return " / ".join(flags) or str(call.get("source") or "provider")


def _format_int(value: object) -> str:
    if isinstance(value, bool):
        return "0"
    if isinstance(value, (int, float)):
        return f"{int(value):,}"
    if isinstance(value, str) and value.strip().isdigit():
        return f"{int(value.strip()):,}"
    return "0"


def _render_core_diagnostics(value: object, summary: dict[str, Any]) -> str:
    core = _dict(value)
    ledger = _dict(core.get("cost_ledger"))
    opportunities = [_dict(item) for item in _list(core.get("improvement_opportunities"))]
    findings = [_dict(item) for item in _list(core.get("findings"))]
    top_opportunity = opportunities[0] if opportunities else {}
    top_finding = findings[0] if findings else {}
    cost_items = [
        ("failed_commands", "失败命令"),
        ("repeated_commands", "重复命令"),
        ("repeated_file_reads", "重复读取"),
        ("user_corrections", "用户纠正"),
        ("verification_followups", "验证追问"),
    ]
    cost_html = "".join(
        f"<li>{_h(label)}：{_h(str(ledger.get(key) or 0))}</li>"
        for key, label in cost_items
        if int(ledger.get(key) or 0) > 0
    )
    if not cost_html:
        cost_html = "<li>未发现明确可避免成本</li>"
    return (
        '<div class="kv">'
        f'<p><b>最大可避免成本：</b>{_h(str(summary.get("max_avoidable_cost") or ""))}</p>'
        f"<ul>{cost_html}</ul>"
        "<p><b>主要成因：</b>"
        f'{_h(str(top_finding.get("cause") or summary.get("primary_cause") or ""))}</p>'
        "<p><b>首要改进：</b>"
        f'{_h(str(top_opportunity.get("title") or summary.get("primary_improvement") or ""))}</p>'
        "<p><b>建议沉淀到：</b>"
        f'{_h(_display_mechanism(str(top_opportunity.get("recommended_mechanism") or "review")))}</p>'
        "</div>"
    )


def _render_core_answers(report: dict[str, Any]) -> str:
    answers = _dict(report.get("core_answers"))
    outcome = _dict(report.get("task_outcome"))
    effect = _dict(report.get("effect_observation"))
    rows = [
        ("本次最终结果", outcome.get("result") or outcome.get("verification_status") or ""),
        ("最大可避免成本", answers.get("most_expensive_avoidable_cost") or ""),
        ("为什么发生", answers.get("why_it_happened") or ""),
        ("改哪里最划算", answers.get("highest_leverage_change") or ""),
        ("建议沉淀到哪里", _display_mechanism(str(answers.get("what_should_be_preserved_as_artifact") or ""))),
        ("沉淀后是否已验证", effect.get("message") or answers.get("has_effect_been_observed") or ""),
    ]
    return (
        '<div class="kv">'
        + "".join(
            f"<p><b>{_h(label)}：</b>{_h(str(value or '暂无'))}</p>"
            for label, value in rows
        )
        + "</div>"
    )


def _render_evidence_audit(value: object) -> str:
    audit = _dict(value)
    if not audit:
        return ""
    metrics = _dict(audit.get("metrics"))
    problems = [_dict(item) for item in _list(audit.get("problems"))[:4]]
    problem_html = "".join(
        f"<li>{_h(str(problem.get('target') or 'unknown'))}：{_h(str(problem.get('message') or ''))}</li>"
        for problem in problems
    )
    if not problem_html:
        problem_html = "<li>未发现证据引用断链</li>"
    status = str(audit.get("status") or "unknown")
    return (
        '<section class="panel">'
        "<h2>证据检查</h2>"
        '<div class="kv">'
        f"<p><b>状态：</b>{_h(_display_status(status))}</p>"
        f"<p><b>可追溯率：</b>{_h(str(metrics.get('traceability') or 0))}</p>"
        f"<p><b>检查对象：</b>{_h(str(metrics.get('audited_claims') or 0))}</p>"
        f"<p><b>摘要：</b>{_h(str(audit.get('summary') or ''))}</p>"
        f"<ul>{problem_html}</ul>"
        "</div>"
        "</section>"
    )


def _render_core_chain(value: object) -> str:
    core = _dict(value)
    return (
        '<div class="core-chain">'
        '<div class="core-column">'
        "<h3>问题</h3>"
        f'{_render_core_finding_cards(core.get("findings"))}'
        "</div>"
        '<div class="core-column">'
        "<h3>改进动作</h3>"
        f'{_render_core_opportunity_cards(core.get("improvement_opportunities"))}'
        "</div>"
        '<div class="core-column">'
        "<h3>沉淀建议</h3>"
        f'{_render_core_artifact_cards(core.get("artifact_candidates"))}'
        "</div>"
        "</div>"
    )


def _render_core_finding_cards(value: object) -> str:
    items = _dedupe_by_title(_dict(item) for item in _list(value))[:4]
    if not items:
        return '<p class="muted">暂无核心问题。</p>'
    return "".join(
        '<article class="core-card">'
        f'<span class="label">{_h(_display_status(str(item.get("severity") or "medium")))}</span>'
        f'<strong>{_h(str(item.get("title") or "核心问题"))}</strong>'
        f'<p>{_h(str(item.get("cause") or item.get("observation") or ""))}</p>'
        f'<p>证据数量：{_h(str(len(_list(item.get("evidence_refs")))))}</p>'
        "</article>"
        for item in items
    )


def _render_core_opportunity_cards(value: object) -> str:
    items = _dedupe_by_title(_dict(item) for item in _list(value))[:4]
    if not items:
        return '<p class="muted">暂无改进机会。</p>'
    return "".join(
        '<article class="core-card">'
        f'<span class="label">{_h(_display_mechanism(str(item.get("recommended_mechanism") or "review")))}</span>'
        f'<strong>{_h(str(item.get("title") or "改进机会"))}</strong>'
        f'<p>{_h(str(item.get("routing_reason") or item.get("best_action") or ""))}</p>'
        f'<p>可预防性：{_h(_display_status(str(item.get("preventability") or "unknown")))}</p>'
        "</article>"
        for item in items
    )


def _render_core_artifact_cards(value: object) -> str:
    items = [_dict(item) for item in _list(value)][:4]
    if not items:
        return '<p class="muted">暂无沉淀建议。</p>'
    return "".join(
        '<article class="core-card">'
        f'<span class="label">{_h(_display_mechanism(str(item.get("mechanism") or item.get("artifact_type") or "artifact")))}</span>'
        f'<strong>{_h(str(item.get("target_path") or item.get("scope") or "人工确认"))}</strong>'
        f'<p>{_h(str(item.get("rationale") or ""))}</p>'
        f'<p>状态：{_h(_display_status(str(item.get("status") or "proposed")))}</p>'
        "</article>"
        for item in items
    )


def _render_efficiency_diagnosis(value: object) -> str:
    diagnosis = _dict(value)
    if not diagnosis:
        return '<p class="muted">暂无效率诊断过程。</p>'
    process = [_dict(item) for item in _list(diagnosis.get("process"))]
    signals = [
        item
        for item in [_dict(raw) for raw in _list(diagnosis.get("signal_summary"))]
        if int(item.get("count") or 0) > 0
    ][:6]
    problems = [_dict(item) for item in _list(diagnosis.get("efficiency_problems"))][:5]
    process_html = "".join(
        '<article class="core-card">'
        f'<span class="label">{_h(_display_process_step(str(item.get("step") or "")))}</span>'
        f'<strong>{_h(str(item.get("title") or ""))}</strong>'
        f'<p>{_h(str(item.get("description") or ""))}</p>'
        f'<p>输出：{_h(str(item.get("output") or ""))}</p>'
        "</article>"
        for item in process
    )
    signal_html = "".join(
        '<article class="core-card">'
        f'<span class="label">命中强度 {_h(str(item.get("score") or 0))}</span>'
        f'<strong>{_h(str(item.get("title") or item.get("label") or ""))}</strong>'
        f'<p>命中 {_h(str(item.get("count") or 0))} 条用户消息；词：{_h("、".join(str(term) for term in _list(item.get("matched_terms"))[:8]))}</p>'
        f'{_render_signal_evidence(item.get("evidence"))}'
        "</article>"
        for item in signals
    )
    problem_html = "".join(
        '<article class="chat-card">'
        "<header>"
        f'<div><span class="label">排序 {_h(str(item.get("rank") or ""))}</span>'
        f'<strong>{_h(str(item.get("title") or "效率问题"))}</strong></div>'
        f'<span class="status">{_h(_display_mechanism(str(item.get("suggested_artifact") or "action")))}</span>'
        "</header>"
        f'<p class="basis">{_h(str(item.get("problem") or ""))}</p>'
        f'<p>{_h(str(item.get("why_it_slows_work") or ""))}</p>'
        '<div class="kv">'
        f'<p><b>提效建议：</b>{_h(str(item.get("recommended_action") or ""))}</p>'
        f'<p><b>落点：</b>{_h(str(item.get("suggested_target") or ""))}</p>'
        "</div>"
        "</article>"
        for item in problems
    )
    signals_block = signal_html or '<p class="muted">暂无命中信号。</p>'
    problems_block = problem_html or '<p class="muted">暂无效率问题。</p>'
    return (
        f'<p class="muted">{_h(str(diagnosis.get("analysis_summary") or ""))}</p>'
        '<h3>分析步骤</h3>'
        f'<div class="core-chain">{process_html}</div>'
        '<h3 style="margin-top:14px">效率信号</h3>'
        f'<div class="core-chain">{signals_block}</div>'
        '<h3 style="margin-top:14px">抽象后的效率问题</h3>'
        f'<div class="chat-analysis">{problems_block}</div>'
    )


def _render_signal_evidence(value: object) -> str:
    evidence = [_dict(item) for item in _list(value)][:2]
    if not evidence:
        return ""
    return '<div class="chat-evidence-list">' + "".join(
        '<div class="chat-evidence">'
        f'<code>{_h(str(item.get("event_id") or ""))}</code>'
        f'<blockquote>{_h(str(item.get("quote") or ""))}</blockquote>'
        "</div>"
        for item in evidence
    ) + "</div>"


def _render_efficiency_actions(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无可直接执行的提效动作。</p>'
    return "".join(
        '<article class="core-card">'
        f'<span class="label">{_h(_display_mechanism(str(item.get("suggested_artifact") or "action")))}</span>'
        f'<strong>{_h(str(item.get("title") or "提效动作"))}</strong>'
        '<div class="kv">'
        f'<p><b>触发：</b>{_h(str(item.get("trigger") or ""))}</p>'
        f'<p><b>下次动作：</b>{_h(str(item.get("next_action") or ""))}</p>'
        f'<p><b>节省：</b>{_h(str(item.get("expected_efficiency_gain") or ""))}</p>'
        f'<p><b>落点：</b>{_h(str(item.get("suggested_target") or ""))}</p>'
        f'<p><b>证据：</b>{_h(str(item.get("evidence_summary") or ""))}</p>'
        "</div>"
        "</article>"
        for item in items[:3]
    )


def _render_user_efficiency_analysis(value: object, chat_value: object) -> str:
    analysis = _dict(value)
    chat = _dict(chat_value)
    guidance = [_dict(item) for item in _list(analysis.get("top_guidance"))]
    summary = str(analysis.get("summary") or "")
    guidance_html = "".join(
        '<article class="core-card">'
        f'<span class="label">建议动作 {index}</span>'
        f'<strong>{_h(str(item.get("title") or "提效动作"))}</strong>'
        '<div class="kv">'
        f'<p><b>为什么：</b>{_h(str(item.get("why") or ""))}</p>'
        f'<p><b>下次动作：</b>{_h(str(item.get("next_action") or ""))}</p>'
        f'<p><b>预计节省：</b>{_h(str(item.get("expected_efficiency_gain") or ""))}</p>'
        f'<p><b>落点：</b>{_h(str(item.get("suggested_target") or ""))}</p>'
        f'<p><b>证据：</b>{_h(", ".join(str(ref) for ref in _list(item.get("evidence_refs"))) or "待补充")}</p>'
        "</div>"
        "</article>"
        for index, item in enumerate(guidance[:3], start=1)
    ) or '<p class="muted">暂无可直接执行的提效动作。</p>'
    method = _dict(analysis.get("method"))
    merged_sources = "、".join(
        _display_scope(str(item))
        for item in _list(method.get("merged_sources"))
    )
    excluded = "、".join(
        _display_excluded(str(item))
        for item in _list(method.get("excluded"))
    )
    observations = _render_text_bullets(
        [str(item) for item in _list(chat.get("key_observations"))[:4]],
        "暂无关键观察。",
    )
    sample = [_dict(item) for item in _list(chat.get("transcript_sample"))[:4]]
    sample_html = "".join(
        '<div class="chat-evidence">'
        f'<code>{_h(str(item.get("event_id") or ""))} / {_h(str(item.get("role") or ""))}</code>'
        f'<blockquote>{_h(str(item.get("quote") or ""))}</blockquote>'
        "</div>"
        for item in sample
    ) or '<p class="muted">暂无聊天样例。</p>'
    return (
        '<article class="chat-card">'
        "<header>"
        '<div><span class="label">开发提效建议</span>'
        "<strong>把聊天记录和效率成本放在一起看</strong></div>"
        "</header>"
        f'<p class="basis">{_h(summary)}</p>'
        '<div class="kv">'
        f'<p><b>合并来源：</b>{_h(merged_sources or "聊天文字、提效信号、可节省成本")}</p>'
        f'<p><b>排除：</b>{_h(excluded or "工具输出、助手成功声明")}</p>'
        "</div>"
        f"{guidance_html}"
        '<h3 style="margin-top:14px">聊天观察</h3>'
        f"{observations}"
        '<h3 style="margin-top:14px">聊天样例</h3>'
        f'<div class="chat-evidence-list">{sample_html}</div>'
        "</article>"
    )


def _render_chat_transcript_analysis(value: object) -> str:
    analysis = _dict(value)
    if not analysis:
        return '<p class="muted">暂无聊天内容分析。</p>'
    method = _dict(analysis.get("method"))
    observations = [str(item) for item in _list(analysis.get("key_observations"))[:5]]
    friction_points = [str(item) for item in _list(analysis.get("friction_points"))[:5]]
    sample = [_dict(item) for item in _list(analysis.get("transcript_sample"))[:6]]
    refs = ", ".join(str(ref) for ref in _list(analysis.get("evidence_refs"))) or "无"
    observation_html = _render_text_bullets(observations, "暂无关键观察。")
    friction_html = _render_text_bullets(friction_points, "暂无协作摩擦点。")
    sample_html = "".join(
        '<div class="chat-evidence">'
        f'<code>{_h(str(item.get("event_id") or ""))} / {_h(str(item.get("role") or ""))}</code>'
        f'<blockquote>{_h(str(item.get("quote") or ""))}</blockquote>'
        "</div>"
        for item in sample
    ) or '<p class="muted">暂无聊天样例。</p>'
    return (
        '<article class="chat-card">'
        "<header>"
        f'<div><span class="label">{_h(_display_analysis_source(str(analysis.get("source") or "rules")))}</span>'
        "<strong>只基于用户/助手文字的分析</strong></div>"
        f'<span class="status">{_h(str(analysis.get("message_count") or 0))} 条消息</span>'
        "</header>"
        f'<p class="basis">{_h(str(analysis.get("summary") or ""))}</p>'
        '<div class="kv">'
        f'<p><b>范围：</b>{_h(_display_scope(str(method.get("scope") or "raw_user_and_assistant_chat_text")))}</p>'
        f'<p><b>排除：</b>{_h("、".join(_display_excluded(str(item)) for item in _list(method.get("excluded"))))}</p>'
        f'<p><b>证据：</b>{_h(refs)}</p>'
        "</div>"
        '<h3 style="margin-top:14px">关键观察</h3>'
        f"{observation_html}"
        '<h3 style="margin-top:14px">协作摩擦</h3>'
        f"{friction_html}"
        '<h3 style="margin-top:14px">聊天样例</h3>'
        f'<div class="chat-evidence-list">{sample_html}</div>'
        "</article>"
    )


def _render_text_bullets(items: list[str], empty: str) -> str:
    if not items:
        return f'<p class="muted">{_h(empty)}</p>'
    return "<ul class=\"flow\">" + "".join(
        f'<li><p>{_h(item)}</p></li>'
        for item in items
    ) + "</ul>"


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
            f'<p><b>载体：</b>{_h(_display_mechanism(str(item.get("target") or "流程")))}</p></div></article>'
        )
    return "".join(parts)


def _render_conversation_analysis(value: object) -> str:
    items = [_dict(item) for item in _list(value)]
    if not items:
        return '<p class="muted">暂无可解析到聊天片段的核心证据。</p>'
    parts = []
    for item in items:
        evidence_html = "".join(
            '<div class="chat-evidence">'
            f'<code>{_h(str(evidence.get("ref_id") or evidence.get("event_id") or "evidence"))}</code>'
            f'<blockquote>{_h(str(evidence.get("quote") or ""))}</blockquote>'
            "</div>"
            for evidence in [_dict(entry) for entry in _list(item.get("evidence"))]
        )
        llm_notes = "".join(
            f'<p class="muted"><b>模型分析：</b>{_h(str(note.get("title") or ""))} - {_h(str(note.get("body") or ""))}</p>'
            for note in [_dict(entry) for entry in _list(item.get("llm_notes"))]
        )
        parts.append(
            '<article class="chat-card">'
            "<header>"
            f'<div><span class="label">{_h(str(item.get("evidence_label") or "聊天证据"))}</span>'
            f'<strong>{_h(str(item.get("title") or "聊天记录分析"))}</strong></div>'
            f'<span class="status">{_h(str(item.get("kind") or "analysis"))}</span>'
            "</header>"
            f'<p class="basis">{_h(str(item.get("basis") or ""))}</p>'
            f'<p>{_h(str(item.get("analysis") or ""))}</p>'
            f'<div class="chat-evidence-list">{evidence_html}</div>'
            f"{llm_notes}"
            "</article>"
        )
    return '<div class="chat-analysis">' + "".join(parts) + "</div>"


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


def _dict_items(value: object) -> list[dict[str, Any]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _dedupe_by_title(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or item.get("id") or "")
        if title in seen:
            continue
        seen.add(title)
        result.append(item)
    return result


def _first_dict(value: object) -> dict[str, Any]:
    items = _list(value)
    return _dict(items[0]) if items else {}


def _json_ready(value: object) -> Any:
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _report_safe_payload(value: object) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _report_safe_payload(item)
            for key, item in value.items()
            if key not in {"category", "card_type"}
        }
    if isinstance(value, list):
        return [_report_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_report_safe_payload(item) for item in value]
    return value


def _h(value: str) -> str:
    return html.escape(value, quote=True)


def _class_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return cleaned or "medium"


def _display_mechanism(value: str) -> str:
    normalized = value.lower().strip()
    return {
        "agents_md": "AGENTS.md",
        "hook_or_ci": "自动检查",
        "hook": "自动检查",
        "ci": "自动检查",
        "script": "脚本",
        "skill": "固定流程",
        "checklist": "检查清单",
        "prompt_template": "对话模板",
        "review": "人工确认",
        "action": "行动项",
        "artifact": "沉淀建议",
        "implementation_ledger": "实现账本",
        "smoke_checklist": "冒烟检查清单",
    }.get(normalized, value or "人工确认")


def _display_status(value: str) -> str:
    normalized = value.lower().strip()
    return {
        "pass": "通过",
        "passed": "通过",
        "ok": "通过",
        "success": "通过",
        "succeeded": "通过",
        "supported": "有证据支持",
        "pending": "待观察",
        "not observed": "待观察",
        "not_observed": "待观察",
        "unknown": "未知",
        "completed_with_evidence": "已完成，有验证",
        "completed_with_verification_gap": "已完成，但验证不足",
        "needs_review": "需要确认",
        "proposed": "建议中",
        "ready_for_review": "待确认",
        "high": "高",
        "medium": "中",
        "medium_low": "中低",
        "low": "低",
        "critical": "严重",
        "failure": "失败",
        "failed": "失败",
    }.get(normalized, value or "未知")


def _display_analysis_source(value: str) -> str:
    normalized = value.lower().strip()
    if normalized == "llm":
        return "模型分析"
    if normalized == "rules":
        return "规则分析"
    return value or "规则分析"


def _display_scope(value: str) -> str:
    normalized = value.strip()
    return {
        "raw_user_and_assistant_chat_text": "你和助手的文字消息",
        "pure_user_messages": "你的文字消息",
        "user_message_efficiency_signals": "聊天中的提效信号",
        "avoidable_cost_findings": "可节省成本问题",
    }.get(normalized, value or "聊天文字")


def _display_excluded(value: str) -> str:
    return {
        "tool_calls": "工具调用",
        "tool_outputs": "工具输出",
        "command_results": "命令结果",
        "environment_context": "环境上下文",
        "system_or_developer_instructions": "系统和开发者指令",
        "tool_outputs_as_chat_conclusions": "把工具输出当作聊天结论",
        "assistant_success_claims_as_primary_subject": "把助手成功声明当作主结论",
    }.get(value, value)


def _display_process_step(value: str) -> str:
    return {
        "extract_user_messages": "提取用户消息",
        "classify_efficiency_signals": "归类效率信号",
        "rank_efficiency_problems": "排序效率问题",
        "select_representative_evidence": "选择代表证据",
        "route_to_reusable_actions": "生成提效动作",
    }.get(value, value)
