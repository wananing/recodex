from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import reports_dir


def load_mining_review(
    *,
    reports_base: Path | None = None,
    output_dir: Path | None = None,
    cluster_id: str | None = None,
    card_limit: int = 12,
) -> dict[str, Any]:
    report_root = reports_dir(str(reports_base) if reports_base else None)
    base_dir = output_dir or report_root / "evidence-mining"
    clusters_path = base_dir / "clusters.json"
    cards_path = base_dir / "cards.jsonl"
    review_queue_path = base_dir / "review_queue.json"
    artifact_candidates_path = base_dir / "artifact_candidates.json"
    coverage_path = base_dir / "coverage_report.md"

    if not base_dir.exists():
        return {
            "ok": True,
            "exists": False,
            "base_dir": str(base_dir),
            "coverage": {},
            "clusters": [],
            "review_queue": [],
            "artifact_candidates": [],
            "artifact_review_queue": [],
            "selected_cluster": None,
            "cards": [],
            "coverage_report": "",
        }

    raw_clusters = _read_json_list(clusters_path)
    raw_review_queue = _read_json_list(review_queue_path)
    artifact_candidates = [
        _artifact_candidate_payload(candidate, source="mining_output")
        for candidate in _read_json_list(artifact_candidates_path)
    ]
    raw_clusters = sorted(
        raw_clusters,
        key=lambda item: (
            -_float(item.get("priority_score")),
            -_int(item.get("frequency")),
            str(item.get("title") or ""),
        ),
    )
    selected_cluster = _selected_cluster(raw_clusters, raw_review_queue, cluster_id)
    selected_card_ids = [
        str(card_id)
        for card_id in (selected_cluster or {}).get("card_ids", [])
        if isinstance(card_id, str)
    ]
    cards = _read_cards(cards_path, selected_card_ids, limit=card_limit)
    coverage_report = _read_text(coverage_path)
    return {
        "ok": True,
        "exists": True,
        "base_dir": str(base_dir),
        "coverage": _parse_coverage(coverage_report),
        "clusters": [_trim_card_ids(cluster) for cluster in raw_clusters],
        "review_queue": [_trim_card_ids(item) for item in raw_review_queue],
        "artifact_candidates": artifact_candidates,
        "artifact_review_queue": _artifact_review_queue(artifact_candidates),
        "selected_cluster": _trim_card_ids(selected_cluster) if selected_cluster else None,
        "cards": cards,
        "coverage_report": coverage_report,
    }


def _selected_cluster(
    clusters: list[dict[str, Any]],
    review_queue: list[dict[str, Any]],
    cluster_id: str | None,
) -> dict[str, Any] | None:
    if cluster_id:
        for cluster in clusters:
            if cluster.get("cluster_id") == cluster_id:
                return cluster
    review_ids = [item.get("cluster_id") for item in review_queue]
    for review_id in review_ids:
        for cluster in clusters:
            if cluster.get("cluster_id") == review_id:
                return cluster
    return clusters[0] if clusters else None


def _read_cards(path: Path, wanted_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    wanted = set(wanted_ids)
    cards: list[dict[str, Any]] = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            card = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(card, dict):
            continue
        if wanted and card.get("card_id") not in wanted:
            continue
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards


def _trim_card_ids(item: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    payload = dict(item)
    raw_ids = payload.get("card_ids")
    if not isinstance(raw_ids, list):
        return payload
    card_ids = [str(card_id) for card_id in raw_ids if isinstance(card_id, str)]
    payload["card_count"] = len(card_ids)
    payload["card_ids"] = card_ids[:limit]
    return payload


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _artifact_candidate_payload(candidate: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        **candidate,
        "artifact_source": source,
    }


def _artifact_review_queue(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in candidates
        if str(candidate.get("status") or "proposed") in {"proposed", "ready_for_review"}
    ]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_coverage(markdown: str) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for line in markdown.splitlines():
        match = re.match(r"^- ([^:]+):\s*(.+)$", line.strip())
        if not match:
            continue
        key = _slug_key(match.group(1))
        value = match.group(2).strip()
        coverage[key] = _int(value) if value.isdigit() else value
    return coverage


def _slug_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
