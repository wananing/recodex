from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodexSourceConfig:
    enabled: bool
    sessions_dir: Path


@dataclass(frozen=True)
class SourcesConfig:
    codex: CodexSourceConfig


@dataclass(frozen=True)
class PrivacyConfig:
    redact_secrets: bool
    redact_api_keys: bool
    redact_tokens: bool
    redact_passwords: bool
    redact_env_files: bool
    redact_home_path: bool
    redact_emails: bool


@dataclass(frozen=True)
class AnalysisConfig:
    model: str | None
    local_only: bool
    max_session_tokens: int
    llm_provider: str
    llm_model: str | None
    llm_api_key: str | None
    llm_api_key_env: str | None
    llm_base_url: str | None


@dataclass(frozen=True)
class OutputsConfig:
    reports_dir: Path
    agents_md: Path
    skills_dir: Path
    checklists_dir: Path
    scripts_dir: Path


@dataclass(frozen=True)
class ReviewConfig:
    sources: SourcesConfig
    privacy: PrivacyConfig
    analysis: AnalysisConfig
    outputs: OutputsConfig


def load_config(project_dir: Path | None = None) -> ReviewConfig:
    root = (project_dir or Path.cwd()).expanduser().resolve()
    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser().resolve()
    raw = _default_raw_config(root, home)

    global_path = home / ".ai-review" / "config.toml"
    project_path = root / ".ai-review.toml"
    if global_path.exists():
        _deep_update(raw, _load_toml(global_path))
    if project_path.exists():
        _deep_update(raw, _load_toml(project_path))

    return _build_config(raw, root, home)


def _default_raw_config(root: Path, home: Path) -> dict[str, Any]:
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    return {
        "sources": {
            "codex": {
                "enabled": True,
                "sessions_dir": str(codex_home / "sessions"),
            }
        },
        "privacy": {
            "redact_secrets": True,
            "redact_api_keys": True,
            "redact_tokens": True,
            "redact_passwords": True,
            "redact_env_files": True,
            "redact_home_path": True,
            "redact_emails": True,
        },
        "analysis": {
            "model": None,
            "local_only": True,
            "max_session_tokens": 80_000,
            "llm_provider": "openai",
            "llm_model": None,
            "llm_api_key": None,
            "llm_api_key_env": None,
            "llm_base_url": None,
        },
        "outputs": {
            "reports_dir": str(root / ".ai-review" / "reports"),
            "agents_md": str(root / "AGENTS.md"),
            "skills_dir": str(root / ".agents" / "skills"),
            "checklists_dir": str(root / "docs" / "ai-checklists"),
            "scripts_dir": str(root / "scripts" / "ai"),
        },
    }


def _build_config(raw: dict[str, Any], root: Path, home: Path) -> ReviewConfig:
    codex = raw.get("sources", {}).get("codex", {})
    privacy = raw.get("privacy", {})
    analysis = raw.get("analysis", {})
    outputs = raw.get("outputs", {})
    return ReviewConfig(
        sources=SourcesConfig(
            codex=CodexSourceConfig(
                enabled=bool(codex.get("enabled", True)),
                sessions_dir=_resolve_path(codex.get("sessions_dir"), root, home),
            )
        ),
        privacy=PrivacyConfig(
            redact_secrets=bool(privacy.get("redact_secrets", True)),
            redact_api_keys=bool(privacy.get("redact_api_keys", True)),
            redact_tokens=bool(privacy.get("redact_tokens", True)),
            redact_passwords=bool(privacy.get("redact_passwords", True)),
            redact_env_files=bool(privacy.get("redact_env_files", True)),
            redact_home_path=bool(privacy.get("redact_home_path", True)),
            redact_emails=bool(privacy.get("redact_emails", True)),
        ),
        analysis=AnalysisConfig(
            model=analysis.get("model"),
            local_only=bool(analysis.get("local_only", True)),
            max_session_tokens=int(analysis.get("max_session_tokens", 80_000)),
            llm_provider=str(analysis.get("llm_provider") or analysis.get("provider") or "openai"),
            llm_model=analysis.get("llm_model") or analysis.get("model"),
            llm_api_key=analysis.get("llm_api_key") or analysis.get("api_key"),
            llm_api_key_env=analysis.get("llm_api_key_env") or analysis.get("api_key_env"),
            llm_base_url=analysis.get("llm_base_url") or analysis.get("base_url"),
        ),
        outputs=OutputsConfig(
            reports_dir=_resolve_path(outputs.get("reports_dir"), root, home),
            agents_md=_resolve_path(outputs.get("agents_md"), root, home),
            skills_dir=_resolve_path(outputs.get("skills_dir"), root, home),
            checklists_dir=_resolve_path(outputs.get("checklists_dir"), root, home),
            scripts_dir=_resolve_path(outputs.get("scripts_dir"), root, home),
        ),
    )


def _resolve_path(value: object, root: Path, home: Path) -> Path:
    path = Path(str(value)).expanduser()
    if str(value).startswith("~/"):
        path = home / str(value)[2:]
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _parse_simple_toml(path.read_text(encoding="utf-8"))
    with path.open("rb") as file:
        return tomllib.load(file)


def _parse_simple_toml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current: dict[str, Any] = data
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = data
            for part in line[1:-1].split("."):
                current = current.setdefault(part.strip(), {})
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        current[key] = _parse_simple_value(value)
    return data


def _parse_simple_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
