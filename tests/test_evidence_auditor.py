from __future__ import annotations

import unittest

from recodex.evidence_auditor import audit_report_evidence
from recodex.html_report import build_session_report_data
from recodex.models import SessionRecord, TranscriptEvent


class EvidenceAuditorTests(unittest.TestCase):
    def test_audits_core_report_evidence_refs(self) -> None:
        report = build_session_report_data(_session(), _diagnostic_events("audit-session"))

        audit = audit_report_evidence(report)

        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["metrics"]["traceability"], 1.0)  # type: ignore[index]
        self.assertGreaterEqual(audit["metrics"]["finding_count"], 1)  # type: ignore[index]
        self.assertGreaterEqual(audit["metrics"]["artifact_candidate_count"], 1)  # type: ignore[index]

    def test_unknown_evidence_ref_marks_audit_weak(self) -> None:
        report = {
            "core_diagnostics": {
                "evidence_refs": [
                    {"id": "evref_known", "quote": "user correction", "content_hash": "abc"}
                ],
                "findings": [
                    {
                        "id": "finding_1",
                        "title": "Unsupported finding",
                        "evidence_refs": ["evref_missing"],
                    }
                ],
                "improvement_opportunities": [],
                "artifact_candidates": [],
            },
            "issues": [],
            "evidence": [],
        }

        audit = audit_report_evidence(report)

        self.assertEqual(audit["status"], "weak")
        self.assertEqual(audit["metrics"]["high_problem_count"], 1)  # type: ignore[index]
        self.assertEqual(audit["problems"][0]["code"], "unknown_evidence_ref")  # type: ignore[index]

    def test_v2_efficiency_finding_without_evidence_refs_marks_audit_weak(self) -> None:
        report = {
            "core_diagnostics": {
                "evidence_refs": [],
                "findings": [],
                "improvement_opportunities": [],
                "artifact_candidates": [],
            },
            "efficiency_analysis": {
                "evidence_refs": [
                    {"id": "eref_known", "quote": "user correction", "content_hash": "abc"}
                ],
                "findings": [
                    {
                        "id": "eff_missing",
                        "title": "Missing evidence",
                        "problem_type": "verification_debt",
                        "evidence_refs": [],
                    }
                ],
                "artifact_candidates": [],
            },
            "issues": [],
            "evidence": [],
        }

        audit = audit_report_evidence(report)

        self.assertEqual(audit["status"], "weak")
        self.assertEqual(audit["metrics"]["efficiency_finding_count"], 1)  # type: ignore[index]
        self.assertTrue(
            any(problem["code"] == "missing_evidence_refs" for problem in audit["problems"])
        )


def _session(session_id: str = "audit-session") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-05-29T01:00:00+00:00",
        updated_at="2026-05-29T01:12:00+00:00",
        title="修复 CI failure",
        tool="codex",
        message_count=5,
        user_message_count=3,
        assistant_message_count=1,
        command_count=1,
        error_count=0,
        raw_preview="修复 CI failure",
        project_path="/work/aicoo",
    )


def _diagnostic_events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id=session_id,
            event_index=0,
            role="user",
            kind="message",
            text="帮我修 CI failure。",
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=1,
            role="assistant",
            kind="message",
            text="我已经修好了。",
            created_at="2026-05-29T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=2,
            role="user",
            kind="message",
            text="你还没看 CI 日志，也没跑失败的 test。先看日志，定位具体失败命令。",
            created_at="2026-05-29T01:02:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=3,
            role="tool",
            kind="exec_command",
            text="command=npm test\nProcess exited with code 0",
            created_at="2026-05-29T01:03:00+00:00",
            metadata={"command": "npm test", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=4,
            role="user",
            kind="message",
            text="不是 npm test，CI 失败的是 pnpm test:payment。",
            created_at="2026-05-29T01:04:00+00:00",
        ),
    ]
