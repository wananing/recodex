from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from recodex.cli import main


class QuickstartTests(unittest.TestCase):
    def test_default_command_prints_dashboard_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db)]), 0)

            text = output.getvalue()
            self.assertIn("Dashboard-first report workflow", text)
            self.assertIn("recodex serve", text)

    def test_latest_command_is_retired(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            reports = root / "reports"
            db = root / "state.sqlite3"
            _write_session(sessions_dir / "recent.jsonl", "recent", "/work/project-a", "Fix recent tests")

            with patch("webbrowser.open", return_value=True) as browser_open:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(
                        main(
                            [
                                "--db",
                                str(db),
                                "latest",
                                "--sessions-dir",
                                str(sessions_dir),
                                "--reports-dir",
                                str(reports),
                            ]
                        ),
                        1,
                    )

            browser_open.assert_not_called()
            self.assertIn("retired", output.getvalue().lower())

    def test_old_default_latest_flags_route_to_retired_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            reports = root / "reports"
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
                            "--json",
                        ]
                    ),
                    1,
                )

            self.assertIn("retired", output.getvalue().lower())
            self.assertFalse((reports / "recent" / "report.json").exists())

    def test_open_latest_reopens_most_recent_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = root / "reports"
            newer = reports / "newer"
            older = reports / "older"
            newer.mkdir(parents=True)
            older.mkdir(parents=True)
            (older / "report.html").write_text("<html>old</html>", encoding="utf-8")
            (newer / "report.html").write_text("<html>new</html>", encoding="utf-8")
            old_ts = time.time() - 60
            os.utime(older / "report.html", (old_ts, old_ts))

            with patch("webbrowser.open", return_value=True) as browser_open:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["open", "latest", "--reports-dir", str(reports)]), 0)

            self.assertIn(str(newer / "report.html"), output.getvalue())
            self.assertIn("/newer/report.html", browser_open.call_args.args[0])

    def test_quickstart_command_is_retired(self) -> None:
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
                    1,
                )

            text = output.getvalue()
            self.assertIn("retired", text.lower())
            self.assertFalse((reports / "quickstart-index.md").exists())


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
