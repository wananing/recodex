from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main


class ExtendedCliTests(unittest.TestCase):
    def test_sessions_search_review_and_export_commands(self) -> None:
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
                                "session_id": "extended-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "Fix sandbox failure."}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "exec_command",
                                "session_id": "extended-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "arguments": {"cmd": "python3 -m unittest"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "extended-1",
                                "timestamp": "2026-05-28T01:02:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "The sandbox permission error failed the first test run.",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "sessions", "list"]), 0)
            self.assertIn("extended-1", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "sessions", "show", "latest"]), 0)
            self.assertIn("Fix sandbox failure", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "search", "sandbox"]), 0)
            self.assertIn("extended-1", output.getvalue())
            self.assertIn("sandbox", output.getvalue().lower())

            self.assertEqual(
                main(
                    [
                        "--db",
                        str(db),
                        "improvements",
                        "propose",
                        "--since",
                        "3650d",
                        "--reports-dir",
                        str(reports),
                    ]
                ),
                0,
            )

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "improvements", "list"]), 0)
            self.assertIn("#1", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "improvements", "show", "1"]), 0)
            self.assertIn("Evidence", output.getvalue())

            self.assertEqual(main(["--db", str(db), "improvements", "accept", "1"]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "improvements", "list", "--status", "accepted"]), 0)
            self.assertIn("[accepted]", output.getvalue())

            self.assertEqual(
                main(["--db", str(db), "export", "checklist", "--exports-dir", str(exports)]),
                0,
            )
            self.assertEqual(
                main(["--db", str(db), "export", "scripts", "--exports-dir", str(exports)]),
                0,
            )
            self.assertEqual(
                main(["--db", str(db), "export", "ci", "--exports-dir", str(exports)]),
                0,
            )

            self.assertTrue((exports / "checklists" / "recodex-checklist.md").exists())
            self.assertTrue((exports / "scripts" / "recodex-verify.sh").exists())
            self.assertTrue((exports / "ci" / "verify.yml").exists())


if __name__ == "__main__":
    unittest.main()
