from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_dev_review.html_report import build_session_report_data, render_report_html, write_report_bundle
from ai_dev_review.models import SessionRecord, TranscriptEvent


class HtmlReportTests(unittest.TestCase):
    def test_session_report_data_has_display_schema(self) -> None:
        session = _session()
        events = _events(session.session_id)

        report = build_session_report_data(session, events)

        for key in (
            "meta",
            "summary",
            "metrics",
            "flow",
            "issues",
            "context_frontload",
            "intervention",
            "verification",
            "suggestions",
            "artifacts",
            "evidence",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["meta"]["project"], "/work/aicoo")  # type: ignore[index]
        self.assertEqual(report["meta"]["source"], "codex")  # type: ignore[index]
        self.assertFalse(report["metrics"]["verification_found"])  # type: ignore[index]
        self.assertTrue(report["issues"])
        self.assertTrue(report["suggestions"])

    def test_html_report_embeds_json_without_fetching_sidecar_file(self) -> None:
        session = _session()
        events = _events(session.session_id)
        report = build_session_report_data(session, events)

        html = render_report_html(report)

        self.assertIn("<!doctype html>", html.lower())
        self.assertIn('<script id="report-data" type="application/json">', html)
        self.assertNotIn("fetch(", html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("验收证据不足", html)
        self.assertNotIn("规则经验库对照", html)

    def test_write_report_bundle_writes_json_and_single_file_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            report = build_session_report_data(_session(), _events("html-session"))

            json_path, html_path = write_report_bundle(directory, "report", report)

            self.assertEqual(json_path, directory / "report.json")
            self.assertEqual(html_path, directory / "report.html")
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["meta"]["project"], "/work/aicoo")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("report-data", html)
            self.assertIn("AI Dev Review", html)


def _session() -> SessionRecord:
    return SessionRecord(
        session_id="html-session",
        source_path="/tmp/rollout-html.jsonl",
        started_at="2026-05-29T01:00:00+00:00",
        updated_at="2026-05-29T01:12:00+00:00",
        title="修复 aicoo 登录问题",
        tool="codex",
        message_count=4,
        user_message_count=1,
        assistant_message_count=2,
        command_count=0,
        error_count=0,
        raw_preview="修复 aicoo 登录问题",
        project_path="/work/aicoo",
    )


def _events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id=session_id,
            event_index=0,
            role="user",
            kind="message",
            text="帮我修复 token refresh。password=secret-value",
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=1,
            role="assistant",
            kind="message",
            text="我修改了认证逻辑，但最终没有运行测试 </script><script>alert(1)</script>",
            created_at="2026-05-29T01:10:00+00:00",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
