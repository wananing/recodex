from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text
from .transcripts import extract_user_input_text, looks_like_user_correction

METHOD_VERSION = "codebook_qualitative_coding_v1"


@dataclass(frozen=True)
class CodeDefinition:
    code_id: str
    label: str
    definition: str
    include_terms: tuple[str, ...]
    theme_id: str


DEFAULT_CODEBOOK: tuple[CodeDefinition, ...] = (
    CodeDefinition(
        "task_request",
        "Task request",
        "User asks the agent to implement, fix, refactor, start, verify, or otherwise perform work.",
        (
            "修复",
            "实现",
            "重构",
            "优化",
            "启动",
            "开始",
            "改",
            "新增",
            "梳理",
            "确认",
            "测试",
            "验证",
            "fix",
            "implement",
            "add",
            "build",
            "refactor",
            "verify",
            "start",
        ),
        "task_intent",
    ),
    CodeDefinition(
        "reporting_experience",
        "Report analysis experience",
        "User asks for report generation, report analysis, or report page behavior.",
        ("报告", "分析报告", "report"),
        "reporting_workflow",
    ),
    CodeDefinition(
        "artifact_workflow",
        "Artifact preview and import workflow",
        "User asks for previewing or importing generated artifacts such as skills or markdown.",
        ("预览", "skill", "SKILL", "md", "markdown", "一键导入"),
        "reporting_workflow",
    ),
    CodeDefinition(
        "ui_integration",
        "UI integration constraint",
        "User constrains how a page or function should fit into the existing product surface.",
        ("dashboard", "页面", "单独", "一体", "settings", "设置"),
        "reporting_workflow",
    ),
    CodeDefinition(
        "user_correction",
        "User correction",
        "User corrects direction, scope, placement, or interpretation.",
        ("不要", "不对", "不是", "我的意思", "应该", "wrong", "not"),
        "reporting_workflow",
    ),
    CodeDefinition(
        "llm_reliability",
        "LLM reliability failure",
        "User reports unstable LLM analysis, invalid JSON, provider errors, or response parsing failures.",
        ("LLM", "llm", "analysis failed", "valid JSON", "JSON output", "OpenAI", "Volcengine", "Ark"),
        "llm_analysis_reliability",
    ),
    CodeDefinition(
        "import_quality",
        "Transcript import quality",
        "User reports poor import quality or asks to improve external conversation/log ingestion.",
        ("聊天记录", "导入功能", "导入 context", "Import context", "watch source", "watch sources", "开源工具"),
        "context_ingestion_quality",
    ),
)

THEME_LABELS: dict[str, str] = {
    "task_intent": "Task intent and requested outcomes",
    "reporting_workflow": "Reporting and artifact workflow",
    "llm_analysis_reliability": "LLM analysis reliability",
    "context_ingestion_quality": "Context ingestion quality",
}


def build_session_qualitative_analysis(
    session: SessionRecord,
    events: list[TranscriptEvent],
    *,
    codebook: tuple[CodeDefinition, ...] = DEFAULT_CODEBOOK,
) -> dict[str, Any]:
    segments = _coded_segments(session, events, codebook)
    return {
        "method": METHOD_VERSION,
        "session": {
            "session_id": session.session_id,
            "title": redact_text(session.title),
        },
        "codebook": [_codebook_payload(code) for code in codebook],
        "segments": segments,
        "themes": _themes(segments, codebook),
        "audit_trail": {
            "unit_of_analysis": "pure_user_input_segment",
            "excluded_sources": ["assistant", "tool", "system", "context_only_user_rows"],
            "coding_mode": "deterministic_codebook_probe",
        },
    }


def _coded_segments(
    session: SessionRecord,
    events: list[TranscriptEvent],
    codebook: tuple[CodeDefinition, ...],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for event in events:
        if event.role != "user":
            continue
        user_input = extract_user_input_text(event.text)
        if not user_input:
            continue
        units = _meaning_units(user_input)
        for unit_index, unit in enumerate(units, start=1):
            text = redact_text(unit)
            source_ref = (
                f"{session.session_id}:event_{event.event_index}"
                if len(units) == 1
                else f"{session.session_id}:event_{event.event_index}:unit_{unit_index}"
            )
            segments.append(
                {
                    "segment_id": f"seg_{event.event_index}_{unit_index}",
                    "session_id": session.session_id,
                    "source_ref": source_ref,
                    "event_index": event.event_index,
                    "unit_index": unit_index,
                    "created_at": event.created_at,
                    "role": "user",
                    "text": text,
                    "codes": _codes_for_text(text, codebook),
                }
            )
    return segments


def _codes_for_text(text: str, codebook: tuple[CodeDefinition, ...]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for code in codebook:
        if code.code_id == "reporting_experience" and _looks_like_domain_report(text):
            continue
        terms = _matched_terms(text, code.include_terms)
        if code.code_id == "user_correction":
            terms = _correction_terms_for_text(text, terms)
        if not terms:
            continue
        matched.append(
            {
                "code_id": code.code_id,
                "label": code.label,
                "theme_id": code.theme_id,
                "matched_terms": list(terms),
                "confidence": _confidence_for_terms(terms),
            }
        )
    return matched


def _correction_terms_for_text(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    direct_terms = tuple(term for term in terms if term not in {"不要", "别"})
    if looks_like_user_correction(text) and "correction_pattern" not in direct_terms:
        direct_terms = ("correction_pattern", *direct_terms)
    if direct_terms:
        return direct_terms
    if _looks_like_scope_correction(text):
        return ("scope_correction",)
    return ()


def _looks_like_scope_correction(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not any(term in compact for term in ("不要", "别")):
        return False
    return any(term in compact for term in ("不是", "而是", "要跟", "应该", "单独", "范围", "我的意思"))


def _looks_like_domain_report(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in ("recodex", "dashboard", "skill", "markdown", "llm")):
        return False
    if any(term in text for term in ("分析报告", "报告页", "报告页面")):
        return False
    business_markers = (
        "报表",
        "订单",
        "礼包",
        "批次",
        "超时",
        "接口",
        "请求报文",
        "用户编码",
        "定时",
        "cron",
        "schedule",
        "usercode",
    )
    return "报告" in text and any(marker in lowered or marker in text for marker in business_markers)


def _themes(segments: list[dict[str, Any]], codebook: tuple[CodeDefinition, ...]) -> list[dict[str, Any]]:
    code_to_theme = {code.code_id: code.theme_id for code in codebook}
    by_theme: dict[str, dict[str, Any]] = {}
    for segment in segments:
        for code in segment["codes"]:
            theme_id = code_to_theme.get(str(code["code_id"]))
            if not theme_id:
                continue
            theme = by_theme.setdefault(
                theme_id,
                {
                    "theme_id": theme_id,
                    "label": THEME_LABELS.get(theme_id, theme_id.replace("_", " ")),
                    "codes": set(),
                    "evidence_refs": [],
                    "representative_quotes": [],
                },
            )
            theme["codes"].add(code["code_id"])
            if segment["source_ref"] not in theme["evidence_refs"]:
                theme["evidence_refs"].append(segment["source_ref"])
                theme["representative_quotes"].append(_quote_excerpt(segment["text"]))
    themes = []
    for theme in by_theme.values():
        evidence_refs = list(theme["evidence_refs"])
        themes.append(
            {
                "theme_id": theme["theme_id"],
                "label": theme["label"],
                "codes": sorted(theme["codes"]),
                "evidence_refs": evidence_refs,
                "representative_quotes": theme["representative_quotes"][:3],
                "validation": {
                    "status": "supported" if evidence_refs else "unsupported",
                    "evidence_count": len(evidence_refs),
                    "rule": "theme requires at least one coded pure-user-input segment",
                },
            }
        )
    return sorted(themes, key=lambda item: (-len(item["evidence_refs"]), item["theme_id"]))


def _matched_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(term for term in terms if term.lower() in lowered)


def _meaning_units(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    starts = [match.start() for match in re.finditer(r"\bDescription\b", stripped)]
    if len(starts) <= 1:
        return [stripped]
    units: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(stripped)
        unit = stripped[start:end].strip()
        if unit:
            units.append(unit)
    return units or [stripped]


def _quote_excerpt(text: str, limit: int = 700) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _confidence_for_terms(terms: tuple[str, ...]) -> float:
    if len(terms) >= 2:
        return 0.86
    return 0.72


def _codebook_payload(code: CodeDefinition) -> dict[str, Any]:
    return {
        "code_id": code.code_id,
        "label": code.label,
        "definition": code.definition,
        "include_terms": list(code.include_terms),
        "theme_id": code.theme_id,
    }
