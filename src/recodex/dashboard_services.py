from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any

from recodex.analysis import mechanism_for_improvement_category, propose_improvements
from recodex.analysis_workflow import (
    WORKFLOW_PROMPT_VERSION,
    WORKFLOW_VERSION,
    WorkflowLLMStage,
    WorkflowStageOutput,
    build_workflow_llm_request,
    run_analysis_workflow,
    workflow_result_to_report_data,
    workflow_schema_version,
)
from recodex.db import (
    connect,
    find_cached_llm_output,
    get_events,
    get_improvement,
    get_session,
    get_setting,
    insert_improvements,
    insert_llm_job,
    insert_llm_output,
    list_improvements,
    list_sessions,
    now_utc,
    record_analysis_feedback,
    record_judge_failure,
    record_prompt_version,
    set_setting,
    update_improvement_status,
    update_llm_job_status,
)
from recodex.efficiency_analysis import run_efficiency_analysis
from recodex.exports.skill import render_skill_md, write_skill_md_exports_to_root
from recodex.html_report import build_session_report_data, write_report_html, write_report_json
from recodex.llm import (
    LLMResponseIncompleteError,
    SESSION_RETRO_MAX_OUTPUT_TOKENS,
    build_session_retro_request,
    default_model_for_provider,
    generate_session_retro_analysis,
    llm_cached_usage_payload,
    llm_token_usage_report,
    llm_usage_has_tokens,
    llm_usage_payload,
    normalize_provider_name,
    parse_llm_usage_json,
    provider_for_name,
    validate_session_retro_output,
)
from recodex.paths import exports_dir, reports_dir
from recodex.privacy import redact_text
from recodex.reports import (
    improvements_report_path,
    patterns_report_path,
    render_agents_patch,
    render_checklist_export,
    render_ci_rule_export,
    render_improvements,
    render_patterns,
    render_retro,
    render_retro_with_findings,
    retro_report_path,
    write_checklist_export,
    write_ci_rule_export,
    write_scripts_export,
    write_text,
)

LLM_SETTINGS_KEY = "llm_config"
SUPPORTED_DASHBOARD_LLM_PROVIDERS = {
    "mock",
    "openai",
    "openai-compatible",
    "volcengine",
    "dashscope",
    "siliconflow",
}


def list_dashboard_reports(state_db: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect(state_db)
    rows = conn.execute(
        """
        SELECT *
        FROM generated_reports
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_report_row(row) for row in rows]


def generate_session_report(
    state_db: Path,
    *,
    target: str = "latest",
    project_path: str | None = None,
    report_dir: Path | None = None,
    llm_settings: dict[str, Any] | None = None,
    deep: bool = True,
) -> dict[str, Any]:
    conn = connect(state_db)
    session = _session_for_target(conn, target, project_path=project_path)
    if session is None:
        raise ValueError(_no_session_message(project_path))
    events = get_events(conn, session.session_id)
    root = report_dir or reports_dir(None)
    markdown_path = retro_report_path(root, session)
    resolved_llm = _llm_settings_for_run(conn, llm_settings)
    analysis = _run_llm_session_retro(conn, session, events, resolved_llm) if resolved_llm["enabled"] else None
    write_text(
        markdown_path,
        render_retro_with_findings(session, events, analysis) if analysis else render_retro(session, events),
    )
    report_data = build_session_report_data(session, events, analysis, deep=deep)
    json_path = write_report_json(markdown_path.with_suffix(".json"), report_data)
    html_path = write_report_html(markdown_path.with_suffix(".html"), report_data)
    report_id = str(report_data["meta"]["report_id"])
    _upsert_report(
        conn,
        report_id=report_id,
        kind="session",
        session_id=session.session_id,
        project_path=session.project_path,
        title=session.title,
        html_path=html_path,
        markdown_path=markdown_path,
        json_path=json_path,
        created_at=str(report_data["meta"]["generated_at"]),
    )
    return _report_row_by_id(conn, report_id)


def get_dashboard_llm_settings(state_db: Path) -> dict[str, Any]:
    conn = connect(state_db)
    return {"ok": True, "settings": _public_llm_settings(_raw_llm_settings(conn))}


def save_dashboard_llm_settings(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    conn = connect(state_db)
    current = _raw_llm_settings(conn)
    next_settings = _merge_llm_settings(current, payload, include_secret=True)
    set_setting(conn, LLM_SETTINGS_KEY, json.dumps(next_settings, ensure_ascii=False, sort_keys=True))
    return {"ok": True, "settings": _public_llm_settings(next_settings)}


def report_content(state_db: Path, report_id: str, content_type: str) -> dict[str, Any]:
    conn = connect(state_db)
    row = _report_db_row(conn, report_id)
    if row is None:
        raise ValueError(f"No report found for `{report_id}`.")
    key = {
        "html": "html_path",
        "markdown": "markdown_path",
        "md": "markdown_path",
        "json": "json_path",
    }.get(content_type)
    if key is None:
        raise ValueError(f"Unsupported report content type: {content_type}")
    path = Path(str(row[key] or ""))
    if not path.exists():
        raise ValueError(f"Report file is missing: {path}")
    return {
        "id": report_id,
        "content_type": "markdown" if content_type == "md" else content_type,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def _project_path_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("project_path") or payload.get("project")
    if raw is None:
        return None
    value = str(raw).strip()
    return value if value and value != "all" else None


def _session_for_target(conn: Connection, target: str, *, project_path: str | None = None) -> Any | None:
    scoped_project = _project_path_from_payload({"project_path": project_path})
    if target == "latest" and scoped_project:
        sessions = list_sessions(conn, project_path=scoped_project)
        return sessions[0] if sessions else None
    session = get_session(conn, target)
    if session is not None and scoped_project and (session.project_path or "(unknown)") != scoped_project:
        raise ValueError("Selected session does not belong to the selected project.")
    return session


def _no_session_message(project_path: str | None) -> str:
    if _project_path_from_payload({"project_path": project_path}):
        return "No sessions found for the selected project. Run import first."
    return "No sessions found. Run import first."


def _payload_llm_settings(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("llm")
    return raw if isinstance(raw, dict) else None


def _default_llm_settings() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": "mock",
        "model": "mock-model",
        "api_key_env": "",
        "base_url": "",
        "local_only": True,
        "allow_cloud": False,
    }


def _raw_llm_settings(conn: Connection) -> dict[str, Any]:
    raw = _default_llm_settings()
    stored = get_setting(conn, LLM_SETTINGS_KEY)
    if stored:
        try:
            parsed = json.loads(stored)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            raw.update(parsed)
    return _normalize_llm_settings(raw, include_secret=True)


def _llm_settings_for_run(conn: Connection, patch: dict[str, Any] | None) -> dict[str, Any]:
    return _merge_llm_settings(_raw_llm_settings(conn), patch or {}, include_secret=True)


def _merge_llm_settings(
    current: dict[str, Any],
    patch: dict[str, Any],
    *,
    include_secret: bool,
) -> dict[str, Any]:
    merged = dict(current)
    for key in ("enabled", "provider", "model", "api_key_env", "base_url", "local_only", "allow_cloud"):
        if key in patch:
            merged[key] = patch[key]
    if patch.get("clear_api_key"):
        merged.pop("api_key", None)
    api_key = patch.get("api_key")
    if isinstance(api_key, str) and api_key:
        merged["api_key"] = api_key
    return _normalize_llm_settings(merged, include_secret=include_secret)


def _normalize_llm_settings(settings: dict[str, Any], *, include_secret: bool) -> dict[str, Any]:
    provider = normalize_provider_name(str(settings.get("provider") or "mock"))
    if provider not in SUPPORTED_DASHBOARD_LLM_PROVIDERS:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    model = str(settings.get("model") or default_model_for_provider(provider))
    normalized = {
        "enabled": bool(settings.get("enabled", False)),
        "provider": provider,
        "model": model,
        "api_key_env": str(settings.get("api_key_env") or ""),
        "base_url": str(settings.get("base_url") or ""),
        "local_only": bool(settings.get("local_only", True)),
        "allow_cloud": bool(settings.get("allow_cloud", False)),
    }
    api_key = settings.get("api_key")
    if include_secret and isinstance(api_key, str) and api_key:
        normalized["api_key"] = api_key
    return normalized


def _public_llm_settings(settings: dict[str, Any]) -> dict[str, Any]:
    public = _normalize_llm_settings(settings, include_secret=False)
    public["api_key_configured"] = bool(settings.get("api_key"))
    return public


def _run_llm_session_retro(
    conn: Connection,
    session: Any,
    events: list[Any],
    settings: dict[str, Any],
) -> dict[str, object]:
    provider_name = normalize_provider_name(str(settings.get("provider") or "mock"))
    if provider_name != "mock" and settings.get("local_only", True) and not settings.get("allow_cloud", False):
        raise ValueError(
            "Cloud LLM calls are blocked by local-only mode. Enable allow_cloud or use the mock provider."
        )
    model = str(settings.get("model") or default_model_for_provider(provider_name))
    api_key = str(settings.get("api_key") or "")
    api_key_env = str(settings.get("api_key_env") or "")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")
    base_url = str(settings.get("base_url") or "") or None
    request = build_session_retro_request(
        session,
        events,
        provider=provider_name,
        model=model,
    )
    cached = find_cached_llm_output(
        conn,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        input_hash=request.input_hash,
    )
    if cached is not None:
        cached_usage_json = parse_llm_usage_json(cached["usage_json"])
        if not llm_usage_has_tokens(cached_usage_json):
            cached = None
    if cached is not None:
        cleaned, _warnings = validate_session_retro_output(json.loads(cached["output_json"]))
        cached_usage = llm_cached_usage_payload(
            request,
            cached_usage=cached_usage_json,
            max_output_tokens=SESSION_RETRO_MAX_OUTPUT_TOKENS,
            warnings=_warnings,
        )
        cleaned["_recodex_token_usage"] = llm_token_usage_report([cached_usage])
        return cleaned

    job_id = f"job_{uuid.uuid4().hex[:24]}"
    insert_llm_job(
        conn,
        job_id=job_id,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        rulebase_version="rulebase_v1",
        input_hash=request.input_hash,
        status="running",
    )
    try:
        provider = provider_for_name(
            provider_name,
            api_key=api_key or None,
            base_url=base_url,
        )
        result = generate_session_retro_analysis(provider, request)
        cleaned = result.output
        usage = result.usage
        insert_llm_output(
            conn,
            output_id=f"out_{uuid.uuid4().hex[:24]}",
            job_id=job_id,
            output_json=json.dumps(cleaned, ensure_ascii=False),
            usage_json=json.dumps(usage, ensure_ascii=False),
            validation_status="ok" if not result.warnings else "ok_with_warnings",
        )
        update_llm_job_status(conn, job_id=job_id, status="ok")
        cleaned["_recodex_token_usage"] = llm_token_usage_report([usage])
        return cleaned
    except Exception as exc:
        update_llm_job_status(conn, job_id=job_id, status="error", error=str(exc))
        raise RuntimeError(f"LLM analysis failed: {exc}") from exc


def _llm_settings_for_workflow(conn: Connection, patch: dict[str, Any] | None) -> dict[str, Any]:
    settings = _llm_settings_for_run(conn, patch)
    if not settings["enabled"]:
        settings = {
            **settings,
            "enabled": True,
            "provider": "mock",
            "model": default_model_for_provider("mock"),
            "local_only": True,
            "allow_cloud": False,
        }
    return settings


def _run_llm_workflow_stage(
    conn: Connection,
    stage: WorkflowLLMStage,
    settings: dict[str, Any],
) -> WorkflowStageOutput:
    provider_name = normalize_provider_name(str(settings.get("provider") or "mock"))
    if provider_name != "mock" and settings.get("local_only", True) and not settings.get("allow_cloud", False):
        raise ValueError(
            "Cloud LLM calls are blocked by local-only mode. Enable allow_cloud or use the mock provider."
        )
    model = str(settings.get("model") or default_model_for_provider(provider_name))
    api_key = str(settings.get("api_key") or "")
    api_key_env = str(settings.get("api_key_env") or "")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")
    base_url = str(settings.get("base_url") or "") or None
    request = build_workflow_llm_request(stage, provider=provider_name, model=model)
    cached = find_cached_llm_output(
        conn,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        input_hash=request.input_hash,
    )
    if cached is not None:
        cached_usage_json = parse_llm_usage_json(cached["usage_json"])
        if not llm_usage_has_tokens(cached_usage_json):
            cached = None
    if cached is not None:
        cached_usage = llm_cached_usage_payload(
            request,
            cached_usage=cached_usage_json,
            max_output_tokens=stage.max_output_tokens,
            stage=stage.stage,
        )
        return WorkflowStageOutput(
            output=json.loads(cached["output_json"]),
            cached=True,
            usage=cached_usage,
        )

    job_id = f"job_{uuid.uuid4().hex[:24]}"
    insert_llm_job(
        conn,
        job_id=job_id,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        rulebase_version=WORKFLOW_VERSION,
        input_hash=request.input_hash,
        status="running",
    )
    try:
        provider = provider_for_name(
            provider_name,
            api_key=api_key or None,
            base_url=base_url,
        )
        active_stage = stage
        active_request = request
        warnings: tuple[str, ...] = ()
        try:
            raw_output = provider.generate_json(
                model=active_request.model,
                system=active_request.system,
                messages=active_request.messages,
                schema=active_request.schema,
                temperature=0,
                max_output_tokens=active_stage.max_output_tokens,
                metadata=active_request.metadata,
            )
        except LLMResponseIncompleteError as exc:
            if not _should_retry_workflow_incomplete(stage, exc):
                raise
            active_stage = _compact_extract_retry_stage(stage, reason=exc.reason)
            active_request = build_workflow_llm_request(active_stage, provider=provider_name, model=model)
            warnings = ("compact_retry", f"compact_retry_reason:{exc.reason}")
            raw_output = provider.generate_json(
                model=active_request.model,
                system=active_request.system,
                messages=active_request.messages,
                schema=active_request.schema,
                temperature=0,
                max_output_tokens=active_stage.max_output_tokens,
                metadata=active_request.metadata,
            )
        usage = llm_usage_payload(
            active_request,
            raw_output,
            max_output_tokens=active_stage.max_output_tokens,
            provider_usage=getattr(provider, "last_usage", {}),
            warnings=warnings,
            retried=bool(warnings),
            stage=active_stage.stage,
        )
        insert_llm_output(
            conn,
            output_id=f"out_{uuid.uuid4().hex[:24]}",
            job_id=job_id,
            output_json=json.dumps(raw_output, ensure_ascii=False),
            usage_json=json.dumps(usage, ensure_ascii=False),
            validation_status="pending_workflow_validation" if not warnings else "pending_workflow_validation_with_retry",
        )
        update_llm_job_status(conn, job_id=job_id, status="ok")
        return WorkflowStageOutput(output=raw_output, warnings=warnings, usage=usage)
    except Exception as exc:
        update_llm_job_status(conn, job_id=job_id, status="error", error=str(exc))
        raise RuntimeError(f"LLM workflow stage `{stage.stage}` failed: {exc}") from exc


def _should_retry_workflow_incomplete(stage: WorkflowLLMStage, exc: LLMResponseIncompleteError) -> bool:
    reason = exc.reason.lower()
    return stage.stage == "extract" and reason in {"length", "max_output_tokens"}


def _compact_extract_retry_stage(stage: WorkflowLLMStage, *, reason: str) -> WorkflowLLMStage:
    payload = _compact_extract_retry_payload(stage.payload, reason=reason)
    return replace(
        stage,
        payload=payload,
        system="\n".join(
            [
                stage.system,
                "重试模式：上一次输出超长。只输出 1 个最重要 issue。",
                "所有文本字段必须极简，一句话即可；observations 输出空数组即可。",
            ]
        ),
        input_summary={**stage.input_summary, "retry": "compact", "retry_reason": reason},
        max_output_tokens=max(stage.max_output_tokens * 2, 6500),
    )


def _compact_extract_retry_payload(payload: dict[str, object], *, reason: str) -> dict[str, object]:
    analysis_unit = payload.get("analysis_unit") if isinstance(payload.get("analysis_unit"), dict) else {}
    segments = payload.get("qualitative_segments") if isinstance(payload.get("qualitative_segments"), list) else []
    compact_segments = _compact_retry_segments(segments)
    return {
        "session": payload.get("session") if isinstance(payload.get("session"), dict) else {},
        "analysis_focus": {
            "primary": "qualitative_segments",
            "rule": "Compact retry after provider length truncation. Use only the listed source_ref values.",
        },
        "analysis_unit": analysis_unit,
        "qualitative_theme": payload.get("qualitative_theme") if isinstance(payload.get("qualitative_theme"), dict) else {},
        "qualitative_segments": compact_segments,
        "output_contract": {
            "analysis_unit_id": analysis_unit.get("id", "") if isinstance(analysis_unit, dict) else "",
            "evidence_refs": "Use only source_ref values from qualitative_segments.",
        },
        "response_limits": {
            "max_issues": 1,
            "max_evidence_refs_per_issue": 2,
            "max_text_chars_per_field": 120,
            "style": "compact_json_only",
        },
        "retry_context": {
            "reason": reason,
            "strategy": "compact_extract_retry",
        },
    }


def _compact_retry_segments(segments: list[object]) -> list[dict[str, object]]:
    compact: list[dict[str, object]] = []
    for item in segments[:4]:
        if not isinstance(item, dict):
            continue
        codes = item.get("codes") if isinstance(item.get("codes"), list) else []
        compact.append(
            {
                "source_ref": str(item.get("source_ref") or ""),
                "role": "user",
                "text": _short_text(str(item.get("text") or ""), 360),
                "code_ids": [
                    str(code.get("code_id") or "")
                    for code in codes
                    if isinstance(code, dict) and code.get("code_id")
                ][:4],
            }
        )
    return compact


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def run_dashboard_analysis(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "improvements").lower()
    if mode in {"report", "retro"}:
        report = generate_session_report(
            state_db,
            target=str(payload.get("target") or payload.get("session_id") or "latest"),
            project_path=_project_path_from_payload(payload),
            report_dir=_optional_path(payload.get("reports_dir")),
            llm_settings=_payload_llm_settings(payload),
        )
        return {"ok": True, "mode": mode, "report": report}
    if mode == "patterns":
        return _run_patterns_analysis(state_db, payload)
    if mode == "workflow":
        return _run_workflow_analysis(state_db, payload)
    if mode == "improvements":
        return _run_improvements_analysis(state_db, payload)
    raise ValueError(f"Unsupported analysis mode: {mode}")


def list_dashboard_improvements(
    state_db: Path,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return [_improvement_payload(row) for row in list_improvements(connect(state_db), status=status, limit=limit)]


def set_dashboard_improvement_status(
    state_db: Path,
    improvement_id: int,
    status: str,
) -> dict[str, Any]:
    conn = connect(state_db)
    if update_improvement_status(conn, [improvement_id], status) == 0:
        raise ValueError(f"No improvement candidate found for #{improvement_id}.")
    row = get_improvement(conn, improvement_id)
    if row is None:
        raise ValueError(f"No improvement candidate found for #{improvement_id}.")
    record_analysis_feedback(
        conn,
        source_type="improvement",
        source_id=str(improvement_id),
        label=status,
        payload=_improvement_payload(row),
    )
    return _improvement_payload(row)


def artifact_preview(
    state_db: Path,
    *,
    artifact_type: str,
    improvement_id: int | None = None,
    report_id: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    if report_id:
        candidate = _report_artifact_candidate(state_db, report_id, artifact_id)
        file_payload = _artifact_candidate_file(candidate)
        return {
            "ok": True,
            "artifact_source": "report_candidate",
            "artifact_type": _artifact_type_for_candidate(candidate),
            "report_id": report_id,
            "artifact_id": str(candidate.get("id") or ""),
            "status": str(candidate.get("status") or "proposed"),
            "files": [file_payload],
        }
    rows = _artifact_rows(state_db, improvement_id)
    files = _artifact_files(artifact_type, rows)
    return {
        "ok": True,
        "artifact_source": "improvement",
        "artifact_type": artifact_type,
        "improvement_id": improvement_id,
        "files": files,
    }


def artifact_export(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("report_id"):
        return _export_report_artifact_candidate(state_db, payload)
    artifact_type = str(payload.get("type") or payload.get("artifact_type") or "skill")
    improvement_id = int(payload["improvement_id"]) if payload.get("improvement_id") is not None else None
    rows = _artifact_rows(state_db, improvement_id)
    paths: list[Path]
    conn = connect(state_db)
    try:
        if artifact_type == "skill":
            if any(str(row["status"]) != "accepted" for row in rows):
                raise ValueError("Skill export requires accepted improvement candidates.")
            root = _resolve_skill_root(conn, payload)
            paths = write_skill_md_exports_to_root(
                root,
                rows,
                on_conflict=str(payload.get("on_conflict") or "rename"),
            )
            set_setting(conn, "last_skill_export_dir", str(root))
        else:
            root = _resolve_export_root(payload)
            paths = _write_non_skill_artifact(artifact_type, root, rows)
        for path in paths:
            _record_artifact_export(
                conn,
                artifact_type=artifact_type,
                improvement_id=improvement_id,
                target_path=path,
                status="ok",
                conflict_policy=str(payload.get("on_conflict") or ""),
                error=None,
            )
    except Exception as exc:
        _record_artifact_export(
            conn,
            artifact_type=artifact_type,
            improvement_id=improvement_id,
            target_path=_resolve_export_root(payload),
            status="error",
            conflict_policy=str(payload.get("on_conflict") or ""),
            error=str(exc),
        )
        raise
    return {
        "ok": True,
        "artifact_source": "improvement",
        "artifact_type": artifact_type,
        "paths": [str(path) for path in paths],
    }


def artifact_effectiveness(state_db: Path, *, limit: int = 50) -> dict[str, Any]:
    conn = connect(state_db)
    exports = conn.execute(
        """
        SELECT *
        FROM artifact_exports
        WHERE status = 'ok'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    report_rows = conn.execute(
        """
        SELECT id, json_path, created_at
        FROM generated_reports
        WHERE json_path IS NOT NULL
        ORDER BY created_at ASC
        """
    ).fetchall()
    reports = [_report_cost_snapshot(row) for row in report_rows]
    reports = [snapshot for snapshot in reports if snapshot is not None]
    artifacts = [
        _artifact_effectiveness_payload(export, reports)
        for export in exports
    ]
    return {
        "ok": True,
        "artifacts": artifacts,
        "summary": _effectiveness_summary(artifacts),
    }


def list_report_artifact_candidates(state_db: Path, report_id: str) -> list[dict[str, Any]]:
    conn = connect(state_db)
    row = _report_db_row(conn, report_id)
    if row is None:
        raise ValueError(f"No report found for `{report_id}`.")
    report = _read_report_json(row["json_path"])
    return [
        {
            **candidate,
            "artifact_source": "report_candidate",
            "report_id": report_id,
        }
        for candidate in _report_artifact_candidates(report)
    ]


def review_report_artifact_candidate(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    report_id = str(payload.get("report_id") or "")
    artifact_id = str(payload.get("artifact_id") or "")
    status = str(payload.get("status") or "")
    if not report_id or not artifact_id:
        raise ValueError("Artifact review requires `report_id` and `artifact_id`.")
    if status not in {"accepted", "rejected", "proposed"}:
        raise ValueError("Artifact review status must be accepted, rejected, or proposed.")

    conn = connect(state_db)
    row = _report_db_row(conn, report_id)
    if row is None:
        raise ValueError(f"No report found for `{report_id}`.")
    report_path = _required_report_json_path(row["json_path"])
    report = _read_report_json(report_path)
    candidate = _update_report_artifact_candidate(report, artifact_id, status)
    if candidate:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reviewed = {
            **candidate,
            "artifact_source": "report_candidate",
            "report_id": report_id,
        }
        record_analysis_feedback(
            conn,
            source_type="artifact_candidate",
            source_id=artifact_id,
            label=status,
            payload=reviewed,
        )
        return reviewed
    raise ValueError(f"No artifact candidate `{artifact_id}` found for report `{report_id}`.")


def _run_improvements_analysis(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    conn = connect(state_db)
    since_label = str(payload.get("since") or "30d")
    since = _parse_since(since_label)
    sessions = list_sessions(conn, since)
    events_by_session = {session.session_id: get_events(conn, session.session_id) for session in sessions}
    drafts = propose_improvements(sessions, events_by_session)
    created, skipped = insert_improvements(conn, drafts)
    rows = list_improvements(conn, limit=100)
    report_root = _optional_path(payload.get("reports_dir")) or reports_dir(None)
    path = improvements_report_path(report_root)
    write_text(path, render_improvements(rows))
    return {
        "ok": True,
        "mode": "improvements",
        "created": created,
        "skipped": skipped,
        "report_path": str(path),
        "improvements": [_improvement_payload(row) for row in rows],
    }


def _run_patterns_analysis(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    conn = connect(state_db)
    since_label = str(payload.get("since") or "30d")
    since = _parse_since(since_label)
    sessions = list_sessions(conn, since)
    events_by_session = {session.session_id: get_events(conn, session.session_id) for session in sessions}
    report_root = _optional_path(payload.get("reports_dir")) or reports_dir(None)
    path = patterns_report_path(report_root, since_label)
    write_text(path, render_patterns(sessions, events_by_session, since_label))
    report_id = f"rep_patterns_{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:10]}"
    _upsert_report(
        conn,
        report_id=report_id,
        kind="patterns",
        session_id=None,
        project_path=None,
        title=f"Patterns since {since_label}",
        html_path=None,
        markdown_path=path,
        json_path=None,
        created_at=now_utc(),
    )
    return {"ok": True, "mode": "patterns", "report": _report_row_by_id(conn, report_id)}


def _run_workflow_analysis(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    conn = connect(state_db)
    project_path = _project_path_from_payload(payload)
    session = _session_for_target(
        conn,
        str(payload.get("target") or payload.get("session_id") or "latest"),
        project_path=project_path,
    )
    if session is None:
        raise ValueError(_no_session_message(project_path))
    events = get_events(conn, session.session_id)
    settings = _llm_settings_for_workflow(conn, _payload_llm_settings(payload))

    def stage_runner(stage: WorkflowLLMStage) -> WorkflowStageOutput:
        return _run_llm_workflow_stage(conn, stage, settings)

    workflow = run_analysis_workflow(session, events, stage_runner=stage_runner)
    workflow["efficiency_analysis"] = run_efficiency_analysis(
        [session],
        {session.session_id: events},
    ).to_payload()
    _record_workflow_feedback_seed(conn, workflow)
    report_id = f"rep_workflow_{session.session_id}_{hashlib.sha256(json.dumps(workflow, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:10]}"
    generated_at = now_utc()
    report_data = workflow_result_to_report_data(
        session,
        workflow,
        report_id=report_id,
        generated_at=generated_at,
    )
    root = _optional_path(payload.get("reports_dir")) or reports_dir(None)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"workflow-{session.session_id[:16]}-{report_id[-10:]}"
    json_path = write_report_json(root / f"{stem}.json", report_data)
    markdown_path = write_text(root / f"{stem}.md", _render_workflow_markdown(report_data))
    _upsert_report(
        conn,
        report_id=report_id,
        kind="workflow",
        session_id=session.session_id,
        project_path=session.project_path,
        title=f"Workflow analysis: {session.title}",
        html_path=None,
        markdown_path=markdown_path,
        json_path=json_path,
        created_at=generated_at,
    )
    return {
        "ok": True,
        "mode": "workflow",
        "report": _report_row_by_id(conn, report_id),
        "workflow": workflow,
    }


def _record_workflow_feedback_seed(conn: Connection, workflow: dict[str, Any]) -> None:
    record_prompt_version(
        conn,
        version=WORKFLOW_PROMPT_VERSION,
        workflow_version=WORKFLOW_VERSION,
        prompts={
            "stages": ["extract", "cluster", "validate", "report"],
            "policy": "normalized trace -> evidence pack -> per-episode extraction -> clustering -> validation -> report",
        },
        schema={
            stage: workflow_schema_version(stage)
            for stage in ("extract", "cluster", "validate", "report")
        },
    )
    validation = workflow.get("validation") if isinstance(workflow.get("validation"), dict) else {}
    human_queue = validation.get("human_queue") if isinstance(validation.get("human_queue"), list) else []
    session = workflow.get("session") if isinstance(workflow.get("session"), dict) else {}
    source_id = str(session.get("id") or "unknown")
    for item in human_queue[:20]:
        if not isinstance(item, dict):
            continue
        record_judge_failure(
            conn,
            workflow_version=WORKFLOW_VERSION,
            source_id=source_id,
            reason=str(item.get("reason") or "human review required"),
            payload=item,
        )


def _artifact_rows(state_db: Path, improvement_id: int | None) -> list[Row]:
    conn = connect(state_db)
    if improvement_id is not None:
        row = get_improvement(conn, improvement_id)
        if row is None:
            raise ValueError(f"No improvement candidate found for #{improvement_id}.")
        return [row]
    rows = list_improvements(conn, status="accepted", limit=20)
    if not rows:
        rows = list_improvements(conn, status="proposed", limit=20)
    return rows


def _report_artifact_candidate(
    state_db: Path,
    report_id: str,
    artifact_id: str | None,
) -> dict[str, Any]:
    conn = connect(state_db)
    row = _report_db_row(conn, report_id)
    if row is None:
        raise ValueError(f"No report found for `{report_id}`.")
    report = _read_report_json(row["json_path"])
    candidates = _report_artifact_candidates(report)
    if not candidates:
        raise ValueError(f"No artifact candidates found for report `{report_id}`.")
    if not artifact_id:
        return candidates[0]
    for candidate in candidates:
        if str(candidate.get("id") or "") == artifact_id:
            return candidate
    raise ValueError(f"No artifact candidate `{artifact_id}` found for report `{report_id}`.")


EFFECT_COST_KEYS = (
    "extra_turns",
    "failed_commands",
    "user_corrections",
    "verification_followups",
)


def _report_cost_snapshot(row: Row) -> dict[str, Any] | None:
    try:
        report = _read_report_json(row["json_path"])
    except ValueError:
        return None
    ledger = report.get("cost_ledger") if isinstance(report.get("cost_ledger"), dict) else {}
    if not ledger:
        core = report.get("core_diagnostics") if isinstance(report.get("core_diagnostics"), dict) else {}
        ledger = core.get("cost_ledger") if isinstance(core.get("cost_ledger"), dict) else {}
    if not ledger:
        return None
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"] or ""),
        "costs": {key: _int_value(ledger.get(key)) for key in EFFECT_COST_KEYS},
    }


def _artifact_effectiveness_payload(export: Row, reports: list[dict[str, Any]]) -> dict[str, Any]:
    exported_at = str(export["created_at"] or "")
    before = [report for report in reports if str(report.get("created_at") or "") < exported_at]
    after = [report for report in reports if str(report.get("created_at") or "") > exported_at]
    before_costs = _cost_window(before)
    after_costs = _cost_window(after)
    delta = {
        key: after_costs[key] - before_costs[key]
        for key in EFFECT_COST_KEYS
    }
    return {
        "id": export["id"],
        "artifact_type": export["artifact_type"],
        "target_path": export["target_path"],
        "exported_at": exported_at,
        "before": {"report_count": len(before), **before_costs},
        "after": {"report_count": len(after), **after_costs},
        "delta": delta,
        "status": _effect_status(len(before), len(after), delta),
    }


def _cost_window(reports: list[dict[str, Any]]) -> dict[str, int]:
    if not reports:
        return {key: 0 for key in EFFECT_COST_KEYS}
    return {
        key: round(
            sum(_int_value(report.get("costs", {}).get(key)) for report in reports)
            / len(reports)
        )
        for key in EFFECT_COST_KEYS
    }


def _effect_status(before_count: int, after_count: int, delta: dict[str, int]) -> str:
    if before_count == 0 or after_count == 0:
        return "insufficient_data"
    total_delta = sum(delta.values())
    if total_delta < 0:
        return "improved"
    if total_delta > 0:
        return "regressed"
    return "flat"


def _effectiveness_summary(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"improved": 0, "regressed": 0, "flat": 0, "insufficient_data": 0}
    for artifact in artifacts:
        status = str(artifact.get("status") or "insufficient_data")
        if status in counts:
            counts[status] += 1
    return counts


def _int_value(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _export_report_artifact_candidate(state_db: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("reviewed") is not True:
        raise ValueError("Artifact candidate export requires `reviewed: true`.")
    report_id = str(payload.get("report_id") or "")
    if not report_id:
        raise ValueError("Artifact candidate export requires `report_id`.")
    artifact_id = str(payload.get("artifact_id") or "") or None
    candidate = _report_artifact_candidate(state_db, report_id, artifact_id)
    file_payload = _artifact_candidate_file(candidate)
    root = _resolve_export_root(payload)
    target_path = _safe_export_path(root, file_payload["path"])
    conn = connect(state_db)
    try:
        written = write_text(target_path, file_payload["content"])
        _record_artifact_export(
            conn,
            artifact_type=_artifact_type_for_candidate(candidate),
            improvement_id=None,
            target_path=written,
            status="ok",
            conflict_policy=str(payload.get("on_conflict") or ""),
            error=None,
        )
    except Exception as exc:
        _record_artifact_export(
            conn,
            artifact_type=_artifact_type_for_candidate(candidate),
            improvement_id=None,
            target_path=target_path,
            status="error",
            conflict_policy=str(payload.get("on_conflict") or ""),
            error=str(exc),
        )
        raise
    return {
        "ok": True,
        "artifact_source": "report_candidate",
        "artifact_type": _artifact_type_for_candidate(candidate),
        "report_id": report_id,
        "artifact_id": str(candidate.get("id") or ""),
        "paths": [str(written)],
    }


def _artifact_candidate_file(candidate: dict[str, Any]) -> dict[str, str]:
    relative_path = _artifact_candidate_relative_path(candidate)
    content = str(candidate.get("proposed_content") or "").strip()
    if not content:
        content = _artifact_candidate_fallback_content(candidate)
    return {"path": relative_path.as_posix(), "content": content.rstrip() + "\n"}


def _artifact_candidate_relative_path(candidate: dict[str, Any]) -> Path:
    raw_target = str(candidate.get("target_path") or "").strip()
    path = Path(raw_target) if raw_target else _artifact_candidate_default_path(candidate)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Unsafe artifact candidate target path: {raw_target}")
    if str(path) in {"", "."}:
        return _artifact_candidate_default_path(candidate)
    return path


def _artifact_candidate_default_path(candidate: dict[str, Any]) -> Path:
    artifact_type = _artifact_type_for_candidate(candidate)
    artifact_id = str(candidate.get("id") or "candidate")
    if artifact_type == "checklist":
        return Path("docs/ai-coding-checklist.md")
    if artifact_type in {"agents", "agents_md"}:
        return Path("AGENTS.patch.md")
    if artifact_type == "script":
        return Path("scripts/recodex-verify.sh")
    if artifact_type in {"ci", "hook_or_ci"}:
        return Path("ci/recodex-guard.md")
    if artifact_type == "skill":
        return Path("skills/recodex-candidate/SKILL.md")
    return Path("artifacts") / f"{artifact_id}.md"


def _artifact_candidate_fallback_content(candidate: dict[str, Any]) -> str:
    artifact_type = _artifact_type_for_candidate(candidate)
    return "\n".join(
        [
            f"# {artifact_type or 'Artifact Candidate'}",
            "",
            str(candidate.get("rationale") or "Review this candidate before applying it."),
            "",
            "Source findings: "
            + ", ".join(str(item) for item in _list(candidate.get("source_finding_ids"))),
        ]
    )


def _safe_export_path(root: Path, relative_path: str) -> Path:
    root_path = root.expanduser().resolve()
    candidate = _safe_relative_path(relative_path)
    target = (root_path / candidate).resolve()
    if not target.is_relative_to(root_path):
        raise ValueError(f"Unsafe artifact export path: {relative_path}")
    return target


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Unsafe artifact export path: {value}")
    return path


def _render_workflow_markdown(report_data: dict[str, Any]) -> str:
    meta = report_data.get("meta") if isinstance(report_data.get("meta"), dict) else {}
    summary = report_data.get("summary") if isinstance(report_data.get("summary"), dict) else {}
    workflow = report_data.get("workflow") if isinstance(report_data.get("workflow"), dict) else {}
    facts = workflow.get("deterministic_facts") if isinstance(workflow.get("deterministic_facts"), dict) else {}
    windows = workflow.get("evidence_windows") if isinstance(workflow.get("evidence_windows"), list) else []
    claims = workflow.get("micro_claims") if isinstance(workflow.get("micro_claims"), list) else []
    cards = workflow.get("analysis_cards") if isinstance(workflow.get("analysis_cards"), list) else []
    verifications = workflow.get("card_verifications") if isinstance(workflow.get("card_verifications"), list) else []
    qualitative = workflow.get("qualitative_analysis") if isinstance(workflow.get("qualitative_analysis"), dict) else {}
    themes = qualitative.get("themes") if isinstance(qualitative.get("themes"), list) else []
    clusters = report_data.get("issues") if isinstance(report_data.get("issues"), list) else []
    user_intent = report_data.get("user_intent") if isinstance(report_data.get("user_intent"), dict) else {}
    timeline = user_intent.get("timeline") if isinstance(user_intent.get("timeline"), list) else []
    validation = workflow.get("validation") if isinstance(workflow.get("validation"), dict) else {}
    lines = [
        "# LLM Analysis Workflow",
        "",
        f"- Session: `{_md_text(meta.get('session_id', ''))}`",
        f"- Generated: {_md_text(meta.get('generated_at', ''))}",
        f"- Workflow: `{_md_text(meta.get('workflow_version', ''))}`",
        "",
        "## Summary",
        "",
        _md_text(summary.get("headline") or ""),
        "",
        _md_text(summary.get("overall") or ""),
        "",
        "## Evidence Pipeline",
        "",
        f"- Episodes: `{len(workflow.get('episodes', []) if isinstance(workflow.get('episodes'), list) else [])}`",
        f"- Evidence windows: `{len(windows)}`",
        f"- Micro claims: `{len(claims)}`",
        f"- Analysis cards: `{len(cards)}`",
        f"- Card verifications: `{len(verifications)}`",
        f"- Validated clusters: `{len(clusters)}`",
        "",
        "## User Intent",
        "",
        f"- Primary request: {_md_text(user_intent.get('primary_request', ''))}",
        f"- Latest request: {_md_text(user_intent.get('latest_request', ''))}",
        f"- User inputs: `{user_intent.get('user_input_count', 0)}`",
        f"- Corrections: `{user_intent.get('correction_count', 0)}`",
        "",
    ]
    for item in timeline[:8]:
        if not isinstance(item, dict):
            continue
        lines.append(f"- `{_md_text(item.get('source_ref', ''))}` {_md_text(item.get('text', ''))}")
    lines.extend(["", "## Evidence Windows", ""])
    if windows:
        for window in windows[:8]:
            if not isinstance(window, dict):
                continue
            lines.extend(
                [
                    f"### {_md_text(window.get('center_signal_type', 'signal'))} / `{_md_text(window.get('window_id', ''))}`",
                    "",
                    f"- Episode: `{_md_text(window.get('episode_id', ''))}`",
                    f"- Events: {', '.join(f'`{_md_text(event_id)}`' for event_id in window.get('event_ids', [])[:8]) if isinstance(window.get('event_ids'), list) else ''}",
                    "",
                    "```text",
                    _md_text(window.get("compact_text", "")),
                    "```",
                    "",
                ]
            )
    else:
        lines.append("No evidence windows were produced.")
        lines.append("")
    lines.extend(["## Micro Claims", ""])
    if claims:
        for claim in claims[:16]:
            if not isinstance(claim, dict):
                continue
            refs = ", ".join(f"`{_md_text(ref)}`" for ref in claim.get("supporting_event_ids", [])[:6]) if isinstance(claim.get("supporting_event_ids"), list) else ""
            lines.append(f"- `{_md_text(claim.get('claim_id', ''))}` {_md_text(claim.get('claim_type', ''))}: {_md_text(claim.get('claim', ''))} ({refs})")
    else:
        lines.append("- No micro claims were produced.")
    lines.extend(["", "## Analysis Cards", ""])
    if cards:
        verification_by_card = {
            str(item.get("card_id")): item
            for item in verifications
            if isinstance(item, dict) and item.get("card_id")
        }
        for card in cards[:12]:
            if not isinstance(card, dict):
                continue
            verification = verification_by_card.get(str(card.get("card_id") or ""), {})
            lines.extend(
                [
                    f"### {_md_text(card.get('title', 'Untitled card'))}",
                    "",
                    f"- Type: `{_md_text(card.get('card_type', ''))}`",
                    f"- Destination: `{_md_text(card.get('candidate_destination', ''))}`",
                    f"- Readiness: `{_md_text(card.get('artifact_readiness', ''))}`",
                    f"- Verification: `{_md_text(verification.get('verdict', 'unknown'))}`",
                    f"- Evidence claims: {', '.join(f'`{_md_text(claim_id)}`' for claim_id in card.get('evidence_claim_ids', [])[:8]) if isinstance(card.get('evidence_claim_ids'), list) else ''}",
                    "",
                    f"Fact: {_md_text(card.get('observed_fact', ''))}",
                    "",
                    f"Problem: {_md_text(card.get('inferred_problem', ''))}",
                    "",
                ]
            )
    else:
        lines.append("No analysis cards were produced.")
        lines.append("")
    lines.extend(["", "## Qualitative Themes", ""])
    if themes:
        for theme in themes[:8]:
            if not isinstance(theme, dict):
                continue
            codes = ", ".join(str(code) for code in theme.get("codes", [])[:6]) if isinstance(theme.get("codes"), list) else ""
            evidence_count = (theme.get("validation") or {}).get("evidence_count", len(theme.get("evidence_refs", []) or [])) if isinstance(theme.get("validation"), dict) else len(theme.get("evidence_refs", []) or [])
            lines.append(f"- {_md_text(theme.get('label', theme.get('theme_id', 'Theme')))}: `{evidence_count}` refs ({_md_text(codes)})")
    else:
        lines.append("- No qualitative themes were detected.")
    lines.extend([
        "",
        "## Deterministic Facts",
        "",
    ])
    for key in (
        "command_count",
        "command_failure_count",
        "test_run_count",
        "verification_present",
        "sandbox_or_network_errors",
        "user_correction_count",
        "skipped_verification",
    ):
        if key in facts:
            lines.append(f"- {key}: `{facts[key]}`")
    lines.extend(["", "## Validated Clusters", ""])
    if clusters:
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            lines.extend(
                [
                    f"### {_md_text(cluster.get('title', 'Untitled cluster'))}",
                    "",
                    f"- Severity: `{_md_text(cluster.get('severity', 'medium'))}`",
                    f"- Readiness: `{_md_text(cluster.get('readiness', ''))}`",
                    f"- Destinations: {', '.join(f'`{_md_text(destination)}`' for destination in cluster.get('recommended_destinations', [])[:6]) if isinstance(cluster.get('recommended_destinations'), list) else ''}",
                    f"- Evidence: {', '.join(f'`{_md_text(ref)}`' for ref in cluster.get('evidence_refs', [])[:6]) if isinstance(cluster.get('evidence_refs'), list) else ''}",
                    "",
                    _md_text(cluster.get("recommended_change") or cluster.get("recommendation") or ""),
                    "",
                ]
            )
    else:
        lines.append("No supported clusters were produced.")
        lines.append("")
    rejected = validation.get("rejected_ids") if isinstance(validation.get("rejected_ids"), list) else []
    if rejected:
        lines.extend(["## Rejected LLM Candidates", ""])
        for item in rejected[:12]:
            lines.append(f"- `{_md_text(item)}`")
        lines.append("")
    return "\n".join(lines)


def _md_text(value: object) -> str:
    return redact_text(str(value or ""))


def _artifact_files(artifact_type: str, rows: list[Row]) -> list[dict[str, str]]:
    if artifact_type == "skill":
        files = []
        for row in rows:
            slug, content = render_skill_md(row)
            files.append({"path": f"skills/{slug}/SKILL.md", "content": content})
        return files
    if artifact_type == "markdown":
        return [{"path": "improvements.md", "content": render_improvements(rows)}]
    if artifact_type == "agents":
        return [{"path": "AGENTS.patch.md", "content": render_agents_patch(rows)}]
    if artifact_type == "checklist":
        return [{"path": "checklists/recodex-checklist.md", "content": render_checklist_export(rows)}]
    if artifact_type == "ci":
        return [{"path": "ci/verify.yml", "content": render_ci_rule_export(rows)}]
    raise ValueError(f"Unsupported artifact type: {artifact_type}")


def _write_non_skill_artifact(artifact_type: str, root: Path, rows: list[Row]) -> list[Path]:
    if artifact_type == "markdown":
        return [write_text(root / "improvements.md", render_improvements(rows))]
    if artifact_type == "agents":
        return [write_text(root / "AGENTS.patch.md", render_agents_patch(rows))]
    if artifact_type == "checklist":
        return [write_checklist_export(root, rows)]
    if artifact_type == "ci":
        return [write_ci_rule_export(root, rows)]
    if artifact_type == "script":
        return [write_scripts_export(root, rows)]
    raise ValueError(f"Unsupported artifact type: {artifact_type}")


def _resolve_export_root(payload: dict[str, Any]) -> Path:
    out = payload.get("out") or payload.get("exports_dir")
    return exports_dir(str(out) if out else None)


def _resolve_skill_root(conn: Connection, payload: dict[str, Any]) -> Path:
    out = payload.get("out")
    target = str(payload.get("target") or "project")
    if target == "custom":
        if not out:
            raise ValueError("Custom skill export requires `out`.")
        return Path(str(out)).expanduser().resolve()
    if out:
        return Path(str(out)).expanduser().resolve()
    if target == "project":
        return (Path.cwd() / ".agents" / "skills").resolve()
    if target == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        return (codex_home / "skills").resolve()
    if target == "cursor":
        return (Path.cwd() / ".cursor" / "rules").resolve()
    if target == "last":
        previous = get_setting(conn, "last_skill_export_dir")
        if not previous:
            raise ValueError("No previous skill export directory recorded for this database.")
        return Path(previous).expanduser().resolve()
    raise ValueError(f"Unsupported skill export target: {target}")


def _upsert_report(
    conn: Connection,
    *,
    report_id: str,
    kind: str,
    session_id: str | None,
    project_path: str | None,
    title: str,
    html_path: Path | None,
    markdown_path: Path | None,
    json_path: Path | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO generated_reports (
            id, kind, session_id, project_path, title,
            html_path, markdown_path, json_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            session_id = excluded.session_id,
            project_path = excluded.project_path,
            title = excluded.title,
            html_path = excluded.html_path,
            markdown_path = excluded.markdown_path,
            json_path = excluded.json_path,
            created_at = excluded.created_at
        """,
        (
            report_id,
            kind,
            session_id,
            project_path,
            title,
            str(html_path) if html_path else None,
            str(markdown_path) if markdown_path else None,
            str(json_path) if json_path else None,
            created_at,
        ),
    )
    conn.commit()


def _report_db_row(conn: Connection, report_id: str) -> Row | None:
    return conn.execute("SELECT * FROM generated_reports WHERE id = ? LIMIT 1", (report_id,)).fetchone()


def _report_row_by_id(conn: Connection, report_id: str) -> dict[str, Any]:
    row = _report_db_row(conn, report_id)
    if row is None:
        raise ValueError(f"No report found for `{report_id}`.")
    return _report_row(row)


def _report_row(row: Row) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "kind": row["kind"],
        "session_id": row["session_id"],
        "project_path": row["project_path"],
        "title": row["title"],
        "html_path": row["html_path"],
        "markdown_path": row["markdown_path"],
        "json_path": row["json_path"],
        "created_at": row["created_at"],
    }
    payload["core_summary"] = _report_core_summary(row["json_path"])
    return payload


def _read_report_json(json_path: object) -> dict[str, Any]:
    path = _required_report_json_path(json_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Report JSON is not readable: {path}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"Report JSON must be an object: {path}")
    return report


def _required_report_json_path(json_path: object) -> Path:
    if not json_path:
        raise ValueError("Report JSON path is missing.")
    path = Path(str(json_path))
    if not path.exists():
        raise ValueError(f"Report file is missing: {path}")
    return path


def _report_artifact_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    focus = report.get("report_focus") if isinstance(report.get("report_focus"), dict) else {}
    focus_artifacts = [
        _report_artifact_candidate_payload(candidate)
        for candidate in _dict_items(focus.get("recommended_artifacts"))
    ]
    top_level = _dict_items(report.get("artifact_candidates"))
    candidates = [
        *focus_artifacts,
        *[_report_artifact_candidate_payload(candidate) for candidate in top_level],
    ]
    if candidates:
        return _dedupe_report_artifact_candidates(candidates)
    efficiency = (
        report.get("efficiency_analysis")
        if isinstance(report.get("efficiency_analysis"), dict)
        else {}
    )
    return [
        _report_artifact_candidate_payload(candidate)
        for candidate in _dict_items(efficiency.get("artifact_candidates"))[:3]
    ]


def _report_artifact_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = dict(candidate)
    payload.setdefault("artifact_type", _artifact_type_for_candidate(candidate))
    payload.setdefault("mechanism", str(candidate.get("mechanism") or payload["artifact_type"]))
    return payload


def _dedupe_report_artifact_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = (
            str(candidate.get("id") or ""),
            str(candidate.get("mechanism") or ""),
            str(candidate.get("target_path") or ""),
            str(candidate.get("title") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _artifact_type_for_candidate(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("artifact_type") or candidate.get("mechanism") or "candidate")
    return {
        "agents_md": "agents_md",
        "path_rule": "agents_md",
        "project_doc": "markdown",
        "task_template": "markdown",
        "checklist": "checklist",
        "script": "script",
        "hook": "ci",
        "ci": "ci",
        "skill": "skill",
        "mcp_integration": "markdown",
        "environment_config": "markdown",
        "coaching": "markdown",
    }.get(raw, raw)


def _update_report_artifact_candidate(
    report: dict[str, Any],
    artifact_id: str,
    status: str,
) -> dict[str, Any] | None:
    reviewed_at = now_utc()
    updated: dict[str, Any] | None = None
    top_level = _dict_items(report.get("artifact_candidates"))
    for candidate in top_level:
        if str(candidate.get("id") or "") != artifact_id:
            continue
        candidate["status"] = status
        candidate["reviewed_at"] = reviewed_at
        updated = candidate
        break

    efficiency = (
        report.get("efficiency_analysis")
        if isinstance(report.get("efficiency_analysis"), dict)
        else {}
    )
    efficiency_candidates = _dict_items(efficiency.get("artifact_candidates"))
    for candidate in efficiency_candidates:
        if str(candidate.get("id") or "") != artifact_id:
            continue
        candidate["status"] = status
        candidate["reviewed_at"] = reviewed_at
        if updated is None:
            updated = candidate
        break

    if updated is None:
        return None
    if top_level:
        report["artifact_candidates"] = top_level
        report["artifact_review_queue"] = _report_artifact_review_queue(top_level)
    if efficiency_candidates:
        efficiency["artifact_candidates"] = efficiency_candidates
        report["efficiency_analysis"] = efficiency
    return updated


def _report_artifact_review_queue(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for artifact in artifacts:
        status = str(artifact.get("status") or "proposed")
        if status not in {"proposed", "ready_for_review"}:
            continue
        queue.append(
            {
                "id": str(artifact.get("id") or ""),
                "mechanism": str(
                    artifact.get("mechanism") or artifact.get("artifact_type") or ""
                ),
                "target_path": artifact.get("target_path"),
                "status": status,
                "source_finding_ids": _list(artifact.get("source_finding_ids")),
                "reason": str(artifact.get("rationale") or "human review required"),
            }
        )
    return queue


def _report_core_summary(json_path: object) -> dict[str, Any]:
    if not json_path:
        return {}
    path = Path(str(json_path))
    if not path.exists():
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(report, dict):
        return {}

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    findings = _dict_items(report.get("findings"))
    opportunities = _dict_items(report.get("improvement_opportunities"))
    artifacts = _report_artifact_candidates(report)
    if not summary and not findings and not opportunities and not artifacts:
        return {}

    return {
        "max_avoidable_cost": str(summary.get("max_avoidable_cost") or ""),
        "primary_cause": str(summary.get("primary_cause") or ""),
        "primary_improvement": str(summary.get("primary_improvement") or ""),
        "finding_count": len(findings),
        "opportunity_count": len(opportunities),
        "artifact_candidate_count": len(artifacts),
        "recommended_mechanisms": _unique_nonempty(
            [
                *[str(item.get("mechanism") or "") for item in artifacts],
                *[str(item.get("recommended_mechanism") or "") for item in opportunities],
            ]
        ),
        "artifact_types": _unique_nonempty(
            _artifact_type_for_candidate(item) for item in artifacts
        ),
        "top_opportunities": [
            {
                "title": str(item.get("title") or ""),
                "recommended_mechanism": str(item.get("recommended_mechanism") or ""),
                "routing_reason": str(item.get("routing_reason") or ""),
                "suggested_target": item.get("suggested_target"),
                "best_action": str(item.get("best_action") or ""),
            }
            for item in opportunities[:3]
        ],
        "top_artifact_candidates": [
            {
                "artifact_type": _artifact_type_for_candidate(item),
                "mechanism": str(item.get("mechanism") or ""),
                "target_path": item.get("target_path"),
                "status": str(item.get("status") or ""),
                "source_finding_ids": _list(item.get("source_finding_ids")),
            }
            for item in artifacts[:3]
        ],
    }


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _metric_count(metrics: dict[str, Any], key: str, fallback_items: list[dict[str, Any]]) -> int:
    raw = metrics.get(key)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return len(fallback_items)
    return len(fallback_items)


def _unique_nonempty(values: object) -> list[str]:
    unique: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if value and value not in unique:
            unique.append(value)
    return unique


def _improvement_payload(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "session_id": row["session_id"],
        "mechanism": mechanism_for_improvement_category(row["category"]),
        "title": row["title"],
        "evidence": row["evidence"],
        "recommendation": row["recommendation"],
        "status": row["status"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
    }


def _record_artifact_export(
    conn: Connection,
    *,
    artifact_type: str,
    improvement_id: int | None,
    target_path: Path,
    status: str,
    conflict_policy: str,
    error: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO artifact_exports (
            artifact_type, improvement_id, target_path, status,
            conflict_policy, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_type,
            improvement_id,
            str(target_path),
            status,
            conflict_policy,
            error,
            now_utc(),
        ),
    )
    conn.commit()


def _optional_path(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser().resolve()


def _parse_since(value: str) -> str:
    lowered = value.strip().lower()
    amount_text = lowered[:-1] if lowered else ""
    unit = lowered[-1:] if lowered else ""
    if amount_text.isdigit() and unit in {"h", "d", "w"}:
        amount = int(amount_text)
        delta = {
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    return datetime.fromisoformat(value).isoformat()
