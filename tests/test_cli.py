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
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "smoke-1",
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
                                "session_id": "smoke-1",
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

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as report_output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "report",
                            "latest",
                            "--reports-dir",
                            str(reports),
                            "--llm",
                            "--llm-provider",
                            "mock",
                        ]
                    ),
                    0,
                )
            self.assertIn(".html", report_output.getvalue())
            report_json = next(reports.glob("retro-*.json"))
            report_payload = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertIn("evidence_audit", report_payload)
            self.assertIn("deep-audit", report_payload["meta"]["analysis_mode"])
            with contextlib.redirect_stdout(io.StringIO()) as legacy_output:
                self.assertEqual(
                    main(["--db", str(db), "patterns", "--since", "3650d", "--reports-dir", str(reports)]),
                    1,
                )
            self.assertIn("retired", legacy_output.getvalue().lower())
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
            self.assertIn("agents_md", output.getvalue())

            self.assertEqual(
                main(["--db", str(db), "export", "agents", "--exports-dir", str(exports)]),
                0,
            )
            self.assertEqual(main(["--db", str(db), "improvements", "accept", "1"]), 0)
            self.assertEqual(
                main(["--db", str(db), "export", "skills", "--exports-dir", str(exports)]),
                0,
            )

            self.assertTrue(any(reports.glob("retro-*.md")))
            self.assertTrue(any(reports.glob("retro-*.json")))
            self.assertTrue(any(reports.glob("retro-*.html")))
            self.assertFalse((reports / "patterns-3650d.md").exists())
            self.assertTrue((reports / "improvements.md").exists())
            self.assertTrue((exports / "AGENTS.patch.md").exists())
            self.assertTrue(any((exports / "skills").glob("*/SKILL.md")))

    def test_mine_command_writes_evidence_pipeline_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "correction.jsonl"
            db = root / "state.sqlite3"
            output = root / "mining"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "mine-1",
                                "timestamp": "2026-06-15T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {"type": "input_text", "text": "帮我修 CI failure。"}
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "mine-1",
                                "timestamp": "2026-06-15T01:01:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "我已经修好了。"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "mine-1",
                                "timestamp": "2026-06-15T01:02:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "你还没看 CI 日志，也没跑失败的 test。",
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
                main(
                    [
                        "--db",
                        str(db),
                        "mine",
                        "--since",
                        "3650d",
                        "--output-dir",
                        str(output),
                    ]
                ),
                0,
            )

            self.assertTrue((output / "cards.jsonl").exists())
            self.assertTrue((output / "clusters.json").exists())
            self.assertTrue((output / "review_queue.json").exists())
            self.assertTrue((output / "coverage_report.md").exists())

    def test_evals_run_outputs_golden_metrics(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["evals", "run", "--json"]), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["case_count"], 2)
        self.assertEqual(payload["routing_accuracy"], 1.0)
        self.assertEqual(payload["false_skill_promotions"], 0)


if __name__ == "__main__":
    unittest.main()
