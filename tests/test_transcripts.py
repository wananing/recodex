from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recodex.transcripts import parse_transcript_file


class TranscriptParsingTests(unittest.TestCase):
    def test_parse_codex_like_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "session_id": "codex-session-1",
                    "timestamp": "2026-05-28T01:00:00+00:00",
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-28T01:01:00+00:00",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Please run the tests."}],
                    },
                },
                {
                    "type": "exec_command",
                    "timestamp": "2026-05-28T01:02:00+00:00",
                    "arguments": {"cmd": "python -m unittest"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-28T01:03:00+00:00",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Tests failed with an error."}],
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            parsed = parse_transcript_file(path)

            self.assertEqual(parsed.session.session_id, "codex-session-1")
            self.assertEqual(parsed.session.user_message_count, 1)
            self.assertEqual(parsed.session.assistant_message_count, 1)
            self.assertGreaterEqual(parsed.session.command_count, 1)
            self.assertGreaterEqual(parsed.session.error_count, 1)
            self.assertIn("Please run the tests", parsed.session.title)
            self.assertGreater(parsed.events[0].metadata["byte_start"], 0)
            self.assertGreater(parsed.events[0].metadata["byte_end"], 0)
            self.assertEqual(parsed.events[0].metadata["physical_line"], 2)

    def test_parse_plain_text_with_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.txt"
            path.write_text(
                "User: implement a CLI\n\nAssistant: created files\n\nTool: pytest failed",
                encoding="utf-8",
            )

            parsed = parse_transcript_file(path)

            self.assertEqual(parsed.session.user_message_count, 1)
            self.assertEqual(parsed.session.assistant_message_count, 1)
            self.assertEqual(len(parsed.events), 3)
            self.assertGreaterEqual(parsed.session.error_count, 1)


if __name__ == "__main__":
    unittest.main()
