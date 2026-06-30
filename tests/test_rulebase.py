from __future__ import annotations

import unittest

from recodex.models import SessionRecord, TranscriptEvent
from recodex.reports import render_retro
from recodex.rulebase import evaluate_session_rules, get_rule, list_rules


class RulebaseTests(unittest.TestCase):
    def test_default_rulebase_has_rule_cards_and_matches_session(self) -> None:
        self.assertEqual(len(list_rules()), 100)
        rule = get_rule("R002")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.name, "Bugfix 应先复现再修改")
        extended_rule = get_rule("R100")
        self.assertIsNotNone(extended_rule)
        self.assertEqual(extended_rule.name, "团队规则必须有为什么，否则难以维护")

        session, events = _bugfix_without_verification()
        results = evaluate_session_rules(session, events)
        result_by_id = {result.rule.id: result for result in results}
        self.assertIn("R047", result_by_id)
        self.assertEqual(result_by_id["R047"].status, "violated")
        self.assertEqual(result_by_id["R047"].severity, "high")
        self.assertIn("R041", result_by_id)

    def test_retro_report_keeps_rulebase_internal(self) -> None:
        session, events = _bugfix_without_verification()
        report = render_retro(session, events)
        self.assertNotIn("规则经验库对照", report)
        self.assertNotIn("根据规则经验库", report)
        self.assertNotIn("R005", report)
        self.assertNotIn("R047", report)

def _bugfix_without_verification() -> tuple[SessionRecord, list[TranscriptEvent]]:
    session = SessionRecord(
        session_id="rulebase-session",
        source_path="/tmp/rulebase.jsonl",
        started_at="2026-05-29T01:00:00+00:00",
        updated_at="2026-05-29T01:05:00+00:00",
        title="Fix failed login bug",
        tool="codex",
        message_count=3,
        user_message_count=1,
        assistant_message_count=1,
        command_count=1,
        error_count=1,
        raw_preview="Fix failed login bug without tests.",
    )
    events = [
        TranscriptEvent(
            session_id=session.session_id,
            event_index=0,
            role="user",
            kind="message",
            text="Fix failed login bug.",
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session.session_id,
            event_index=1,
            role="assistant",
            kind="message",
            text="I edited the auth handler directly.",
            created_at="2026-05-29T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id=session.session_id,
            event_index=2,
            role="tool",
            kind="exec_command",
            text="python app.py failed with error",
            created_at="2026-05-29T01:02:00+00:00",
            metadata={"command": "python app.py"},
        ),
    ]
    return session, events


if __name__ == "__main__":
    unittest.main()
