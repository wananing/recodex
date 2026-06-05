from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main


class CliSmokeTests(unittest.TestCase):
    def test_core_command_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "transcript.jsonl"
            db = root / "state.sqlite3"
            reports = root / "reports"
            exports = root / "exports"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "smoke-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "Build the tool."}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "exec_command",
                                "session_id": "smoke-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "arguments": {"cmd": "python -m unittest"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "smoke-1",
                                "timestamp": "2026-05-28T01:02:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "A sandbox permission error happened, then tests passed.",
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
            self.assertEqual(
                main(["--db", str(db), "retro", "latest", "--reports-dir", str(reports)]),
                0,
            )
            with contextlib.redirect_stdout(io.StringIO()) as report_output:
                self.assertEqual(
                    main(["--db", str(db), "report", "latest", "--reports-dir", str(reports)]),
                    0,
                )
            self.assertIn(".html", report_output.getvalue())
            self.assertEqual(
                main(["--db", str(db), "patterns", "--since", "3650d", "--reports-dir", str(reports)]),
                0,
            )
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
                self.assertEqual(main(["--db", str(db), "improvements", "review"]), 0)
            self.assertIn("sandbox", output.getvalue().lower())

            self.assertEqual(
                main(["--db", str(db), "export", "agents", "--exports-dir", str(exports)]),
                0,
            )
            self.assertEqual(
                main(["--db", str(db), "export", "skills", "--exports-dir", str(exports)]),
                0,
            )

            self.assertTrue(any(reports.glob("retro-*.md")))
            self.assertTrue(any(reports.glob("retro-*.json")))
            self.assertTrue(any(reports.glob("retro-*.html")))
            self.assertTrue((reports / "patterns-3650d.md").exists())
            self.assertTrue((reports / "improvements.md").exists())
            self.assertTrue((exports / "AGENTS.patch.md").exists())
            self.assertTrue((exports / "skills" / "recodex-retro" / "SKILL.md").exists())
            self.assertTrue((exports / "checklists" / "recodex-retro.md").exists())
            self.assertTrue((exports / "scripts" / "recodex-weekly.sh").exists())


if __name__ == "__main__":
    unittest.main()
