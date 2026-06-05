from __future__ import annotations

import unittest

from recodex.analysis import propose_improvements
from recodex.models import SessionRecord, TranscriptEvent


class ImprovementAnalysisTests(unittest.TestCase):
    def test_project_level_candidates_are_chinese_and_deduplicated(self) -> None:
        sessions = [
            _session("s1", "修复 GPS 采集失败", errors=3, commands=8),
            _session("s2", "优化 2s 首音策略", errors=2, commands=7),
        ]
        events_by_session = {
            session.session_id: _events(session, "pytest tests failed with error")
            for session in sessions
        }

        drafts = propose_improvements(sessions, events_by_session)
        joined = "\n".join(draft.title + "\n" + draft.recommendation for draft in drafts)

        self.assertNotIn("Add a failure triage checklist", joined)
        self.assertNotIn("Document sandbox", joined)
        failure_candidates = [draft for draft in drafts if "失败分诊" in draft.title]
        self.assertEqual(len(failure_candidates), 1)
        self.assertIn("涉及 2 个会话", failure_candidates[0].evidence)
        self.assertIn("验证命令", failure_candidates[0].recommendation)

    def test_evidence_prefers_user_goal_failed_command_and_final_answer(self) -> None:
        session = _session("s1", "修复 GPS 采集失败", errors=1, commands=1)
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="unknown",
                kind="session_meta",
                text="cwd=/home/wang/workspace/aicoo You are Codex, a coding agent based on GPT-5.",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="user",
                kind="message",
                text="<environment_context><cwd>/home/wang/workspace/aicoo</cwd></environment_context>",
                created_at="2026-05-29T01:00:01+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="请修复 GPS 采集失败，并说明验证结果。",
                created_at="2026-05-29T01:00:02+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="tool",
                kind="exec_command",
                text="pytest tests/test_gps.py failed with AssertionError",
                created_at="2026-05-29T01:01:00+00:00",
                metadata={"command": "pytest tests/test_gps.py"},
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=4,
                role="assistant",
                kind="message",
                text="已修改 GPS 过滤逻辑，但 pytest 仍失败，不能声明完成。",
                created_at="2026-05-29T01:02:00+00:00",
            ),
        ]

        drafts = propose_improvements([session], {session.session_id: events})
        evidence = "\n".join(draft.evidence for draft in drafts)

        self.assertIn("请修复 GPS 采集失败", evidence)
        self.assertIn("pytest tests/test_gps.py", evidence)
        self.assertIn("不能声明完成", evidence)
        self.assertNotIn("You are Codex", evidence)
        self.assertNotIn("<environment_context>", evidence)

    def test_evidence_skips_successful_large_tool_output_even_with_error_words(self) -> None:
        session = _session("s1", "查看 core 任务", errors=1, commands=3)
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="看下 core 的任务相关内容。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="unknown",
                kind="tool_output",
                text="Chunk ID: abc Wall time: 0.1 seconds Process exited with code 0 Original token count: 9301 Output: error policy notes...",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="assistant",
                kind="message",
                text="已经整理 core 任务内容。",
                created_at="2026-05-29T01:02:00+00:00",
            ),
        ]

        drafts = propose_improvements([session], {session.session_id: events})
        evidence = "\n".join(draft.evidence for draft in drafts)

        self.assertIn("看下 core 的任务", evidence)
        self.assertNotIn("Original token count", evidence)
        self.assertNotIn("Chunk ID", evidence)


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


if __name__ == "__main__":
    unittest.main()
