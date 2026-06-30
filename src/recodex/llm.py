from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .analysis import ERROR_TERMS, TEST_TERMS, count_terms
from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text
from .transcripts import extract_user_input_text

PROMPT_VERSION = "session_retro_v4_user_efficiency_guidance"
SCHEMA_VERSION = "session_retro.v4"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_VOLCENGINE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_VOLCENGINE_MODEL = "doubao-seed-2-0-lite-260215"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_DASHSCOPE_MODEL = "qwen-plus"
DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_MODEL = "deepseek-ai/DeepSeek-V3.1"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://api.openai.com/v1"
SESSION_RETRO_MAX_OUTPUT_TOKENS = 4500
SESSION_RETRO_RETRY_MAX_OUTPUT_TOKENS = 6500


class LLMProvider(Protocol):
    provider_name: str
    last_usage: dict[str, object]

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        metadata: dict[str, object],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class LLMAnalysisRequest:
    task_type: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    input_hash: str
    system: str
    messages: list[dict[str, str]]
    schema: dict[str, Any]
    metadata: dict[str, object]


@dataclass(frozen=True)
class LLMAnalysisResult:
    output: dict[str, Any]
    usage: dict[str, object]
    warnings: tuple[str, ...] = ()
    cached: bool = False


class LLMResponseIncompleteError(RuntimeError):
    def __init__(self, provider_label: str, reason: str) -> None:
        self.provider_label = provider_label
        self.reason = reason
        super().__init__(f"{provider_label} response incomplete: {reason or 'unknown reason'}")


class MockProvider:
    provider_name = "mock"

    def __init__(self) -> None:
        self.last_usage: dict[str, object] = {}

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        metadata: dict[str, object],
    ) -> dict[str, Any]:
        self.last_usage = {}
        task_type = str(metadata.get("task_type") or "")
        if task_type.startswith("analysis_workflow_"):
            return _mock_workflow_output(task_type, messages)
        content = "\n".join(message.get("content", "") for message in messages)
        payload = _last_json_message(messages)
        raw_transcript = payload.get("raw_chat_transcript") if isinstance(payload.get("raw_chat_transcript"), dict) else {}
        chat_messages = [
            item
            for item in raw_transcript.get("messages", [])
            if isinstance(item, dict)
        ] if isinstance(raw_transcript, dict) else []
        chat_refs = [str(item.get("event_id")) for item in chat_messages if item.get("event_id")]
        has_verification = "verification_present=true" in content
        finding = {
            "title": "验收条件没有在开工前固定" if not has_verification else "验证结果需要进入用户验收清单",
            "category": "verification_gap",
            "severity": "high" if not has_verification else "medium",
            "confidence": 0.86,
            "problem": "会话中存在修改或执行动作，但用户没有提前拿到可持续对照的验收条件。",
            "evidence_refs": ["event_0"],
            "impact": "收尾阶段还要重新确认任务是否真的完成，后续容易返工。",
            "recommendation": "发起任务时先要求列出最小相关验证、完成标准、未覆盖风险和收尾对照格式。",
            "suggested_artifacts": ["checklist", "agents_md"],
        }
        return {
            "overall_assessment": "本次会话需要补强开发提效方式，重点是开工前验收边界和收尾对照。",
            "main_findings": [finding],
            "chat_findings": [
                {
                    "title": finding["title"],
                    "problem": finding["problem"],
                    "cause": "任务启动时没有把原话、阶段目标和验证证据绑定成可更新清单。",
                    "impact": finding["impact"],
                    "recommendation": finding["recommendation"],
                    "severity": finding["severity"],
                    "confidence": finding["confidence"],
                    "evidence_refs": chat_refs[:5] or ["event_0"],
                    "artifact_type": "checklist",
                    "artifact_title": "用户任务验收边界清单",
                    "artifact_target_path": "docs/ai-workflow-checklist.md",
                }
            ],
            "what_went_well": ["会话中保留了可引用的目标和聊天线索。"],
            "next_time_suggestions": ["用户下次先把验证命令、完成标准和未覆盖风险作为开工条件。"],
            "improvement_candidates": [
                {
                    "title": "增加用户任务验收边界清单",
                    "artifact_type": "checklist",
                    "priority": "high",
                    "effort": "low",
                    "why": "该改进能降低收尾阶段才补验收口径的成本。",
                    "evidence_refs": ["event_0"],
                }
            ],
            "chat_transcript_analysis": {
                "summary": (
                    f"已基于 {len(chat_messages)} 条纯聊天文本识别用户诉求、验收边界和提效机会。"
                    if chat_messages
                    else "未提取到纯聊天文本，无法做聊天原文分析。"
                ),
                "key_observations": [
                    "目标和验收要求已从工具输出中分离，适合单独判断开发提效机会。",
                ],
                "friction_points": [
                    "完成判断需要回到原话、任务清单和验收标准，不能只依赖命令或工具结果。",
                ],
                "evidence_refs": chat_refs[:5] or ["event_0"],
            },
        }


def _mock_workflow_output(task_type: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    payload = _last_json_message(messages)
    if task_type.endswith("_extract"):
        return _mock_workflow_extract(payload)
    if task_type.endswith("_cluster"):
        return _mock_workflow_cluster(payload)
    if task_type.endswith("_validate"):
        return _mock_workflow_validate(payload)
    if task_type.endswith("_report"):
        return _mock_workflow_report(payload)
    return {}


def _last_json_message(messages: list[dict[str, str]]) -> dict[str, Any]:
    for message in reversed(messages):
        content = message.get("content", "")
        if not content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _mock_workflow_extract(payload: dict[str, Any]) -> dict[str, Any]:
    unit = payload.get("analysis_unit") if isinstance(payload.get("analysis_unit"), dict) else {}
    segments = payload.get("qualitative_segments") if isinstance(payload.get("qualitative_segments"), list) else []
    refs = [str(item.get("source_ref")) for item in segments if isinstance(item, dict) and item.get("source_ref")]
    refs = list(dict.fromkeys(refs))
    text = "\n".join(
        str(item.get("text") or "")
        for item in segments
        if isinstance(item, dict)
    )
    has_error = count_terms(text, ERROR_TERMS) > 0
    issue_type = "user_intent_needs_workflow_translation"
    if "json" in text.lower() or "llm" in text.lower():
        issue_type = "llm_analysis_reliability"
    elif "导入" in text or "聊天记录" in text:
        issue_type = "context_ingestion_quality"
    severity = "high" if has_error else "medium"
    unit_id = str(unit.get("id") or "qualitative_unit_1")
    return {
        "analysis_unit_id": unit_id,
        "issues": [
            {
                "id": f"{unit_id}_issue_1",
                "issue_type": issue_type,
                "severity": severity,
                "evidence_refs": refs[:3] or ["missing_evidence"],
                "user_impact": "用户输入中的真实诉求如果不先结构化，后续报告容易跑偏。",
                "root_cause_hypothesis": "分析直接消费聊天文本或执行痕迹，没有先建立用户输入的编码单元。",
                "recommended_change": "先用定性编码单元提取 issue，再聚类、验证、合成报告。",
                "confidence": 0.84,
                "missing_evidence": [] if refs else ["source_ref"],
            }
        ],
        "observations": [
            "mock extractor used bounded qualitative user-input units instead of the full transcript.",
        ],
    }


def _mock_workflow_cluster(payload: dict[str, Any]) -> dict[str, Any]:
    issues = [item for item in payload.get("issues", payload.get("findings", [])) if isinstance(item, dict)]
    refs = []
    for issue in issues:
        refs.extend(str(ref) for ref in issue.get("evidence_refs", []) if ref)
    return {
        "clusters": [
            {
                "id": "cluster_verification_closure",
                "title": "验证闭环需要成为报告生成前置条件",
                "pattern": "会话完成判断依赖明确证据，而不是直接总结聊天内容。",
                "pattern_type": "verification_gap",
                "severity": "high" if issues else "medium",
                "confidence": 0.82,
                "issue_ids": [str(item.get("id")) for item in issues if item.get("id")],
                "evidence_refs": refs[:6],
                "impact": "报告可信度下降，用户需要重新确认完成状态。",
                "recommended_change": "将验证结果、失败原因和证据引用作为 LLM 报告阶段的必填输入。",
                "skill_candidate_allowed": len(issues) >= 2,
                "skill_gate_reason": "requires repeated supported evidence and human confirmation",
            }
        ] if issues else [],
        "discarded_issue_ids": [],
    }


def _mock_workflow_validate(payload: dict[str, Any]) -> dict[str, Any]:
    issues = [item for item in payload.get("issues", payload.get("findings", [])) if isinstance(item, dict)]
    clusters = [item for item in payload.get("clusters", []) if isinstance(item, dict)]
    validated_issues = [
        {
            "id": str(item.get("id") or ""),
            "status": "supported" if item.get("evidence_refs") else "weak",
            "confidence": min(0.9, float(item.get("confidence") or 0.5)),
            "reason": "mock validator confirmed that the issue cites source_refs.",
            "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if ref][:5],
        }
        for item in issues
    ]
    validated_clusters = [
        {
            "id": str(item.get("id") or ""),
            "status": "supported" if item.get("evidence_refs") else "weak",
            "confidence": min(0.9, float(item.get("confidence") or 0.5)),
            "reason": "mock validator confirmed that the pattern cites issue-backed source_refs.",
            "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if ref][:5],
        }
        for item in clusters
    ]
    return {
        "validated_issues": validated_issues,
        "validated_clusters": validated_clusters,
        "human_queue": [
            {
                "id": item["id"],
                "reason": "low confidence or missing evidence needs human review",
            }
            for item in [*validated_issues, *validated_clusters]
            if item["status"] != "supported" or item["confidence"] < 0.6
        ],
        "rejected_ids": [
            str(item.get("id")) for item in [*issues, *clusters] if not item.get("evidence_refs") and item.get("id")
        ],
        "warnings": [],
    }


def _mock_workflow_report(payload: dict[str, Any]) -> dict[str, Any]:
    qualitative = payload.get("qualitative_analysis") if isinstance(payload.get("qualitative_analysis"), dict) else {}
    themes = [item for item in qualitative.get("themes", []) if isinstance(item, dict)]
    clusters = [item for item in payload.get("validated_clusters", payload.get("clusters", [])) if isinstance(item, dict)]
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    validated = validation.get("validated_clusters") if isinstance(validation.get("validated_clusters"), list) else []
    skill_candidates = [item for item in payload.get("skill_candidates", []) if isinstance(item, dict)]
    user_intent = payload.get("user_intent") if isinstance(payload.get("user_intent"), dict) else {}
    primary_request = str(user_intent.get("primary_request") or "")
    return {
        "headline": "LLM 分阶段分析已完成",
        "overall": "本次分析先把用户输入转成定性编码单元，再进行 issue 提取、聚类、验证和报告合成。",
        "user_intent_summary": primary_request or "未抽取到明确用户输入，报告仅展示可验证执行证据。",
        "flow": [
            {
                "stage": str(theme.get("theme_id") or f"theme_{index + 1}"),
                "title": str(theme.get("label") or "Qualitative theme"),
                "description": f"用户输入证据 {theme.get('evidence_count', len(theme.get('evidence_refs', []) or []))} 条，编码：{', '.join(str(code) for code in theme.get('codes', [])[:4])}",
            }
            for index, theme in enumerate(themes[:8])
        ],
        "clusters": [
            {
                "title": str(item.get("title") or "Workflow issue"),
                "severity": str(item.get("severity") or "medium"),
                "pattern": str(item.get("pattern") or ""),
                "impact": str(item.get("impact") or ""),
                "recommended_change": str(item.get("recommended_change") or ""),
                "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if ref][:6],
            }
            for item in clusters[:8]
        ],
        "suggestions": [
            {
                "title": "把验证和证据引用设为报告门禁",
                "priority": "high",
                "why": "LLM 报告只消费校验后的 findings，可以降低幻觉和过度总结。",
                "recommendation": "保留 extractor/clusterer/validator/reporter 的阶段输出供人工审阅。",
            }
        ],
        "skill_drafts": [
            {
                "title": str(item.get("title") or ""),
                "status": str(item.get("status") or "draftable_after_human_confirmation"),
                "reason": str(item.get("reason") or ""),
                "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if ref],
            }
            for item in skill_candidates[:5]
        ],
        "verification": {
            "validated_items": len(validated),
            "method": "validator stage checks evidence refs before report synthesis",
            "status": "supported" if validated else "weak",
        },
    }


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.last_usage: dict[str, object] = {}

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        metadata: dict[str, object],
    ) -> dict[str, Any]:
        self.last_usage = {}
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI LLM analysis.")
        payload = openai_responses_payload(
            model=model,
            system=system,
            messages=messages,
            schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            metadata=metadata,
        )
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API request failed: HTTP {exc.code}: {body}") from exc
        self.last_usage = normalize_llm_usage(data.get("usage"))
        text = extract_response_text(data, provider_label="OpenAI")
        return parse_json_output_text(text, "OpenAI")


class OpenAICompatibleProvider:
    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        provider_name: str = "openai-compatible",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.provider_name = normalize_provider_name(provider_name)
        self.api_key = api_key or _api_key_from_env(self.provider_name)
        self.base_url = (base_url or _base_url_from_env(self.provider_name)).rstrip("/")
        self.last_usage: dict[str, object] = {}

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        metadata: dict[str, object],
    ) -> dict[str, Any]:
        self.last_usage = {}
        if not self.api_key:
            raise RuntimeError(
                f"{_default_api_key_env(self.provider_name)} is required for "
                f"{_provider_display_name(self.provider_name)} LLM analysis."
            )
        payload = chat_completions_payload(
            model=model,
            system=system,
            messages=messages,
            schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{_provider_display_name(self.provider_name)} API request failed: HTTP {exc.code}: {body}"
            ) from exc
        self.last_usage = normalize_llm_usage(data.get("usage"))
        text = extract_chat_completion_text(data, _provider_display_name(self.provider_name))
        return parse_json_output_text(text, _provider_display_name(self.provider_name))


class VolcengineProvider:
    provider_name = "volcengine"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = (
            api_key
            or os.environ.get("ARK_API_KEY")
            or os.environ.get("VOLCENGINE_API_KEY")
        )
        self.base_url = (
            base_url
            or os.environ.get("ARK_BASE_URL")
            or os.environ.get("VOLCENGINE_BASE_URL")
            or DEFAULT_VOLCENGINE_BASE_URL
        ).rstrip("/")
        self.last_usage: dict[str, object] = {}

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        metadata: dict[str, object],
    ) -> dict[str, Any]:
        self.last_usage = {}
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is required for Volcengine Ark LLM analysis.")
        payload = volcengine_responses_payload(
            model=model,
            system=system,
            messages=messages,
            schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Volcengine Ark API request failed: HTTP {exc.code}: {body}") from exc
        self.last_usage = normalize_llm_usage(data.get("usage"))
        text = extract_response_text(data, provider_label="Volcengine Ark")
        return parse_json_output_text(text, "Volcengine Ark")


def provider_for_name(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    normalized = normalize_provider_name(name)
    if normalized == "mock":
        return MockProvider()
    if normalized == "openai":
        return OpenAIProvider(api_key=api_key, base_url=base_url)
    if normalized in {"openai-compatible", "dashscope", "siliconflow"}:
        return OpenAICompatibleProvider(provider_name=normalized, api_key=api_key, base_url=base_url)
    if normalized == "volcengine":
        return VolcengineProvider(api_key=api_key, base_url=base_url)
    raise ValueError(f"Unsupported LLM provider: {name}")


def normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized in {"volcengine", "volc", "ark", "doubao", "byteplus"}:
        return "volcengine"
    if normalized in {"openai-compatible", "openai_compatible", "openai compatible", "compatible", "chat-completions", "chat_completions"}:
        return "openai-compatible"
    if normalized in {"dashscope", "aliyun", "alibaba", "bailian", "qwen", "tongyi"}:
        return "dashscope"
    if normalized in {"siliconflow", "silicon-flow", "silicon_flow"}:
        return "siliconflow"
    return normalized


def default_model_for_provider(name: str) -> str:
    normalized = normalize_provider_name(name)
    if normalized == "mock":
        return "mock-model"
    if normalized == "volcengine":
        return DEFAULT_VOLCENGINE_MODEL
    if normalized == "dashscope":
        return DEFAULT_DASHSCOPE_MODEL
    if normalized == "siliconflow":
        return DEFAULT_SILICONFLOW_MODEL
    return DEFAULT_OPENAI_MODEL


def default_base_url_for_provider(name: str) -> str:
    normalized = normalize_provider_name(name)
    if normalized == "volcengine":
        return DEFAULT_VOLCENGINE_BASE_URL
    if normalized == "dashscope":
        return DEFAULT_DASHSCOPE_BASE_URL
    if normalized == "siliconflow":
        return DEFAULT_SILICONFLOW_BASE_URL
    if normalized in {"openai", "openai-compatible"}:
        return DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    return ""


def build_session_retro_request(
    session: SessionRecord,
    events: list[TranscriptEvent],
    *,
    provider: str,
    model: str,
) -> LLMAnalysisRequest:
    context = build_session_context(session, events)
    serialized_context = json.dumps(context, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(
        "\n".join([PROMPT_VERSION, SCHEMA_VERSION, model, serialized_context]).encode("utf-8")
    ).hexdigest()
    return LLMAnalysisRequest(
        task_type="session_retro",
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        input_hash=input_hash,
        system=session_retro_system_prompt(),
        messages=[
            {
                "role": "user",
                "content": serialized_context,
            }
        ],
        schema=session_retro_schema(),
        metadata={
            "task_type": "session_retro",
            "session_id": session.session_id,
            "project_path": redact_text(session.project_path or ""),
            "privacy_level": "redacted",
        },
    )


def build_session_context(session: SessionRecord, events: list[TranscriptEvent]) -> dict[str, object]:
    signal_events = [event for event in events if _is_signal_event(event)]
    text = "\n".join(event.text for event in signal_events)
    verification_count = count_terms(text, TEST_TERMS)
    error_count = session.error_count + count_terms(text, ERROR_TERMS)
    evidence = [_event_ref(event) for event in _select_context_events(signal_events)]
    user_intent = _session_user_intent(signal_events)
    raw_chat_transcript = _raw_chat_transcript(events)
    return {
        "session": {
            "id": session.session_id,
            "title": redact_text(session.title),
            "project_path": redact_text(session.project_path or ""),
            "started_at": session.started_at,
            "updated_at": session.updated_at,
            "message_count": session.message_count,
            "command_count": session.command_count,
            "error_count": session.error_count,
        },
        "facts": {
            "verification_present": verification_count > 0,
            "verification_signals": verification_count,
            "error_signals": error_count,
            "has_commands": session.command_count > 0,
            "user_input_count": user_intent["user_input_count"],
            "context_event_count": user_intent["context_event_count"],
            "chat_transcript_messages": raw_chat_transcript["included_message_count"],
        },
        "analysis_focus": {
            "primary": "user_intent.timeline",
            "supporting": ["raw_chat_transcript", "evidence", "facts"],
            "rule": "Use pure user inputs as the task intent. Context-only rows and tool outputs can only support evidence-backed findings.",
            "chat_rule": "Analyze raw_chat_transcript as the conversation text. It excludes tool calls, tool outputs, command results, and environment context.",
        },
        "user_intent": user_intent,
        "raw_chat_transcript": raw_chat_transcript,
        "evidence": evidence,
    }


def _raw_chat_transcript(events: list[TranscriptEvent]) -> dict[str, object]:
    total_chat_messages = 0
    omitted_tool_events = 0
    omitted_context_events = 0
    messages: list[dict[str, object]] = []
    for event in events:
        if event.role not in {"user", "assistant"}:
            if event.role == "tool" or _is_tool_like_event(event):
                omitted_tool_events += 1
            continue
        if _is_tool_like_event(event):
            omitted_tool_events += 1
            continue
        text = extract_user_input_text(event.text) if event.role == "user" else event.text
        if not text or _is_non_chat_context_text(text):
            omitted_context_events += 1
            continue
        cleaned = _excerpt(redact_text(text.strip()), 2000)
        if not cleaned:
            omitted_context_events += 1
            continue
        total_chat_messages += 1
        if len(messages) >= 80:
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
    return {
        "scope": "user_and_assistant_chat_text_only",
        "privacy": "redacted",
        "excludes": [
            "tool_calls",
            "tool_outputs",
            "command_results",
            "environment_context",
            "system_or_developer_instructions",
        ],
        "message_count": total_chat_messages,
        "included_message_count": len(messages),
        "omitted_tool_event_count": omitted_tool_events,
        "omitted_context_event_count": omitted_context_events,
        "truncated": total_chat_messages > len(messages),
        "messages": messages,
    }


def _is_tool_like_event(event: TranscriptEvent) -> bool:
    kind = event.kind.lower()
    if any(token in kind for token in ("tool", "command", "exec", "function_call")):
        return True
    if any(key in event.metadata for key in ("command", "cmd", "tool_call_id", "exit_code")):
        return True
    lowered = event.text.strip().lower()
    return lowered.startswith(("command=", "cmd=", "tool output:", "process exited with code"))


def _is_non_chat_context_text(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    if lowered.startswith(
        (
            "<environment_context>",
            "<permissions",
            "<collaboration_mode>",
            "<skills_instructions>",
            "<turn_aborted>",
            "chunk id:",
            "cwd=",
            "model=",
        )
    ):
        return True
    return "you are codex" in lowered or "original token count" in lowered


def session_retro_system_prompt() -> str:
    return "\n".join(
        [
            "你是面向用户的 AI 编程提效复盘分析器。",
            "报告读者是使用 AI 编程工具的用户/开发者；分析主体必须是用户如何组织需求、上下文、验收和收尾。",
            "必须先基于 user_intent.timeline 识别用户真实诉求；工具输出、环境上下文和 AGENTS 只能作为支撑证据。",
            "必须单独阅读 raw_chat_transcript，分析原始聊天文字中的用户诉求、完成度追问、纠偏、验收边界和提效机会。",
            "raw_chat_transcript 只包含用户/助手聊天文字；不要把工具调用、工具输出或命令结果当成聊天原文。",
            "chat_transcript_analysis 必须引用 raw_chat_transcript.messages 中的 event_id。",
            "chat_findings 的 recommendation 必须写成用户可执行动作，例如如何描述需求、要求任务列表、维护完成度账本、约定验证方式、沉淀项目知识。",
            "不要把主建议写成助手/agent 应该主动展示、邀请用户确认、给用户说明或在最终回答中呈现成功。",
            "报告展示字段要直接面向读者，优先写“下次发起任务前……”“开工前先……”，不要反复写“用户……”。",
            "只基于提供的事实和证据判断，不要编造。",
            "不要输出内部规则编号。",
            "每个 finding 必须包含 evidence_refs。",
            "建议必须具体，并能落地为 AGENTS.md、checklist、skill、script、CI、hook 或 prompt template。",
            "输出必须符合提供的 JSON schema。",
        ]
    )


def session_retro_schema() -> dict[str, Any]:
    finding = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "category",
            "severity",
            "confidence",
            "problem",
            "evidence_refs",
            "impact",
            "recommendation",
            "suggested_artifacts",
        ],
        "properties": {
            "title": {"type": "string"},
            "category": {
                "type": "string",
                "enum": [
                    "verification_gap",
                    "context_gap",
                    "planning_gap",
                    "bugfix_gap",
                    "tool_waste",
                    "safety_risk",
                    "false_completion",
                    "scope_creep",
                    "other",
                ],
            },
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "confidence": {"type": "number"},
            "problem": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "impact": {"type": "string"},
            "recommendation": {"type": "string"},
            "suggested_artifacts": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "agents_md",
                        "checklist",
                        "skill",
                        "script",
                        "ci",
                        "hook",
                        "prompt_template",
                        "none",
                    ],
                },
            },
        },
    }
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "artifact_type", "priority", "effort", "why", "evidence_refs"],
        "properties": {
            "title": {"type": "string"},
            "artifact_type": {
                "type": "string",
                "enum": ["agents_md", "checklist", "skill", "script", "ci", "hook", "prompt_template"],
            },
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "effort": {"type": "string", "enum": ["low", "medium", "high"]},
            "why": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    chat_transcript_analysis = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "key_observations", "friction_points", "evidence_refs"],
        "properties": {
            "summary": {"type": "string"},
            "key_observations": {"type": "array", "items": {"type": "string"}},
            "friction_points": {"type": "array", "items": {"type": "string"}},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    chat_finding = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "problem",
            "cause",
            "impact",
            "recommendation",
            "severity",
            "confidence",
            "evidence_refs",
            "artifact_type",
            "artifact_title",
            "artifact_target_path",
        ],
        "properties": {
            "title": {"type": "string"},
            "problem": {"type": "string"},
            "cause": {"type": "string"},
            "impact": {"type": "string"},
            "recommendation": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "confidence": {"type": "number"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "artifact_type": {
                "type": "string",
                "enum": ["agents_md", "checklist", "skill", "script", "ci", "hook", "prompt_template", "none"],
            },
            "artifact_title": {"type": "string"},
            "artifact_target_path": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "overall_assessment",
            "main_findings",
            "what_went_well",
            "next_time_suggestions",
            "improvement_candidates",
            "chat_transcript_analysis",
            "chat_findings",
        ],
        "properties": {
            "overall_assessment": {"type": "string"},
            "main_findings": {"type": "array", "items": finding},
            "chat_findings": {"type": "array", "items": chat_finding},
            "what_went_well": {"type": "array", "items": {"type": "string"}},
            "next_time_suggestions": {"type": "array", "items": {"type": "string"}},
            "improvement_candidates": {"type": "array", "items": candidate},
            "chat_transcript_analysis": chat_transcript_analysis,
        },
    }


def validate_session_retro_output(output: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    warnings: list[str] = []
    required = {
        "overall_assessment",
        "main_findings",
        "what_went_well",
        "next_time_suggestions",
        "improvement_candidates",
        "chat_findings",
    }
    missing = [key for key in required if key not in output]
    if missing:
        raise ValueError(f"LLM output missing required fields: {', '.join(missing)}")
    findings = []
    for finding in output.get("main_findings", [])[:5]:
        if not isinstance(finding, dict):
            warnings.append("Dropped non-object finding.")
            continue
        refs = finding.get("evidence_refs") or []
        if not refs:
            warnings.append(f"Dropped finding without evidence: {finding.get('title', '<untitled>')}")
            continue
        findings.append(finding)
    cleaned = dict(output)
    cleaned["main_findings"] = findings[:5]
    cleaned["what_went_well"] = list(output.get("what_went_well") or [])[:5]
    cleaned["next_time_suggestions"] = list(output.get("next_time_suggestions") or [])[:5]
    cleaned["improvement_candidates"] = list(output.get("improvement_candidates") or [])[:5]
    cleaned["chat_findings"] = _clean_chat_findings(output.get("chat_findings"), warnings)
    cleaned["chat_transcript_analysis"] = _clean_chat_transcript_analysis(
        output.get("chat_transcript_analysis"),
        warnings,
    )
    return cleaned, tuple(warnings)


def _clean_chat_transcript_analysis(value: object, warnings: list[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        warnings.append("Dropped invalid chat_transcript_analysis.")
        return {
            "summary": "LLM did not return a valid chat transcript analysis.",
            "key_observations": [],
            "friction_points": [],
            "evidence_refs": [],
        }
    return {
        "summary": str(value.get("summary") or ""),
        "key_observations": _clean_string_list(value.get("key_observations"), 5),
        "friction_points": _clean_string_list(value.get("friction_points"), 5),
        "evidence_refs": _clean_string_list(value.get("evidence_refs"), 8),
    }


def _clean_chat_findings(value: object, warnings: list[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        warnings.append("Dropped invalid chat_findings.")
        return []
    findings: list[dict[str, object]] = []
    for index, raw in enumerate(value[:5]):
        if not isinstance(raw, dict):
            warnings.append("Dropped non-object chat finding.")
            continue
        refs = _clean_string_list(raw.get("evidence_refs"), 8)
        if not refs:
            warnings.append(f"Dropped chat finding without evidence: {raw.get('title', '<untitled>')}")
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            warnings.append(f"Dropped chat finding without title at index {index}.")
            continue
        artifact_type = str(raw.get("artifact_type") or "none")
        findings.append(
            {
                "id": str(raw.get("id") or f"chat_finding_{len(findings) + 1}"),
                "title": title,
                "problem": str(raw.get("problem") or ""),
                "cause": str(raw.get("cause") or raw.get("problem") or ""),
                "impact": str(raw.get("impact") or ""),
                "recommendation": str(raw.get("recommendation") or ""),
                "severity": str(raw.get("severity") or "medium"),
                "confidence": _number(raw.get("confidence"), 0.5),
                "evidence_refs": refs,
                "artifact_type": artifact_type,
                "artifact_title": str(raw.get("artifact_title") or title),
                "artifact_target_path": str(raw.get("artifact_target_path") or _default_chat_artifact_target(artifact_type)),
            }
        )
    return findings


def _default_chat_artifact_target(artifact_type: str) -> str:
    return {
        "agents_md": "AGENTS.md",
        "checklist": "docs/ai-workflow-checklist.md",
        "skill": "skills/session-retro-review/SKILL.md",
        "script": "scripts/ai-workflow-check.sh",
        "ci": ".github/workflows/ai-review.yml",
        "hook": ".githooks/pre-commit",
        "prompt_template": "docs/prompts/session-retro-template.md",
    }.get(artifact_type, "docs/session-retro-notes.md")


def _number(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clean_string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


def normalize_llm_usage(raw_usage: Any) -> dict[str, object]:
    if not isinstance(raw_usage, dict):
        return {}
    input_tokens = _usage_int(raw_usage.get("input_tokens"), raw_usage.get("prompt_tokens"))
    output_tokens = _usage_int(raw_usage.get("output_tokens"), raw_usage.get("completion_tokens"))
    total_tokens = _usage_int(raw_usage.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

    result: dict[str, object] = {}
    if input_tokens is not None:
        result["input_tokens"] = input_tokens
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    if total_tokens is not None:
        result["total_tokens"] = total_tokens

    input_details = _usage_details(raw_usage.get("input_tokens_details"), raw_usage.get("prompt_tokens_details"))
    output_details = _usage_details(raw_usage.get("output_tokens_details"), raw_usage.get("completion_tokens_details"))
    cached_tokens = _usage_int(input_details.get("cached_tokens"))
    reasoning_tokens = _usage_int(output_details.get("reasoning_tokens"))
    if cached_tokens is not None:
        result["cached_tokens"] = cached_tokens
    if reasoning_tokens is not None:
        result["reasoning_tokens"] = reasoning_tokens
    if result:
        result["source"] = "provider"
    return result


def llm_usage_payload(
    request: LLMAnalysisRequest,
    output: dict[str, Any],
    *,
    max_output_tokens: int,
    provider_usage: dict[str, object] | None = None,
    cached: bool = False,
    warnings: tuple[str, ...] = (),
    retried: bool = False,
    stage: str | None = None,
) -> dict[str, object]:
    usage = normalize_llm_usage(provider_usage or {})
    if not usage:
        usage = estimate_llm_usage(request, output)
    total_tokens = int(usage.get("total_tokens") or 0)
    payload: dict[str, object] = {
        **usage,
        "task_type": request.task_type,
        "provider": request.provider,
        "model": request.model,
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "max_output_tokens": max_output_tokens,
        "cached": cached,
        "retried": retried,
        "warnings": list(warnings),
        "current_run_total_tokens": 0 if cached else total_tokens,
    }
    if stage:
        payload["stage"] = stage
    return payload


def generate_session_retro_analysis(
    provider: LLMProvider,
    request: LLMAnalysisRequest,
) -> LLMAnalysisResult:
    active_request = request
    max_output_tokens = SESSION_RETRO_MAX_OUTPUT_TOKENS
    retry_warnings: tuple[str, ...] = ()
    try:
        raw_output = provider.generate_json(
            model=active_request.model,
            system=active_request.system,
            messages=active_request.messages,
            schema=active_request.schema,
            temperature=0,
            max_output_tokens=max_output_tokens,
            metadata=active_request.metadata,
        )
    except LLMResponseIncompleteError as exc:
        if not _should_retry_session_retro_incomplete(exc):
            raise
        active_request = _compact_session_retro_retry_request(request, reason=exc.reason)
        max_output_tokens = SESSION_RETRO_RETRY_MAX_OUTPUT_TOKENS
        retry_warnings = ("compact_retry", f"compact_retry_reason:{exc.reason}")
        raw_output = provider.generate_json(
            model=active_request.model,
            system=active_request.system,
            messages=active_request.messages,
            schema=active_request.schema,
            temperature=0,
            max_output_tokens=max_output_tokens,
            metadata=active_request.metadata,
        )
    cleaned, validation_warnings = validate_session_retro_output(raw_output)
    warnings = (*retry_warnings, *validation_warnings)
    usage = llm_usage_payload(
        active_request,
        cleaned,
        max_output_tokens=max_output_tokens,
        provider_usage=getattr(provider, "last_usage", {}),
        warnings=warnings,
        retried=bool(retry_warnings),
    )
    return LLMAnalysisResult(output=cleaned, usage=usage, warnings=warnings)


def _should_retry_session_retro_incomplete(exc: LLMResponseIncompleteError) -> bool:
    return exc.reason.lower() in {"length", "max_output_tokens"}


def _compact_session_retro_retry_request(
    request: LLMAnalysisRequest,
    *,
    reason: str,
) -> LLMAnalysisRequest:
    context = _session_retro_request_context(request)
    compact_context = _compact_session_retro_context(context, reason=reason)
    return replace(
        request,
        messages=[
            {
                "role": "user",
                "content": json.dumps(compact_context, ensure_ascii=False, sort_keys=True),
            }
        ],
        system="\n".join(
            [
                request.system,
                "重试模式：上一次输出被供应商截断。",
                "只输出最关键结论；每个文本字段必须短句化，不要展开背景。",
                "main_findings 最多 2 条，improvement_candidates 最多 3 条，what_went_well 和 next_time_suggestions 最多 2 条。",
                "chat_transcript_analysis 必须保留，但只总结最核心的用户纠偏、验收边界和提效机会。",
                "建议必须以用户为动作主体，不要写成 assistant 主动向用户展示或邀请用户确认。",
            ]
        ),
        metadata={
            **request.metadata,
            "retry": "compact",
            "retry_reason": reason,
        },
    )


def _session_retro_request_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    for message in request.messages:
        content = message.get("content", "")
        if not content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _compact_session_retro_context(
    context: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    user_intent = context.get("user_intent") if isinstance(context.get("user_intent"), dict) else {}
    raw_chat = context.get("raw_chat_transcript") if isinstance(context.get("raw_chat_transcript"), dict) else {}
    return {
        "session": context.get("session") if isinstance(context.get("session"), dict) else {},
        "facts": context.get("facts") if isinstance(context.get("facts"), dict) else {},
        "analysis_focus": {
            "primary": "raw_chat_transcript",
            "supporting": ["user_intent", "facts", "evidence"],
            "rule": "Compact retry after provider length truncation. Preserve only the highest-leverage issue.",
            "chat_rule": "Use only user and assistant chat text; tool outputs remain excluded.",
        },
        "user_intent": {
            "primary_request": _compact_text(user_intent.get("primary_request"), 260),
            "latest_request": _compact_text(user_intent.get("latest_request"), 260),
            "user_input_count": user_intent.get("user_input_count", 0),
            "context_event_count": user_intent.get("context_event_count", 0),
            "timeline": _compact_timeline(user_intent.get("timeline"), limit=12),
        },
        "raw_chat_transcript": {
            "scope": raw_chat.get("scope", "user_and_assistant_chat_text_only"),
            "privacy": raw_chat.get("privacy", "redacted"),
            "excludes": raw_chat.get("excludes", []),
            "message_count": raw_chat.get("message_count", 0),
            "included_message_count": min(
                _int_value(raw_chat.get("included_message_count"), 0),
                12,
            ),
            "truncated": bool(raw_chat.get("truncated")) or _int_value(raw_chat.get("message_count"), 0) > 12,
            "messages": _compact_chat_messages(raw_chat.get("messages"), limit=12),
        },
        "evidence": _compact_evidence(context.get("evidence"), limit=6),
        "response_limits": {
            "max_findings": 2,
            "max_improvement_candidates": 3,
            "max_evidence_refs_per_item": 3,
            "max_text_chars_per_field": 180,
            "style": "compact_json_only",
        },
        "retry_context": {
            "reason": reason,
            "strategy": "compact_session_retro_retry",
        },
    }


def _compact_timeline(value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    selected = _head_tail_items(value, limit=limit)
    compact: list[dict[str, Any]] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "event_id": str(item.get("event_id") or item.get("source_ref") or ""),
                "source_ref": str(item.get("source_ref") or item.get("event_id") or ""),
                "created_at": item.get("created_at"),
                "text": _compact_text(item.get("text"), 260),
            }
        )
    return compact


def _compact_chat_messages(value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    selected = _head_tail_items(value, limit=limit)
    compact: list[dict[str, Any]] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "event_id": str(item.get("event_id") or ""),
                "role": str(item.get("role") or ""),
                "kind": str(item.get("kind") or ""),
                "created_at": item.get("created_at"),
                "text": _compact_text(item.get("text"), 300),
            }
        )
    return compact


def _compact_evidence(value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "id": str(item.get("id") or ""),
                "role": str(item.get("role") or ""),
                "kind": str(item.get("kind") or ""),
                "text": _compact_text(item.get("text"), 260),
                "user_input_text": _compact_text(item.get("user_input_text"), 260),
            }
        )
    return compact


def _head_tail_items(value: list[object], *, limit: int) -> list[object]:
    if len(value) <= limit:
        return value
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count)
    return [*value[:head_count], *value[-tail_count:]]


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def llm_cached_usage_payload(
    request: LLMAnalysisRequest,
    *,
    cached_usage: Any,
    max_output_tokens: int,
    warnings: tuple[str, ...] = (),
    stage: str | None = None,
) -> dict[str, object]:
    previous = cached_usage if isinstance(cached_usage, dict) else {}
    usage = _existing_usage_payload(previous) or normalize_llm_usage(previous)
    if not usage:
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "source": "missing_cache_usage",
            "estimated": False,
            "missing_usage": True,
        }
    previous_warnings = [str(item) for item in previous.get("warnings", [])] if isinstance(previous.get("warnings"), list) else []
    payload: dict[str, object] = {
        **usage,
        "task_type": request.task_type,
        "provider": request.provider,
        "model": request.model,
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "max_output_tokens": int(previous.get("max_output_tokens") or max_output_tokens),
        "cached": True,
        "cache_hit": True,
        "retried": bool(previous.get("retried")),
        "warnings": [*previous_warnings, *list(warnings)],
        "original_total_tokens": int(usage.get("total_tokens") or 0),
        "current_run_total_tokens": 0,
    }
    if stage or previous.get("stage"):
        payload["stage"] = str(stage or previous.get("stage"))
    if previous.get("estimated"):
        payload["estimated"] = True
    return payload


def estimate_llm_usage(request: LLMAnalysisRequest, output: dict[str, Any]) -> dict[str, object]:
    input_payload = {
        "system": request.system,
        "messages": request.messages,
        "schema": request.schema,
        "metadata": request.metadata,
    }
    input_tokens = _estimate_tokens(input_payload)
    output_tokens = _estimate_tokens(output)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "source": "estimated",
        "estimated": True,
    }


def llm_token_usage_report(calls: list[dict[str, object]]) -> dict[str, object]:
    cleaned = [_clean_usage_call(call) for call in calls if isinstance(call, dict)]
    totals = {
        "input_tokens": sum(int(call.get("input_tokens") or 0) for call in cleaned),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in cleaned),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in cleaned),
        "current_run_total_tokens": sum(int(call.get("current_run_total_tokens") or 0) for call in cleaned),
        "cached_calls": sum(1 for call in cleaned if call.get("cached")),
        "estimated_calls": sum(1 for call in cleaned if call.get("estimated")),
        "provider_reported_calls": sum(1 for call in cleaned if call.get("source") == "provider"),
    }
    return {
        "calls": cleaned,
        "totals": totals,
    }


def parse_llm_usage_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def llm_usage_has_tokens(value: dict[str, object]) -> bool:
    if any(_usage_int(value.get(key)) is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
        return True
    calls = value.get("calls")
    if isinstance(calls, list):
        return any(isinstance(call, dict) and llm_usage_has_tokens(call) for call in calls)
    totals = value.get("totals")
    return isinstance(totals, dict) and any(
        _usage_int(totals.get(key)) is not None
        for key in ("input_tokens", "output_tokens", "total_tokens")
    )


def _usage_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            integer = int(value)
            return integer if integer >= 0 else None
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _usage_details(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _estimate_tokens(value: object) -> int:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _clean_usage_call(call: dict[str, object]) -> dict[str, object]:
    allowed = {
        "task_type",
        "stage",
        "provider",
        "model",
        "prompt_version",
        "schema_version",
        "input_hash",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "current_run_total_tokens",
        "original_total_tokens",
        "max_output_tokens",
        "cached",
        "cache_hit",
        "estimated",
        "missing_usage",
        "source",
        "retried",
        "warnings",
    }
    return {key: value for key, value in call.items() if key in allowed}


def _existing_usage_payload(value: dict[str, object]) -> dict[str, object]:
    token_keys = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "estimated",
        "missing_usage",
        "source",
    }
    result = {key: value[key] for key in token_keys if key in value}
    if not any(key in result for key in {"input_tokens", "output_tokens", "total_tokens"}):
        return {}
    return result


def openai_responses_payload(
    *,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    temperature: float,
    max_output_tokens: int,
    metadata: dict[str, object],
) -> dict[str, Any]:
    return responses_api_payload(
        model=model,
        system=system,
        messages=messages,
        schema=schema,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
        include_metadata=True,
    )


def volcengine_responses_payload(
    *,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    temperature: float,
    max_output_tokens: int,
) -> dict[str, Any]:
    payload = responses_api_payload(
        model=model,
        system=system,
        messages=messages,
        schema=schema,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        metadata={},
        include_metadata=False,
    )
    payload["thinking"] = {"type": "disabled"}
    return payload


def chat_completions_payload(
    *,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    temperature: float,
    max_output_tokens: int,
) -> dict[str, Any]:
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    system_with_schema = "\n\n".join(
        [
            system,
            "Return only valid JSON matching this JSON Schema.",
            schema_text,
        ]
    )
    return {
        "model": model,
        "messages": [{"role": "system", "content": system_with_schema}, *messages],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }


def responses_api_payload(
    *,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    temperature: float,
    max_output_tokens: int,
    metadata: dict[str, object],
    include_metadata: bool,
) -> dict[str, Any]:
    input_messages = [{"role": "system", "content": system}, *messages]
    payload = {
        "model": model,
        "input": input_messages,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "session_retro",
                "schema": schema,
                "strict": True,
            }
        },
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if include_metadata:
        payload["metadata"] = {key: str(value) for key, value in metadata.items() if value is not None}
    return payload


def extract_response_text(response: dict[str, Any], provider_label: str = "OpenAI") -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    if isinstance(response.get("error"), dict):
        error = response["error"]
        message = error.get("message") or error.get("code") or error.get("type") or error
        raise RuntimeError(f"{provider_label} response error: {message}")
    status = response.get("status")
    if status == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else details
        raise LLMResponseIncompleteError(provider_label, str(reason or "unknown reason"))
    chunks: list[str] = []
    refusals: list[str] = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and isinstance(item.get("text"), str):
            chunks.append(item["text"])
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
            elif isinstance(content.get("json"), dict):
                chunks.append(json.dumps(content["json"], ensure_ascii=False))
            elif isinstance(content.get("parsed"), dict):
                chunks.append(json.dumps(content["parsed"], ensure_ascii=False))
            elif content.get("type") == "refusal":
                refusals.append(str(content.get("refusal") or content.get("text") or "refusal"))
    if chunks:
        return "\n".join(chunks)
    if refusals:
        raise RuntimeError(f"{provider_label} response refusal: {' / '.join(refusals)}")
    output_types = [
        str(item.get("type"))
        for item in response.get("output", []) or []
        if isinstance(item, dict) and item.get("type")
    ]
    suffix = f" output_types={output_types}" if output_types else ""
    raise RuntimeError(f"{provider_label} response did not contain output text.{suffix}")


def extract_chat_completion_text(
    response: dict[str, Any],
    provider_label: str = "Chat completions",
) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            finish_reason = str(first.get("finish_reason") or "").lower()
            if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                raise LLMResponseIncompleteError(provider_label, finish_reason)
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [
                        str(item.get("text"))
                        for item in content
                        if isinstance(item, dict) and isinstance(item.get("text"), str)
                    ]
                    if parts:
                        return "\n".join(parts)
    raise RuntimeError("Chat completions response did not contain message content.")


def parse_json_output_text(text: str, provider_label: str) -> dict[str, Any]:
    candidate = _json_text_candidate(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider_label} response did not contain valid JSON output.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{provider_label} response JSON output must be an object.")
    return parsed


def _json_text_candidate(text: str) -> str:
    stripped = _strip_json_code_fence(text)
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    balanced = _extract_balanced_json(stripped)
    return balanced or stripped


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_balanced_json(text: str) -> str | None:
    start = _first_json_start(text)
    if start is None:
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    stack = [closer]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[start : index + 1].strip()
    return None


def _first_json_start(text: str) -> int | None:
    object_index = text.find("{")
    array_index = text.find("[")
    candidates = [index for index in (object_index, array_index) if index >= 0]
    return min(candidates) if candidates else None


def _api_key_from_env(provider_name: str) -> str | None:
    normalized = normalize_provider_name(provider_name)
    if normalized == "dashscope":
        return (
            os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ALIYUN_API_KEY")
            or os.environ.get("ALIBABA_CLOUD_API_KEY")
        )
    if normalized == "siliconflow":
        return os.environ.get("SILICONFLOW_API_KEY")
    return os.environ.get("OPENAI_API_KEY")


def _base_url_from_env(provider_name: str) -> str:
    normalized = normalize_provider_name(provider_name)
    if normalized == "dashscope":
        return os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL
    if normalized == "siliconflow":
        return os.environ.get("SILICONFLOW_BASE_URL") or DEFAULT_SILICONFLOW_BASE_URL
    return os.environ.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_COMPATIBLE_BASE_URL


def _default_api_key_env(provider_name: str) -> str:
    normalized = normalize_provider_name(provider_name)
    if normalized == "dashscope":
        return "DASHSCOPE_API_KEY"
    if normalized == "siliconflow":
        return "SILICONFLOW_API_KEY"
    return "OPENAI_API_KEY"


def _provider_display_name(provider_name: str) -> str:
    normalized = normalize_provider_name(provider_name)
    if normalized == "dashscope":
        return "Alibaba Bailian"
    if normalized == "siliconflow":
        return "SiliconFlow"
    if normalized == "openai-compatible":
        return "OpenAI-compatible"
    return normalized


def _select_context_events(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    selected: list[TranscriptEvent] = []
    user_goal = next((event for event in events if event.role == "user" and extract_user_input_text(event.text)), None)
    failed = next((event for event in events if _looks_failed(event.text)), None)
    final = next((event for event in reversed(events) if event.role == "assistant"), None)
    for event in (user_goal, failed, final):
        if event is not None and event not in selected:
            selected.append(event)
    return selected or events[:3]


def _event_ref(event: TranscriptEvent) -> dict[str, str]:
    ref = f"event_{event.event_index}"
    user_input = extract_user_input_text(event.text) if event.role == "user" else None
    return {
        "id": ref,
        "role": event.role,
        "kind": event.kind,
        "text": _excerpt(redact_text(user_input or event.text), 700),
        "user_input_text": _excerpt(redact_text(user_input), 700) if user_input else "",
    }


def _session_user_intent(events: list[TranscriptEvent]) -> dict[str, object]:
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
                "created_at": event.created_at,
                "text": _excerpt(redact_text(user_input), 700),
            }
        )
    primary = str(timeline[0]["text"]) if timeline else ""
    return {
        "primary_request": primary,
        "latest_request": str(timeline[-1]["text"]) if timeline else primary,
        "user_input_count": len(timeline),
        "context_event_count": context_count,
        "timeline": timeline[:40],
    }


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


def _looks_failed(text: str) -> bool:
    lowered = text.lower()
    if "process exited with code 0" in lowered:
        return False
    return count_terms(lowered, ERROR_TERMS) > 0


def _excerpt(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
