from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from sqlite3 import Row
from typing import Literal

from recodex.privacy import redact_text

ExportConflict = Literal["skip", "overwrite", "rename"]
_MANIFEST_NAME = ".recodex-export.json"


def write_skill_md_exports(
    directory: Path,
    rows: list[Row] | list[dict[str, object]],
    *,
    on_conflict: ExportConflict = "rename",
) -> list[Path]:
    """Materialize improvement rows as `skills/<slug>/SKILL.md` files."""
    return write_skill_md_exports_to_root(
        directory / "skills",
        rows,
        on_conflict=on_conflict,
    )


def write_skill_md_exports_to_root(
    skill_root: Path,
    rows: list[Row] | list[dict[str, object]],
    *,
    on_conflict: ExportConflict = "rename",
) -> list[Path]:
    """Materialize improvement rows as `<skill_root>/<slug>/SKILL.md` files."""
    if on_conflict not in {"skip", "overwrite", "rename"}:
        raise ValueError(f"Unsupported conflict policy: {on_conflict}")

    manifest_path = skill_root / _MANIFEST_NAME
    old_manifest = _load_manifest(manifest_path)
    new_manifest: dict[str, dict[str, str]] = {}
    written: list[Path] = []
    used_slugs: set[str] = set()

    for row in rows:
        row_id = str(_row(row, "id") or len(new_manifest) + 1)
        title = str(_row(row, "title") or f"recodex improvement {row_id}")
        base_slug = _slugify(title, fallback=f"improvement-{row_id}")
        slug = _unique_slug(base_slug, row_id, used_slugs)
        md = _skill_markdown(row, title)
        digest = _content_hash(md)

        prev = old_manifest.get(row_id)
        if prev and prev.get("hash") == digest:
            previous_path = skill_root / str(prev.get("slug") or slug) / "SKILL.md"
            if previous_path.exists():
                new_manifest[row_id] = {"slug": str(prev.get("slug") or slug), "hash": digest}
                used_slugs.add(str(prev.get("slug") or slug))
                continue

        target = skill_root / slug / "SKILL.md"
        if target.exists() and not _manifest_owns_slug(old_manifest, slug):
            if on_conflict == "skip":
                continue
            if on_conflict == "rename":
                slug = _unique_slug(f"{base_slug}-{row_id}", row_id, used_slugs)
                target = skill_root / slug / "SKILL.md"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(md, encoding="utf-8")
        written.append(target)
        new_manifest[row_id] = {"slug": slug, "hash": digest}
        used_slugs.add(slug)

    if new_manifest or old_manifest:
        skill_root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return written


def render_skill_md(row: Row | dict[str, object]) -> tuple[str, str]:
    """Return the default skill slug and SKILL.md content for one improvement row."""
    row_id = str(_row(row, "id") or 1)
    title = str(_row(row, "title") or f"recodex improvement {row_id}")
    slug = _slugify(title, fallback=f"improvement-{row_id}")
    return slug, _skill_markdown(row, title)


def _skill_markdown(row: Row | dict[str, object], title: str) -> str:
    recommendation = redact_text(str(_row(row, "recommendation") or "Review this workflow."))
    evidence = redact_text(str(_row(row, "evidence") or "No evidence recorded."))
    slug_name = title.replace(":", " ").strip()
    return "\n".join(
        [
            "---",
            f"name: {slug_name}",
            "description: Workflow improvement exported from an accepted recodex candidate.",
            "---",
            "",
            f"# {slug_name}",
            "",
            "Use this skill when a similar AI coding workflow or failure mode appears.",
            "",
            "## Guidance",
            "",
            recommendation,
            "",
            "## Evidence",
            "",
            evidence,
            "",
        ]
    )


def _row(row: Row | dict[str, object], key: str) -> object:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _slugify(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _unique_slug(base_slug: str, row_id: str, used_slugs: set[str]) -> str:
    if base_slug not in used_slugs:
        return base_slug
    candidate = f"{base_slug}-{row_id}"
    suffix = 2
    while candidate in used_slugs:
        candidate = f"{base_slug}-{row_id}-{suffix}"
        suffix += 1
    return candidate


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, dict)
    }


def _manifest_owns_slug(manifest: dict[str, dict[str, str]], slug: str) -> bool:
    return any(record.get("slug") == slug for record in manifest.values())
