from __future__ import annotations

import json
import unittest

from recodex.analysis_workflow import workflow_result_to_report_data
from recodex.efficiency_analysis import run_efficiency_analysis
from recodex.html_report import build_project_report_data
from recodex.models import SessionRecord, TranscriptEvent


class EfficiencyAnalysisTests(unittest.TestCase):
    def test_mvp_problem_types_have_evidence_and_artifact_routes(self) -> None:
        sessions = [_session("eff-s1"), _session("eff-s2")]
        events_by_session = {
            "eff-s1": _mvp_events("eff-s1"),
            "eff-s2": _mvp_events("eff-s2"),
        }

        result = run_efficiency_analysis(sessions, events_by_session)
        payload = result.to_payload()
        problem_types = {finding["problem_type"] for finding in payload["findings"]}
        evidence_ids = {ref["id"] for ref in payload["evidence_refs"]}

        self.assertTrue(
            {
                "repeated_user_requirement",
                "project_knowledge_rediscovery",
                "repeated_workflow_orchestration",
                "repeated_command_sequence",
                "hypothesis_stagnation",
                "verification_debt",
            }.issubset(problem_types)
        )
        self.assertTrue(payload["artifact_candidates"])
        self.assertGreaterEqual(payload["cost_ledger"]["user_corrections"], 2)
        for finding in payload["findings"]:
            self.assertNotIn("category", finding)
            self.assertNotIn("card_type", finding)
            self.assertTrue(finding["evidence_refs"])
            self.assertTrue(set(finding["evidence_refs"]).issubset(evidence_ids))
            self.assertIn(finding["mechanism"], payload["mechanism_counts"])

    def test_each_mvp_problem_type_has_a_golden_case(self) -> None:
        cases = {
            "repeated_user_requirement": _golden_repeated_user_requirement,
            "project_knowledge_rediscovery": _golden_project_knowledge,
            "repeated_workflow_orchestration": _golden_workflow_orchestration,
            "repeated_command_sequence": _golden_command_sequence,
            "hypothesis_stagnation": _golden_hypothesis_stagnation,
            "verification_debt": _golden_verification_debt,
        }

        for problem_type, fixture in cases.items():
            with self.subTest(problem_type=problem_type):
                sessions, events_by_session = fixture()
                result = run_efficiency_analysis(sessions, events_by_session).to_payload()
                findings = {
                    finding["problem_type"]: finding
                    for finding in result["findings"]
                }

                self.assertIn(problem_type, findings)
                self.assertTrue(findings[problem_type]["evidence_refs"])
                self.assertGreater(findings[problem_type]["confidence"], 0.5)

    def test_project_report_exposes_v2_efficiency_analysis_contract(self) -> None:
        sessions = [_session("eff-r1"), _session("eff-r2")]
        events_by_session = {
            "eff-r1": _mvp_events("eff-r1"),
            "eff-r2": _mvp_events("eff-r2"),
        }

        report = build_project_report_data(
            "/work/recodex",
            sessions,
            events_by_session,
            [],
            "30d",
        )
        efficiency = report["efficiency_analysis"]  # type: ignore[index]

        self.assertEqual(efficiency["schema_version"], "recodex_efficiency_analysis_v2")
        self.assertTrue(efficiency["findings"])
        self.assertTrue(efficiency["artifact_candidates"])
        self.assertTrue(
            all("problem_type" in finding for finding in efficiency["findings"])
        )
        self.assertTrue(
            all("category" not in finding for finding in efficiency["findings"])
        )

    def test_workflow_report_preserves_v2_efficiency_analysis_payload(self) -> None:
        session = _session("eff-wf")
        result = run_efficiency_analysis([session], {session.session_id: _mvp_events("eff-wf")})

        report = workflow_result_to_report_data(
            session,
            {
                "workflow_version": "test",
                "deterministic_facts": {},
                "episodes": [],
                "evidence_packs": [],
                "evidence_windows": [],
                "micro_claims": [],
                "analysis_cards": [],
                "card_verifications": [],
                "pattern_clusters": [],
                "stages": [],
                "issues": [],
                "validated_clusters": [],
                "validation": {},
                "report": {},
                "efficiency_analysis": result.to_payload(),
            },
            report_id="rep_eff_wf",
            generated_at="2026-06-15T02:00:00+00:00",
        )

        self.assertEqual(
            report["efficiency_analysis"]["schema_version"],  # type: ignore[index]
            "recodex_efficiency_analysis_v2",
        )
        self.assertTrue(report["efficiency_analysis"]["findings"])  # type: ignore[index]
        self.assertTrue(report["findings"])  # type: ignore[index]
        self.assertTrue(report["artifact_candidates"])  # type: ignore[index]
        self.assertEqual(
            report["artifact_review_queue"][0]["id"],  # type: ignore[index]
            report["artifact_candidates"][0]["id"],  # type: ignore[index]
        )
        self.assertNotIn(
            '"card_type"',
            json.dumps(report, ensure_ascii=False),
        )
        self.assertNotIn(
            '"category"',
            json.dumps(report, ensure_ascii=False),
        )


def _session(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-06-15T01:00:00+00:00",
        updated_at="2026-06-15T01:20:00+00:00",
        title="Auth verification workflow",
        tool="codex",
        message_count=10,
        user_message_count=4,
        assistant_message_count=3,
        command_count=5,
        error_count=2,
        raw_preview="Auth verification workflow",
        project_path="/work/recodex",
    )


def _mvp_events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id,
            0,
            "user",
            "message",
            (
                "Auth 改动先读 AGENTS.md 和 Makefile，再构建、备份、重启、健康检查。"
                "如果健康检查失败先看日志。"
            ),
            "2026-06-15T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            1,
            "tool",
            "exec_command",
            "command=cat AGENTS.md\nProcess exited with code 0",
            "2026-06-15T01:01:00+00:00",
            {"command": "cat AGENTS.md", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id,
            2,
            "tool",
            "exec_command",
            "command=sed -n '1,120p' Makefile\nProcess exited with code 0",
            "2026-06-15T01:02:00+00:00",
            {"command": "sed -n '1,120p' Makefile", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id,
            3,
            "tool",
            "exec_command",
            "command=npm install\nProcess exited with code 0",
            "2026-06-15T01:03:00+00:00",
            {"command": "npm install", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id,
            4,
            "tool",
            "exec_command",
            "command=npm test\nError: use pnpm test:auth\nProcess exited with code 1",
            "2026-06-15T01:04:00+00:00",
            {"command": "npm test", "exit_code": 1},
        ),
        TranscriptEvent(
            session_id,
            5,
            "tool",
            "exec_command",
            "command=npm test\nError: use pnpm test:auth\nProcess exited with code 1",
            "2026-06-15T01:05:00+00:00",
            {"command": "npm test", "exit_code": 1},
        ),
        TranscriptEvent(
            session_id,
            6,
            "user",
            "message",
            "不是 npm test，我说过这个项目不要用 npm，要用 pnpm test:auth。",
            "2026-06-15T01:06:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            7,
            "assistant",
            "message",
            "我已修改认证逻辑，完成。",
            "2026-06-15T01:07:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            8,
            "user",
            "message",
            "你没有运行 pnpm test:auth，验证成本又转给我了。",
            "2026-06-15T01:08:00+00:00",
        ),
    ]


def _golden_repeated_user_requirement() -> tuple[
    list[SessionRecord],
    dict[str, list[TranscriptEvent]],
]:
    sessions = [_session("gold-e01-a"), _session("gold-e01-b")]
    return sessions, {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "user",
                "message",
                "这个项目不要用 npm，要用 pnpm test:auth。",
                "2026-06-15T01:00:00+00:00",
            )
        ]
        for session in sessions
    }


def _golden_project_knowledge() -> tuple[list[SessionRecord], dict[str, list[TranscriptEvent]]]:
    sessions = [_session("gold-e02-a"), _session("gold-e02-b")]
    return sessions, {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "tool",
                "exec_command",
                "command=sed -n '1,120p' Makefile\nProcess exited with code 0",
                "2026-06-15T01:00:00+00:00",
                {"command": "sed -n '1,120p' Makefile", "exit_code": 0},
            )
        ]
        for session in sessions
    }


def _golden_workflow_orchestration() -> tuple[
    list[SessionRecord],
    dict[str, list[TranscriptEvent]],
]:
    sessions = [_session("gold-e03-a"), _session("gold-e03-b")]
    return sessions, {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "user",
                "message",
                "发布流程先构建，再备份，然后重启，最后健康检查；如果失败先看日志。",
                "2026-06-15T01:00:00+00:00",
            )
        ]
        for session in sessions
    }


def _golden_command_sequence() -> tuple[list[SessionRecord], dict[str, list[TranscriptEvent]]]:
    sessions = [_session("gold-e04-a"), _session("gold-e04-b")]
    return sessions, {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "tool",
                "exec_command",
                "command=npm install\nProcess exited with code 0",
                "2026-06-15T01:00:00+00:00",
                {"command": "npm install", "exit_code": 0},
            ),
            TranscriptEvent(
                session.session_id,
                1,
                "tool",
                "exec_command",
                "command=npm test\nProcess exited with code 0",
                "2026-06-15T01:01:00+00:00",
                {"command": "npm test", "exit_code": 0},
            ),
        ]
        for session in sessions
    }


def _golden_hypothesis_stagnation() -> tuple[list[SessionRecord], dict[str, list[TranscriptEvent]]]:
    session = _session("gold-e06")
    return [session], {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "tool",
                "exec_command",
                "command=pnpm test:auth\nAssertionError\nProcess exited with code 1",
                "2026-06-15T01:00:00+00:00",
                {"command": "pnpm test:auth", "exit_code": 1},
            ),
            TranscriptEvent(
                session.session_id,
                1,
                "tool",
                "exec_command",
                "command=pnpm test:auth\nAssertionError\nProcess exited with code 1",
                "2026-06-15T01:01:00+00:00",
                {"command": "pnpm test:auth", "exit_code": 1},
            ),
        ]
    }


def _golden_verification_debt() -> tuple[list[SessionRecord], dict[str, list[TranscriptEvent]]]:
    session = _session("gold-e11")
    return [session], {
        session.session_id: [
            TranscriptEvent(
                session.session_id,
                0,
                "user",
                "message",
                "改完认证后必须运行 pnpm test:auth。",
                "2026-06-15T01:00:00+00:00",
            ),
            TranscriptEvent(
                session.session_id,
                1,
                "assistant",
                "message",
                "我已修改认证逻辑，完成。",
                "2026-06-15T01:01:00+00:00",
            ),
            TranscriptEvent(
                session.session_id,
                2,
                "user",
                "message",
                "你没有运行 pnpm test:auth，验证成本又转给我了。",
                "2026-06-15T01:02:00+00:00",
            ),
        ]
    }


if __name__ == "__main__":
    unittest.main()
