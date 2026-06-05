from __future__ import annotations

import os
from pathlib import Path

APP_DIR = ".recodex"
DB_FILENAME = "recodex.sqlite3"


def state_dir() -> Path:
    configured = os.environ.get("RECODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / APP_DIR).resolve()


def db_path(configured: str | None = None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    env_db = os.environ.get("RECODEX_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()
    return state_dir() / DB_FILENAME


def reports_dir(configured: str | None = None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    return state_dir() / "reports"


def exports_dir(configured: str | None = None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    return state_dir() / "exports"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
