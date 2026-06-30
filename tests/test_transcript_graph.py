from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recodex.db import connect, get_session, save_transcript
from recodex.transcript_graph import build_transcript_graph, get_transcript_graph, get_transcript_lineage
from recodex.transcripts import parse_transcript_file


class TranscriptGraphTests(unittest.TestCase):
    def test_build_graph_links_raw_records_turns_events_and_semantic_refs(self) -> None:
        parsed = _parsed_transcript()
        graph = build_transcript_graph(parsed.session, list(parsed.events))

        self.assertEqual(graph.session["session_id"], "graph-session")
        self.assertEqual(len(graph.turns), 2)
        self.assertGreaterEqual(len(graph.events), 6)
        self.assertTrue(all(event["source_ref"].startswith("codex:graph-session:turn_") for event in graph.events))
        self.assertTrue(graph.tool_calls)
        self.assertTrue(graph.tool_results)
        self.assertTrue(any(ref["path"] == "src/login.py" for ref in graph.file_refs))
        self.assertTrue(any(ref["framework"] == "pytest" and ref["status"] == "failed" for ref in graph.test_refs))
        self.assertTrue(any(ref["error_type"] == "test_failure" for ref in graph.error_refs))
        self.assertTrue(any(item["correction_type"] == "missed_requirement" for item in graph.user_corrections))
        self.assertTrue(any(edge["relation"] == "derived_from" for edge in graph.edges))

    def test_save_transcript_persists_graph_idempotently_and_lineage_is_queryable(self) -> None:
        parsed = _parsed_transcript()
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "state.sqlite3"
            conn = connect(db)
            save_transcript(conn, parsed)
            save_transcript(conn, parsed)

            session = get_session(conn, "graph-session")
            self.assertIsNotNone(session)
            graph = get_transcript_graph(conn, "graph-session")
            self.assertEqual(len(graph["turns"]), 2)
            self.assertTrue(graph["events"])
            self.assertEqual(len(graph["raw_records"]), len(parsed.events))

            event = next(item for item in graph["events"] if item["event_type"] == "test_run")
            lineage = get_transcript_lineage(conn, "graph-session", str(event["source_ref"]))

            self.assertEqual(lineage["ref"], event["source_ref"])
            self.assertTrue(any(item["type"] == "raw_record" for item in lineage["upstream"]))
            self.assertTrue(any(item["type"] == "test_ref" for item in lineage["downstream"]))
            self.assertTrue(any(item["type"] == "error_ref" for item in lineage["downstream"]))

    def test_get_graph_backfills_existing_indexed_events(self) -> None:
        parsed = _parsed_transcript()
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "state.sqlite3"
            conn = connect(db)
            save_transcript(conn, parsed)
            _delete_graph_rows(conn, "graph-session")

            graph = get_transcript_graph(conn, "graph-session")

            self.assertTrue(graph["events"])
            self.assertTrue(graph["raw_records"])
            persisted = conn.execute(
                "SELECT COUNT(*) AS count FROM normalized_events WHERE session_id = ?",
                ("graph-session",),
            ).fetchone()
            self.assertEqual(int(persisted["count"]), len(parsed.events))

    def test_codex_item_types_are_kept_as_standalone_graph_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            _write_codex_split_items_session(path)
            parsed = parse_transcript_file(path)
            graph = build_transcript_graph(parsed.session, list(parsed.events))

        types = [event["event_type"] for event in graph.events]
        phases = [event["phase"] for event in graph.events]

        self.assertIn("reasoning", types)
        self.assertIn("exploration", types)
        self.assertIn("patch", types)
        self.assertIn("tool_call", types)
        self.assertIn("reasoning", phases)
        self.assertTrue(any(call["tool_name"] == "apply_patch" for call in graph.tool_calls))
        self.assertTrue(any(call["tool_name"] == "sed" and call["command"].startswith("sed ") for call in graph.tool_calls))
        self.assertTrue(any(ref["path_role"] == "patch" for ref in graph.file_refs))

    def test_codex_ide_context_graph_excerpt_uses_prompt_but_raw_keeps_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            _write_codex_ide_context_session(path)
            parsed = parse_transcript_file(path)
            graph = build_transcript_graph(parsed.session, list(parsed.events))

        user_event = next(event for event in graph.events if event["role"] == "user")
        raw_record = graph.raw_records[user_event["event_index"]]
        metadata = json.loads(user_event["metadata_json"])

        self.assertEqual(user_event["text_excerpt"], "Build the readable transcript import")
        self.assertEqual(user_event["user_input_text"], "Build the readable transcript import")
        self.assertIn("# Context from my IDE setup:", raw_record["raw_text_preview"])
        self.assertIn("# Context from my IDE setup:", json.loads(raw_record["raw_json"])["text"])
        self.assertEqual(metadata["codex_prompt"], "Build the readable transcript import")
        self.assertEqual(metadata["user_input_text"], "Build the readable transcript import")

    def test_context_only_user_rows_are_not_marked_as_user_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            _write_context_then_request_session(path)
            parsed = parse_transcript_file(path)
            graph = build_transcript_graph(parsed.session, list(parsed.events))

        context_event, request_event = [event for event in graph.events if event["role"] == "user"][:2]

        self.assertEqual(context_event["event_type"], "context")
        self.assertEqual(context_event["phase"], "context")
        self.assertIsNone(context_event["user_input_text"])
        self.assertEqual(request_event["event_type"], "message")
        self.assertEqual(request_event["phase"], "user_request")
        self.assertEqual(request_event["user_input_text"], "真正的用户请求。")

    def test_persisted_graph_returns_user_input_text_for_lineage_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            db = Path(temp) / "state.sqlite3"
            _write_codex_ide_context_session(path)
            parsed = parse_transcript_file(path)
            conn = connect(db)
            save_transcript(conn, parsed)

            graph = get_transcript_graph(conn, "codex-ide-context")
            user_event = next(event for event in graph["events"] if event["role"] == "user")
            lineage = get_transcript_lineage(conn, "codex-ide-context", str(user_event["source_ref"]))

        self.assertEqual(user_event["user_input_text"], "Build the readable transcript import")
        self.assertEqual(lineage["evidence"][0]["user_input_text"], "Build the readable transcript import")
        self.assertEqual(lineage["evidence"][0]["text_excerpt"], "Build the readable transcript import")

    def test_get_graph_rebuilds_when_normalizer_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            db = Path(temp) / "state.sqlite3"
            _write_codex_split_items_session(path)
            parsed = parse_transcript_file(path)
            conn = connect(db)
            save_transcript(conn, parsed)
            conn.execute(
                "UPDATE normalized_events SET event_type = 'message', phase = 'planning' WHERE session_id = ?",
                ("codex-split-items",),
            )
            conn.execute(
                "UPDATE normalization_runs SET normalizer_version = 'normalizer.v1' WHERE session_id = ?",
                ("codex-split-items",),
            )
            conn.commit()

            graph = get_transcript_graph(conn, "codex-split-items")

        self.assertIn("reasoning", [event["event_type"] for event in graph["events"]])
        self.assertIn("exploration", [event["event_type"] for event in graph["events"]])


def _parsed_transcript():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "session.jsonl"
        _write_graph_session(path)
        return parse_transcript_file(path)


def _write_graph_session(path: Path) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "修复 src/login.py 的登录失败。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:01:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "我会先查看登录代码，再运行测试。"}],
            },
        },
        {
            "type": "exec_command",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:02:00+00:00",
            "arguments": {"cmd": "sed -n '1,120p' src/login.py"},
            "output": "process exited with code 0",
        },
        {
            "type": "exec_command",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:03:00+00:00",
            "arguments": {"cmd": "pytest tests/test_login.py"},
            "output": "pytest tests/test_login.py failed with AssertionError in src/login.py",
            "exit_code": 1,
        },
        {
            "type": "response_item",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:04:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "已修改 src/login.py。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": "graph-session",
            "timestamp": "2026-06-13T01:05:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "不对，我的意思是不要改认证外的逻辑。"}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def _write_codex_split_items_session(path: Path) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "修复认证逻辑，并展示过程。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:01:00+00:00",
            "item": {
                "type": "reasoning",
                "summary": "Need inspect auth flow before patching.",
            },
        },
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:02:00+00:00",
            "item": {
                "type": "exec",
                "parsedCmd": {"type": "read", "isFinished": True},
                "cmd": "sed -n '1,120p' src/auth.py",
                "output": "class AuthService: pass",
            },
        },
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:03:00+00:00",
            "item": {
                "type": "exec",
                "parsedCmd": {"type": "shell", "isFinished": True},
                "cmd": "git status --short",
                "output": " M src/auth.py",
            },
        },
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:04:00+00:00",
            "item": {
                "type": "patch",
                "cmd": "apply_patch",
                "content": "*** Begin Patch\n*** Update File: src/auth.py\n*** End Patch",
            },
        },
        {
            "type": "response_item",
            "session_id": "codex-split-items",
            "timestamp": "2026-06-13T02:05:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "已完成修改，建议运行测试。"}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def _write_codex_ide_context_session(path: Path) -> None:
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": "codex-ide-context", "cwd": "/workspace/project"},
            "timestamp": "2026-06-13T03:00:00+00:00",
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-13T03:01:00+00:00",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "# Context from my IDE setup:\n\n"
                            "## Active selection: docs/example.md\n"
                            "## My request for Codex:\n"
                            "selected content, not the request\n\n"
                            "## My request for Codex: Build the readable transcript import"
                        ),
                    }
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def _write_context_then_request_session(path: Path) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": "context-request",
            "timestamp": "2026-06-13T04:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /workspace/project\n\nFollow repo rules."}],
            },
        },
        {
            "type": "response_item",
            "session_id": "context-request",
            "timestamp": "2026-06-13T04:01:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "真正的用户请求。"}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def _delete_graph_rows(conn, session_id: str) -> None:
    for table in (
        "transcript_edges",
        "user_corrections",
        "error_refs",
        "test_refs",
        "file_refs",
        "tool_results",
        "tool_calls",
        "normalized_events",
        "turns",
        "raw_records",
        "raw_artifacts",
    ):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
    conn.commit()


if __name__ == "__main__":
    unittest.main()
