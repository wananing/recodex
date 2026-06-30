from __future__ import annotations

import unittest

from recodex.analysis import propose_improvements
from recodex.models import SessionRecord, TranscriptEvent


class ImprovementAnalysisTests(unittest.TestCase):
    def test_candidates_come_from_v2_artifact_routes(self) -> None:
        sessions = [
            _session("s1", "Package manager guidance", errors=0, commands=0),
            _session("s2", "Package manager guidance", errors=0, commands=0),
        ]
        events_by_session = {
            "s1": [
                _event("s1", 0, "user", "Use pnpm instead of npm for package manager commands."),
            ],
            "s2": [
                _event("s2", 0, "user", "Use pnpm instead of npm for package manager commands."),
            ],
        }

        drafts = propose_improvements(sessions, events_by_session)
        joined = "\n".join(draft.title + "\n" + draft.evidence for draft in drafts)

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].category, "agents_md")
        self.assertIn("Source findings:", joined)
        self.assertIn("Evidence refs:", joined)
        self.assertNotIn("失败分诊", joined)

    def test_legacy_counter_signals_do_not_create_candidates(self) -> None:
        sessions = [
            _session("s1", "修复 GPS 采集失败", errors=3, commands=8),
            _session("s2", "优化 2s 首音策略", errors=2, commands=7),
        ]
        events_by_session = {
            session.session_id: _events(session, "pytest tests failed with error")
            for session in sessions
        }

        drafts = propose_improvements(sessions, events_by_session)

        self.assertEqual(drafts, [])


def _session(session_id: str, title: str, *, errors: int, commands: int) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-05-29T01:00:00+00:00",
        updated_at="2026-05-29T01:05:00+00:00",
        title=title,
        tool="codex",
        message_count=6,
        user_message_count=1,
        assistant_message_count=2,
        command_count=commands,
        error_count=errors,
        raw_preview=title,
        project_path="/work/aicoo",
    )


def _events(session: SessionRecord, tool_output: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id=session.session_id,
            event_index=0,
            role="user",
            kind="message",
            text=session.title,
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session.session_id,
            event_index=1,
            role="tool",
            kind="exec_command",
            text=tool_output,
            created_at="2026-05-29T01:01:00+00:00",
            metadata={"command": "pytest"},
        ),
        TranscriptEvent(
            session_id=session.session_id,
            event_index=2,
            role="assistant",
            kind="message",
            text="测试失败，保留风险。",
            created_at="2026-05-29T01:02:00+00:00",
        ),
    ]


def _event(session_id: str, index: int, role: str, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        session_id=session_id,
        event_index=index,
        role=role,
        kind="message",
        text=text,
        created_at="2026-05-29T01:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
