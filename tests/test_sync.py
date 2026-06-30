from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from recodex.db import connect, count_sessions
from recodex.importers import get_importer
from recodex.sync import sync_import_paths


def _write_transcript(path: Path, *, session_id: str = "sync-1") -> None:
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "session_id": session_id,
                "timestamp": "2026-05-28T01:00:00+00:00",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Sync this transcript."}],
                },
            }
        ),
        encoding="utf-8",
    )


class SyncImportTests(unittest.TestCase):
    def test_repeated_import_skips_unchanged_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            transcript = root / "session.jsonl"
            _write_transcript(transcript)
            conn = connect(db)
            importer = get_importer("codex")

            first = sync_import_paths(conn, importer, [transcript])
            second = sync_import_paths(conn, importer, [transcript])

            self.assertEqual(first.scanned, 1)
            self.assertEqual(first.imported, 1)
            self.assertEqual(first.skipped, 0)
            self.assertEqual(second.scanned, 1)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(count_sessions(conn), 1)

            rows = conn.execute("SELECT source, path FROM sync_files").fetchall()
            self.assertEqual([(row["source"], row["path"]) for row in rows], [("codex", str(transcript.resolve()))])

            runs = conn.execute(
                "SELECT source, scanned, imported, skipped, failed FROM import_runs ORDER BY id"
            ).fetchall()
            self.assertEqual(len(runs), 2)
            self.assertEqual((runs[0]["scanned"], runs[0]["imported"], runs[0]["skipped"], runs[0]["failed"]), (1, 1, 0, 0))
            self.assertEqual((runs[1]["scanned"], runs[1]["imported"], runs[1]["skipped"], runs[1]["failed"]), (1, 0, 1, 0))

    def test_changed_mtime_same_content_refreshes_record_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "session.jsonl"
            _write_transcript(transcript)
            conn = connect(root / "state.sqlite3")
            importer = get_importer("codex")

            first = sync_import_paths(conn, importer, [transcript])
            old_record = conn.execute("SELECT mtime FROM sync_files WHERE path = ?", (str(transcript.resolve()),)).fetchone()
            os.utime(transcript, (transcript.stat().st_atime + 10, transcript.stat().st_mtime + 10))
            second = sync_import_paths(conn, importer, [transcript])
            new_record = conn.execute("SELECT mtime FROM sync_files WHERE path = ?", (str(transcript.resolve()),)).fetchone()

            self.assertEqual(first.imported, 1)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.skipped, 1)
            self.assertGreater(new_record["mtime"], old_record["mtime"])


if __name__ == "__main__":
    unittest.main()
