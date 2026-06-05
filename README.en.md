# ai-dev-review

English | [中文](README.md)

> Review your AI coding sessions and identify concrete ways to use Codex, Claude Code, and Cursor better next time.

`ai-dev-review` is a local-first CLI for AI coding retrospectives. It reads local AI coding session transcripts, analyzes how the session was used, and generates static reports plus reviewable workflow improvement candidates.

The first supported data source is Codex session transcripts. The first default output is a local `report.html`.

![ai-dev-review generated hero](docs/assets/ai-dev-review-hero.png)

It focuses on:

- which context arrived too late
- whether the task boundary was too large or drifted
- whether the user should have paused, corrected direction, or split the task earlier
- whether the session ended without test, build, typecheck, lint, or manual verification evidence
- which facts should become `AGENTS.md`, checklists, scripts, hooks, CI rules, or skills

It is not a chat viewer, not a prompt rewriting tool, and not a generic AI summary tool.

![ai-dev-review overview](docs/assets/readme-overview.svg)

---

## Demo

Run the default flow:

```bash
ai-review
```

Default behavior:

```text
Found recent Codex sessions
Grouped sessions by project
Generated retrospectives
Generated project report.json and report.html
Proposed improvement candidates
Exported AGENTS/checklist/script/skill/CI artifacts
```

![Quickstart flow](docs/assets/quickstart-flow.svg)

Example output:

```text
Quickstart scanned 2 session(s) from the last 7d.

Projects:
Project: /path/to/project
  Reports: .ai-review/reports/projects/project-1234abcd
  Report JSON: .ai-review/reports/projects/project-1234abcd/report.json
  Report HTML: .ai-review/reports/projects/project-1234abcd/report.html
  Exports: .ai-review/exports/quickstart/projects/project-1234abcd
```

Generated report screenshot:

![Generated HTML report screenshot](docs/assets/report-page-screenshot.png)

Generate a report for the latest indexed session:

```bash
ai-review report latest
```

Generate and open the HTML report:

```bash
ai-review report latest --open
```

---

## Why

Using Codex well is not only about model quality.

A messy AI coding session often comes from workflow issues:

- The task starts without enough context.
- Important project rules appear too late.
- One session mixes debugging, refactoring, deployment, and docs.
- The agent keeps exploring the wrong path.
- The final response says "done" without verification evidence.
- The same project facts are explained again and again.

`ai-dev-review` turns real AI coding sessions into actionable usage feedback.

The goal is simple:

> Learn how to use AI coding agents better from your own sessions.

---

## What It Generates

The default quickstart flow writes project-level reports and artifacts.

### HTML Report

`report.html` is the user-facing static report. It is a self-contained HTML file with structured JSON embedded inside:

```html
<script id="report-data" type="application/json">...</script>
```

The page does not scan Codex sessions and does not fetch an external JSON file at runtime. The CLI performs parsing and analysis first, then renders the page.

![Report anatomy](docs/assets/report-anatomy.svg)

### Structured JSON

`report.json` is the standard structured data source for the page. It contains:

- `meta`
- `summary`
- `metrics`
- `flow`
- `issues`
- `context_frontload`
- `intervention`
- `verification`
- `suggestions`
- `artifacts`
- `evidence`

### Improvement Candidates

The tool proposes concrete workflow improvements, such as:

- update `AGENTS.md`
- add a completion checklist
- turn repeated commands into scripts
- suggest hook or CI checks
- create reusable skills

All improvement candidates should be reviewed before they are applied or exported.

![Improvement loop](docs/assets/improvement-loop.svg)

---

## What It Is Not

`ai-dev-review` is not:

- a full Codex transcript viewer
- a prompt rewriting assistant
- a user-facing rulebase management system
- a generic chat summarizer
- a replacement for tests or code review
- a tool that judges whether the final code is correct

It analyzes the **usage process** around an AI coding session.

---

## Installation

From source:

```bash
git clone git@github.com:wananing/ai-review.git
cd ai-review
uv sync
uv run ai-review
```

Run without installing:

```bash
PYTHONPATH=src python3 -m ai_dev_review
```

Run through `uv`:

```bash
uv run ai-review
```

---

## Quick Start

Analyze recent Codex sessions and generate project reports:

```bash
ai-review
```

Limit the scan window:

```bash
ai-review --since 7d --limit 5
```

Generate a single-session HTML report:

```bash
ai-review report latest
```

Generate and open a single-session HTML report:

```bash
ai-review report latest --open
```

Run deterministic local analysis:

```bash
ai-review retro latest --local-only
```

Test the LLM analysis path with the mock provider:

```bash
ai-review retro latest --llm --llm-provider mock
```

---

## Core Commands

### `ai-review`

Default quickstart flow. It reads a small recent window, groups sessions by project, generates HTML reports, proposes improvements, and exports workflow artifacts.

```bash
ai-review
```

Default outputs:

```text
.ai-review/reports/quickstart-index.md
.ai-review/reports/projects/<project>/
  report.json
  report.html
  retro-*.md
  retro-*.json
  retro-*.html
  patterns-7d.md
  improvements.md
.ai-review/exports/quickstart/projects/<project>/
  AGENTS.patch.md
  skills/ai-dev-review-retro/SKILL.md
  checklists/ai-review-checklist.md
  scripts/ai-review-verify.sh
  ci/verify.yml
```

### `ai-review init`

Catalog Codex transcript metadata first, without fully reading every session. This is useful when `~/.codex/sessions` is large.

```bash
ai-review init
ai-review init --select 1 --process-limit 20
```

### `ai-review scan`

Parse transcript files into local SQLite.

```bash
ai-review scan ~/.codex/sessions
ai-review import ./some-session.jsonl
```

### `ai-review report latest`

Generate a static HTML report for one indexed session.

```bash
ai-review report latest
ai-review report latest --open
```

This also writes matching `retro-*.json` and `retro-*.md` files.

### `ai-review retro`

Generate Markdown retrospectives and matching JSON / HTML files.

```bash
ai-review retro latest
ai-review retro --since 7d
```

Optional LLM analysis:

```bash
ai-review retro latest --llm --llm-provider mock
ai-review retro latest --llm --allow-cloud
```

### `ai-review patterns --since 30d`

Summarize repeated patterns across recent sessions.

```bash
ai-review patterns --since 30d
```

Typical themes:

- sessions ended without verification evidence
- project context appeared late
- repeated sandbox or permission friction
- repeated command failures
- repeated user corrections

### `ai-review improvements`

Generate and review improvement candidates.

```bash
ai-review improvements propose --since 30d
ai-review improvements list
ai-review improvements show <id>
ai-review improvements accept <id>
ai-review improvements reject <id>
ai-review improvements apply <id>
```

### `ai-review export`

Export workflow artifacts.

```bash
ai-review export agents
ai-review export skills
ai-review export checklist
ai-review export scripts
ai-review export ci
```

### `ai-review storage`

Inspect and manage large Codex session storage.

```bash
ai-review storage stats
ai-review storage top --limit 50
ai-review storage index --incremental
ai-review storage archive --older-than 30d --dry-run
ai-review storage archive --older-than 30d
ai-review storage restore <session-id>
ai-review storage vacuum
```

![Storage manager](docs/assets/storage-manager.svg)

Archive commands move old JSONL files out of the Codex hot path instead of deleting them.

---

## Report Output

Default reports directory:

```text
.ai-review/reports/
```

Project quickstart output:

```text
.ai-review/reports/projects/<project>/
  report.html
  report.json
  retro-*.md
  retro-*.json
  retro-*.html
  patterns-7d.md
  improvements.md
```

Single-session output:

```text
.ai-review/reports/
  retro-<title>-<session>.md
  retro-<title>-<session>.json
  retro-<title>-<session>.html
```

Files:

- `report.html`: user-facing static HTML report
- `report.json`: structured analysis data
- `retro-*.md`: Markdown retrospective
- `improvements.md`: reviewable improvement candidates
- `patterns-*.md`: cross-session pattern summary

---

## Data Flow

![ai-dev-review data flow](docs/assets/data-flow.svg)

```text
Codex session transcript
  ↓
Local parser
  ↓
Fact extraction
  ↓
Rulebase-guided analysis
  ↓
LLM-assisted diagnosis, optional
  ↓
report.json
  ↓
HTML renderer
  ↓
report.html
```

The HTML page only displays structured analysis generated by the CLI.

---

## Analysis Focus

`ai-dev-review` analyzes five usage dimensions.

### 1. Task Setup

- Was the initial task clear enough?
- Was the task too large?
- Were constraints missing?
- Was the completion condition unclear?

### 2. Context Timing

- Which important facts appeared too late?
- Did the user have to correct project paths or commands?
- Should stable context move into `AGENTS.md`?

### 3. Process Intervention

- Did the agent continue after repeated failed attempts?
- Should the user have paused and reset direction earlier?
- Did the session drift into unrelated work?

### 4. Verification & Acceptance

- Was there test/build/typecheck/lint/manual verification?
- Did the final response include command results?
- Was completion accepted without evidence?

### 5. Reusable Improvements

- Should project commands be documented?
- Should a checklist be created?
- Should repeated commands become scripts?
- Should repeated validation gaps become hooks or CI checks?

---

## Optional LLM Analysis

LLM analysis is opt-in. By default, `ai-review` uses local deterministic parsing, Rulebase matching, and heuristic recommendations only.

![LLM providers](docs/assets/llm-providers.svg)

### OpenAI

```bash
export OPENAI_API_KEY=...
ai-review retro latest --llm --allow-cloud
```

### Volcengine Ark / Doubao

```bash
export ARK_API_KEY=...
ai-review retro latest --llm --llm-provider volcengine --allow-cloud
```

Or configure `~/.ai-review/config.toml`:

```toml
[analysis]
local_only = false
llm_provider = "volcengine"
# Optional. Defaults to doubao-seed-2-0-lite-260215.
# llm_model = "doubao-seed-2-0-lite-260215"
```

Then one API key is enough:

```bash
export ARK_API_KEY=...
ai-review retro latest --llm
```

The Volcengine provider defaults to:

```text
https://ark.cn-beijing.volces.com/api/v3
```

---

## Privacy

`ai-dev-review` is local-first by design.

Default behavior:

- reads local Codex transcripts as read-only
- does not modify original Codex session files
- stores reports locally
- redacts sensitive content before optional LLM analysis
- blocks cloud LLM calls while `analysis.local_only = true`
- supports `--local-only`

Redaction covers:

- API keys
- tokens
- `.env` content
- database URLs
- cookies
- private keys
- authorization headers
- home directory paths
- emails

Run locally:

```bash
ai-review retro latest --local-only
```

---

## Configuration

Project config: `.ai-review.toml`

```toml
[project]
name = "my-project"
root = "."

[sources.codex]
enabled = true
sessions_dir = "~/.codex/sessions"

[privacy]
redact_secrets = true
redact_env_files = true
redact_home_path = true

[analysis]
local_only = true
max_session_tokens = 80000
# llm_provider = "volcengine"
# llm_model = "doubao-seed-2-0-lite-260215"
# llm_api_key_env = "ARK_API_KEY"

[outputs]
reports_dir = "./.ai-review/reports"
agents_md = "./AGENTS.md"
skills_dir = "./.agents/skills"
checklists_dir = "./docs/ai-checklists"
scripts_dir = "./scripts/ai"
```

Global config: `~/.ai-review/config.toml`

```toml
[analysis]
local_only = false
llm_provider = "volcengine"
llm_api_key_env = "ARK_API_KEY"
```

---

## Rulebase

`ai-dev-review` uses a built-in Rules & Experience Library as the internal judgment layer for retrospectives and improvement recommendations.

It is not exposed as a separate user-facing rule management command. Reports do not show "rule hits" as a separate section. The Rulebase is used internally to keep analysis stable, traceable, and evidence-driven.

Coverage includes prompt quality, task planning, bugfix workflow, verification, context management, tool usage, user correction, project memory, automation, safety, reviewability, and productivity metrics.

---

## Roadmap

### v0.1 Codex Local Review

- [x] Read local Codex sessions
- [x] SQLite indexing
- [x] CLI scan/list/search
- [x] Markdown retrospective reports

### v0.2 HTML Reports

- [x] Generate `report.json`
- [x] Render static `report.html`
- [x] Embed JSON into single-file HTML
- [x] Generate HTML by default

### v0.3 Improvement Engine

- [x] Cross-session pattern report
- [x] Improvement candidates
- [x] Review queue
- [x] AGENTS/checklist/script/skill/CI exporters

### v0.4 Storage Manager

- [x] Incremental raw session index
- [x] Storage stats and largest files
- [x] Archive / restore old Codex sessions
- [x] Hot/warm/cold storage direction

### v0.5 LLM Gateway

- [x] Mock provider for tests
- [x] OpenAI provider
- [x] Volcengine Ark / Doubao provider
- [x] Structured JSON output validation
- [ ] Batch analysis
- [ ] Eval suite

### v0.6 Cross-Agent

- [ ] Claude Code adapter
- [ ] Cursor adapter
- [ ] Git / GitHub adapter
- [ ] CI logs adapter

---

## FAQ

### Is this a prompt optimizer?

No. It may notice that some information should have appeared earlier, but the product is not centered on rewriting prompts.

### Does it judge whether the final code is correct?

No. It checks whether the session produced enough verification evidence.

### Does it upload my Codex sessions?

Not by default. The default path is local deterministic analysis. If LLM analysis is enabled, the tool sends a redacted compact analysis package rather than the full transcript.

### Why generate HTML by default?

Terminal output is good for a quick summary, but HTML is easier to scan, save, share, print, and attach to issues or notes.

---

## Development

Run tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m py_compile src/ai_dev_review/*.py
```

Run from source:

```bash
PYTHONPATH=src python3 -m ai_dev_review
```

Run through `uv`:

```bash
uv run ai-review
```

---

## License

MIT
