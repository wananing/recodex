# recodex

English | [中文](README.md)

> Review your latest Codex session and see what to improve next time.

`recodex` is a local-first CLI that reads your local Codex session transcripts, analyzes how you used Codex, and opens a static HTML report by default.

It helps you find:

- context that arrived too late
- task boundaries that drifted
- moments where earlier intervention would help
- missing verification evidence
- project facts that should be documented before the next session

It is not a transcript viewer, not a prompt optimizer, and not a generic AI summary tool.

It reviews the usage process around a Codex session.

```bash
recodex
```

```text
[ok] Found latest Codex session
[ok] Quick analysis completed
[ok] Generated report.html
[ok] Opened report in browser

Key findings:
- Key context arrived too late
- The task boundary drifted slightly
- The session ended without verification evidence
```

![recodex HTML report](docs/assets/report-page-screenshot.png)

---

## Why

Using Codex well is not only about model quality.

A messy AI coding session is often a workflow problem:

- the task starts without enough context
- important project facts appear too late
- debugging, refactoring, deployment, and docs are mixed into one session
- the agent keeps exploring the wrong path
- the final answer says "done" without tests, build, typecheck, lint, or manual verification evidence
- the user explains the same project fact again and again

`recodex` has a narrow goal: turn real Codex sessions into concrete feedback for using AI coding agents better next time.

---

## What It Analyzes

`recodex` focuses on five usage dimensions:

- **Task start**: goal, context, constraints, and done condition
- **Context timing**: which facts arrived too late and caused wasted exploration
- **Intervention**: when the user should pause, reset assumptions, or split the session
- **Verification and acceptance**: whether the final result has reviewable evidence
- **Reusable improvements**: which facts, workflows, or commands should become docs, checklists, scripts, hooks, CI, or skills

---

## What It Generates

By default, recodex generates a local static report:

```text
.recodex/reports/<session-id>/
  report.html
  report.json
  report.md
```

`report.html` is a self-contained HTML file. Structured JSON is embedded inside the page:

```html
<script id="report-data" type="application/json">...</script>
```

The page does not scan Codex sessions and does not fetch external JSON at runtime. The CLI parses and analyzes first, then renders the page.

The report includes:

1. Overview
2. Flow timeline
3. Main issues
4. Context frontload analysis
5. Intervention analysis
6. Verification and acceptance
7. Actionable suggestions
8. Evidence appendix

![Report anatomy](docs/assets/report-anatomy.svg)

---

## Quick Start

Run from source:

```bash
git clone <repo-url>
cd recodex
uv sync
uv run recodex
```

Common usage:

```bash
recodex              # analyze latest session and open the HTML report
recodex --no-open    # generate the report without opening a browser
recodex --terminal   # keep the browser closed and print the terminal summary
recodex --json       # generate only report.json
```

Explicit latest:

```bash
recodex latest
recodex latest --since 30d
```

---

## Commands

Common commands:

```bash
recodex              # analyze latest session and open HTML report
recodex latest       # explicit latest-session analysis
recodex open latest  # reopen the latest generated report
recodex history      # summarize repeated patterns across recent sessions
recodex doctor       # inspect Codex session storage and recodex state
```

Advanced commands:

```bash
recodex scan ~/.codex/sessions
recodex report latest --open
recodex retro latest --local-only
recodex quickstart --since 7d --limit 5
recodex history --since 30d
recodex export agents
recodex export checklist
recodex storage stats
```

`quickstart` is the explicit multi-session flow. It groups recent sessions by project, generates project reports, and exports workflow artifacts. It is not the default entry point.

---

## Actionable Suggestions

`recodex` may suggest follow-up actions such as:

- document project commands in `AGENTS.md`
- add a completion checklist
- script repeated commands
- add a hook or CI check
- create a reusable skill for repeated workflows

Suggestions are not applied automatically. Review them before applying.

---

## Optional Local Report Server

By default, `recodex` generates a self-contained `report.html` and opens it in your browser. It does not require a background service.

A local report server is planned for browsing multiple reports, searching report history, and viewing weekly trends. This is an optional enhancement, not the default entry point.

Planned command:

```bash
recodex serve
```

---

## Privacy

`recodex` is local-first:

- reads local Codex transcripts as read-only
- does not modify original Codex session files
- writes reports under local `.recodex`
- keeps LLM analysis disabled by default
- redacts content before optional LLM analysis
- supports deterministic local analysis

Redaction covers API keys, tokens, `.env` content, database URLs, cookies, private keys, Authorization headers, home paths, and emails.

---

## Optional LLM Analysis

LLM analysis is opt-in. The default path uses local deterministic parsing, Rulebase matching, and heuristic suggestions.

Test the LLM path:

```bash
recodex retro latest --llm --llm-provider mock
```

OpenAI:

```bash
export OPENAI_API_KEY=...
recodex retro latest --llm --allow-cloud
```

Volcengine Ark / Doubao:

```bash
export ARK_API_KEY=...
recodex retro latest --llm --llm-provider volcengine --allow-cloud
```

Or configure `~/.recodex/config.toml`:

```toml
[analysis]
local_only = false
llm_provider = "volcengine"
llm_api_key_env = "ARK_API_KEY"
# llm_model = "doubao-seed-2-0-lite-260215"
```

---

## Configuration

Project config: `.recodex.toml`

```toml
[sources.codex]
enabled = true
sessions_dir = "~/.codex/sessions"

[privacy]
redact_secrets = true
redact_env_files = true
redact_home_path = true

[analysis]
local_only = true

[outputs]
reports_dir = "./.recodex/reports"
```

Global config: `~/.recodex/config.toml`

---

## Roadmap

Current focus:

- [x] Analyze latest Codex session
- [x] Generate self-contained HTML report
- [x] Detect late context
- [x] Detect missing verification evidence
- [x] Generate top suggestions and evidence appendix

Next:

- [ ] Better evidence appendix
- [ ] `recodex open` report selection
- [ ] `recodex doctor` for large session directories
- [ ] AGENTS.md suggestion snippets
- [ ] Checklist suggestions
- [ ] Optional local report server

Later:

- [ ] Deep analysis mode
- [ ] Batch analysis
- [ ] Eval suite
- [ ] Claude Code adapter
- [ ] Cursor adapter
- [ ] Git / GitHub adapter
- [ ] CI logs adapter

---

## FAQ

### Is this a prompt optimizer?

No. It may detect that some information should have appeared earlier, but the product is not centered on rewriting prompts.

It reviews the usage process: context, task boundary, intervention timing, verification, and reusable improvements.

### Does it judge whether the final code is correct?

No. It checks whether the session produced enough verification evidence.

If the agent changed code but did not run tests, build, typecheck, lint, or manual verification, the report will lower completion confidence.

### Does it upload my Codex sessions?

Not by default.

The default path is local deterministic analysis. If LLM analysis is enabled, the tool sends a redacted, compact analysis package rather than the full raw transcript.

### Why generate HTML by default?

Terminal output is good for quick summaries, but not for reading structured retrospectives.

HTML is easier to scan, save, share, print, and attach to issues or notes.
