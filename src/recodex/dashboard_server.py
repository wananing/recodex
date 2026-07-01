from __future__ import annotations

import json
import hashlib
import mimetypes
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from recodex.dashboard_services import (
    artifact_effectiveness,
    artifact_export,
    artifact_preview,
    generate_session_report,
    get_dashboard_llm_settings,
    list_dashboard_improvements,
    list_dashboard_reports,
    list_report_artifact_candidates,
    report_content,
    review_report_artifact_candidate,
    run_dashboard_analysis,
    save_dashboard_llm_settings,
    set_dashboard_improvement_status,
)
from recodex.db import (
    connect,
    count_catalog_entries,
    count_sessions,
    get_session,
    list_catalog_entries,
    list_catalog_projects,
    list_catalog_projects_for_source,
    list_improvements,
    list_session_projects,
    list_sessions,
    search_events,
)
from recodex.importers import get_importer
from recodex.mining_review import load_mining_review
from recodex.paths import db_path as default_db_path
from recodex.privacy import redact_text
from recodex.provider_assets import discover_providers, list_provider_assets
from recodex.sync import CatalogReport, ImportReport, sync_catalog_paths, sync_import_paths
from recodex.transcript_graph import get_transcript_graph, get_transcript_lineage
from recodex.watch import (
    WatchSource,
    add_watch_source,
    get_watch_source,
    list_watch_sources,
    run_enabled_watch_sources,
    run_watch_source,
)


@dataclass(frozen=True)
class DashboardResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


class DashboardApp:
    def __init__(self, *, db_path: Path, dashboard_dir: Path | None) -> None:
        self.db_path = db_path
        self.dashboard_dir = dashboard_dir
        self.analysis_jobs = AnalysisJobStore()

    def handle_get(self, path: str) -> DashboardResponse:
        parsed = urlparse(path)
        route = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        try:
            if route == "/health":
                return self.json({"ok": True, "app": "recodex", "db": str(self.db_path)})
            if route == "/overview":
                return self.json(_overview_payload(self.db_path))
            if route == "/projects":
                return self.json(_projects_payload(self.db_path))
            if route == "/sessions":
                return self.json(_sessions_payload(self.db_path, query))
            if route == "/catalog/projects":
                return self.json(_catalog_projects_payload(self.db_path, query))
            if route == "/catalog/sessions":
                return self.json(_catalog_sessions_payload(self.db_path, query))
            if route == "/sessions/search":
                return self.json(_session_search_payload(self.db_path, query))
            if route == "/providers":
                return self.json(_providers_payload(self.db_path))
            if route.startswith("/providers/"):
                return self.json(_provider_assets_route(self.db_path, route, query))
            if route == "/mining/review":
                return self.json(_mining_review_payload(self.db_path, query))
            if route.startswith("/transcripts/"):
                return self.json(_transcript_graph_route(self.db_path, route, query))
            if route in {"/watch", "/watch/status"}:
                return self.json(_watch_status_payload(self.db_path))
            if route == "/settings/llm":
                return self.json(get_dashboard_llm_settings(self.db_path))
            if route == "/reports":
                return self.json({"ok": True, "reports": list_dashboard_reports(self.db_path)})
            if route.startswith("/analysis/jobs/"):
                return self.json({"ok": True, "job": self.analysis_jobs.get(_job_id_from_route(route))})
            if route.startswith("/reports/"):
                return self.json(_report_content_route(self.db_path, route))
            if route == "/artifacts/effectiveness":
                return self.json(artifact_effectiveness(self.db_path))
            if route == "/improvements":
                return self.json(
                    {
                        "ok": True,
                        "improvements": list_dashboard_improvements(
                            self.db_path,
                            status=query.get("status"),
                        ),
                    }
                )
            if route == "/artifacts/preview":
                improvement_id = int(query["improvement_id"]) if query.get("improvement_id") else None
                return self.json(
                    artifact_preview(
                        self.db_path,
                        artifact_type=str(query.get("type") or "skill"),
                        improvement_id=improvement_id,
                        report_id=query.get("report_id"),
                        artifact_id=query.get("artifact_id"),
                    )
                )
            return self.static(route)
        except ValueError as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except sqlite3.Error as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        except OSError as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_post(self, path: str, payload: dict[str, Any]) -> DashboardResponse:
        parsed = urlparse(path)
        route = parsed.path
        try:
            if route == "/catalog/scan":
                return self.json(_catalog_scan(self.db_path, payload))
            if route == "/catalog/import":
                return self.json(_catalog_import(self.db_path, payload))
            if route == "/import/run":
                return self.json(_import_run(self.db_path, payload))
            if route == "/watch/add":
                return self.json({"source": _watch_source_payload(_watch_add(self.db_path, payload))})
            if route == "/watch/run":
                return self.json(_watch_run(self.db_path, payload))
            if route == "/skills/export":
                return self.json(_skills_export(self.db_path, payload))
            if route == "/settings/llm":
                return self.json(save_dashboard_llm_settings(self.db_path, payload))
            if route == "/reports/generate":
                return self.json(
                    {
                        "ok": True,
                        "report": generate_session_report(
                            self.db_path,
                            target=str(payload.get("target") or payload.get("session_id") or "latest"),
                            project_path=_project_path_from_payload(payload),
                            report_dir=_optional_path(payload.get("reports_dir")),
                            llm_settings=payload.get("llm") if isinstance(payload.get("llm"), dict) else None,
                        ),
                    }
                )
            if route == "/analysis/run":
                return self.json(run_dashboard_analysis(self.db_path, payload))
            if route == "/analysis/jobs":
                return self.json({"ok": True, "job": self.analysis_jobs.start(self.db_path, payload)})
            if route.startswith("/improvements/"):
                return self.json(_improvement_action_route(self.db_path, route))
            if route == "/artifacts/review":
                return self.json(
                    {
                        "ok": True,
                        "artifact": review_report_artifact_candidate(self.db_path, payload),
                    }
                )
            if route == "/artifacts/export":
                return self.json(artifact_export(self.db_path, payload))
            return self.json(
                {"ok": False, "error": f"Unknown API route: {route}"},
                status=HTTPStatus.NOT_FOUND,
            )
        except ValueError as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except sqlite3.Error as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        except OSError as exc:
            return self.json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def static(self, request_path: str) -> DashboardResponse:
        root = self.dashboard_dir
        if root is None:
            return self.json(
                {"ok": False, "error": "Dashboard build not found. Run `make dashboard-build`."},
                status=HTTPStatus.NOT_FOUND,
            )
        normalized = unquote(request_path.lstrip("/"))
        if normalized in {"", "/"}:
            file_path = root / "index.html"
        else:
            file_path = (root / normalized).resolve()
            if not _is_relative_to(file_path, root) or not file_path.exists() or file_path.is_dir():
                file_path = root / "index.html"
        if not file_path.exists():
            return self.json(
                {"ok": False, "error": "Dashboard index.html not found. Run `make dashboard-build`."},
                status=HTTPStatus.NOT_FOUND,
            )
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return DashboardResponse(HTTPStatus.OK, mime, file_path.read_bytes())

    def json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> DashboardResponse:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return DashboardResponse(status, "application/json; charset=utf-8", body)


class AnalysisJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
        job_type = str(payload.get("type") or payload.get("job_type") or "analysis")
        if job_type not in {"analysis", "report"}:
            raise ValueError(f"Unsupported analysis job type: {job_type}")
        job_id = f"job_{uuid.uuid4().hex[:24]}"
        now = _now_iso()
        job = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "phase": "queued",
            "message": _job_message(job_type, "queued"),
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "finished_at": None,
            "elapsed_ms": 0,
            "request": _job_request_payload(payload),
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job_id, state_db, dict(payload)), daemon=True)
        thread.start()
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"No analysis job found for `{job_id}`.")
            return _job_public_payload(job)

    def _run(self, job_id: str, state_db: Path, payload: dict[str, Any]) -> None:
        job_type = str(payload.get("type") or payload.get("job_type") or "analysis")
        self._patch(
            job_id,
            status="running",
            phase="running",
            message=_job_message(job_type, "running"),
            started_at=_now_iso(),
        )
        try:
            if job_type == "report":
                report = generate_session_report(
                    state_db,
                    target=str(payload.get("target") or payload.get("session_id") or "latest"),
                    project_path=_project_path_from_payload(payload),
                    report_dir=_optional_path(payload.get("reports_dir")),
                    llm_settings=_report_job_llm_settings(payload),
                )
                result: dict[str, Any] = {"ok": True, "report": report}
            else:
                result = run_dashboard_analysis(state_db, payload)
            self._patch(
                job_id,
                status="succeeded",
                phase="done",
                message=_job_message(job_type, "succeeded"),
                result=result,
                finished_at=_now_iso(),
            )
        except Exception as exc:
            self._patch(
                job_id,
                status="failed",
                phase="error",
                message=_job_message(job_type, "failed"),
                error=str(exc),
                finished_at=_now_iso(),
            )

    def _patch(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(updates)
            job["updated_at"] = _now_iso()
            job["elapsed_ms"] = _elapsed_ms(job)


def _job_id_from_route(route: str) -> str:
    parts = [part for part in route.split("/") if part]
    if len(parts) != 3 or parts[0] != "analysis" or parts[1] != "jobs":
        raise ValueError(f"Unsupported analysis job route: {route}")
    return unquote(parts[2])


def _job_public_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job)
    payload["elapsed_ms"] = _elapsed_ms(job)
    return payload


def _job_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"llm"} and isinstance(value, (str, int, float, bool, type(None)))
    }


def _report_job_llm_settings(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("llm")
    if isinstance(raw, dict):
        return raw
    return {}


def _project_path_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("project_path") or payload.get("project")
    if raw is None:
        return None
    value = str(raw).strip()
    return value if value and value != "all" else None


def _source_filter_from_query(query: dict[str, str] | None) -> str | None:
    raw = (query or {}).get("source")
    if raw is None or raw == "auto":
        return None
    return get_importer(raw).name


def _job_message(job_type: str, status: str) -> str:
    if status == "queued":
        return "Job queued."
    if status == "running":
        return "Generating core report." if job_type == "report" else "Running analysis workflow."
    if status == "succeeded":
        return "Report job completed." if job_type == "report" else "Analysis job completed."
    if status == "failed":
        return "Report job failed." if job_type == "report" else "Analysis job failed."
    return status


def _elapsed_ms(job: dict[str, Any]) -> int:
    started = job.get("started_at")
    if not isinstance(started, str) or not started:
        return 0
    end = job.get("finished_at") if isinstance(job.get("finished_at"), str) else _now_iso()
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = datetime.fromisoformat(str(end))
    except ValueError:
        return int(job.get("elapsed_ms") or 0)
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_dashboard_server(
    *,
    db_path: Path | str | None = None,
    dashboard_dir: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    state_db = Path(db_path).expanduser().resolve() if db_path else default_db_path()
    static_root = _dashboard_dir(Path(dashboard_dir).expanduser() if dashboard_dir else None)
    app = DashboardApp(db_path=state_db, dashboard_dir=static_root)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "recodex-dashboard/0.2.0"

        def do_OPTIONS(self) -> None:
            self._send_bytes(b"", status=HTTPStatus.NO_CONTENT)

        def do_GET(self) -> None:
            self._send_response(app.handle_get(self.path))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                payload = self._read_json()
                self._send_response(app.handle_post(path, payload))
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON request body: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON request body must be an object.")
            return data

        def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                status=status,
                content_type="application/json; charset=utf-8",
            )

        def _send_response(self, response: DashboardResponse) -> None:
            self._send_bytes(
                response.body,
                status=response.status,
                content_type=response.content_type,
            )

        def _send_bytes(
            self,
            body: bytes,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            content_type: str = "text/plain; charset=utf-8",
        ) -> None:
            self.send_response(int(status))
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type")
            self.end_headers()
            if body:
                self.wfile.write(body)

    return ThreadingHTTPServer((host, port), DashboardHandler)


def serve_dashboard(
    *,
    db_path: Path | str | None = None,
    dashboard_dir: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    server = create_dashboard_server(
        db_path=db_path,
        dashboard_dir=dashboard_dir,
        host=host,
        port=port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _overview_payload(state_db: Path) -> dict[str, Any]:
    conn = connect(state_db)
    proposed = len(list_improvements(conn, status="proposed"))
    accepted = len(list_improvements(conn, status="accepted"))
    return {
        "ok": True,
        "sessions": count_sessions(conn),
        "catalog_sessions": count_catalog_entries(conn),
        "projects": len(list_session_projects(conn)),
        "catalog_projects": len(list_catalog_projects(conn)),
        "improvements": {"proposed": proposed, "accepted": accepted},
        "watch_sources": len(list_watch_sources(conn)),
    }


def _projects_payload(state_db: Path) -> dict[str, Any]:
    conn = connect(state_db)
    return {
        "ok": True,
        "projects": [_project_payload(row) for row in list_session_projects(conn)],
    }


def _sessions_payload(state_db: Path, query: dict[str, str] | None = None) -> dict[str, Any]:
    conn = connect(state_db)
    project_path = (query or {}).get("project")
    return {
        "ok": True,
        "sessions": [
            _session_payload(session)
            for session in list_sessions(conn, project_path=project_path)
        ],
    }


def _catalog_projects_payload(state_db: Path, query: dict[str, str] | None = None) -> dict[str, Any]:
    conn = connect(state_db)
    source = _source_filter_from_query(query)
    return {
        "ok": True,
        "projects": [_catalog_project_payload(row) for row in list_catalog_projects_for_source(conn, source=source)],
    }


def _catalog_sessions_payload(state_db: Path, query: dict[str, str] | None = None) -> dict[str, Any]:
    conn = connect(state_db)
    raw_limit = (query or {}).get("limit")
    limit = int(raw_limit) if raw_limit else 200
    project_path = (query or {}).get("project")
    source = _source_filter_from_query(query)
    rows = list_catalog_entries(conn, project_path=project_path, source=source, limit=limit)
    imported_ids = {
        session.session_id
        for session in list_sessions(conn, project_path=project_path)
    }
    return {
        "ok": True,
        "sessions": [
            _catalog_session_payload(row, imported=bool(row["session_id"] in imported_ids))
            for row in rows
        ],
    }


def _session_search_payload(state_db: Path, query: dict[str, str]) -> dict[str, Any]:
    q = str(query.get("q") or query.get("query") or "").strip()
    raw_limit = query.get("limit")
    limit = int(raw_limit) if raw_limit else 50
    if not q:
        return {"ok": True, "query": q, "results": []}
    conn = connect(state_db)
    rows = search_events(conn, q, limit=limit)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        session_id = str(row["session_id"])
        grouped.setdefault(session_id, []).append(
            {
                "event_index": int(row["event_index"]),
                "role": row["role"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "text": _excerpt(str(row["text"] or ""), q),
            }
        )
    results = []
    for session_id, matches in grouped.items():
        session = get_session(conn, session_id)
        if session is None:
            continue
        results.append({"session": _session_payload(session), "matches": matches})
    return {"ok": True, "query": q, "results": results}


def _providers_payload(state_db: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "providers": [provider.to_payload() for provider in discover_providers(state_db=state_db)],
    }


def _provider_assets_route(state_db: Path, route: str, query: dict[str, str]) -> dict[str, Any]:
    parts = [part for part in route.split("/") if part]
    if len(parts) != 3 or parts[0] != "providers" or parts[2] != "assets":
        raise ValueError(f"Unsupported provider route: {route}")
    provider_id = unquote(parts[1])
    raw_limit = query.get("limit")
    limit = int(raw_limit) if raw_limit else 200
    assets = list_provider_assets(
        provider_id,
        query.get("type"),
        state_db=state_db,
        limit=limit,
    )
    return {
        "ok": True,
        "provider_id": provider_id,
        "asset_type": query.get("type") or "all",
        "assets": [asset.to_payload() for asset in assets],
    }


def _mining_review_payload(state_db: Path, query: dict[str, str]) -> dict[str, Any]:
    raw_limit = query.get("card_limit")
    card_limit = int(raw_limit) if raw_limit else 12
    payload = load_mining_review(
        reports_base=_optional_path(query.get("reports_dir")),
        output_dir=_optional_path(query.get("output_dir")),
        cluster_id=query.get("cluster_id"),
        card_limit=card_limit,
    )
    report_id = query.get("report_id")
    if report_id:
        candidates = list_report_artifact_candidates(state_db, report_id)
        payload["artifact_candidates"] = [
            *list(payload.get("artifact_candidates") or []),
            *candidates,
        ]
        payload["artifact_review_queue"] = [
            *list(payload.get("artifact_review_queue") or []),
            *[
                candidate
                for candidate in candidates
                if str(candidate.get("status") or "proposed") in {"proposed", "ready_for_review"}
            ],
        ]
    return payload


def _project_payload(row: sqlite3.Row) -> dict[str, Any]:
    project_path = str(row["project_path"] or "(unknown)")
    sources = [
        source
        for source in str(row["sources"] or "").split(",")
        if source
    ]
    return {
        "project_id": _project_id(project_path),
        "project_path": project_path,
        "project_name": _project_name(project_path),
        "session_count": int(row["session_count"] or 0),
        "command_count": int(row["command_count"] or 0),
        "error_count": int(row["error_count"] or 0),
        "latest_at": row["latest_at"],
        "sources": sources,
    }


def _catalog_project_payload(row: sqlite3.Row) -> dict[str, Any]:
    project_path = str(row["project_path"] or "(unknown)")
    sources = [
        source
        for source in str(row["sources"] or "").split(",")
        if source
    ]
    return {
        "project_id": _project_id(project_path),
        "project_path": project_path,
        "project_name": _project_name(project_path),
        "session_count": int(row["session_count"] or 0),
        "catalog_session_count": int(row["session_count"] or 0),
        "total_bytes": int(row["total_bytes"] or 0),
        "latest_at": row["latest_at"],
        "sources": sources or ["catalog"],
    }


def _session_payload(session: Any) -> dict[str, Any]:
    project_path = session.project_path or "(unknown)"
    return {
        "session_id": session.session_id,
        "source": session.source or session.tool,
        "title": session.title,
        "updated_at": session.updated_at,
        "command_count": session.command_count,
        "error_count": session.error_count,
        "project_id": _project_id(project_path),
        "project_path": project_path,
        "project_name": _project_name(project_path),
    }


def _catalog_session_payload(row: sqlite3.Row, *, imported: bool) -> dict[str, Any]:
    project_path = str(row["project_path"] or "(unknown)")
    return {
        "session_id": str(row["session_id"]),
        "source": row["source"] or "catalog",
        "title": str(row["title"] or row["session_id"]),
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "model": row["model"],
        "source_path": row["source_path"],
        "file_size": int(row["file_size"] or 0),
        "imported": imported,
        "command_count": 0,
        "error_count": 0,
        "project_id": _project_id(project_path),
        "project_path": project_path,
        "project_name": _project_name(project_path),
    }


def _project_id(project_path: str) -> str:
    digest = hashlib.sha256(project_path.encode("utf-8")).hexdigest()[:12]
    return f"project_{digest}"


def _project_name(project_path: str) -> str:
    if project_path == "(unknown)":
        return "(unknown)"
    name = Path(project_path).name
    return name or project_path


def _excerpt(text: str, query: str = "", limit: int = 320) -> str:
    cleaned = " ".join(redact_text(text).split())
    if len(cleaned) <= limit:
        return cleaned
    needle = query.lower().strip()
    index = cleaned.lower().find(needle) if needle else -1
    if index < 0:
        return cleaned[: limit - 1] + "…"
    start = max(0, index - limit // 3)
    end = min(len(cleaned), start + limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return prefix + cleaned[start:end] + suffix


def _transcript_graph_route(state_db: Path, route: str, query: dict[str, str]) -> dict[str, Any]:
    parts = [part for part in route.split("/") if part]
    if len(parts) != 3 or parts[0] != "transcripts":
        raise ValueError(f"Unsupported transcript route: {route}")
    _transcripts, session_id, action = parts
    session_id = unquote(session_id)
    conn = connect(state_db)
    if action == "graph":
        return {"ok": True, **get_transcript_graph(conn, session_id)}
    if action == "lineage":
        ref = query.get("ref")
        if not ref:
            raise ValueError("Missing required query parameter `ref`.")
        return {"ok": True, **get_transcript_lineage(conn, session_id, ref)}
    raise ValueError(f"Unsupported transcript action: {action}")


def _watch_status_payload(state_db: Path) -> dict[str, Any]:
    conn = connect(state_db)
    return {
        "ok": True,
        "sources": [_watch_source_payload(source) for source in list_watch_sources(conn)],
    }


def _catalog_scan(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "codex")
    raw_limit = payload.get("limit")
    limit = int(raw_limit) if raw_limit not in (None, "") else None
    importer = get_importer(source)
    path_value = str(payload.get("path") or "").strip()
    roots = [Path(path_value).expanduser()] if path_value else list(importer.default_roots)
    if not roots:
        raise ValueError(f"No default transcript roots found for `{source}`.")
    report = sync_catalog_paths(connect(state_db), importer, roots, limit=limit)
    return {"ok": True, **_catalog_report_payload(report)}


def _catalog_import(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "codex")
    project_path = _project_path_from_payload(payload)
    session_id = str(payload.get("session_id") or "").strip() or None
    raw_limit = payload.get("limit")
    limit = int(raw_limit) if raw_limit not in (None, "") else None
    if project_path is None and session_id is None:
        raise ValueError("Missing required field `project` or `session_id`.")
    conn = connect(state_db)
    importer = get_importer(source)
    rows = list_catalog_entries(
        conn,
        project_path=project_path,
        session_id=session_id,
        source=importer.name,
        limit=limit,
    )
    paths = [Path(str(row["source_path"])) for row in rows]
    report = sync_import_paths(conn, importer, paths)
    return {
        "ok": True,
        "selected": len(rows),
        **_report_payload(report),
    }


def _import_run(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "auto")
    path = _required_path(payload, "path")
    importer = get_importer(source)
    report = sync_import_paths(connect(state_db), importer, [path])
    return {"ok": True, **_report_payload(report)}


def _watch_add(state_db: Path, payload: dict[str, Any]) -> WatchSource:
    source = str(payload.get("source") or "codex")
    path = _required_path(payload, "path")
    scope = str(payload["scope"]) if payload.get("scope") else None
    enabled = bool(payload.get("enabled", True))
    return add_watch_source(
        connect(state_db),
        source=source,
        path=path,
        scope=scope,
        enabled=enabled,
    )


def _watch_run(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    conn = connect(state_db)
    source_id = payload.get("id")
    if source_id is not None:
        source = get_watch_source(conn, int(source_id))
        if source is None:
            raise ValueError(f"No watch source found for #{source_id}.")
        if not source.enabled:
            raise ValueError(f"Watch source #{source_id} is disabled.")
        results = [(source, run_watch_source(conn, source))]
    else:
        results = run_enabled_watch_sources(conn)
    return {
        "ok": True,
        "results": [
            {"source": _watch_source_payload(source), **_report_payload(report)}
            for source, report in results
        ],
    }


def _skills_export(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = artifact_export(state_db, {"type": "skill", **payload})
    return {
        "ok": True,
        "target": str(Path(str(payload.get("out") or "")).expanduser().resolve()) if payload.get("out") else "",
        "written": result["paths"],
        **result,
    }


def _report_payload(report: ImportReport) -> dict[str, Any]:
    return {
        "source": report.source,
        "scanned": report.scanned,
        "imported": report.imported,
        "skipped": report.skipped,
        "failed": report.failed,
        "errors": list(report.errors),
    }


def _catalog_report_payload(report: CatalogReport) -> dict[str, Any]:
    return {
        "source": report.source,
        "scanned": report.scanned,
        "cataloged": report.cataloged,
        "failed": report.failed,
        "errors": list(report.errors),
    }


def _watch_source_payload(source: WatchSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "source": source.source,
        "path": str(source.path),
        "scope": source.scope,
        "enabled": source.enabled,
        "last_sync_at": source.last_sync_at,
        "last_imported": source.last_imported,
        "last_skipped": source.last_skipped,
        "last_failed": source.last_failed,
        "last_error": source.last_error,
    }


def _required_path(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not value:
        raise ValueError(f"Missing required field `{key}`.")
    return Path(str(value)).expanduser()


def _report_content_route(state_db: Path, route: str) -> dict[str, Any]:
    parts = [part for part in route.split("/") if part]
    if len(parts) != 3:
        raise ValueError(f"Unsupported report route: {route}")
    _reports, report_id, content_type = parts
    return {"ok": True, **report_content(state_db, report_id, content_type)}


def _improvement_action_route(state_db: Path, route: str) -> dict[str, Any]:
    parts = [part for part in route.split("/") if part]
    if len(parts) != 3:
        raise ValueError(f"Unsupported improvement route: {route}")
    _improvements, raw_id, action = parts
    if action not in {"accept", "reject"}:
        raise ValueError(f"Unsupported improvement action: {action}")
    status = "accepted" if action == "accept" else "rejected"
    return {
        "ok": True,
        "improvement": set_dashboard_improvement_status(state_db, int(raw_id), status),
    }


def _optional_path(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser().resolve()


def _dashboard_dir(configured: Path | None) -> Path | None:
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.append(Path(__file__).resolve().parents[2] / "dashboard" / "dist")
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "index.html").exists():
            return resolved
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
