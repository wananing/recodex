from __future__ import annotations

import hashlib
from typing import Any


def efficiency_report_contract(
    *,
    efficiency_analysis: dict[str, Any] | None,
    summary: dict[str, Any],
    verification: dict[str, Any],
    outcome_scope: str,
) -> dict[str, Any]:
    efficiency = _dict(efficiency_analysis)
    cost_ledger = _dict(efficiency.get("cost_ledger"))
    findings = _dict_items(efficiency.get("findings"))[:3]
    opportunities = _improvement_opportunities(findings)
    artifacts = _dict_items(efficiency.get("artifact_candidates"))[:3]
    review_queue = artifact_review_queue(artifacts)
    top_opportunity = opportunities[0] if opportunities else {}
    top_artifact = artifacts[0] if artifacts else {}
    effect_status = "not_observed" if artifacts else "not_applicable"
    return {
        "task_outcome": {
            "scope": outcome_scope,
            "result": _task_outcome_result(verification),
            "completion_confidence": str(summary.get("completion_confidence") or "unknown"),
            "verification_status": str(verification.get("overall") or "unknown"),
            "accepted_and_evidenced": verification.get("overall") == "验证闭环存在",
            "remaining_risk": _task_outcome_risk(verification),
        },
        "cost_ledger": cost_ledger,
        "findings": findings,
        "improvement_opportunities": opportunities,
        "artifact_candidates": artifacts,
        "artifact_review_queue": review_queue,
        "effect_observation": {
            "status": effect_status,
            "message": (
                "沉淀建议尚未经过人工确认和应用，后续会话效果还不能判断。"
                if artifacts
                else "当前没有需要沉淀的建议。"
            ),
            "tracked_cost_keys": [
                "extra_turns",
                "failed_commands",
                "user_corrections",
                "verification_followups",
            ],
        },
        "core_answers": {
            "most_expensive_avoidable_cost": str(summary.get("max_avoidable_cost") or ""),
            "why_it_happened": str(summary.get("primary_cause") or ""),
            "highest_leverage_change": str(
                top_opportunity.get("best_action")
                or top_opportunity.get("recommendation")
                or top_opportunity.get("title")
                or summary.get("primary_improvement")
                or ""
            ),
            "what_should_be_preserved_as_artifact": str(
                top_artifact.get("mechanism") or "不沉淀"
            ),
            "has_effect_been_observed": effect_status,
        },
    }


def artifact_review_queue(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for artifact in artifacts:
        status = str(artifact.get("status") or "proposed")
        if status not in {"proposed", "ready_for_review"}:
            continue
        queue.append(
            {
                "id": str(artifact.get("id") or ""),
                "mechanism": str(artifact.get("mechanism") or ""),
                "target_path": artifact.get("target_path"),
                "status": status,
                "source_finding_ids": _list(artifact.get("source_finding_ids")),
                "reason": str(artifact.get("rationale") or "需要人工确认"),
            }
        )
    return queue


def _improvement_opportunities(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    for finding in findings:
        mechanism = str(finding.get("mechanism") or "coaching")
        opportunities.append(
            {
                "id": _stable_id("opp_v2", finding.get("id"), mechanism),
                "source_finding_ids": [str(finding.get("id") or "")],
                "title": str(
                    finding.get("opportunity_title")
                    or _opportunity_title(str(finding.get("problem_type") or ""))
                ),
                "problem": str(finding.get("observation") or ""),
                "cause": str(finding.get("root_cause") or ""),
                "recurrence": str(finding.get("occurrences") or "unknown"),
                "preventability": "high" if mechanism != "none" else "low",
                "impact": str(finding.get("observation") or ""),
                "confidence": finding.get("confidence") or 0,
                "best_action": str(finding.get("recommendation") or ""),
                "recommended_mechanism": mechanism,
                "mechanism": mechanism,
                "routing_reason": "根据问题类型和建议落点自动归类。",
                "suggested_target": finding.get("suggested_target") or _target_path(mechanism),
                "evidence_refs": _list(finding.get("evidence_refs")),
            }
        )
    return opportunities


def _opportunity_title(problem_type: str) -> str:
    return {
        "verification_debt": "降低完成验证转移成本",
        "repeated_user_requirement": "沉淀重复项目要求",
        "project_knowledge_rediscovery": "前置稳定项目知识",
        "repeated_workflow_orchestration": "沉淀固定协作流程",
        "repeated_command_sequence": "脚本化重复命令序列",
        "hypothesis_stagnation": "缩短重复失败循环",
    }.get(problem_type, "降低重复协作成本")


def _target_path(mechanism: str) -> str | None:
    return {
        "agents_md": "AGENTS.md",
        "project_doc": "docs/agent-workflow.md",
        "checklist": "docs/ai-workflow-checklist.md",
        "script": "scripts/",
        "skill": ".codex/skills/",
        "hook": ".codex/hooks/",
        "ci": ".github/workflows/",
    }.get(mechanism)


def _task_outcome_result(verification: dict[str, Any]) -> str:
    overall = str(verification.get("overall") or "").lower()
    if "missing" in overall or "缺" in overall or "未" in overall:
        return "completed_with_verification_gap"
    if "不足" in overall or "gap" in overall:
        return "completed_with_verification_gap"
    if "验证闭环存在" in overall or overall in {"verified", "pass"}:
        return "completed_with_evidence"
    return "needs_review"


def _task_outcome_risk(verification: dict[str, Any]) -> str:
    if verification.get("overall") == "验证闭环存在":
        return "仍需人工确认报告中的候选沉淀是否适合长期应用。"
    return "验证成本可能被转移给用户；需要补充最小相关验证。"


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return f"{prefix}_{digest.hexdigest()[:10]}"


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_items(value: object) -> list[dict[str, Any]]:
    return [item for item in _list(value) if isinstance(item, dict)]
