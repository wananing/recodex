from __future__ import annotations

import hashlib
from collections import Counter

from .models import ImprovementDraft, SessionRecord, TranscriptEvent
from .privacy import redact_text

SANDBOX_TERMS = ("sandbox", "permission", "escalat", "network", "approval", "restricted")
TEST_TERMS = ("test", "pytest", "unittest", "npm run build", "ruff", "lint", "verification")
WORKFLOW_TERMS = ("agents.md", "skill", "checklist", "workflow", "retro", "review")
ERROR_TERMS = ("error", "failed", "exception", "traceback", "timeout", "not found", "失败", "报错")


def count_terms(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def session_signals(events: list[TranscriptEvent]) -> Counter[str]:
    signal_events = [event for event in events if _is_signal_event(event)]
    text = "\n".join(event.text for event in signal_events)
    signals: Counter[str] = Counter()
    signals["errors"] = count_terms(text, ERROR_TERMS)
    signals["sandbox"] = count_terms(text, SANDBOX_TERMS)
    signals["tests"] = count_terms(text, TEST_TERMS)
    signals["workflow"] = count_terms(text, WORKFLOW_TERMS)
    signals["commands"] = sum(1 for event in signal_events if "command" in event.kind.lower())
    return signals


def top_terms(events: list[TranscriptEvent], terms: tuple[str, ...]) -> list[tuple[str, int]]:
    text = "\n".join(event.text for event in events)
    counts = Counter({term: count_terms(text, (term,)) for term in terms})
    return [(term, count) for term, count in counts.most_common() if count > 0]


def mechanism_for_improvement_category(category: object) -> str:
    raw = str(category or "")
    return {
        "agents": "agents_md",
        "workflow": "skill",
        "patterns": "project_doc",
    }.get(raw, raw or "none")


def propose_improvements(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[ImprovementDraft]:
    return _dedupe(_efficiency_improvement_drafts(sessions, events_by_session))


def _efficiency_improvement_drafts(
    sessions: list[SessionRecord],
    events_by_session: dict[str, list[TranscriptEvent]],
) -> list[ImprovementDraft]:
    from .efficiency_analysis import run_efficiency_analysis

    analysis = run_efficiency_analysis(sessions, events_by_session)
    findings_by_id = {finding.id: finding for finding in analysis.findings}
    drafts: list[ImprovementDraft] = []
    for candidate in analysis.artifact_candidates:
        source_findings = [
            finding
            for finding_id in candidate.source_finding_ids
            if (finding := findings_by_id.get(finding_id)) is not None
        ]
        evidence_refs = [
            ref_id
            for finding in source_findings
            for ref_id in finding.evidence_refs
        ]
        evidence = " ".join(
            part
            for part in (
                f"Source findings: {', '.join(candidate.source_finding_ids)}.",
                (
                    f"Evidence refs: {', '.join(dict.fromkeys(evidence_refs))}."
                    if evidence_refs
                    else ""
                ),
                candidate.rationale,
            )
            if part
        )
        recommendation = candidate.proposed_content or candidate.rationale
        drafts.append(
            _draft(
                None,
                candidate.mechanism,
                candidate.title,
                evidence,
                recommendation,
            )
        )
    return drafts


def _draft(
    session_id: str | None,
    category: str,
    title: str,
    evidence: str,
    recommendation: str,
) -> ImprovementDraft:
    fingerprint_source = "\n".join([category, title])
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:32]
    return ImprovementDraft(
        fingerprint=fingerprint,
        session_id=session_id,
        category=category,
        title=redact_text(title)[:180],
        evidence=redact_text(evidence)[:1_000],
        recommendation=redact_text(recommendation)[:1_000],
    )


def _dedupe(drafts: list[ImprovementDraft]) -> list[ImprovementDraft]:
    seen: set[str] = set()
    unique: list[ImprovementDraft] = []
    for draft in drafts:
        key = _draft_key(draft)
        if key in seen:
            continue
        seen.add(key)
        unique.append(draft)
    return unique


def _is_signal_event(event: TranscriptEvent) -> bool:
    if event.role not in {"user", "assistant", "tool", "unknown"} or not event.text.strip():
        return False
    lowered = event.text.strip().lower()
    noise_prefixes = (
        "<environment_context>",
        "<permissions",
        "<collaboration_mode>",
        "<skills_instructions>",
        "cwd=",
        "model=",
    )
    noise_terms = (
        "you are codex",
        "knowledge cutoff",
        "sandbox_mode",
    )
    if lowered.startswith(noise_prefixes):
        return False
    if any(term in lowered for term in noise_terms):
        return False
    if not event.metadata.get("command") and lowered.startswith("chunk id:"):
        return False
    return True


def _draft_key(draft: ImprovementDraft) -> str:
    normalized_title = " ".join(draft.title.lower().split())
    return f"{draft.category}:{normalized_title}"
