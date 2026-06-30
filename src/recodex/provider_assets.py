from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED_ASSET_TYPES = {
    "instructions",
    "config",
    "skills",
    "mcp",
    "rules",
    "hooks",
    "commands",
    "plans",
    "memories",
}


@dataclass(frozen=True)
class ProviderCapabilities:
    has_sessions: bool = False
    has_instructions: bool = False
    has_config: bool = False
    has_skills: bool = False
    has_plans: bool = False
    has_hooks: bool = False
    has_commands: bool = False
    has_rules: bool = False
    has_memories: bool = False
    has_session_search: bool = False
    has_mcp_servers: bool = False

    def to_payload(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderInfo:
    provider_id: str
    name: str
    home_path: str
    detected: bool
    capabilities: ProviderCapabilities

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "name": self.name,
            "home_path": self.home_path,
            "detected": self.detected,
            "capabilities": self.capabilities.to_payload(),
        }


@dataclass(frozen=True)
class ProviderAsset:
    asset_id: str
    provider_id: str
    asset_type: str
    name: str
    path: str | None
    scope: str
    project_path: str | None
    description: str | None
    modified_at: str | None
    size_bytes: int | None
    tags: tuple[str, ...]
    metadata: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.asset_id,
            "provider_id": self.provider_id,
            "asset_type": self.asset_type,
            "name": self.name,
            "path": self.path,
            "scope": self.scope,
            "project_path": self.project_path,
            "description": self.description,
            "modified_at": self.modified_at,
            "size_bytes": self.size_bytes,
            "tags": list(self.tags),
            "metadata": self.metadata,
        }


def discover_providers(
    *,
    state_db: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    project_roots: tuple[Path, ...] | None = None,
) -> list[ProviderInfo]:
    project_roots = (
        _normalized_project_roots(project_roots)
        if project_roots is not None
        else _project_roots_from_db(state_db)
    )
    codex = _codex_home(codex_home)
    claude = _claude_home(claude_home)
    return [
        _codex_provider_info(codex, project_roots),
        _claude_provider_info(claude, project_roots),
    ]


def list_provider_assets(
    provider_id: str,
    asset_type: str | None = None,
    *,
    state_db: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    project_roots: tuple[Path, ...] | None = None,
    limit: int = 200,
) -> list[ProviderAsset]:
    normalized_type = (asset_type or "all").strip().lower()
    if normalized_type != "all" and normalized_type not in SUPPORTED_ASSET_TYPES:
        raise ValueError(f"Unsupported provider asset type: {asset_type}")
    if limit <= 0:
        return []

    project_roots = (
        _normalized_project_roots(project_roots)
        if project_roots is not None
        else _project_roots_from_db(state_db)
    )
    normalized_provider = provider_id.strip().lower()
    if normalized_provider == "codex":
        assets = _codex_assets(_codex_home(codex_home), project_roots, limit=limit)
    elif normalized_provider in {"claude", "claude-code", "claude_code"}:
        assets = _claude_assets(_claude_home(claude_home), project_roots, limit=limit)
    else:
        raise ValueError(f"Unsupported provider: {provider_id}")

    if normalized_type == "all":
        return assets[:limit]
    return [asset for asset in assets if asset.asset_type == normalized_type][:limit]


def _codex_provider_info(home: Path, project_roots: tuple[Path, ...]) -> ProviderInfo:
    capabilities = ProviderCapabilities(
        has_sessions=(home / "sessions").exists(),
        has_instructions=any(_existing_paths(_codex_instruction_paths(home, project_roots))),
        has_config=(home / "config.toml").exists(),
        has_skills=(home / "skills").exists()
        or any(
            (root / ".codex" / "skills").exists()
            or (root / ".agents" / "skills").exists()
            for root in project_roots
        ),
        has_rules=(home / "rules").exists(),
        has_session_search=(home / "sessions").exists(),
        has_mcp_servers=bool(_parse_mcp_servers(home / "config.toml")),
    )
    detected = home.exists() or any(
        getattr(capabilities, key)
        for key in (
            "has_sessions",
            "has_instructions",
            "has_config",
            "has_skills",
            "has_rules",
            "has_mcp_servers",
        )
    )
    return ProviderInfo(
        provider_id="codex",
        name="Codex CLI",
        home_path=str(home),
        detected=detected,
        capabilities=capabilities,
    )


def _claude_provider_info(home: Path, project_roots: tuple[Path, ...]) -> ProviderInfo:
    settings = home / "settings.json"
    commands = home / "commands"
    skills = home / "skills"
    plans = home / "plans"
    memories = home / "memories"
    hooks = home / "hooks"
    capabilities = ProviderCapabilities(
        has_sessions=(home / "projects").exists(),
        has_instructions=any((root / "CLAUDE.md").exists() for root in project_roots),
        has_config=settings.exists(),
        has_skills=skills.exists(),
        has_plans=plans.exists(),
        has_hooks=hooks.exists(),
        has_commands=commands.exists(),
        has_memories=memories.exists(),
        has_session_search=(home / "projects").exists(),
    )
    detected = home.exists() or any(asdict(capabilities).values())
    return ProviderInfo(
        provider_id="claude-code",
        name="Claude Code",
        home_path=str(home),
        detected=detected,
        capabilities=capabilities,
    )


def _codex_assets(
    home: Path,
    project_roots: tuple[Path, ...],
    *,
    limit: int,
) -> list[ProviderAsset]:
    assets: list[ProviderAsset] = []
    assets.extend(
        _path_assets("codex", "instructions", _codex_instruction_paths(home, project_roots))
    )
    assets.extend(_path_assets("codex", "config", [(home / "config.toml", "global", None)]))
    assets.extend(_scan_skill_assets("codex", home / "skills", "global", None, limit))
    for root in project_roots:
        assets.extend(
            _scan_skill_assets("codex", root / ".codex" / "skills", "project", root, limit)
        )
        assets.extend(
            _scan_skill_assets("codex", root / ".agents" / "skills", "project", root, limit)
        )
    assets.extend(_mcp_assets("codex", home / "config.toml"))
    assets.extend(_scan_file_assets("codex", "rules", home / "rules", "global", None, limit))
    assets.extend(_scan_file_assets("codex", "hooks", home / "hooks", "global", None, limit))
    assets.extend(_scan_file_assets("codex", "commands", home / "commands", "global", None, limit))
    return _dedupe_assets(assets)[:limit]


def _claude_assets(
    home: Path,
    project_roots: tuple[Path, ...],
    *,
    limit: int,
) -> list[ProviderAsset]:
    assets: list[ProviderAsset] = []
    assets.extend(_path_assets("claude-code", "config", [(home / "settings.json", "global", None)]))
    assets.extend(
        _path_assets(
            "claude-code",
            "instructions",
            [(root / "CLAUDE.md", "project", root) for root in project_roots],
        )
    )
    assets.extend(_scan_skill_assets("claude-code", home / "skills", "global", None, limit))
    assets.extend(_scan_file_assets("claude-code", "plans", home / "plans", "global", None, limit))
    assets.extend(_scan_file_assets("claude-code", "hooks", home / "hooks", "global", None, limit))
    assets.extend(
        _scan_file_assets("claude-code", "commands", home / "commands", "global", None, limit)
    )
    assets.extend(
        _scan_file_assets("claude-code", "memories", home / "memories", "global", None, limit)
    )
    return _dedupe_assets(assets)[:limit]


def _codex_instruction_paths(
    home: Path,
    project_roots: tuple[Path, ...],
) -> list[tuple[Path, str, Path | None]]:
    paths: list[tuple[Path, str, Path | None]] = [
        (home / "AGENTS.override.md", "global", None),
        (home / "AGENTS.md", "global", None),
    ]
    for root in project_roots:
        paths.append((root / "AGENTS.md", "project", root))
        paths.append((root / ".codex" / "AGENTS.md", "project", root))
    return paths


def _path_assets(
    provider_id: str,
    asset_type: str,
    paths: list[tuple[Path, str, Path | None]],
) -> list[ProviderAsset]:
    return [
        _asset_from_path(
            provider_id,
            asset_type,
            path,
            scope=scope,
            project_path=str(project) if project else None,
        )
        for path, scope, project in paths
        if path.exists() and path.is_file()
    ]


def _scan_skill_assets(
    provider_id: str,
    root: Path,
    scope: str,
    project_root: Path | None,
    limit: int,
) -> list[ProviderAsset]:
    if not root.exists() or not root.is_dir():
        return []
    paths = sorted(root.rglob("SKILL.md"))[:limit]
    return [
        _asset_from_path(
            provider_id,
            "skills",
            path,
            scope=scope,
            project_path=str(project_root) if project_root else None,
        )
        for path in paths
        if path.is_file()
    ]


def _scan_file_assets(
    provider_id: str,
    asset_type: str,
    root: Path,
    scope: str,
    project_root: Path | None,
    limit: int,
) -> list[ProviderAsset]:
    if not root.exists() or not root.is_dir():
        return []
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in {".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh"}
    ][:limit]
    return [
        _asset_from_path(
            provider_id,
            asset_type,
            path,
            scope=scope,
            project_path=str(project_root) if project_root else None,
        )
        for path in paths
    ]


def _mcp_assets(provider_id: str, config_path: Path) -> list[ProviderAsset]:
    return [
        ProviderAsset(
            asset_id=_asset_id(provider_id, "mcp", server["name"]),
            provider_id=provider_id,
            asset_type="mcp",
            name=f"MCP: {server['name']}",
            path=str(config_path),
            scope="global",
            project_path=None,
            description=server.get("command"),
            modified_at=_mtime_iso(config_path),
            size_bytes=_size_bytes(config_path),
            tags=("mcp",),
            metadata=server,
        )
        for server in _parse_mcp_servers(config_path)
    ]


def _asset_from_path(
    provider_id: str,
    asset_type: str,
    path: Path,
    *,
    scope: str,
    project_path: str | None,
) -> ProviderAsset:
    metadata = _markdown_metadata(path) if path.suffix.lower() in {".md", ".markdown"} else {}
    name = (
        _string_or_none(metadata.get("name"))
        or _string_or_none(metadata.get("title"))
        or _heading_title(path)
        or _default_asset_name(asset_type, path)
    )
    description = (
        _string_or_none(metadata.get("description"))
        or _first_paragraph(path)
        or _asset_description(asset_type, path)
    )
    return ProviderAsset(
        asset_id=_asset_id(provider_id, asset_type, f"{scope}:{project_path or ''}:{path}"),
        provider_id=provider_id,
        asset_type=asset_type,
        name=name,
        path=str(path),
        scope=scope,
        project_path=project_path,
        description=description,
        modified_at=_mtime_iso(path),
        size_bytes=_size_bytes(path),
        tags=_asset_tags(asset_type, path, scope),
        metadata={
            key: value
            for key, value in metadata.items()
            if isinstance(value, (str, int, float, bool))
        },
    )


def _markdown_metadata(path: Path) -> dict[str, Any]:
    text = _read_text_prefix(path)
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end_index is None:
        return {}
    metadata: dict[str, Any] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            metadata[key] = value
    return metadata


def _heading_title(path: Path) -> str | None:
    for line in _read_text_prefix(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


def _first_paragraph(path: Path) -> str | None:
    in_frontmatter = False
    frontmatter_done = False
    for line in _read_text_prefix(path).splitlines():
        stripped = line.strip()
        if stripped == "---" and not frontmatter_done:
            in_frontmatter = not in_frontmatter
            if not in_frontmatter:
                frontmatter_done = True
            continue
        if in_frontmatter or not stripped or stripped.startswith("#"):
            continue
        return stripped[:280]
    return None


def _asset_description(asset_type: str, path: Path) -> str | None:
    if asset_type == "config":
        return "Provider configuration"
    if asset_type == "instructions":
        return "Instruction file"
    if asset_type == "skills":
        return "Reusable workflow skill"
    return path.name


def _default_asset_name(asset_type: str, path: Path) -> str:
    if asset_type == "skills" and path.parent.name:
        return path.parent.name
    return path.name


def _asset_tags(asset_type: str, path: Path, scope: str) -> tuple[str, ...]:
    tags = [asset_type, scope]
    if path.suffix:
        tags.append(path.suffix.lower().lstrip("."))
    return tuple(tags)


def _parse_mcp_servers(config_path: Path) -> list[dict[str, str]]:
    if not config_path.exists() or not config_path.is_file():
        return []
    servers: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in _read_text_prefix(config_path, limit=256_000).splitlines():
        line = raw_line.strip()
        match = re.match(r"^\[mcp_servers\.([^\]]+)\]$", line)
        if match:
            if current:
                servers.append(current)
            current = {"name": match.group(1).strip().strip("\"'")}
            continue
        if current is None:
            continue
        if line.startswith("[") and line.endswith("]"):
            servers.append(current)
            current = None
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key in {"command", "url"}:
            current[key] = value
    if current:
        servers.append(current)
    return servers


def _project_roots_from_db(state_db: Path | None) -> tuple[Path, ...]:
    if state_db is None or not state_db.exists():
        return ()
    try:
        conn = sqlite3.connect(state_db)
        rows = conn.execute(
            """
            SELECT DISTINCT project_path
            FROM sessions
            WHERE project_path IS NOT NULL AND project_path != '' AND project_path != '(unknown)'
            ORDER BY project_path
            """
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        with suppress(UnboundLocalError):
            conn.close()
    roots: list[Path] = []
    for row in rows:
        root = Path(str(row[0])).expanduser()
        if root.exists() and root.is_dir():
            roots.append(root.resolve())
    return tuple(dict.fromkeys(roots))


def _normalized_project_roots(project_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in project_roots:
        expanded = root.expanduser()
        if expanded.exists() and expanded.is_dir():
            roots.append(expanded.resolve())
    return tuple(dict.fromkeys(roots))


def _existing_paths(paths: list[tuple[Path, str, Path | None]]) -> list[Path]:
    return [path for path, _scope, _project in paths if path.exists()]


def _dedupe_assets(assets: list[ProviderAsset]) -> list[ProviderAsset]:
    seen: set[str] = set()
    unique: list[ProviderAsset] = []
    for asset in assets:
        if asset.asset_id in seen:
            continue
        seen.add(asset.asset_id)
        unique.append(asset)
    return unique


def _codex_home(path: Path | None) -> Path:
    return _home_path(path, "CODEX_HOME", "~/.codex")


def _claude_home(path: Path | None) -> Path:
    return _home_path(path, "CLAUDE_HOME", "~/.claude")


def _home_path(path: Path | None, env_key: str, default: str) -> Path:
    raw = path if path is not None else Path(os.environ.get(env_key, default))
    return raw.expanduser()


def _asset_id(provider_id: str, asset_type: str, key: str) -> str:
    digest = hashlib.sha256(f"{provider_id}:{asset_type}:{key}".encode()).hexdigest()[:20]
    return f"{provider_id}_{asset_type}_{digest}"


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _size_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _read_text_prefix(path: Path, *, limit: int = 64_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
