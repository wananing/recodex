from __future__ import annotations

import unittest

from recodex.models import SessionRecord, TranscriptEvent
from recodex.reports import (
    render_checklist_export,
    render_ci_rule_export,
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
        self.assertIn("recodex retro latest", script)
        self.assertIn("recodex improvements propose", script)
        self.assertIn("Promote unit test loop", script)
        self.assertIn("Evidence:", script)

        self.assertIn("name: recodex", ci_rule)
        self.assertIn("workflow_dispatch:", ci_rule)
        self.assertIn("recodex patterns --since", ci_rule)
        self.assertIn("recodex improvements propose", ci_rule)
        self.assertIn("Promote unit test loop", ci_rule)
        self.assertIn("Evidence:", ci_rule)

    def test_export_renderers_have_reasonable_empty_placeholders(self) -> None:
        checklist = render_checklist_export([])
        script = render_scripts_export([])
        ci_rule = render_ci_rule_export([])

        self.assertIn("No improvement candidates yet", checklist)
        self.assertIn("recodex scan", script)
        self.assertIn("No candidate-specific script suggestions yet", script)
        self.assertIn("workflow_dispatch:", ci_rule)
        self.assertIn("No candidate-specific CI rules yet", ci_rule)


if __name__ == "__main__":
    unittest.main()
