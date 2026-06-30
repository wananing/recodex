from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .analysis import ERROR_TERMS, SANDBOX_TERMS, TEST_TERMS, count_terms
from .db import now_utc
from .models import ParsedTranscript, SessionRecord, TranscriptEvent
from .privacy import redact_text
from .transcripts import extract_user_input_text

GRAPH_SCHEMA_VERSION = "transcript_graph.v1"
NORMALIZER_VERSION = "normalizer.v5.user-input-context"
MAX_PREVIEW_CHARS = 1_200
MAX_EXCERPT_CHARS = 2_000

PATCH_TERMS = ("apply_patch", "patch", "diff", "已修改", "修改", "updated", "write")
NETWORK_TERMS = ("network", "dns", "connection", "proxy", "timeout", "registry", "pypi", "npm")
VERIFICATION_TERMS = ("验证", "测试", "构建", "检查", "pytest", "unittest", "npm test", "build", "lint")
USER_CORRECTION_TERMS = (
    "不是",
    "不对",
    "错了",
    "我说的是",
    "我的意思",
    "偏题",
    "你漏",
    "你忘",
    "wrong",
    "not what",
    "i mean",
)
SCOPE_CORRECTION_TERMS = ("不要", "别")
PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[./~]?[\w.-]+/)+(?:[\w.@-]+)(?:\.[A-Za-z0-9_+-]+)?")


@dataclass(frozen=True)
class TranscriptGraph:
    session: dict[str, Any]
    raw_artifacts: list[dict[str, Any]]
    raw_records: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    events: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    file_refs: list[dict[str, Any]]
    test_refs: list[dict[str, Any]]
    error_refs: list[dict[str, Any]]
    user_corrections: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def build_transcript_graph(session: SessionRecord, events: list[TranscriptEvent]) -> TranscriptGraph:
    source_type = _source_type(session)
    artifact = _raw_artifact(session, events, source_type)
    raw_records: list[dict[str, Any]] = []
    turns_by_id: dict[str, dict[str, Any]] = {}
    normalized_events: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    file_refs: list[dict[str, Any]] = []
    test_refs: list[dict[str, Any]] = []
    error_refs: list[dict[str, Any]] = []
    user_corrections: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    turn_index = 0

    for event in events:
        if event.role == "user" or turn_index == 0:
            turn_index += 1
        turn_id = f"{session.session_id}:turn_{turn_index}"
        turn = turns_by_id.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "session_id": session.session_id,
                "turn_index": turn_index,
                "initiator": event.role if event.role in {"user", "assistant", "system", "tool"} else "unknown",
                "phase_hint": "",
                "started_at": event.created_at,
                "ended_at": event.created_at,
                "parent_turn_id": None,
                "source_refs": [],
            },
        )
        if event.created_at:
            turn["started_at"] = min(str(turn["started_at"] or event.created_at), event.created_at)
            turn["ended_at"] = max(str(turn["ended_at"] or event.created_at), event.created_at)

        raw_record = _raw_record(artifact["artifact_id"], event)
        raw_records.append(raw_record)
        command = _command_for_event(event)
        status = _status_for_event(event, command)
        event_type = _event_type(event, command, status)
        phase = _phase_for_event(event, event_type, command, status)
        source_ref = f"{source_type}:{session.session_id}:turn_{turn_index}:event_{event.event_index}"
        event_id = _stable_id("event", source_ref)
        normalized = {
            "event_id": event_id,
            "session_id": session.session_id,
            "turn_id": turn_id,
            "event_index": event.event_index,
            "role": event.role,
            "event_type": event_type,
            "kind": event.kind,
            "phase": phase,
            "created_at": event.created_at,
            "source_ref": source_ref,
            "text_excerpt": _event_text_excerpt(event),
            "user_input_text": _event_user_input_text(event),
            "raw_record_ids": [raw_record["raw_record_id"]],
            "metadata_json": json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
        }
        normalized_events.append(normalized)
        turn["source_refs"].append(source_ref)
        if not turn["phase_hint"]:
            turn["phase_hint"] = phase
        edges.append(_edge(session.session_id, raw_record["raw_record_id"], "raw_record", event_id, "event", "derived_from"))

        tool_call_id: str | None = None
        if command:
            tool_call = _tool_call(event, normalized, turn_id, command, status)
            tool_calls.append(tool_call)
            tool_call_id = tool_call["tool_call_id"]
            edges.append(_edge(session.session_id, event_id, "event", tool_call_id, "tool_call", "calls"))

            tool_result = _tool_result(event, normalized, tool_call_id, status)
            tool_results.append(tool_result)
            edges.append(_edge(session.session_id, tool_call_id, "tool_call", tool_result["tool_result_id"], "tool_result", "returns"))

        for index, file_ref in enumerate(_file_refs(event.text, command)):
            ref = _file_ref(event, normalized, file_ref, index, command, event_type)
            file_refs.append(ref)
            edges.append(_edge(session.session_id, event_id, "event", ref["file_ref_id"], "file_ref", _file_relation(ref["path_role"])))

        if _is_test_event(event, command):
            test_ref = _test_ref(event, normalized, tool_call_id, command, status)
            test_refs.append(test_ref)
            edges.append(_edge(session.session_id, event_id, "event", test_ref["test_ref_id"], "test_ref", "tests"))

        if _is_error_event(event, status):
            error_ref = _error_ref(event, normalized, tool_call_id, status)
            error_refs.append(error_ref)
            edges.append(_edge(session.session_id, event_id, "event", error_ref["error_ref_id"], "error_ref", "fails"))

        if _is_user_correction(event):
            correction = _user_correction(event, normalized)
            user_corrections.append(correction)
            edges.append(_edge(session.session_id, event_id, "event", correction["correction_id"], "user_correction", "corrects"))

    turns = list(turns_by_id.values())
    for turn in turns:
        turn["source_refs"] = list(turn["source_refs"])

    return TranscriptGraph(
        session={
            "session_id": session.session_id,
            "source_type": source_type,
            "title": session.title,
            "project_path": session.project_path,
            "workspace_id": session.project_path or session.cwd or "",
            "model": session.model,
            "started_at": session.started_at,
            "ended_at": session.ended_at or session.updated_at,
            "updated_at": session.updated_at,
            "status": session.status or "unknown",
            "raw_artifact_ids": [artifact["artifact_id"]],
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
        },
        raw_artifacts=[artifact],
        raw_records=raw_records,
        turns=turns,
        events=normalized_events,
        tool_calls=tool_calls,
        tool_results=tool_results,
        file_refs=file_refs,
        test_refs=test_refs,
        error_refs=error_refs,
        user_corrections=user_corrections,
        edges=edges,
    )


def save_transcript_graph(conn: sqlite3.Connection, parsed: ParsedTranscript, *, commit: bool = True) -> TranscriptGraph:
    graph = build_transcript_graph(parsed.session, list(parsed.events))
    session_id = parsed.session.session_id
    _delete_graph(conn, session_id)
    _insert_many(conn, "raw_artifacts", graph.raw_artifacts)
    _insert_many(conn, "raw_records", graph.raw_records)
    _insert_many(conn, "turns", graph.turns)
    _insert_many(conn, "normalized_events", graph.events)
    _insert_many(conn, "tool_calls", graph.tool_calls)
    _insert_many(conn, "tool_results", graph.tool_results)
    _insert_many(conn, "file_refs", graph.file_refs)
    _insert_many(conn, "test_refs", graph.test_refs)
    _insert_many(conn, "error_refs", graph.error_refs)
    _insert_many(conn, "user_corrections", graph.user_corrections)
    _insert_many(conn, "transcript_edges", graph.edges)
    conn.execute(
        """
        INSERT INTO normalization_runs (
            session_id, graph_schema_version, normalizer_version,
            raw_record_count, event_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            GRAPH_SCHEMA_VERSION,
            NORMALIZER_VERSION,
            len(graph.raw_records),
            len(graph.events),
            now_utc(),
        ),
    )
    if commit:
        conn.commit()
    return graph


def get_transcript_graph(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    ensure_transcript_graph(conn, session_id)
    return {
        "session": _graph_session_payload(conn, session_id),
        "raw_artifacts": _rows(conn, "raw_artifacts", "session_id = ?", (session_id,)),
        "raw_records": _rows(conn, "raw_records", "session_id = ?", (session_id,)),
        "turns": _rows(conn, "turns", "session_id = ?", (session_id,), order_by="turn_index ASC"),
        "events": _rows(conn, "normalized_events", "session_id = ?", (session_id,), order_by="event_index ASC"),
        "tool_calls": _rows(conn, "tool_calls", "session_id = ?", (session_id,)),
        "tool_results": _rows(conn, "tool_results", "session_id = ?", (session_id,)),
        "file_refs": _rows(conn, "file_refs", "session_id = ?", (session_id,)),
        "test_refs": _rows(conn, "test_refs", "session_id = ?", (session_id,)),
        "error_refs": _rows(conn, "error_refs", "session_id = ?", (session_id,)),
        "user_corrections": _rows(conn, "user_corrections", "session_id = ?", (session_id,)),
        "edges": _rows(conn, "transcript_edges", "session_id = ?", (session_id,)),
    }


def get_transcript_lineage(conn: sqlite3.Connection, session_id: str, ref: str) -> dict[str, Any]:
    ensure_transcript_graph(conn, session_id)
    node = _node_for_ref(conn, session_id, ref)
    if node is None:
        raise ValueError(f"No graph node found for ref `{ref}` in session `{session_id}`.")
    upstream_edges = _lineage_edges(conn, session_id, to_type=node["type"], to_id=node["id"])
    downstream_edges = _lineage_edges(conn, session_id, from_type=node["type"], from_id=node["id"])
    return {
        "ref": ref,
        "node": node,
        "upstream": [_edge_endpoint(conn, edge, upstream=True) for edge in upstream_edges],
        "downstream": [_edge_endpoint(conn, edge, upstream=False) for edge in downstream_edges],
        "evidence": _evidence_for_node(conn, session_id, node),
    }


def ensure_transcript_graph(conn: sqlite3.Connection, session_id: str) -> None:
    if _has_current_transcript_graph(conn, session_id):
        return
    session_row = conn.execute("SELECT * FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
    if session_row is None:
        return
    event_rows = conn.execute(
        """
        SELECT * FROM events
        WHERE session_id = ?
        ORDER BY event_index ASC
        """,
        (session_id,),
    ).fetchall()
    if not event_rows:
        return
    parsed = ParsedTranscript(
        session=_session_from_row(session_row),
        events=tuple(_event_from_row(row) for row in event_rows),
    )
    save_transcript_graph(conn, parsed, commit=True)


def _raw_artifact(session: SessionRecord, events: list[TranscriptEvent], source_type: str) -> dict[str, Any]:
    path = Path(session.source_path)
    stat = path.stat() if path.exists() else None
    content_hash = session.raw_hash or _stable_id("content", *(event.text for event in events))
    artifact_id = _stable_id("artifact", source_type, session.source_path, content_hash)
    return {
        "artifact_id": artifact_id,
        "session_id": session.session_id,
        "source_type": source_type,
        "source_path": session.source_path,
        "content_hash": content_hash,
        "mtime": stat.st_mtime if stat else None,
        "size_bytes": stat.st_size if stat else None,
        "ingest_run_id": None,
        "first_seen_at": now_utc(),
        "last_seen_at": now_utc(),
    }


def _raw_record(artifact_id: str, event: TranscriptEvent) -> dict[str, Any]:
    raw_json = {
        "role": event.role,
        "kind": event.kind,
        "text": event.text,
        "created_at": event.created_at,
        "metadata": event.metadata,
    }
    raw_hash = _stable_id("raw_hash", json.dumps(raw_json, ensure_ascii=False, sort_keys=True))
    return {
        "raw_record_id": _stable_id("raw_record", artifact_id, event.event_index, raw_hash),
        "artifact_id": artifact_id,
        "session_id": event.session_id,
        "provider_record_id": _provider_record_id(event),
        "physical_index": event.event_index,
        "raw_json": json.dumps(raw_json, ensure_ascii=False, sort_keys=True),
        "raw_text_preview": _excerpt(redact_text(event.text), MAX_PREVIEW_CHARS),
        "raw_hash": raw_hash,
        "created_at": event.created_at,
    }


def _event_text_excerpt(event: TranscriptEvent) -> str:
    source = _event_user_input_text(event) or event.text
    return _excerpt(redact_text(source), MAX_EXCERPT_CHARS)


def _event_user_input_text(event: TranscriptEvent) -> str | None:
    if event.role != "user":
        return None
    metadata_input = event.metadata.get("user_input_text") or event.metadata.get("codex_prompt")
    if metadata_input:
        return _excerpt(redact_text(str(metadata_input)), MAX_EXCERPT_CHARS)
    extracted = extract_user_input_text(event.text)
    return _excerpt(redact_text(extracted), MAX_EXCERPT_CHARS) if extracted else None


def _tool_call(
    event: TranscriptEvent,
    normalized: dict[str, Any],
    turn_id: str,
    command: str,
    status: str | None,
) -> dict[str, Any]:
    tool_name = _tool_name(event, command)
    return {
        "tool_call_id": _stable_id("tool_call", normalized["source_ref"], command),
        "session_id": event.session_id,
        "event_id": normalized["event_id"],
        "turn_id": turn_id,
        "tool_name": tool_name,
        "command": command,
        "arguments_json": json.dumps({"command": command}, ensure_ascii=False, sort_keys=True),
        "cwd": event.metadata.get("cwd"),
        "started_at": event.created_at,
        "status": status or "unknown",
    }


def _tool_result(
    event: TranscriptEvent,
    normalized: dict[str, Any],
    tool_call_id: str,
    status: str | None,
) -> dict[str, Any]:
    exit_code = _exit_code(event)
    return {
        "tool_result_id": _stable_id("tool_result", normalized["source_ref"], event.text),
        "session_id": event.session_id,
        "tool_call_id": tool_call_id,
        "event_id": normalized["event_id"],
        "exit_code": exit_code,
        "stdout_preview": _excerpt(str(event.metadata.get("stdout") or event.text), MAX_PREVIEW_CHARS),
        "stderr_preview": _excerpt(str(event.metadata.get("stderr") or ""), MAX_PREVIEW_CHARS),
        "duration_ms": None,
        "status": status or ("failed" if exit_code and exit_code != 0 else "unknown"),
        "error_type": _error_type(event.text, status),
        "output_hash": _stable_id("output", event.text),
    }


def _file_ref(
    event: TranscriptEvent,
    normalized: dict[str, Any],
    path: str,
    index: int,
    command: str | None,
    event_type: str,
) -> dict[str, Any]:
    path_role = _path_role(path, command, event_type)
    return {
        "file_ref_id": _stable_id("file_ref", normalized["source_ref"], index, path, path_role),
        "session_id": event.session_id,
        "event_id": normalized["event_id"],
        "path": path,
        "path_role": path_role,
        "line_start": None,
        "line_end": None,
        "language": _language_for_path(path),
        "operation": path_role,
    }


def _test_ref(
    event: TranscriptEvent,
    normalized: dict[str, Any],
    tool_call_id: str | None,
    command: str | None,
    status: str | None,
) -> dict[str, Any]:
    test_status = "failed" if _is_error_event(event, status) else "passed" if status == "ok" else "unknown"
    return {
        "test_ref_id": _stable_id("test_ref", normalized["source_ref"], command or event.text),
        "session_id": event.session_id,
        "event_id": normalized["event_id"],
        "tool_call_id": tool_call_id,
        "command": command,
        "framework": _test_framework(command or event.text),
        "status": test_status,
        "failure_count": 1 if test_status == "failed" else 0,
        "summary": _excerpt(redact_text(event.text), MAX_PREVIEW_CHARS),
    }


def _error_ref(
    event: TranscriptEvent,
    normalized: dict[str, Any],
    tool_call_id: str | None,
    status: str | None,
) -> dict[str, Any]:
    return {
        "error_ref_id": _stable_id("error_ref", normalized["source_ref"], event.text),
        "session_id": event.session_id,
        "event_id": normalized["event_id"],
        "tool_call_id": tool_call_id,
        "error_type": _error_type(event.text, status),
        "message": _excerpt(redact_text(event.text), MAX_PREVIEW_CHARS),
        "stack_preview": _stack_preview(event.text),
        "is_recovered": False,
    }


def _user_correction(event: TranscriptEvent, normalized: dict[str, Any]) -> dict[str, Any]:
    summary = _event_user_input_text(event) or event.text
    return {
        "correction_id": _stable_id("correction", normalized["source_ref"], summary),
        "session_id": event.session_id,
        "event_id": normalized["event_id"],
        "turn_id": normalized["turn_id"],
        "correction_type": _correction_type(summary),
        "target_event_ids": json.dumps([], ensure_ascii=False),
        "summary": _excerpt(redact_text(summary), MAX_PREVIEW_CHARS),
    }


def _edge(session_id: str, from_id: str, from_type: str, to_id: str, to_type: str, relation: str) -> dict[str, Any]:
    return {
        "edge_id": _stable_id("edge", from_type, from_id, relation, to_type, to_id),
        "session_id": session_id,
        "from_type": from_type,
        "from_id": from_id,
        "to_type": to_type,
        "to_id": to_id,
        "relation": relation,
        "confidence": 1.0,
        "created_at": now_utc(),
    }


def _delete_graph(conn: sqlite3.Connection, session_id: str) -> None:
    for table in (
        "normalization_runs",
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


def _has_current_transcript_graph(conn: sqlite3.Connection, session_id: str) -> bool:
    existing = conn.execute(
        "SELECT COUNT(*) AS count FROM normalized_events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not existing or int(existing["count"]) <= 0:
        return False
    run = conn.execute(
        """
        SELECT graph_schema_version, normalizer_version
        FROM normalization_runs
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    return bool(
        run
        and run["graph_schema_version"] == GRAPH_SCHEMA_VERSION
        and run["normalizer_version"] == NORMALIZER_VERSION
    )


def _insert_many(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [[_sqlite_value(row.get(column)) for column in columns] for row in rows],
    )


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


def _rows(
    conn: sqlite3.Connection,
    table: str,
    where: str,
    params: tuple[Any, ...],
    *,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {table} WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    return [_row_payload(row) for row in conn.execute(sql, params).fetchall()]


def _graph_session_payload(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"No session found for `{session_id}`.")
    return _row_payload(row)


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=str(row["session_id"]),
        source_path=str(row["source_path"]),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        title=str(row["title"]),
        tool=str(row["tool"]),
        message_count=int(row["message_count"]),
        user_message_count=int(row["user_message_count"]),
        assistant_message_count=int(row["assistant_message_count"]),
        command_count=int(row["command_count"]),
        error_count=int(row["error_count"]),
        raw_preview=str(row["raw_preview"]),
        id=_row_value(row, "id"),
        source=_row_value(row, "source"),
        project_path=_row_value(row, "project_path"),
        transcript_path=_row_value(row, "transcript_path"),
        ended_at=_row_value(row, "ended_at"),
        model=_row_value(row, "model"),
        status=_row_value(row, "status"),
        raw_hash=_row_value(row, "raw_hash"),
    )


def _event_from_row(row: sqlite3.Row) -> TranscriptEvent:
    return TranscriptEvent(
        session_id=str(row["session_id"]),
        event_index=int(row["event_index"]),
        role=str(row["role"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        created_at=row["created_at"],
    )


def _row_value(row: sqlite3.Row, name: str) -> str | None:
    return row[name] if name in row.keys() and row[name] is not None else None


def _node_for_ref(conn: sqlite3.Connection, session_id: str, ref: str) -> dict[str, str] | None:
    event = conn.execute(
        "SELECT event_id FROM normalized_events WHERE session_id = ? AND source_ref = ? LIMIT 1",
        (session_id, ref),
    ).fetchone()
    if event:
        return {"type": "event", "id": str(event["event_id"])}
    for table, node_type, id_col in (
        ("tool_calls", "tool_call", "tool_call_id"),
        ("tool_results", "tool_result", "tool_result_id"),
        ("file_refs", "file_ref", "file_ref_id"),
        ("test_refs", "test_ref", "test_ref_id"),
        ("error_refs", "error_ref", "error_ref_id"),
        ("user_corrections", "user_correction", "correction_id"),
    ):
        row = conn.execute(
            f"""
            SELECT t.{id_col}
            FROM {table} t
            JOIN normalized_events e ON e.event_id = t.event_id
            WHERE t.session_id = ? AND e.source_ref = ?
            LIMIT 1
            """,
            (session_id, ref),
        ).fetchone()
        if row:
            return {"type": node_type, "id": str(row[id_col])}
    return None


def _lineage_edges(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    from_type: str | None = None,
    from_id: str | None = None,
    to_type: str | None = None,
    to_id: str | None = None,
) -> list[sqlite3.Row]:
    clauses = ["session_id = ?"]
    params: list[Any] = [session_id]
    if from_type is not None:
        clauses.append("from_type = ?")
        params.append(from_type)
    if from_id is not None:
        clauses.append("from_id = ?")
        params.append(from_id)
    if to_type is not None:
        clauses.append("to_type = ?")
        params.append(to_type)
    if to_id is not None:
        clauses.append("to_id = ?")
        params.append(to_id)
    return list(conn.execute(f"SELECT * FROM transcript_edges WHERE {' AND '.join(clauses)}", params).fetchall())


def _edge_endpoint(conn: sqlite3.Connection, edge: sqlite3.Row, *, upstream: bool) -> dict[str, Any]:
    node_type = str(edge["from_type"] if upstream else edge["to_type"])
    node_id = str(edge["from_id"] if upstream else edge["to_id"])
    return {"type": node_type, "id": node_id, "relation": edge["relation"], "node": _node_payload(conn, node_type, node_id)}


def _node_payload(conn: sqlite3.Connection, node_type: str, node_id: str) -> dict[str, Any] | None:
    mapping = {
        "raw_record": ("raw_records", "raw_record_id"),
        "event": ("normalized_events", "event_id"),
        "tool_call": ("tool_calls", "tool_call_id"),
        "tool_result": ("tool_results", "tool_result_id"),
        "file_ref": ("file_refs", "file_ref_id"),
        "test_ref": ("test_refs", "test_ref_id"),
        "error_ref": ("error_refs", "error_ref_id"),
        "user_correction": ("user_corrections", "correction_id"),
    }
    if node_type not in mapping:
        return None
    table, id_col = mapping[node_type]
    row = conn.execute(f"SELECT * FROM {table} WHERE {id_col} = ? LIMIT 1", (node_id,)).fetchone()
    return _row_payload(row) if row else None


def _evidence_for_node(conn: sqlite3.Connection, session_id: str, node: dict[str, str]) -> list[dict[str, Any]]:
    if node["type"] == "event":
        row = conn.execute(
            "SELECT source_ref, text_excerpt, user_input_text FROM normalized_events WHERE session_id = ? AND event_id = ? LIMIT 1",
            (session_id, node["id"]),
        ).fetchone()
        return [_row_payload(row)] if row else []
    event = _node_payload(conn, node["type"], node["id"])
    event_id = event.get("event_id") if event else None
    if not event_id:
        return []
    row = conn.execute(
        "SELECT source_ref, text_excerpt, user_input_text FROM normalized_events WHERE session_id = ? AND event_id = ? LIMIT 1",
        (session_id, event_id),
    ).fetchone()
    return [_row_payload(row)] if row else []


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    payload = {key: row[key] for key in row.keys()}
    for key in ("raw_record_ids", "source_refs", "raw_artifact_ids"):
        if isinstance(payload.get(key), str):
            try:
                payload[key] = json.loads(payload[key])
            except json.JSONDecodeError:
                pass
    return payload


def _source_type(session: SessionRecord) -> str:
    return str(session.source or session.tool or "unknown")


def _provider_record_id(event: TranscriptEvent) -> str | None:
    for key in ("uuid", "requestId", "parentUuid", "id"):
        if event.metadata.get(key):
            return str(event.metadata[key])
    return None


def _command_for_event(event: TranscriptEvent) -> str | None:
    command = event.metadata.get("command")
    if command:
        return str(command)
    match = re.search(r"(?:^|\n)command=([^\n]+)", event.text)
    if match:
        return match.group(1).strip()
    if event.kind.lower() in {"exec", "exec_command", "command", "tool_call", "patch"}:
        return _excerpt(event.text, 240)
    return None


def _status_for_event(event: TranscriptEvent, command: str | None) -> str | None:
    exit_code = _exit_code(event)
    if exit_code == 0:
        return "ok"
    if exit_code is not None and exit_code != 0:
        return "failed"
    lowered = event.text.lower()
    if "process exited with code 0" in lowered or "exit code 0" in lowered:
        return "ok"
    if not command and "tool" not in event.kind.lower() and "command" not in event.kind.lower():
        return None
    if any(term in lowered for term in ("failed", "error", "exception", "traceback", "assertionerror")):
        return "failed"
    return "unknown"


def _event_type(event: TranscriptEvent, command: str | None, status: str | None) -> str:
    item_type = _codex_item_type(event)
    parsed_cmd_type = _parsed_cmd_type(event)
    if _looks_like_context_event(event):
        return "context"
    if _is_user_correction(event):
        return "user_correction"
    if item_type == "reasoning" or event.kind == "reasoning":
        return "reasoning"
    if item_type == "patch" or event.kind == "patch":
        return "patch"
    if item_type in {"exec", "exploration"} and (
        parsed_cmd_type in {"read", "search", "list_files"} or _looks_like_read_command(command or "")
    ):
        return "exploration"
    if command and _is_test_event(event, command):
        return "test_run"
    if _is_error_event(event, status):
        return "error"
    if command:
        lowered = command.lower()
        if _looks_like_read_command(command):
            return "file_read"
        if any(term in lowered for term in PATCH_TERMS):
            return "patch"
        return "tool_call"
    if event.role == "assistant" and _looks_like_final_response(event.text):
        return "final_response"
    return "message"


def _phase_for_event(event: TranscriptEvent, event_type: str, command: str | None, status: str | None) -> str:
    if event_type == "context":
        return "context"
    if event_type == "user_correction":
        return "user_correction"
    if event_type == "reasoning":
        return "reasoning"
    if event_type == "exploration":
        return "exploration"
    if event.role == "user":
        return "user_request"
    if event_type == "error" or status == "failed":
        return "failure_retry"
    if event_type == "patch":
        return "patch"
    if event_type == "test_run" or _is_test_event(event, command):
        return "verification"
    if not command and _has_verification_text(event.text):
        return "verification"
    if command:
        return "tool_execution"
    if event_type == "final_response":
        return "final_response"
    return "planning"


def _file_refs(text: str, command: str | None) -> list[str]:
    refs = PATH_RE.findall(" ".join([command or "", text]))
    unique: list[str] = []
    for ref in refs:
        if ref.startswith(("http://", "https://")):
            continue
        if _looks_like_url_path_ref(ref):
            continue
        if ref not in unique:
            unique.append(ref)
    return unique[:32]


def _looks_like_url_path_ref(ref: str) -> bool:
    head = ref.split("/", 1)[0]
    return head.isdigit() or bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,3}:?\d*", head))


def _path_role(path: str, command: str | None, event_type: str) -> str:
    if event_type == "test_run" or "test" in path:
        return "test"
    if event_type == "patch":
        return "patch"
    if command and _looks_like_read_command(command):
        return "read"
    if command and any(term in command.lower() for term in ("write", "apply_patch", "tee")):
        return "write"
    if event_type == "error":
        return "error"
    return "mention"


def _file_relation(path_role: str) -> str:
    return {
        "patch": "patches",
        "test": "tests",
        "error": "fails",
    }.get(path_role, "mentions")


def _is_test_event(event: TranscriptEvent, command: str | None) -> bool:
    if not command:
        return False
    text = " ".join([command or "", event.text]).lower()
    return count_terms(text, TEST_TERMS) > 0 or any(term.lower() in text for term in VERIFICATION_TERMS)


def _has_verification_text(text: str) -> bool:
    lowered = text.lower()
    return "验证" in lowered or "verification" in lowered


def _is_error_event(event: TranscriptEvent, status: str | None) -> bool:
    if _looks_like_context_event(event):
        return False
    if status == "ok":
        return False
    if status == "failed":
        return True
    return _tool_like_event(event) and count_terms(event.text, ERROR_TERMS) > 0


def _is_user_correction(event: TranscriptEvent) -> bool:
    lowered = (_event_user_input_text(event) or event.text).lower()
    if event.role != "user":
        return False
    if any(term.lower() in lowered for term in USER_CORRECTION_TERMS):
        return True
    if any(term in lowered for term in SCOPE_CORRECTION_TERMS):
        compact = re.sub(r"\s+", "", lowered)
        return any(term in compact for term in ("不是", "而是", "要跟", "单独", "范围", "我的意思"))
    return False


def _correction_type(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("我说的是", "我的意思", "missed", "forgot")):
        return "missed_requirement"
    if any(term in lowered for term in ("不要", "scope", "范围")):
        return "scope_change"
    if any(term in lowered for term in ("不对", "错", "wrong")):
        return "bad_result"
    return "other"


def _error_type(text: str, status: str | None) -> str:
    lowered = text.lower()
    if "assertion" in lowered or "pytest" in lowered or "test" in lowered:
        return "test_failure"
    if count_terms(lowered, SANDBOX_TERMS) > 0:
        return "sandbox"
    if any(term in lowered for term in NETWORK_TERMS):
        return "network"
    if "permission" in lowered:
        return "permission"
    if "json" in lowered:
        return "json_parse"
    if "timeout" in lowered:
        return "timeout"
    if "not found" in lowered:
        return "not_found"
    return "runtime_exception" if status == "failed" else "unknown"


def _exit_code(event: TranscriptEvent) -> int | None:
    raw = event.metadata.get("exit_code")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    match = re.search(r"(?:exit code|process exited with code)\s+(-?\d+)", event.text, re.I)
    if match:
        return int(match.group(1))
    return None


def _looks_like_context_event(event: TranscriptEvent) -> bool:
    if event.role in {"system", "developer"}:
        return True
    lowered = event.text.strip().lower()
    if not lowered:
        return False
    if event.role == "user" and _event_user_input_text(event) is None:
        return True
    return (
        lowered.startswith("<environment_context>")
        or lowered.startswith("<permissions")
        or lowered.startswith("<collaboration_mode>")
        or lowered.startswith("<skills_instructions>")
        or "knowledge cutoff" in lowered[:240]
        or "sandbox_mode" in lowered[:600]
    )


def _tool_like_event(event: TranscriptEvent) -> bool:
    kind = event.kind.lower()
    lowered = event.text.strip().lower()
    return (
        bool(event.metadata.get("command"))
        or event.role == "tool"
        or "tool" in kind
        or "command" in kind
        or lowered.startswith("chunk id:")
        or "process exited with code" in lowered[:240]
        or "apply_patch verification failed" in lowered[:240]
    )


def _tool_name(event: TranscriptEvent, command: str) -> str:
    if event.metadata.get("tool_name"):
        return str(event.metadata["tool_name"])
    first = command.strip().split(maxsplit=1)[0] if command.strip() else event.kind
    return first


def _codex_item_type(event: TranscriptEvent) -> str:
    return str(event.metadata.get("codex_item_type") or event.kind or "").lower()


def _parsed_cmd_type(event: TranscriptEvent) -> str:
    return str(event.metadata.get("parsed_cmd_type") or "").lower()


def _test_framework(text: str) -> str:
    lowered = text.lower()
    if "pytest" in lowered:
        return "pytest"
    if "unittest" in lowered:
        return "unittest"
    if "npm" in lowered:
        return "npm"
    if "vite" in lowered:
        return "vite"
    if "ruff" in lowered:
        return "ruff"
    if "mypy" in lowered:
        return "mypy"
    return "unknown"


def _language_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".md": "markdown",
        ".json": "json",
    }.get(suffix)


def _looks_like_read_command(command: str) -> bool:
    return command.strip().lower().startswith(("sed ", "cat ", "rg ", "grep ", "less ", "head ", "tail ", "nl "))


def _looks_like_final_response(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("完成", "已", "summary", "done", "final", "验证"))


def _stack_preview(text: str) -> str | None:
    if "traceback" not in text.lower():
        return None
    return _excerpt(redact_text(text), MAX_PREVIEW_CHARS)


def _stable_id(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:32]

def _excerpt(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
