from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from ai_dev_review.cli import main
from ai_dev_review.db import connect, count_sessions, get_session


class QuickstartTests(unittest.TestCase):
    def test_default_command_runs_quickstart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            reports = root / "reports"
            exports = root / "exports"
            db = root / "state.sqlite3"
            _write_session(sessions_dir / "recent.jsonl", "recent", "/work/project-a", "Fix recent tests")

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "--sessions-dir",
                            str(sessions_dir),
                            "--reports-dir",
                            str(reports),
                            "--exports-dir",
                            str(exports),
                            "--since",
                            "7d",
                            "--limit",
                            "5",
                        ]
                    ),
                    0,
                )

            text = output.getvalue()
            self.assertIn("Quickstart scanned 1 session(s)", text)
            self.assertTrue((reports / "quickstart-index.md").exists())
            self.assertTrue(any((reports / "projects").glob("*/improvements.md")))
            self.assertTrue(any((reports / "projects").glob("*/report.json")))
            self.assertTrue(any((reports / "projects").glob("*/report.html")))
            self.assertTrue(any((exports / "quickstart" / "projects").glob("*/AGENTS.patch.md")))

    def test_quickstart_scans_recent_limited_sessions_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            reports = root / "reports"
            exports = root / "exports"
            db = root / "state.sqlite3"
            old_file = sessions_dir / "old.jsonl"
            recent_a = sessions_dir / "recent-a.jsonl"
            recent_b = sessions_dir / "recent-b.jsonl"
            _write_session(old_file, "old-session", "/work/project-a", "Old task")
            _write_session(recent_a, "recent-a", "/work/project-a", "Fix recent tests")
            _write_session(recent_b, "recent-b", "/work/project-b", "Add recent feature")
            old_ts = time.time() - 10 * 24 * 60 * 60
            os.utime(old_file, (old_ts, old_ts))

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "quickstart",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--reports-dir",
                            str(reports),
                            "--exports-dir",
                            str(exports),
                            "--since",
                            "7d",
                            "--limit",
                            "5",
                        ]
                    ),
                    0,
                )

            text = output.getvalue()
            self.assertIn("Quickstart scanned 2 session(s)", text)
            self.assertIn("Project: /work/project-a", text)
            self.assertIn("Project: /work/project-b", text)
            self.assertIn("Patterns:", text)
            self.assertIn("Improvements:", text)
            self.assertIn("Exports:", text)

            conn = connect(db)
            self.assertEqual(count_sessions(conn), 2)
            self.assertIsNotNone(get_session(conn, "recent-a"))
            self.assertIsNotNone(get_session(conn, "recent-b"))
            self.assertIsNone(get_session(conn, "old-session"))
            project_dirs = sorted((reports / "projects").glob("*"))
            self.assertEqual(len(project_dirs), 2)
            for project_dir in project_dirs:
                self.assertTrue(any(project_dir.glob("retro-*.md")))
                self.assertTrue((project_dir / "patterns-7d.md").exists())
                self.assertTrue((project_dir / "improvements.md").exists())
                self.assertTrue((project_dir / "report.json").exists())
                self.assertTrue((project_dir / "report.html").exists())
                self.assertIn("report.html", (reports / "quickstart-index.md").read_text(encoding="utf-8"))
            export_dirs = sorted((exports / "quickstart" / "projects").glob("*"))
            self.assertEqual(len(export_dirs), 2)
            for export_dir in export_dirs:
                self.assertTrue((export_dir / "AGENTS.patch.md").exists())
                self.assertTrue((export_dir / "skills" / "ai-dev-review-retro" / "SKILL.md").exists())
                self.assertTrue((export_dir / "checklists" / "ai-review-checklist.md").exists())
                self.assertTrue((export_dir / "scripts" / "ai-review-verify.sh").exists())
                self.assertTrue((export_dir / "ci" / "verify.yml").exists())
            self.assertTrue((reports / "quickstart-index.md").exists())


def _write_session(path: Path, session_id: str, cwd: str, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T00:59:00+00:00",
                        "cwd": cwd,
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T01:00:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": title}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "exec_command",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T01:01:00+00:00",
                        "arguments": {"cmd": "python3 -m unittest"},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T01:02:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Tests failed, then passed."}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
