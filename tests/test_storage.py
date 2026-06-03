from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_dev_review.db import connect, count_sessions, get_session, save_transcript, search_events
from ai_dev_review.models import ParsedTranscript, SessionRecord, TranscriptEvent


class StorageTests(unittest.TestCase):
    def test_save_transcript_counts_latest_and_searches_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "state.sqlite3")
            parsed = ParsedTranscript(
                session=SessionRecord(
                    session_id="storage-1",
                    source_path="/tmp/transcripts/storage-1.jsonl",
                    started_at="2026-05-28T01:00:00+00:00",
                    updated_at="2026-05-28T01:02:00+00:00",
                    title="Storage session",
                    tool="codex",
                    message_count=2,
                    user_message_count=1,
                    assistant_message_count=1,
                    command_count=0,
                    error_count=0,
                    raw_preview="please remember needleword",
                ),
                events=(
                    TranscriptEvent(
                        session_id="storage-1",
                        event_index=0,
                        role="user",
                        kind="message",
                        text="Please remember needleword for storage lookup.",
                        created_at="2026-05-28T01:00:00+00:00",
                    ),
                    TranscriptEvent(
                        session_id="storage-1",
                        event_index=1,
                        role="assistant",
                        kind="message",
                        text="The storage lookup has been recorded.",
                        created_at="2026-05-28T01:02:00+00:00",
                    ),
                ),
            )

            save_transcript(conn, parsed)

            self.assertEqual(count_sessions(conn), 1)
            latest = get_session(conn, "latest")
            self.assertIsNotNone(latest)
            self.assertEqual(latest.session_id, "storage-1")
            by_id = get_session(conn, "storage-1")
            self.assertIsNotNone(by_id)
            self.assertEqual(by_id.title, "Storage session")

            rows = search_events(conn, "needleword", limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["session_id"], "storage-1")
            self.assertEqual(rows[0]["event_index"], 0)
            self.assertIn("needleword", rows[0]["text"])

    def test_save_transcript_is_idempotent_for_search_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "state.sqlite3")
            parsed = ParsedTranscript(
                session=SessionRecord(
                    session_id="storage-dup",
                    source_path="/tmp/transcripts/storage-dup.jsonl",
                    started_at="2026-05-28T02:00:00+00:00",
                    updated_at="2026-05-28T02:01:00+00:00",
                    title="Storage duplicate session",
                    tool="codex",
                    message_count=1,
                    user_message_count=1,
                    assistant_message_count=0,
                    command_count=0,
                    error_count=0,
                    raw_preview="uniqueword appears once",
                ),
                events=(
                    TranscriptEvent(
                        session_id="storage-dup",
                        event_index=0,
                        role="user",
                        kind="message",
                        text="uniqueword appears once in the event table.",
                        created_at="2026-05-28T02:00:00+00:00",
                    ),
                ),
            )

            save_transcript(conn, parsed)
            save_transcript(conn, parsed)

            self.assertEqual(count_sessions(conn), 1)
            rows = search_events(conn, "uniqueword", limit=10)
            self.assertEqual([(row["session_id"], row["event_index"]) for row in rows], [("storage-dup", 0)])

    def test_sessions_schema_exposes_design_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "state.sqlite3")

            rows = conn.execute("PRAGMA table_info(sessions)").fetchall()
            columns = {row["name"] for row in rows}

            self.assertTrue(
                {
                    "id",
                    "source",
                    "project_path",
                    "transcript_path",
                    "started_at",
                    "ended_at",
                    "model",
                    "title",
                    "status",
                    "raw_hash",
                }.issubset(columns)
            )


if __name__ == "__main__":
    unittest.main()
