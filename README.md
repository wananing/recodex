# ai-dev-review

> 复盘你的 AI 编程会话，找出下一次更高效使用 Codex / Claude Code / Cursor 的具体改进点。

`ai-dev-review` is a local-first CLI that reads local AI coding session
transcripts, analyzes how the session was used, and generates static reports and
workflow improvement candidates.

The first supported data source is Codex session transcripts. The first default
output is a local `report.html`.

![ai-dev-review overview](docs/assets/readme-overview.svg)

It focuses on:

- 哪些上下文给得太晚
- 任务边界是否过大或发生漂移
- 过程中是否应该更早暂停、纠偏或拆分任务
- 收尾是否缺少测试、构建、typecheck、lint 或手动验证证据
- 哪些信息应该沉淀到 `AGENTS.md`、checklist、script、hook、CI 或 skill

It is **not** a chat viewer, **not** a prompt rewriting tool, and **not** a
generic AI summary tool.

---

## Demo

Run the default quickstart flow:

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

![Generated HTML report screenshot](docs/assets/report-page-screenshot.png)

Generate a report for the latest indexed session:

```bash
ai-review report latest
```

Open the generated HTML report automatically:

```bash
ai-review report latest --open
```

---

## Why

Using Codex well is not just about model quality.

A messy AI coding session often comes from workflow issues:

- The task starts without enough context.
- Important project rules appear too late.
- The session mixes debugging, refactoring, deployment, and documentation.
- The agent keeps exploring the wrong path.
- The final answer says "done" without verification evidence.
- The same project facts are explained again and again.

`ai-dev-review` turns real AI coding sessions into actionable usage feedback.

The goal is simple:

> Learn how to use AI coding agents better from your own sessions.

---

## What It Generates

The default quickstart flow writes project-level reports and artifacts.

### HTML Report

`report.html` is the user-facing static report. It is generated as a single
self-contained HTML file with structured JSON embedded inside:

```html
<script id="report-data" type="application/json">...</script>
```

The page does not scan Codex sessions and does not fetch a sidecar JSON file at
runtime. The CLI performs all parsing and analysis first, then renders the page.

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
- convert repeated commands into scripts
- suggest hook or CI checks
- create reusable skills

All improvement candidates are reviewable before they are applied or exported.

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
git clone <repo-url>
cd ai-dev-review
uv sync
uv run ai-review
```

Run without installing:

```bash
PYTHONPATH=src python3 -m ai_dev_review
```

Use the installed console command through `uv`:

```bash
uv run ai-review
```

---

## Quick Start

Analyze a few recent Codex sessions and generate project reports:

```bash
ai-review
```

Limit the recent window:

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

Run local-only deterministic analysis:

```bash
ai-review retro latest --local-only
```

Run optional LLM-assisted analysis with a mock provider:

```bash
ai-review retro latest --llm --llm-provider mock
```

---

## Commands

### `ai-review`

Default quickstart flow. It scans a small recent window, groups sessions by
project, writes HTML reports, proposes improvement candidates, and exports
workflow artifacts.

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

---

### `ai-review init`

Catalog Codex transcript metadata first, without fully reading every session.
This is useful when `~/.codex/sessions` is large.

```bash
ai-review init
ai-review init --select 1 --process-limit 20
```

---

### `ai-review scan`

Parse transcript files into the local SQLite database.

```bash
ai-review scan ~/.codex/sessions
ai-review import ./some-session.jsonl
```

---

### `ai-review report latest`

Generate a static HTML report for one indexed session.

```bash
ai-review report latest
ai-review report latest --open
```

This also writes a matching `retro-*.json` and `retro-*.md`.

---

### `ai-review retro`

Generate Markdown retrospectives, with matching JSON and HTML sidecar files.

```bash
ai-review retro latest
ai-review retro --since 7d
```

With optional LLM analysis:

```bash
ai-review retro latest --llm --llm-provider mock
ai-review retro latest --llm --allow-cloud
```

---

### `ai-review patterns --since 30d`

Summarize repeated patterns across recent sessions.

```bash
ai-review patterns --since 30d
```

Example themes:

- sessions ended without verification evidence
- project context appeared late
- repeated sandbox or permission friction
- repeated command failures
- repeated user corrections

---

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

---

### `ai-review export`

Export accepted or proposed workflow artifacts.

```bash
ai-review export agents
ai-review export skills
ai-review export checklist
ai-review export scripts
ai-review export ci
```

---

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

Example output:

```text
Codex sessions:
  path: ~/.codex/sessions
  files: 3120
  total size: 8.4GB
  largest file: 415MB
  files > 10MB: 117
  files older than 30d: 2400
AI Review index:
  indexed sessions: 3010
  summaries: 2800
  archive size: 5.9GB
  hot path size: 680MB
```

Archive commands move old JSONL files out of the Codex hot path instead of
deleting them.

---

## Report Output

By default, reports are written under:

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

- `report.html` — user-facing static HTML report
- `report.json` — structured analysis data
- `retro-*.md` — Markdown retrospective
- `improvements.md` — reviewable improvement candidates
- `patterns-*.md` — cross-session pattern summary

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

The HTML page only displays the structured analysis result generated by the CLI.

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

- Was there a test/build/typecheck/lint/manual verification?
- Did the final response include command results?
- Was completion accepted without evidence?

### 5. Reusable Improvements

- Should project commands be documented?
- Should a checklist be created?
- Should repeated commands become scripts?
- Should repeated validation gaps become hooks or CI checks?

---

## Example Finding

```text
Problem:
关键上下文补充偏晚

Observation:
测试命令和主要代码目录是在会话中段才出现的，AI 前期产生了不必要的探索。

Impact:
这会增加轮次、token 消耗和方向偏差风险。

Suggestion:
把稳定的项目上下文提前放入项目说明，例如测试命令、主要目录和禁止修改范围。
```

---

## Optional LLM Analysis

LLM analysis is opt-in. By default, `ai-review` uses local deterministic parsing,
Rulebase matching, and heuristic recommendations only.

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

Or configure it once in `~/.ai-review/config.toml`:

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

`ai-dev-review` is designed to be local-first.

Default behavior:

- reads local Codex transcripts as read-only
- does not modify original Codex session files
- stores reports locally
- redacts sensitive content before optional LLM analysis
- blocks cloud LLM calls while `analysis.local_only = true`
- supports `--local-only`

Sensitive content redaction includes:

- API keys
- tokens
- `.env` content
- database URLs
- cookies
- private keys
- authorization headers
- home directory paths
- emails

Run local-only analysis:

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

`ai-dev-review` uses a built-in 规则经验库 (Rules & Experience Library) as the
internal judgment layer for retrospectives and improvement recommendations.

It is not exposed as a separate user-facing rule management command. The report
does not show "rule hits" as a separate section. The Rulebase is used internally
to keep analysis consistent and evidence-backed.

Current coverage includes:

- prompt quality
- task planning
- bugfix workflow
- verification
- context management
- tool usage
- user correction
- project memory
- automation
- safety
- reviewability
- productivity metrics

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

No. It may notice that some information should have appeared earlier, but the
product is not centered on rewriting prompts.

The focus is reviewing the whole AI coding usage process: context, task
boundary, intervention, verification, and reusable improvements.

### Does it judge whether the final code is correct?

No. It checks whether the session produced enough verification evidence.

If the agent changed code but did not run tests, build, typecheck, lint, or
manual verification, the report will flag completion confidence as low.

### Does it upload my Codex sessions?

Not by default.

The default path is local deterministic analysis. If LLM analysis is enabled,
the tool sends a redacted, compact analysis package rather than the full raw
transcript.

### Does it replace `AGENTS.md` or skills?

No.

It may suggest what should move into `AGENTS.md`, a checklist, a script, a hook,
CI, or a skill, but every improvement should be reviewed before being applied.

### Why generate HTML by default?

Terminal output is good for a quick summary, but not for reading a structured
review.

HTML is easier to scan, save, share, print, and attach to issues or notes.

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
