# Contributing to recodex

Thanks for helping improve recodex. This project is currently focused on one product path:
profile real AI coding sessions, configure a user-owned LLM provider, and generate an actionable
workflow report from the Dashboard home page.

## What We Maintain

- The LLM-backed AI coding efficiency profiling report.
- Session import, storage, redaction, privacy, and dashboard workflows.
- Provider support for Codex, Claude Code, Cursor, and OpenAI-compatible LLMs.
- Report evidence, artifact suggestions, and verification signals.

Legacy report modes, local rules-only report generation, and broad analytics dashboards are not
current product priorities.

Do not add new public report commands. The only maintained report surface is the Dashboard report
flow, plus `recodex report` as a headless wrapper for the same LLM-backed profiling report.

## Development Setup

```bash
uv sync
make dashboard-install
make test
make dashboard-build
```

Run the app locally with:

```bash
make dashboard-serve
```

## Pull Request Expectations

- Keep each PR scoped to one coherent change.
- Add or update tests for behavior changes.
- Run `make test` for Python changes.
- Run `make dashboard-build` for Dashboard changes.
- Include screenshots or recordings for visible UI changes.
- Do not commit local transcripts, generated `.recodex` state, API keys, or private reports.

## Coding Notes

Python code lives in `src/recodex/` and uses `unittest`. Dashboard code lives in
`dashboard/src/` and uses React + TypeScript. Follow existing naming and structure before adding
new abstractions.

## Issue Triage

When filing an issue, include the affected command or Dashboard page, the expected result, the
actual result, and whether LLM settings were configured. Redact transcript content, keys, paths,
and provider responses before sharing logs.
