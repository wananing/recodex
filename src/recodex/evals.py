from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence_mining import run_evidence_mining
from .models import SessionRecord, TranscriptEvent


@dataclass(frozen=True)
class GoldenCase:
    id: str
    expected_title: str
    expected_mechanism: str
    sessions: list[SessionRecord]
    events_by_session: dict[str, list[TranscriptEvent]]


def run_golden_evals() -> dict[str, Any]:
    cases = _golden_cases()
    results = [_evaluate_case(case) for case in cases]
    passed_routing = sum(1 for result in results if result["routing_passed"])
    traceable = sum(1 for result in results if result["evidence_traceable"])
    false_skill_promotions = sum(int(result["false_skill_promotions"]) for result in results)
    return {
        "ok": passed_routing == len(results) and false_skill_promotions == 0,
        "case_count": len(results),
        "routing_accuracy": _ratio(passed_routing, len(results)),
        "evidence_traceability": _ratio(traceable, len(results)),
        "false_skill_promotions": false_skill_promotions,
        "cases": results,
    }


def _evaluate_case(case: GoldenCase) -> dict[str, Any]:
    result = run_evidence_mining(case.sessions, case.events_by_session)
    opportunities = [
        opportunity
        for opportunity in result.improvement_opportunities
        if opportunity.title == case.expected_title
    ]
    matched = [
        opportunity
        for opportunity in opportunities
        if opportunity.recommended_mechanism == case.expected_mechanism
    ]
    false_skill_promotions = [
        opportunity
        for opportunity in opportunities
        if case.expected_mechanism != "skill" and opportunity.recommended_mechanism == "skill"
    ]
    return {
        "id": case.id,
        "expected_mechanism": case.expected_mechanism,
        "observed_mechanisms": sorted(
            {opportunity.recommended_mechanism for opportunity in opportunities}
        ),
        "routing_passed": bool(matched),
        "evidence_traceable": bool(opportunities)
        and all(opportunity.evidence_refs for opportunity in opportunities)
        and all(finding.evidence_refs for finding in result.findings),
        "false_skill_promotions": len(false_skill_promotions),
        "finding_count": len(result.findings),
        "opportunity_count": len(result.improvement_opportunities),
        "artifact_candidate_count": len(result.artifact_candidates),
    }


def _golden_cases() -> list[GoldenCase]:
    return [
        GoldenCase(
            id="single_validation_gap_routes_checklist",
            expected_title="降低完成验证转移成本",
            expected_mechanism="checklist",
            sessions=[_session("golden-single")],
            events_by_session={"golden-single": _ci_events("golden-single")},
        ),
        GoldenCase(
            id="repeated_validation_gap_routes_skill",
            expected_title="降低完成验证转移成本",
            expected_mechanism="skill",
            sessions=[_session("golden-a"), _session("golden-b"), _session("golden-c")],
            events_by_session={
                "golden-a": _ci_events("golden-a"),
                "golden-b": _ci_events("golden-b"),
                "golden-c": _ci_events("golden-c"),
            },
        ),
    ]


def _session(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-06-15T01:00:00+00:00",
        updated_at="2026-06-15T01:10:00+00:00",
        title="修复 CI failure",
        tool="codex",
        message_count=5,
        user_message_count=3,
        assistant_message_count=1,
        command_count=1,
        error_count=0,
        raw_preview="修复 CI failure",
        project_path="/work/app",
    )


def _ci_events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id,
            0,
            "user",
            "message",
            "帮我修 CI failure。",
            "2026-06-15T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            1,
            "assistant",
            "message",
            "我已经修好了。",
            "2026-06-15T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            2,
            "user",
            "message",
            "你还没看 CI 日志，也没跑失败的 test。先看日志，定位具体失败命令。",
            "2026-06-15T01:02:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            3,
            "tool",
            "exec_command",
            "command=npm test\nProcess exited with code 0",
            "2026-06-15T01:03:00+00:00",
            {"command": "npm test", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id,
            4,
            "user",
            "message",
            "不是 npm test，CI 失败的是 pnpm test:payment。",
            "2026-06-15T01:04:00+00:00",
        ),
    ]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
