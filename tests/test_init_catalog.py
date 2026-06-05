from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main
from recodex.db import connect, count_catalog_entries, count_sessions, list_catalog_projects


class InitCatalogTests(unittest.TestCase):
    def test_init_catalogs_projects_without_full_scan_then_selects_one_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            sessions_dir.mkdir()
            _write_session(sessions_dir / "one.jsonl", "session-one", "/work/project-a", "Fix project A")
            _write_session(sessions_dir / "two.jsonl", "session-two", "/work/project-b", "Fix project B")
            db = root / "state.sqlite3"

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "init",
                            "--project",
                            str(root),
                            "--sessions-dir",
                            str(sessions_dir),
                            "--no-prompt",
                        ]
                    ),
                    0,
                )

            conn = connect(db)
            self.assertEqual(count_catalog_entries(conn), 2)
            self.assertEqual(count_sessions(conn), 0)
            projects = list_catalog_projects(conn)
            self.assertEqual([row["project_path"] for row in projects], ["/work/project-a", "/work/project-b"])
            self.assertIn("[1]", output.getvalue())
            self.assertIn("/work/project-a", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "init",
                            "--project",
                            str(root),
                            "--sessions-dir",
                            str(sessions_dir),
                            "--select",
                            "1",
                            "--no-prompt",
                        ]
                    ),
                    0,
                )

            self.assertEqual(count_sessions(conn), 1)
            self.assertIn("Selected project", output.getvalue())
            self.assertIn("Scanned 1 transcript", output.getvalue())


def _write_session(path: Path, session_id: str, cwd: str, title: str) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T01:00:00+00:00",
                        "cwd": cwd,
                        "model": "gpt-5-codex",
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": session_id,
                        "timestamp": "2026-05-28T01:01:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": title}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()

