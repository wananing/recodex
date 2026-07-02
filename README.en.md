# recodex

English | [中文](README.md)

<p align="center">
  <img src="docs/assets/recodex-promo-hero.jpg" alt="recodex profiles avoidable cost in AI coding sessions and turns it into next actions" width="100%">
</p>

<p align="center">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-10b981">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-2563eb">
  <img alt="Workflow profiler" src="https://img.shields.io/badge/workflow-profiler-f59e0b">
  <img alt="Actionable reports" src="https://img.shields.io/badge/report-actionable-ef4444">
</p>

> Profile your AI coding workflow the way you profile code performance.

`recodex` is an AI coding workflow profiler. It reads real Codex, Claude Code, or Cursor
sessions, calls your configured LLM provider, identifies avoidable collaboration cost, and
generates an actionable report for the next session.

The report is written for the developer using AI coding tools. It does not rate whether the AI is
smart. It explains how to give context earlier, split work better, verify results, reduce
corrections, and preserve reusable project knowledge.

## Why Use It

- **Expose hidden waste**: spot late context, repeated command discovery, permission loops, task drift, and missing verification.
- **Improve the next session**: learn how to provide context, split tasks, and define acceptance criteria earlier.
- **Preserve reusable knowledge**: turn repeated issues into `AGENTS.md`, checklists, scripts, or skill candidates.
- **Use real tool history**: supports Codex, Claude Code, and Cursor session records.
- **Keep privacy controlled**: raw transcripts stay on your machine; only the redacted analysis package goes to your configured LLM.

## Product Preview

<p align="center">
  <img src="docs/assets/recodex-promo-report.jpg" alt="recodex report page with avoidable cost, chat evidence, and next actions" width="100%">
</p>

<p align="center">
  <img src="docs/assets/recodex-promo-workflow.jpg" alt="recodex workflow from real sessions to redacted LLM analysis and profiling report" width="100%">
</p>

## Quick Start

```bash
git clone <repo-url>
cd recodex
uv sync
make dashboard-install
make dashboard-build
make dashboard-serve
```

In the Dashboard:

1. Import local sessions.
2. Configure Provider, Model, Base URL, and API Key in `LLM`.
3. Select a project and session on the home page.
4. Click the efficiency profiling report action.
5. Open previous reports from the Reports menu.

## Core Report

The product keeps one core report: **the AI coding efficiency profiling report**.

Report generation requires an LLM. If no LLM provider is enabled, the Dashboard asks you to configure one and does not generate a report.

The report includes:

- avoidable cost
- next-session actions
- chat evidence based on user/assistant text, not tool output
- efficiency findings with cost, cause, and evidence refs
- reusable artifacts such as `AGENTS.md`, checklists, scripts, or skills
- verification evidence

Generated HTML, Markdown, and JSON files are written under local `.recodex/reports`.

## Commands

```bash
make dashboard-serve
PYTHONPATH=src python3 -m recodex serve --dashboard-dir dashboard/dist
PYTHONPATH=src python3 -m recodex scan ~/.codex/sessions
PYTHONPATH=src python3 -m recodex doctor
```

For automation, use the headless entry for the same LLM-backed profiling report:

```bash
PYTHONPATH=src python3 -m recodex report latest --llm --llm-provider volcengine --allow-cloud
```

Daily report generation should go through the Dashboard home page. Legacy local report commands
such as `latest`, `quickstart`, `retro`, and `patterns` are retired and only print migration
guidance.

## LLM Setup

Dashboard presets include:

- Volcengine Ark / Doubao
- DashScope / Qwen
- SiliconFlow / DeepSeek
- OpenAI Responses
- OpenAI-compatible API

Example:

```bash
export ARK_API_KEY=...
export OPENAI_API_KEY=...
```

You control the provider and API key. `recodex` does not provide a hosted backend.

## Privacy

`recodex` reads and writes local files by default:

- original transcripts are read-only
- reports and SQLite state are stored under local `.recodex`
- report inputs are redacted before LLM calls
- API keys, tokens, `.env`, database URLs, cookies, private keys, Authorization headers, home paths, and emails are handled as sensitive data

LLM report generation sends the necessary redacted analysis package. Chat analysis focuses on user and assistant text, not command or tool results as chat conclusions.

## Development

```bash
make test
make dashboard-build
make build
```

Core Python code lives in `src/recodex/`, the Dashboard lives in `dashboard/src/`, and tests live in `tests/`.

## Maintenance and Contributing

- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Maintenance scope: [docs/maintenance.md](docs/maintenance.md)
- License: [MIT License](LICENSE)

## Promo Assets

- README hero: [docs/assets/recodex-promo-hero.jpg](docs/assets/recodex-promo-hero.jpg)
- Report showcase: [docs/assets/recodex-promo-report.jpg](docs/assets/recodex-promo-report.jpg)
- Workflow showcase: [docs/assets/recodex-promo-workflow.jpg](docs/assets/recodex-promo-workflow.jpg)
- GitHub social preview: [docs/assets/recodex-social-preview.jpg](docs/assets/recodex-social-preview.jpg)
