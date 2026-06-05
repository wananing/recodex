from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from recodex.cli import main
from recodex.db import connect


class StorageManagerTests(unittest.TestCase):
    def test_storage_stats_top_index_archive_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "sessions"
            archive_dir = root / "archive"
            db = root / "state.sqlite3"
            old_file = sessions_dir / "2026" / "04" / "01" / "old.jsonl"
            recent_file = sessions_dir / "2026" / "05" / "28" / "recent.jsonl"
            _write_session(old_file, "old-session", "/work/old", "Fix old issue", repeat=20)
            _write_session(recent_file, "recent-session", "/work/recent", "Fix recent issue", repeat=2)
            old_ts = time.time() - 45 * 24 * 60 * 60
            os.utime(old_file, (old_ts, old_ts))

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "stats",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--archive-dir",
                            str(archive_dir),
                        ]
                    ),
                    0,
                )
            self.assertIn("files: 2", output.getvalue())
            self.assertIn("files older than 30d: 1", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "top",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--limit",
                            "1",
                        ]
                    ),
                    0,
                )
            self.assertIn("old.jsonl", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "index",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--incremental",
                        ]
                    ),
                    0,
                )
            self.assertIn("created=2", output.getvalue())
            conn = connect(db)
            count = conn.execute("SELECT COUNT(*) AS count FROM raw_session_files").fetchone()["count"]
            self.assertEqual(count, 2)

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "index",
                            "--sessions-dir",
                            str(sessions_dir),
                        ]
                    ),
                    0,
                )
            self.assertIn("skipped=2", output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "archive",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--archive-dir",
                            str(archive_dir),
                            "--older-than",
                            "30d",
                            "--dry-run",
                        ]
                    ),
                    0,
                )
            self.assertIn("candidates=1", output.getvalue())
            self.assertTrue(old_file.exists())

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "archive",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--archive-dir",
                            str(archive_dir),
                            "--older-than",
                            "30d",
                        ]
                    ),
                    0,
                )
            self.assertIn("moved=1", output.getvalue())
            archived_file = archive_dir / "2026" / "04" / "01" / "old.jsonl"
            self.assertFalse(old_file.exists())
            self.assertTrue(archived_file.exists())
            archived_row = connect(db).execute(
                "SELECT status, hot_path, archive_path FROM raw_session_files WHERE session_id = ?",
                ("old-session",),
            ).fetchone()
            self.assertEqual(archived_row["status"], "archived")
            self.assertIsNone(archived_row["hot_path"])
            self.assertEqual(archived_row["archive_path"], str(archived_file))

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "storage",
                            "restore",
                            "old-session",
                            "--sessions-dir",
                            str(sessions_dir),
                            "--archive-dir",
                            str(archive_dir),
                        ]
                    ),
                    0,
                )
            self.assertIn("Restored", output.getvalue())
            self.assertTrue(old_file.exists())
            self.assertFalse(archived_file.exists())
            restored_row = connect(db).execute(
                "SELECT status, hot_path, archive_path FROM raw_session_files WHERE session_id = ?",
                ("old-session",),
            ).fetchone()
            self.assertEqual(restored_row["status"], "hot")
            self.assertEqual(restored_row["hot_path"], str(old_file))
            self.assertIsNone(restored_row["archive_path"])

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "storage", "vacuum"]), 0)
            self.assertIn("Vacuumed", output.getvalue())


def _write_session(path: Path, session_id: str, cwd: str, title: str, *, repeat: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "session_meta",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:00:00+00:00",
            "cwd": cwd,
            "model": "gpt-5-codex",
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:01:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": title}],
            },
        },
    ]
    rows.extend(
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:02:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "x" * 512}],
            },
        }
        for _ in range(repeat)
    )
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
