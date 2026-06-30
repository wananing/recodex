from __future__ import annotations

from typing import Any

AUDIT_SCHEMA_VERSION = "evidence_audit_v1"


def audit_report_evidence(report: dict[str, Any], *, mode: str = "deep") -> dict[str, Any]:
    core = _dict(report.get("core_diagnostics"))
    efficiency = _dict(report.get("efficiency_analysis"))
    valid_ref_ids = _valid_evidence_ref_ids(report)
    quote_by_ref = _evidence_quote_map(report)
    problems: list[dict[str, str]] = []
    audited_objects: list[dict[str, Any]] = []

    problems.extend(_evidence_ref_quality_problems(core))
    problems.extend(_evidence_ref_quality_problems(efficiency, prefix="efficiency_analysis"))

    findings = [_dict(item) for item in _list(core.get("findings"))]
    finding_ids = {str(item.get("id")) for item in findings if item.get("id")}
    for index, finding in enumerate(findings):
        audited_objects.append(
            _audit_evidence_refs(
                target=f"core.findings[{index}]",
                kind="finding",
                item=finding,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )

    efficiency_findings = [_dict(item) for item in _list(efficiency.get("findings"))]
    efficiency_finding_ids = {
        str(item.get("id"))
        for item in efficiency_findings
        if item.get("id")
    }
    for index, finding in enumerate(efficiency_findings):
        audited_objects.append(
            _audit_evidence_refs(
                target=f"efficiency_analysis.findings[{index}]",
                kind="efficiency_finding",
                item=finding,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )

    efficiency_artifacts = [
        _dict(item)
        for item in _list(efficiency.get("artifact_candidates"))
    ]
    for index, artifact in enumerate(efficiency_artifacts):
        audited_objects.append(
            _audit_efficiency_artifact_candidate(
                target=f"efficiency_analysis.artifact_candidates[{index}]",
                artifact=artifact,
                finding_ids=efficiency_finding_ids,
                problems=problems,
            )
        )

    opportunities = [_dict(item) for item in _list(core.get("improvement_opportunities"))]
    opportunity_by_id = {
        str(item.get("id")): item
        for item in opportunities
        if item.get("id")
    }
    for index, opportunity in enumerate(opportunities):
        target = f"core.improvement_opportunities[{index}]"
        audited_objects.append(
            _audit_evidence_refs(
                target=target,
                kind="improvement_opportunity",
                item=opportunity,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )
        for finding_id in _string_list(opportunity.get("source_finding_ids")):
            if finding_id not in finding_ids:
                problems.append(
                    _problem(
                        "high",
                        "unknown_source_finding",
                        target,
                        f"改进机会引用了不存在的 finding：{finding_id}",
                    )
                )

    for index, issue in enumerate(_dict(item) for item in _list(report.get("issues"))):
        audited_objects.append(
            _audit_evidence_refs(
                target=f"report.issues[{index}]",
                kind="report_issue",
                item=issue,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )

    report_focus = _dict(report.get("report_focus"))
    focus_artifacts = [_dict(item) for item in _list(report_focus.get("recommended_artifacts"))]
    if report_focus:
        audited_objects.append(
            _audit_evidence_refs(
                target="report.report_focus",
                kind="report_focus",
                item=report_focus,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )
        for index, artifact in enumerate(focus_artifacts):
            audited_objects.append(
                _audit_focus_artifact_candidate(
                    target=f"report.report_focus.recommended_artifacts[{index}]",
                    artifact=artifact,
                    valid_ref_ids=valid_ref_ids,
                    quote_by_ref=quote_by_ref,
                    problems=problems,
                )
            )

    core_artifacts = [
        _dict(item)
        for item in _list(core.get("artifact_candidates"))
    ]
    for index, artifact in enumerate(core_artifacts):
        audited_objects.append(
            _audit_artifact_candidate(
                target=f"core.artifact_candidates[{index}]",
                artifact=artifact,
                opportunity_by_id=opportunity_by_id,
                valid_ref_ids=valid_ref_ids,
                quote_by_ref=quote_by_ref,
                problems=problems,
            )
        )

    supported = sum(1 for item in audited_objects if item["status"] == "supported")
    audited_count = len(audited_objects)
    traceability = _ratio(supported, audited_count)
    high_problem_count = sum(1 for problem in problems if problem["severity"] == "high")
    status = _audit_status(
        audited_count=audited_count,
        evidence_ref_count=len(valid_ref_ids),
        high_problem_count=high_problem_count,
        problem_count=len(problems),
    )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "mode": mode,
        "status": status,
        "ok": status == "pass",
        "summary": _summary(status, supported, audited_count, len(problems)),
        "metrics": {
            "evidence_ref_count": len(valid_ref_ids),
            "audited_claims": audited_count,
            "supported_claims": supported,
            "traceability": traceability,
            "finding_count": len(findings),
            "efficiency_finding_count": len(efficiency_findings),
            "opportunity_count": len(opportunities),
            "artifact_candidate_count": len(core_artifacts),
            "efficiency_artifact_candidate_count": len(efficiency_artifacts),
            "problem_count": len(problems),
            "high_problem_count": high_problem_count,
        },
        "problems": problems,
        "audited_objects": audited_objects,
    }


def _valid_evidence_ref_ids(report: dict[str, Any]) -> set[str]:
    core = _dict(report.get("core_diagnostics"))
    efficiency = _dict(report.get("efficiency_analysis"))
    ids = {
        str(item.get("id"))
        for item in (_dict(value) for value in _list(core.get("evidence_refs")))
        if item.get("id")
    }
    ids.update(
        str(item.get("id"))
        for item in (_dict(value) for value in _list(efficiency.get("evidence_refs")))
        if item.get("id")
    )
    for item in (_dict(value) for value in _list(report.get("evidence"))):
        if item.get("id"):
            ids.add(str(item["id"]))
        if item.get("event_id"):
            ids.add(str(item["event_id"]))
        if item.get("source_ref"):
            ids.add(str(item["source_ref"]))
    user_intent = _dict(report.get("user_intent"))
    for item in (_dict(value) for value in _list(user_intent.get("timeline"))):
        if item.get("event_id"):
            ids.add(str(item["event_id"]))
        if item.get("source_ref"):
            ids.add(str(item["source_ref"]))
    chat_analysis = _dict(report.get("chat_transcript_analysis"))
    for item in (_dict(value) for value in _list(chat_analysis.get("transcript_sample"))):
        if item.get("event_id"):
            ids.add(str(item["event_id"]))
    for ref in _string_list(chat_analysis.get("evidence_refs")):
        ids.add(ref)
    return ids


def _evidence_quote_map(report: dict[str, Any]) -> dict[str, str]:
    core = _dict(report.get("core_diagnostics"))
    efficiency = _dict(report.get("efficiency_analysis"))
    quotes: dict[str, str] = {}
    for item in (_dict(value) for value in _list(core.get("evidence_refs"))):
        _remember_quote(quotes, item.get("id"), item)
    for item in (_dict(value) for value in _list(efficiency.get("evidence_refs"))):
        _remember_quote(quotes, item.get("id"), item)
    for item in (_dict(value) for value in _list(report.get("evidence"))):
        for key in ("id", "event_id", "source_ref"):
            _remember_quote(quotes, item.get(key), item)
    user_intent = _dict(report.get("user_intent"))
    for item in (_dict(value) for value in _list(user_intent.get("timeline"))):
        for key in ("event_id", "source_ref"):
            _remember_quote(quotes, item.get(key), item)
    chat_analysis = _dict(report.get("chat_transcript_analysis"))
    for item in (_dict(value) for value in _list(chat_analysis.get("transcript_sample"))):
        _remember_quote(quotes, item.get("event_id"), item)
    return quotes


def _remember_quote(quotes: dict[str, str], raw_key: object, item: dict[str, Any]) -> None:
    key = str(raw_key or "").strip()
    if not key or key in quotes:
        return
    for field in ("quote", "text", "text_excerpt", "user_input_text", "summary"):
        value = str(item.get(field) or "").strip()
        if value:
            quotes[key] = value[:320]
            return


def _evidence_ref_quality_problems(
    core: dict[str, Any],
    *,
    prefix: str = "core",
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for index, ref in enumerate(_dict(item) for item in _list(core.get("evidence_refs"))):
        target = f"{prefix}.evidence_refs[{index}]"
        if not str(ref.get("id") or "").strip():
            problems.append(
                _problem("high", "missing_evidence_ref_id", target, "证据引用缺少 id。")
            )
        if not str(ref.get("quote") or "").strip():
            problems.append(
                _problem(
                    "medium",
                    "empty_evidence_quote",
                    target,
                    "证据引用缺少可复核 quote。",
                )
            )
        if not str(ref.get("content_hash") or "").strip():
            problems.append(
                _problem(
                    "medium",
                    "missing_content_hash",
                    target,
                    "证据引用缺少 content_hash。",
                )
            )
    return problems


def _audit_efficiency_artifact_candidate(
    *,
    target: str,
    artifact: dict[str, Any],
    finding_ids: set[str],
    problems: list[dict[str, str]],
) -> dict[str, Any]:
    source_finding_ids = _string_list(artifact.get("source_finding_ids"))
    unknown_ids = [finding_id for finding_id in source_finding_ids if finding_id not in finding_ids]
    if not source_finding_ids:
        problems.append(
            _problem(
                "high",
                "artifact_without_source_finding",
                target,
                "v2 artifact candidate 缺少来源 finding。",
            )
        )
    for finding_id in unknown_ids:
        problems.append(
            _problem(
                "high",
                "unknown_artifact_source_finding",
                target,
                f"v2 artifact candidate 引用了不存在的 finding：{finding_id}",
            )
        )
    status = "supported" if source_finding_ids and not unknown_ids else "unsupported"
    return {
        "target": target,
        "kind": "efficiency_artifact_candidate",
        "id": str(artifact.get("id") or ""),
        "title": str(artifact.get("title") or ""),
        "status": status,
        "source_finding_ids": source_finding_ids[:8],
    }


def _audit_evidence_refs(
    *,
    target: str,
    kind: str,
    item: dict[str, Any],
    valid_ref_ids: set[str],
    quote_by_ref: dict[str, str],
    problems: list[dict[str, str]],
) -> dict[str, Any]:
    refs = _string_list(item.get("evidence_refs"))
    unknown_refs = [ref for ref in refs if ref not in valid_ref_ids]
    if not refs:
        problems.append(
            _problem("high", "missing_evidence_refs", target, "诊断对象缺少 evidence_refs。")
        )
    for ref in unknown_refs:
        problems.append(
            _problem(
                "high",
                "unknown_evidence_ref",
                target,
                f"诊断对象引用了不存在的证据：{ref}",
            )
        )
    status = "supported" if refs and not unknown_refs else "unsupported"
    return {
        "target": target,
        "kind": kind,
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or ""),
        "status": status,
        "evidence_refs": refs[:8],
        "evidence_quotes": _evidence_quotes(refs, quote_by_ref),
    }


def _audit_artifact_candidate(
    *,
    target: str,
    artifact: dict[str, Any],
    opportunity_by_id: dict[str, dict[str, Any]],
    valid_ref_ids: set[str],
    quote_by_ref: dict[str, str],
    problems: list[dict[str, str]],
) -> dict[str, Any]:
    opportunity_id = str(artifact.get("opportunity_id") or "")
    opportunity = opportunity_by_id.get(opportunity_id)
    refs = _string_list(opportunity.get("evidence_refs")) if opportunity is not None else []
    unknown_refs = [ref for ref in refs if ref not in valid_ref_ids]
    if opportunity is None:
        problems.append(
            _problem(
                "high",
                "unknown_artifact_opportunity",
                target,
                f"artifact candidate 引用了不存在的 opportunity：{opportunity_id or '(empty)'}",
            )
        )
    elif not refs:
        problems.append(
            _problem(
                "high",
                "artifact_without_evidence",
                target,
                "artifact candidate 的来源机会缺少证据。",
            )
        )
    for ref in unknown_refs:
        problems.append(
            _problem(
                "high",
                "unknown_artifact_evidence_ref",
                target,
                f"artifact candidate 来源证据不存在：{ref}",
            )
        )
    if not str(artifact.get("proposed_content") or "").strip():
        problems.append(
            _problem(
                "medium",
                "empty_artifact_content",
                target,
                "artifact candidate 缺少 proposed_content。",
            )
        )
    status = "supported" if opportunity is not None and refs and not unknown_refs else "unsupported"
    return {
        "target": target,
        "kind": "artifact_candidate",
        "id": str(artifact.get("id") or ""),
        "title": str(artifact.get("artifact_type") or ""),
        "status": status,
        "evidence_refs": refs[:8],
        "evidence_quotes": _evidence_quotes(refs, quote_by_ref),
    }


def _audit_focus_artifact_candidate(
    *,
    target: str,
    artifact: dict[str, Any],
    valid_ref_ids: set[str],
    quote_by_ref: dict[str, str],
    problems: list[dict[str, str]],
) -> dict[str, Any]:
    refs = _string_list(artifact.get("evidence_refs"))
    unknown_refs = [ref for ref in refs if ref not in valid_ref_ids]
    if not refs:
        problems.append(
            _problem(
                "high",
                "focus_artifact_without_evidence",
                target,
                "report_focus 推荐产物缺少 evidence_refs。",
            )
        )
    for ref in unknown_refs:
        problems.append(
            _problem(
                "high",
                "unknown_focus_artifact_evidence_ref",
                target,
                f"report_focus 推荐产物证据不存在：{ref}",
            )
        )
    if not str(artifact.get("proposed_content") or "").strip():
        problems.append(
            _problem(
                "medium",
                "empty_focus_artifact_content",
                target,
                "report_focus 推荐产物缺少 proposed_content。",
            )
        )
    status = "supported" if refs and not unknown_refs else "unsupported"
    return {
        "target": target,
        "kind": "report_focus_artifact",
        "id": str(artifact.get("id") or ""),
        "title": str(artifact.get("title") or ""),
        "status": status,
        "evidence_refs": refs[:8],
        "evidence_quotes": _evidence_quotes(refs, quote_by_ref),
    }


def _evidence_quotes(refs: list[str], quote_by_ref: dict[str, str]) -> list[dict[str, str]]:
    quotes: list[dict[str, str]] = []
    for ref in refs:
        quote = quote_by_ref.get(ref, "").strip()
        if quote:
            quotes.append({"id": ref, "quote": quote[:280]})
        if len(quotes) >= 3:
            break
    return quotes


def _audit_status(
    *,
    audited_count: int,
    evidence_ref_count: int,
    high_problem_count: int,
    problem_count: int,
) -> str:
    if audited_count == 0 or evidence_ref_count == 0:
        return "insufficient_data"
    if high_problem_count:
        return "weak"
    if problem_count:
        return "pass_with_warnings"
    return "pass"


def _summary(status: str, supported: int, audited_count: int, problem_count: int) -> str:
    if status == "pass":
        return f"证据审计通过：{supported}/{audited_count} 个诊断对象可回指到证据。"
    if status == "pass_with_warnings":
        return (
            f"证据审计通过但有 {problem_count} 个低/中风险问题："
            f"{supported}/{audited_count} 个对象可追溯。"
        )
    if status == "weak":
        return (
            f"证据链较弱：{supported}/{audited_count} 个对象可追溯，"
            f"发现 {problem_count} 个问题。"
        )
    return "证据不足，无法完成审计。"


def _problem(severity: str, code: str, target: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "target": target,
        "message": message,
    }


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
