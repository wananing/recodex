from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recodex.transcripts import (
    default_transcript_roots,
    looks_like_user_correction,
    parse_transcript_file,
)


class CodexAdapterParsingTests(unittest.TestCase):
    def test_parse_codex_nested_jsonl_keeps_command_and_session_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "session.jsonl"
            transcript_path = root / "nested.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "session_id": "codex-nested-1",
                    "timestamp": "2026-05-28T01:00:00+00:00",
                    "transcript_path": str(transcript_path),
                    "cwd": "/workspace/project",
                    "model": "gpt-5-codex",
                },
                {
                    "type": "response_item",
                    "session_id": "codex-nested-1",
                    "timestamp": "2026-05-28T01:01:00+00:00",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Run the unit tests."}],
                    },
                },
                {
                    "type": "response_item",
                    "session_id": "codex-nested-1",
                    "timestamp": "2026-05-28T01:02:00+00:00",
                    "item": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps(
                            {
                                "cmd": "PYTHONPATH=src python3 -m unittest discover -s tests",
                                "workdir": "/workspace/project",
                            }
                        ),
                    },
                },
                {
                    "type": "response_item",
                    "session_id": "codex-nested-1",
                    "timestamp": "2026-05-28T01:03:00+00:00",
                    "item": {
                        "type": "function_call_output",
                        "output": {
                            "exit_code": 0,
                            "stdout": "Ran 12 tests in 0.01s\nOK",
                            "stderr": "",
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            parsed = parse_transcript_file(path)

            self.assertEqual(parsed.session.session_id, "codex-nested-1")
            self.assertEqual(parsed.session.model, "gpt-5-codex")
            self.assertEqual(parsed.session.cwd, "/workspace/project")
            self.assertEqual(parsed.session.project_path, "/workspace/project")
            self.assertEqual(parsed.session.transcript_path, str(transcript_path))
            self.assertGreaterEqual(parsed.session.command_count, 1)
            self.assertIn("gpt-5-codex", parsed.session.raw_preview)
            self.assertIn(
                "PYTHONPATH=src python3 -m unittest discover -s tests",
                "\n".join(event.text for event in parsed.events),
            )
            self.assertIn("exit_code=0", "\n".join(event.text for event in parsed.events))
            command_events = [event for event in parsed.events if "command" in event.metadata]
            self.assertEqual(
                command_events[0].metadata["command"],
                "PYTHONPATH=src python3 -m unittest discover -s tests",
            )

    def test_parse_hook_like_jsonl_keeps_hook_command_output_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "hook.jsonl"
            transcript_path = root / "session.jsonl"
            row = {
                "hook_event_name": "PostToolUse",
                "session_id": "hook-session-1",
                "transcript_path": str(transcript_path),
                "cwd": "/workspace/hook-project",
                "model": "gpt-5-codex",
                "tool_name": "exec_command",
                "tool_input": {"cmd": "python -m unittest"},
                "tool_output": {
                    "exit_code": 1,
                    "stdout": "Ran 1 test",
                    "stderr": "FAILED test_example",
                },
            }
            path.write_text(json.dumps(row), encoding="utf-8")

            parsed = parse_transcript_file(path)
            event_text = "\n".join(event.text for event in parsed.events)

            self.assertEqual(parsed.session.session_id, "hook-session-1")
            self.assertEqual(parsed.session.model, "gpt-5-codex")
            self.assertEqual(parsed.session.cwd, "/workspace/hook-project")
            self.assertEqual(parsed.session.transcript_path, str(transcript_path))
            self.assertGreaterEqual(parsed.session.command_count, 1)
            self.assertGreaterEqual(parsed.session.error_count, 1)
            self.assertIn("PostToolUse", event_text)
            self.assertIn("python -m unittest", event_text)
            self.assertIn("exit_code=1", event_text)
            self.assertIn("Ran 1 test", event_text)
            self.assertIn("FAILED test_example", event_text)
            self.assertEqual(parsed.events[0].metadata["hook_event_name"], "PostToolUse")
            self.assertEqual(parsed.events[0].metadata["command"], "python -m unittest")

    def test_codex_sessions_dir_is_default_root_before_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env_sessions = root / "env-sessions"
            codex_home = root / "codex-home"
            env_sessions.mkdir()
            (codex_home / "sessions").mkdir(parents=True)

            with patch.dict(
                os.environ,
                {
                    "CODEX_SESSIONS_DIR": str(env_sessions),
                    "CODEX_HOME": str(codex_home),
                },
            ):
                roots = default_transcript_roots()

            self.assertEqual(roots[:2], [env_sessions, codex_home / "sessions"])

    def test_looks_like_user_correction(self) -> None:
        correction_texts = [
            "不是这个文件，我说的是 reports.py",
            "你忘了刚才的要求",
            "我刚才说过不要改 README",
            "not this implementation",
            "you forgot to preserve stderr",
            "as I said, do not edit cli.py",
        ]
        for text in correction_texts:
            with self.subTest(text=text):
                self.assertTrue(looks_like_user_correction(text))

        for text in ("run the tests", "this output is useful", "please continue"):
            with self.subTest(text=text):
                self.assertFalse(looks_like_user_correction(text))


if __name__ == "__main__":
    unittest.main()
