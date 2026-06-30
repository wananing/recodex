from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main
from recodex.db import connect, get_session
from recodex.watch import list_watch_sources


class WatchCliTests(unittest.TestCase):
    def test_watch_source_runs_incremental_sync_and_respects_disabled_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            source_dir = root / "sessions"
            source_dir.mkdir()
            _write_session(source_dir / "one.jsonl", "watch-1", "Import this watched file.")

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "watch",
                            "add",
                            "--source",
                            "codex",
                            "--path",
                            str(source_dir),
                            "--scope",
                            "project-a",
                        ]
                    ),
                    0,
                )
            self.assertIn("Watch source #1 enabled codex", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "watch", "run"]), 0)
            self.assertIn("imported=1", output.getvalue())
            self.assertIsNotNone(get_session(connect(db), "watch-1"))

            source = list_watch_sources(connect(db))[0]
            self.assertEqual(source.scope, "project-a")
            self.assertEqual(source.last_imported, 1)
            self.assertIsNotNone(source.last_sync_at)

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "watch", "run"]), 0)
            self.assertIn("skipped=1", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "watch", "status"]), 0)
            self.assertIn("last_sync=", output.getvalue())
            self.assertIn("event", output.getvalue())

            self.assertEqual(main(["--db", str(db), "watch", "edit", "1", "--disable"]), 0)
            _write_session(source_dir / "two.jsonl", "watch-2", "Do not import while disabled.")

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "watch", "run"]), 0)
            self.assertIn("No enabled watch sources", output.getvalue())
            self.assertIsNone(get_session(connect(db), "watch-2"))

    def test_watch_source_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            source_dir = root / "sessions"
            source_dir.mkdir()

            self.assertEqual(
                main(["--db", str(db), "watch", "add", "--path", str(source_dir)]),
                0,
            )
            self.assertEqual(main(["--db", str(db), "watch", "delete", "1"]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "watch", "list"]), 0)
            self.assertIn("No watch sources configured", output.getvalue())


def _write_session(path: Path, session_id: str, text: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "session_id": session_id,
                "timestamp": "2026-05-28T01:00:00+00:00",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
