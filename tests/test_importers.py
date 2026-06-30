from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main
from recodex.db import connect, get_session
from recodex.importers import get_importer


class ImporterBoundaryTests(unittest.TestCase):
    def test_codex_importer_parses_existing_transcript_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "importer-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "Use the importer boundary.",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "exec_command",
                                "session_id": "importer-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "arguments": {"cmd": "python -m unittest"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            importer = get_importer("codex")
            parsed = importer.parse_file(path)

            self.assertEqual(parsed.session.session_id, "importer-1")
            self.assertEqual(parsed.session.tool, "codex")
            self.assertEqual(parsed.session.user_message_count, 1)
            self.assertGreaterEqual(parsed.session.command_count, 1)

    def test_auto_importer_defaults_to_codex(self) -> None:
        self.assertIs(get_importer("auto"), get_importer("codex"))

    def test_claude_code_importer_retags_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "claude.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "session_id": "claude-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "role": "user",
                                "content": "Debug the failing pytest run.",
                                "cwd": "/work/project",
                            }
                        ),
                        json.dumps(
                            {
                                "session_id": "claude-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "role": "assistant",
                                "content": "The fixture path is wrong.",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            parsed = get_importer("claude-code").parse_file(path)

            self.assertEqual(parsed.session.session_id, "claude-1")
            self.assertEqual(parsed.session.tool, "claude-code")
            self.assertEqual(parsed.session.source, "claude-code")
            self.assertEqual(parsed.session.metadata["source_tool"], "claude-code")
            self.assertEqual(parsed.events[0].metadata["source_tool"], "claude-code")

    def test_cli_import_accepts_source_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            transcript = root / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": "source-flag-1",
                        "timestamp": "2026-05-28T01:00:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Import with a source flag."}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(
                    [
                        "--db",
                        str(db),
                        "import",
                        "--source",
                        "codex",
                        str(transcript),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("Scanned 1 transcript file", output.getvalue())

    def test_cli_import_accepts_claude_code_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            transcript = root / "claude.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "session_id": "claude-cli-1",
                        "timestamp": "2026-05-28T01:00:00+00:00",
                        "role": "user",
                        "content": "Import a Claude Code session.",
                    }
                ),
                encoding="utf-8",
            )

            code = main(["--db", str(db), "import", "--source", "claude-code", str(transcript)])

            self.assertEqual(code, 0)
            session = get_session(connect(db), "claude-cli-1")
            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual(session.source, "claude-code")


if __name__ == "__main__":
    unittest.main()
