from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .analysis import ERROR_TERMS, TEST_TERMS, count_terms
from .models import SessionRecord, TranscriptEvent
from .privacy import redact_text

PROMPT_VERSION = "session_retro_v1"
SCHEMA_VERSION = "session_retro.v1"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_VOLCENGINE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_VOLCENGINE_MODEL = "doubao-seed-2-0-lite-260215"


class LLMProvider(Protocol):
    provider_name: str

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


class MockProvider:
    provider_name = "mock"

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
        content = "\n".join(message.get("content", "") for message in messages)
        has_verification = "verification_present=true" in content
        finding = {
            "title": "修改后缺少验证闭环" if not has_verification else "验证结果需要明确呈现",
            "category": "verification_gap",
            "severity": "high" if not has_verification else "medium",
            "confidence": 0.86,
            "problem": "会话中存在修改或执行动作，但没有足够清晰的验证结果。",
            "evidence_refs": ["event_0"],
            "impact": "用户无法确认任务是否真的完成，后续容易返工。",
            "recommendation": "完成前运行最小相关验证，并在最终回答中列出命令和结果。",
            "suggested_artifacts": ["checklist", "agents_md"],
        }
        return {
            "overall_assessment": "本次会话需要补强完成可信度，重点是验证闭环和证据呈现。",
            "main_findings": [finding],
            "what_went_well": ["会话中保留了可引用的用户目标和命令线索。"],
            "next_time_suggestions": ["把验证命令作为完成条件，而不是最终补充说明。"],
            "improvement_candidates": [
                {
                    "title": "增加完成验证检查清单",
                    "artifact_type": "checklist",
                    "priority": "high",
                    "effort": "low",
                    "why": "该改进能直接降低未验证完成的风险。",
                    "evidence_refs": ["event_0"],
                }
            ],
        }


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

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
        text = extract_response_text(data)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI response did not contain valid JSON output.") from exc


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
        text = extract_response_text(data)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Volcengine Ark response did not contain valid JSON output.") from exc


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
    if normalized == "volcengine":
        return VolcengineProvider(api_key=api_key, base_url=base_url)
    raise ValueError(f"Unsupported LLM provider: {name}")


def normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized in {"volcengine", "volc", "ark", "doubao", "byteplus"}:
        return "volcengine"
    return normalized


def default_model_for_provider(name: str) -> str:
    normalized = normalize_provider_name(name)
    if normalized == "mock":
        return "mock-model"
    if normalized == "volcengine":
        return DEFAULT_VOLCENGINE_MODEL
    return DEFAULT_OPENAI_MODEL


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
        },
        "evidence": evidence,
    }


def session_retro_system_prompt() -> str:
    return "\n".join(
        [
            "你是 AI 开发会话复盘分析器。",
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
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "overall_assessment",
            "main_findings",
            "what_went_well",
            "next_time_suggestions",
            "improvement_candidates",
        ],
        "properties": {
            "overall_assessment": {"type": "string"},
            "main_findings": {"type": "array", "items": finding},
            "what_went_well": {"type": "array", "items": {"type": "string"}},
            "next_time_suggestions": {"type": "array", "items": {"type": "string"}},
            "improvement_candidates": {"type": "array", "items": candidate},
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
    return cleaned, tuple(warnings)


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
    return responses_api_payload(
        model=model,
        system=system,
        messages=messages,
        schema=schema,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        metadata={},
        include_metadata=False,
    )


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


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    if chunks:
        return "\n".join(chunks)
    raise RuntimeError("OpenAI response did not contain output text.")


def _select_context_events(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    selected: list[TranscriptEvent] = []
    user_goal = next((event for event in events if event.role == "user"), None)
    failed = next((event for event in events if _looks_failed(event.text)), None)
    final = next((event for event in reversed(events) if event.role == "assistant"), None)
    for event in (user_goal, failed, final):
        if event is not None and event not in selected:
            selected.append(event)
    return selected or events[:3]


def _event_ref(event: TranscriptEvent) -> dict[str, str]:
    ref = f"event_{event.event_index}"
    return {
        "id": ref,
        "role": event.role,
        "kind": event.kind,
        "text": _excerpt(redact_text(event.text), 700),
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
