from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main


class FullFeatureCliTests(unittest.TestCase):
    def test_local_workflow_commands_cover_design_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            exports = root / "exports"
            transcript = root / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "full-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "Fix tests. OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "exec_command",
                                "session_id": "full-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "arguments": {"cmd": "python3 -m unittest"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "full-1",
                                "timestamp": "2026-05-28T01:02:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "Tests failed with AssertionError, then passed.",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "full-1",
                                "timestamp": "2026-05-28T01:03:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "Use pnpm instead of npm for package manager commands.",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "full-1",
                                "timestamp": "2026-05-28T01:04:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "Use pnpm instead of npm for package manager commands.",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                main(
                    [
                        "--db",
                        str(db),
                        "init",
                        "--project",
                        str(root),
                        "--sessions-dir",
                        str(root / "empty-sessions"),
                        "--no-prompt",
                    ]
                ),
                0,
            )
            self.assertTrue((root / ".recodex.toml").exists())
            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)

            self.assertEqual(
                main(["--db", str(db), "retro", "full-1", "--reports-dir", str(reports)]),
                0,
            )
            self.assertFalse(any("sk-proj" in path.name for path in reports.glob("retro-*.md")))
            self.assertEqual(
                main(["--db", str(db), "retro", "--since", "3650d", "--reports-dir", str(reports)]),
                0,
            )
            self.assertTrue(any(reports.glob("retro-*.md")))
            self.assertTrue((reports / "retro-index.md").exists())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "privacy", "scan", "latest"]), 0)
            privacy_output = output.getvalue()
            self.assertIn("[REDACTED:API_KEY]", privacy_output)
            self.assertNotIn("sk-proj-", privacy_output)

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "before", "--project", str(root)]), 0)
            self.assertIn("Relevant AI Dev Context", output.getvalue())

            self.assertEqual(
                main(
                    [
                        "--db",
                        str(db),
                        "after",
                        "--session",
                        "latest",
                        "--reports-dir",
                        str(reports),
                    ]
                ),
                0,
            )

            self.assertEqual(
                main(["--db", str(db), "improvements", "edit", "1", "--title", "Edited title"]),
                0,
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "improvements", "show", "1"]), 0)
            self.assertIn("Edited title", output.getvalue())
            self.assertNotIn("sk-proj-", output.getvalue())

            self.assertEqual(main(["--db", str(db), "improvements", "accept", "1"]), 0)
            self.assertEqual(
                main(["--db", str(db), "improvements", "apply", "1", "--exports-dir", str(exports)]),
                0,
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "improvements", "list", "--status", "applied"]), 0)
            self.assertIn("[applied]", output.getvalue())

            self.assertEqual(
                main(["workflow", "install-codex-hooks", "--exports-dir", str(exports)]),
                0,
            )
            self.assertTrue((exports / "workflow" / "codex-after-session.sh").exists())


if __name__ == "__main__":
    unittest.main()
