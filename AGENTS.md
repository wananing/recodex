# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python CLI package with an optional React dashboard. Core code lives in
`src/recodex/`, with CLI entry points in `cli.py` and `__main__.py`. Domain modules include
`analysis.py`, `transcripts.py`, `storage.py`, `reports.py`, and feature folders such as
`importers/` and `exports/`. Python tests are in `tests/` and follow the same feature names
where practical. The dashboard is under `dashboard/`, with React source in `dashboard/src/`,
static assets in `dashboard/public/`, and Vite/TypeScript config at the dashboard root.
Documentation and diagrams live in `docs/` and `docs/assets/`; release/publishing scripts are
in `scripts/`.

## Build, Test, and Development Commands

- `make test`: runs the Python unittest suite with `PYTHONPATH=src`.
- `make build`: builds the Python package with `uv build`.
- `make dashboard-install`: installs dashboard npm dependencies.
- `make dashboard-dev`: starts the Vite dashboard development server.
- `make dashboard-build`: type-checks and builds the dashboard.
- `make dashboard-serve`: builds the dashboard, then serves it through `python3 -m recodex`.
- `PYTHONPATH=src python3 -m recodex scan`: runs the CLI locally without installing.

## Coding Style & Naming Conventions

Python targets 3.10 and uses Ruff settings from `pyproject.toml`: 100-character lines and lint
rules for pycodestyle, pyflakes, import sorting, pyupgrade, bugbear, and simplification. Use
4-space indentation, `snake_case` for modules/functions, `PascalCase` for classes, and clear
CLI option names. Dashboard code is TypeScript/React; use `PascalCase` component files,
`camelCase` helpers, and keep reusable UI under `dashboard/src/components/`.

## Testing Guidelines

Tests use Python `unittest` discovery. Add or update `tests/test_*.py` files for CLI behavior,
storage changes, importers, reports, and analysis logic. Prefer focused fixtures and deterministic
sample transcript data. Run `make test` before submitting Python changes. For dashboard changes,
run `make dashboard-build`; no separate frontend test runner is configured.

## Commit & Pull Request Guidelines

Recent history uses conventional commit prefixes such as `feat:`, `docs:`, and `refactor:`.
Keep subjects imperative and scoped to one change. Pull requests should include a short summary,
linked issue when applicable, commands run with results, and screenshots or recordings for
dashboard UI changes.

## Security & Configuration Tips

Treat local transcripts, reports, and provider settings as sensitive. Do not commit API keys,
private conversation data, generated local databases, or machine-specific configuration. Prefer
documented CLI flags and environment variables over hard-coded paths.
