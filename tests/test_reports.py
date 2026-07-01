from __future__ import annotations

import unittest

from recodex.models import SessionRecord, TranscriptEvent
from recodex.reports import (
    render_checklist_export,
    render_ci_rule_export,
    render_improvements,
    render_patterns,
    render_retro,
    render_scripts_export,
)


class ReportRenderingTests(unittest.TestCase):
    def test_retro_uses_design_sections_and_short_redacted_evidence(self) -> None:
        session = SessionRecord(
            session_id="session-1234567890",
            source_path="/tmp/codex-session.jsonl",
            started_at="2026-05-28T01:00:00+00:00",
            updated_at="2026-05-28T01:10:00+00:00",
            title="Build independent report exports",
            tool="codex",
            message_count=5,
            user_message_count=2,
            assistant_message_count=2,
            command_count=1,
            error_count=1,
            raw_preview="Build independent report exports",
        )
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="Build export reports for checklist/script/CI. password=supersecret",
                created_at="2026-05-28T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="I will inspect reports.py and add tests.",
                created_at="2026-05-28T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="tool",
                kind="exec_command",
                text="PYTHONPATH=src python3 -m unittest discover -s tests failed with AssertionError.",
                created_at="2026-05-28T01:02:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="Don't change cli.py; keep this independent.",
                created_at="2026-05-28T01:03:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=4,
                role="assistant",
                kind="message",
                text="Implemented render functions and tests passed.",
                created_at="2026-05-28T01:10:00+00:00",
            ),
        ]

        report = render_retro(session, events)

        expected_sections = [
            "## 1. Task Goal",
            "## 2. Outcome",
            "## 3. Timeline",
            "## 4. What Went Well",
            "## 5. What Went Wrong",
            "## 6. User Interventions",
            "## 7. Reusable Lessons",
            "## 8. Improvement Candidates",
        ]
        for section in expected_sections:
            self.assertIn(section, report)
        self.assertEqual(
            [line for line in report.splitlines() if line.startswith("## ")],
            expected_sections,
        )
        self.assertIn("证据：", report)
        self.assertIn("session-1234567890#0", report)
        self.assertIn("password=<redacted>", report)
        self.assertNotIn("supersecret", report)
        self.assertNotIn("规则经验库对照", report)

    def test_retro_skips_environment_and_tool_wrapper_noise(self) -> None:
        session = SessionRecord(
            session_id="session-noise",
            source_path="/tmp/codex-session.jsonl",
            started_at="2026-05-28T01:00:00+00:00",
            updated_at="2026-05-28T01:10:00+00:00",
            title="Fix GPS route",
            tool="codex",
            message_count=5,
            user_message_count=2,
            assistant_message_count=1,
            command_count=1,
            error_count=1,
            raw_preview="Fix GPS route",
        )
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="unknown",
                kind="message",
                text="<permissions instructions> sandbox_mode is workspace-write",
                created_at="2026-05-28T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="user",
                kind="message",
                text="<environment_context><cwd>/work/aicoo</cwd></environment_context>",
                created_at="2026-05-28T01:00:01+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="修复 GPS 路线采集失败。",
                created_at="2026-05-28T01:00:02+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="unknown",
                kind="response_item",
                text="Chunk ID: abc Process exited with code 0 Original token count: 999 Output: error policy notes",
                created_at="2026-05-28T01:00:03+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=4,
                role="tool",
                kind="exec_command",
                text="pytest tests/test_gps.py failed with AssertionError",
                created_at="2026-05-28T01:00:04+00:00",
                metadata={"command": "pytest tests/test_gps.py"},
            ),
        ]

        report = render_retro(session, events)

        self.assertIn("修复 GPS 路线采集失败", report)
        self.assertIn("pytest tests/test_gps.py failed", report)
        self.assertNotIn("<permissions instructions>", report)
        self.assertNotIn("<environment_context>", report)
        self.assertNotIn("Chunk ID", report)
        self.assertNotIn("Original token count", report)

    def test_export_renderers_include_expected_commands_sections_and_evidence(self) -> None:
        rows = [
            {
                "id": 7,
                "status": "accepted",
                "category": "script",
                "session_id": "session-1",
                "title": "Promote unit test loop",
                "evidence": (
                    "Session `session-1` from `/tmp/session.jsonl`. Evidence: tool: "
                    "PYTHONPATH=src python3 -m unittest discover -s tests failed once."
                ),
                "recommendation": "Add a weekly script that runs scan, patterns, and improvements propose.",
            }
        ]

        checklist = render_checklist_export(rows)
        script = render_scripts_export(rows)
        ci_rule = render_ci_rule_export(rows)

        self.assertIn("# AI Coding Completion Checklist", checklist)
        self.assertIn("- [ ] Review candidate #7: Promote unit test loop", checklist)
        self.assertIn("Evidence:", checklist)
        self.assertIn("PYTHONPATH=src python3 -m unittest discover -s tests", checklist)

        self.assertIn("#!/usr/bin/env bash", script)
        self.assertIn("recodex scan", script)
        self.assertIn("RECODEX_LLM_PROVIDER", script)
        self.assertIn("recodex report latest --llm", script)
        self.assertIn("Promote unit test loop", script)
        self.assertIn("Evidence:", script)

        self.assertIn("name: recodex", ci_rule)
        self.assertIn("workflow_dispatch:", ci_rule)
        self.assertIn("recodex guide", ci_rule)
        self.assertIn("explicit headless job", ci_rule)
        self.assertIn("Promote unit test loop", ci_rule)
        self.assertIn("Evidence:", ci_rule)

    def test_patterns_report_uses_v2_efficiency_contract(self) -> None:
        sessions = [
            _session("s1", "Package manager guidance"),
            _session("s2", "Package manager guidance"),
        ]
        events_by_session = {
            "s1": [
                _event(
                    "s1",
                    0,
                    "user",
                    "message",
                    "Use pnpm instead of npm for package manager commands.",
                ),
            ],
            "s2": [
                _event(
                    "s2",
                    0,
                    "user",
                    "message",
                    "Use pnpm instead of npm for package manager commands.",
                ),
            ],
        }

        report = render_patterns(sessions, events_by_session, "30d")

        self.assertIn("## Efficiency Findings", report)
        self.assertIn("Problem type: `repeated_user_requirement`", report)
        self.assertIn("Mechanism: `agents_md`", report)
        self.assertIn("Evidence refs:", report)
        self.assertNotIn("Category:", report)
        self.assertNotIn("card_type", report)

    def test_improvements_report_displays_mechanism_not_category(self) -> None:
        report = render_improvements(
            [
                {
                    "id": 7,
                    "status": "accepted",
                    "category": "agents",
                    "session_id": None,
                    "title": "Promote project rule",
                    "evidence": "Evidence refs: eref_1.",
                    "recommendation": "Add a project rule.",
                }
            ]
        )

        self.assertIn("Mechanism: `agents_md`", report)
        self.assertNotIn("Category:", report)

    def test_export_renderers_have_reasonable_empty_placeholders(self) -> None:
        checklist = render_checklist_export([])
        script = render_scripts_export([])
        ci_rule = render_ci_rule_export([])

        self.assertIn("No improvement candidates yet", checklist)
        self.assertIn("recodex scan", script)
        self.assertIn("No candidate-specific script suggestions yet", script)
        self.assertIn("workflow_dispatch:", ci_rule)
        self.assertIn("No candidate-specific CI rules yet", ci_rule)


def _session(session_id: str, title: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-05-28T01:00:00+00:00",
        updated_at="2026-05-28T01:05:00+00:00",
        title=title,
        tool="codex",
        message_count=2,
        user_message_count=1,
        assistant_message_count=0,
        command_count=0,
        error_count=0,
        raw_preview=title,
    )


def _event(
    session_id: str,
    index: int,
    role: str,
    kind: str,
    text: str,
) -> TranscriptEvent:
    return TranscriptEvent(
        session_id=session_id,
        event_index=index,
        role=role,
        kind=kind,
        text=text,
        created_at="2026-05-28T01:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
