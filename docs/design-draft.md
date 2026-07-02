# recodex Design Draft

## Positioning

Product name: `recodex`

One-line positioning:

> Turn AI coding chats, command execution, code changes, and failure processes into retrospectives, improvement suggestions, AGENTS.md, skills, checklists, scripts, and workflow rules.

This should be a self-hosted AI development retrospective and improvement system with explicit
privacy boundaries. Version 1 focuses on Codex. Later versions can expand to Claude Code, Cursor,
GitHub, and CI.

The product should not be a simple Codex chat history viewer, and it should not be only a skills generator.

The intended loop is:

```text
AI development process records
  -> structured parsing
  -> retrospective analysis
  -> repeated problem and success pattern discovery
  -> improvement candidate generation
  -> human review
  -> injection into the next AI development workflow
  -> continued effect observation
```

Codex session transcripts are the first direct data source. Codex stores local session transcripts under `$CODEX_HOME/sessions`, defaulting to `~/.codex/sessions`.

## 1. Product Goals

The product goal is to turn "things I should remind AI about next time" into systematic, traceable, reusable, and verifiable engineering assets.

Core value:

- Review the past, not just view history.
- Generate improvement actions, not just chat summaries.
- Support multiple improvement carriers, not only skills.
- Build a continuous improvement loop, not a one-off output.
- Keep humans in the review path before workflow changes are applied.

## 2. MVP Scope

Version 1 should do four things:

1. Read local Codex sessions.
2. Generate a single-session retrospective.
3. Find repeated issues across sessions.
4. Generate improvement candidates and export them as AGENTS.md, skills, checklists, and scripts.

Do not start with:

- Large web platform.
- Team collaboration permissions.
- Complex dashboard.
- Every AI coding tool.
- Automatic modification of many project files.

The first version should run the loop:

```text
scan -> retro -> propose -> review -> export
```

## 3. Technology Choices

Recommended stack:

- Language: Python 3.11+
- CLI: Typer
- Terminal UI: Rich / Textual
- Data model: Pydantic
- Database: SQLite + FTS5
- Templates: Jinja2
- Config: TOML
- LLM calls: OpenAI API, later local model support
- Optional server: FastAPI
- Optional desktop: Tauri + React in phase 2

Why Python:

1. Fast text parsing.
2. Fast JSONL processing.
3. Efficient LLM workflow development.
4. Mature Markdown, SQLite, and CLI ecosystem.
5. Easy later integration with models, RAG, vector databases, GitHub API, and CI logs.

Go or Rust can be used later for a high-performance watcher or single-file binary, but they are not recommended as the main language for version 1.

## 4. Architecture

```text
recodex
├── adapters/                 # different AI tools and data sources
│   ├── codex/
│   ├── git/
│   ├── github_actions/
│   ├── claude_code/           # future
│   └── cursor/                # future
│
├── core/
│   ├── schema.py              # unified data model
│   ├── storage.py             # SQLite storage
│   ├── normalize.py           # raw logs to unified events
│   ├── evidence.py            # evidence reference system
│   └── privacy.py             # redaction
│
├── analysis/
│   ├── session_retro.py       # single-session retrospective
│   ├── pattern_mining.py      # multi-session pattern discovery
│   ├── metrics.py             # metrics
│   ├── rca.py                 # root cause analysis
│   └── prompts/               # LLM analysis prompts
│
├── improvements/
│   ├── propose.py             # improvement candidate generation
│   ├── review.py              # human accept / reject / edit
│   ├── apply.py               # apply improvements
│   └── exporters/
│       ├── agents_md.py
│       ├── skill.py
│       ├── checklist.py
│       ├── script.py
│       └── ci_rule.py
│
├── workflow/
│   ├── before_task.py         # inject suggestions before a task
│   ├── after_task.py          # automatic retrospective after a task
│   ├── hooks.py               # Codex hooks
│   └── mcp.py                 # future MCP
│
├── reports/
├── templates/
└── cli.py
```

## 5. Core Data Flow

```text
Codex transcript JSONL
  -> RawEvent
  -> NormalizedEvent
  -> Session / Task / ToolCall / Command / FileChange / Error
  -> Session Retrospective
  -> Pattern Mining
  -> ImprovementCandidate
  -> Human Review
  -> Exported Artifact
```

The first version should emphasize structured facts and executable improvements, not just summarization.

## 6. Data Sources

Version 1 supports Codex:

```text
$CODEX_HOME/sessions
~/.codex/sessions
$CODEX_SESSIONS_DIR
```

It should also support manual import:

```bash
recodex import ./some-session.jsonl
```

Future sources:

- Git commit history.
- Git diff.
- PR comments.
- GitHub Actions logs.
- pytest / vitest / jest / mvn test output.
- Claude Code transcript.
- Cursor chat export.
- Linear / Jira issue.

Codex hooks include fields such as `session_id`, `transcript_path`, `cwd`, `hook_event_name`, and `model`, so later versions can use Codex hooks for automatic post-task retrospectives.

## 7. Unified Event Model

Do not analyze raw JSONL directly. Normalize first.

```python
class Session:
    id: str
    source: str              # codex | claude | cursor
    project_path: str
    transcript_path: str
    started_at: str | None
    ended_at: str | None
    model: str | None
    title: str | None
    status: str              # completed | partial | failed | unknown


class Event:
    id: str
    session_id: str
    type: str                # user_message | assistant_message | tool_call | command | file_change | error | result
    timestamp: str | None
    content: str
    metadata: dict


class Command:
    id: str
    session_id: str
    command: str
    cwd: str | None
    exit_code: int | None
    stdout: str | None
    stderr: str | None


class FileChange:
    id: str
    session_id: str
    path: str
    change_type: str         # created | modified | deleted | unknown
    summary: str | None


class ErrorObservation:
    id: str
    session_id: str
    category: str            # test_failure | build_failure | command_error | misunderstanding | missing_context
    evidence: str
    severity: str            # low | medium | high
```

Evidence must be preserved:

```python
class EvidenceRef:
    session_id: str
    event_id: str
    transcript_path: str
    quote: str
    reason: str
```

All summaries, retrospectives, and improvement suggestions must trace back to evidence.

## 8. SQLite Schema

Version 1 can use SQLite.

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  project_path TEXT,
  transcript_path TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  model TEXT,
  title TEXT,
  status TEXT,
  raw_hash TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE events (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  timestamp TEXT,
  content TEXT,
  metadata_json TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE commands (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  event_id TEXT,
  command TEXT,
  cwd TEXT,
  exit_code INTEGER,
  stdout TEXT,
  stderr TEXT
);

CREATE TABLE retrospectives (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  goal TEXT,
  outcome TEXT,
  summary TEXT,
  failures_json TEXT,
  lessons_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE improvement_candidates (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  type TEXT NOT NULL,
  problem TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  affected_projects_json TEXT,
  confidence REAL,
  impact TEXT,
  effort TEXT,
  status TEXT DEFAULT 'proposed',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE event_fts USING fts5(
  content,
  session_id UNINDEXED,
  event_id UNINDEXED
);
```

## 9. Retrospective Report Structure

Each session should generate one report:

```md
# AI Dev Session Retrospective

## 1. Task Goal
What was the user's original goal? Did the AI understand it correctly?

## 2. Outcome
Final result:
- success
- partial
- failed
- abandoned
- unknown

## 3. Timeline
Key steps:
1. User proposed task.
2. AI analyzed.
3. AI executed commands.
4. Error occurred.
5. User corrected.
6. Fix was made.
7. Verification happened.

## 4. What Went Well
Successful paths and effective practices.

## 5. What Went Wrong
Failures, misjudgments, repeated attempts, missing verification, wrong files, etc.

## 6. User Interventions
Where the user corrected the AI.

## 7. Reusable Lessons
Reusable lessons.

## 8. Improvement Candidates
- AGENTS.md rule
- skill
- checklist
- script
- CI rule
- prompt template
- project doc
```

The retrospective should answer:

- How can rework be reduced next time?
- What should be automated?
- What context should be given to AI up front?
- Which verification steps must be enforced?

## 10. Improvement Candidate Model

The improvement candidate model is the core of the system.

```python
class ImprovementCandidate:
    id: str
    title: str
    type: str
    problem: str
    recommendation: str
    evidence: list[EvidenceRef]
    affected_projects: list[str]
    confidence: float
    impact: str              # low | medium | high
    effort: str              # low | medium | high
    status: str              # proposed | accepted | rejected | applied | deprecated
```

Candidate types:

- `agents_md_rule`
- `skill`
- `checklist`
- `script`
- `ci_rule`
- `prompt_template`
- `doc_update`
- `test_template`
- `review_rule`
- `workflow_hook`

Carrier selection logic:

- Long-term project facts -> AGENTS.md.
- Multi-step flow for a task class -> skill.
- Repeated commands -> script.
- Commonly missed verification -> checklist or CI.
- Historical architecture or technical decisions -> knowledge doc.
- Prompt wording affects quality -> prompt template.
- Code quality requirement -> PR review rule, CI, or lint.

## 11. Improvement Outputs

### 11.1 AGENTS.md Updates

Codex reads AGENTS.md before starting work. AGENTS.md is suitable for project structure, build/test commands, engineering conventions, constraints, and completion criteria.

Example generated section:

```md
## AI Development Rules

### Build and Test
- Use `pnpm test` for unit tests.
- Use `pnpm typecheck` before marking TypeScript work complete.
- Do not claim completion before running the relevant verification command.

### Project Structure
- Frontend code is under `apps/web`.
- Shared UI components are under `packages/ui`.
- API routes are under `apps/api`.

### Done Definition
A task is done only when:
1. Relevant tests pass.
2. Typecheck passes.
3. No unrelated files are changed.
4. The final response includes commands that were run.
```

### 11.2 Skill

Codex skills are directories with `SKILL.md`; they may include `scripts/`, `references/`, `assets/`, and other supporting files. `SKILL.md` must include `name` and `description`.

Export structure:

```text
.agents/skills/
  spring-boot-deploy-debug/
    SKILL.md
    scripts/
      check-service.sh
    references/
      deployment-notes.md
```

Example:

````md
---
name: spring-boot-deploy-debug
description: Use this when deploying or debugging this project's Spring Boot service.
---

# Spring Boot Deploy Debug

## When to use

Use this when the user asks to deploy, restart, debug, or verify the Spring Boot service.

## Required context

- Service name
- Port
- Build command
- Deployment directory
- Log command

## Procedure

1. Check current branch and dirty files.
2. Run build.
3. Verify artifact exists.
4. Stop service safely.
5. Deploy artifact.
6. Restart service.
7. Check service logs.
8. Verify health endpoint.

## Commands

```bash
mvn clean package -DskipTests
lsof -i :8080
systemctl status my-service --no-pager
journalctl -u my-service -n 100 --no-pager
```

## Common mistakes

- Do not skip log verification after restart.
- Do not kill an unknown process before checking what owns the port.
- Do not overwrite the running jar without backup.
````

### 11.3 Checklist

```md
# AI Coding Completion Checklist

Before saying a task is done:

- [ ] I identified the files changed.
- [ ] I ran the relevant tests.
- [ ] I ran typecheck or build if available.
- [ ] I checked for unrelated diffs.
- [ ] I summarized verification commands.
- [ ] I listed any remaining risk.
```

### 11.4 Script

If retrospectives show repeated command sequences, generate scripts:

```bash
#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8080}"

echo "Checking port $PORT..."
lsof -i ":$PORT" || true

echo "Checking service..."
systemctl status my-service --no-pager || true

echo "Recent logs..."
journalctl -u my-service -n 80 --no-pager || true
```

### 11.5 CI Rule

If repeated failures come from missing tests or type errors, generate a GitHub Actions suggestion:

```yaml
name: verify

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm test
```

## 12. CLI Design

Version 1 CLI:

```bash
recodex init
recodex scan
recodex sessions list
recodex sessions show latest
recodex search "test failed"
recodex retro latest
recodex retro --since 7d
recodex patterns --since 30d
recodex improvements propose --since 30d
recodex improvements list
recodex improvements show <id>
recodex improvements accept <id>
recodex improvements reject <id>
recodex improvements apply <id>
recodex export agents
recodex export skills
recodex export checklist
recodex export scripts
```

Typical flow:

```bash
recodex scan
recodex retro latest
recodex improvements propose --since 14d
recodex improvements list
recodex improvements accept imp_001
recodex improvements apply imp_001
```

## 13. Configuration

Project config:

```toml
# .recodex.toml
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
model = "gpt-5.5"
local_only = false
max_session_tokens = 80000

[outputs]
agents_md = "./AGENTS.md"
skills_dir = "./.agents/skills"
checklists_dir = "./docs/ai-checklists"
scripts_dir = "./scripts/ai"
reports_dir = "./.recodex/reports"

[workflow]
enable_codex_hooks = false
auto_retro_after_session = false
```

Global config:

```toml
# ~/.recodex/config.toml
[default_sources.codex]
sessions_dir = "~/.codex/sessions"

[privacy]
redact_api_keys = true
redact_tokens = true
redact_passwords = true

[llm]
provider = "openai"
model = "gpt-5.5"
```

## 14. Privacy and Security

The product must keep transcript privacy and redaction boundaries explicit.

Default rules:

1. Original transcripts are read-only.
2. Full logs are not uploaded by default.
3. Redact before sending anything to an LLM.
4. All improvement candidates require human confirmation.
5. Sensitive paths, secrets, or customer information must not be written into AGENTS.md or skills automatically.
6. All outputs preserve source evidence.
7. Support `--local-only`.

Redaction rules:

- `OPENAI_API_KEY`
- `GITHUB_TOKEN`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `DATABASE_URL`
- `JWT_SECRET`
- `.env` content
- Private IPs
- Email addresses
- Home directory paths
- SSH keys
- Cookies
- Authorization headers

Commands:

```bash
recodex privacy scan latest
recodex retro latest --redact
recodex retro latest --local-only
```

## 15. LLM Analysis Strategy

Do not send a whole transcript to a model in one pass.

Use three stages:

1. Structure extraction: extract goal, commands, errors, file changes, user corrections.
2. Retrospective analysis: generate the retrospective from structured facts.
3. Improvement suggestions: cluster across sessions and generate candidates.

Each stage should output JSON for validation.

Single-session analysis output:

```json
{
  "goal": "...",
  "outcome": "success | partial | failed | unknown",
  "task_type": "bugfix | feature | refactor | deploy | test | docs | other",
  "timeline": [],
  "what_went_well": [],
  "what_went_wrong": [],
  "user_corrections": [],
  "reusable_lessons": [],
  "candidate_improvements": []
}
```

Multi-session pattern discovery output:

```json
{
  "patterns": [
    {
      "title": "AI repeatedly skipped typecheck before completion",
      "frequency": 7,
      "sessions": ["..."],
      "impact": "high",
      "recommended_improvement_type": "checklist_or_ci_rule"
    }
  ]
}
```

## 16. Pattern Discovery Rules

Version 1 should use rules plus LLMs, not LLMs alone.

Rule detection:

1. `exit_code != 0` -> command failed.
2. Text contains `error`, `failed`, `traceback`, `exception` -> error candidate.
3. Similar commands run repeatedly -> possible trial and error.
4. User says "not this", "you forgot", "I said earlier" -> user correction.
5. No final test/build/typecheck command -> missing verification.
6. Deploy session has no log/status/health check -> missing deploy verification.
7. Same error text appears across sessions -> repeated failure pattern.
8. Same command sequence appears across sessions -> scriptable workflow.

LLM responsibilities:

1. Infer the true task goal.
2. Infer root cause.
3. Merge similar lessons.
4. Select the best improvement carrier.
5. Generate human-readable improvement suggestions.

## 17. Workflow Integration

### 17.1 Before Task

Before a task:

```bash
recodex before --project .
```

Output:

```md
# Relevant AI Dev Context

## Project Rules
- Run `pnpm typecheck` before completion.
- Do not modify generated files.
- Use `apps/web` for frontend code.

## Recent Failure Patterns
- AI often forgets to run integration tests after API changes.
- Deployment tasks require checking `journalctl`.

## Suggested Checklist
- Identify package.
- Run relevant tests.
- Summarize changed files.
```

This can be copied into Codex manually at first. Later it can be integrated through AGENTS.md, hooks, or MCP.

### 17.2 During Task

Later watcher:

```bash
recodex watch --project .
```

Detect:

- Repeated command failures.
- Sensitive file changes.
- Preparing to finish without tests.
- Critical command output that AI appears to ignore.

This is not required for version 1.

### 17.3 After Task

After a task:

```bash
recodex after --session latest
```

Actions:

1. Read transcript.
2. Generate retrospective.
3. Generate improvement candidates.
4. Place them in the review queue.

Codex hooks provide `transcript_path`, so this can be automated later.

## 18. Human Review Queue

Review is required. Improvements should not be applied fully automatically.

Command:

```bash
recodex improvements list
```

Example output:

```text
[HIGH] imp_001  Update AGENTS.md with test/build commands
[HIGH] imp_002  Add PR completion checklist
[MED ] imp_003  Create skill: spring-boot-deploy-debug
[MED ] imp_004  Generate script: check-service-health.sh
[LOW ] imp_005  Add prompt template: bugfix-investigation
```

Show details:

```bash
recodex improvements show imp_001
```

Example detail:

````md
# imp_001: Update AGENTS.md with test/build commands

## Problem

Codex repeatedly failed to identify the correct test and typecheck commands.

## Recommendation

Add standard build, test, and typecheck commands to AGENTS.md.

## Evidence

- session A: AI searched package scripts multiple times.
- session B: user corrected the test command.
- session C: final response claimed success without running typecheck.

## Proposed Patch

```diff
+ ## Build and Test
+ - Install dependencies with `pnpm install`.
+ - Run unit tests with `pnpm test`.
+ - Run typecheck with `pnpm typecheck`.
+ - Do not mark TypeScript changes complete until typecheck passes.
```

## Risk

Low. This only adds project instructions.
````

Actions:

```bash
recodex improvements accept imp_001
recodex improvements reject imp_002
recodex improvements edit imp_003
recodex improvements apply imp_001
```

## 19. MVP Development Schedule

### Week 1: Codex Parsing and Indexing

Goal:

- Scan `~/.codex/sessions`.
- Parse JSONL.
- List sessions.
- Search by project, time, and keyword.

Deliverables:

```bash
recodex scan
recodex sessions list
recodex sessions show latest
recodex search "error"
```

### Week 2: Single-Session Retrospective

Goal:

- Generate a retrospective for the latest session.
- Identify goal, outcome, commands, errors, and user corrections.

Deliverable:

```bash
recodex retro latest
```

Output:

```text
.recodex/reports/session_xxx.md
```

### Week 3: Multi-Session Pattern Discovery

Goal:

- Aggregate recent 7 / 14 / 30 day issues by project.
- Find repeated failures, repeated commands, missing verification, and user corrections.

Deliverable:

```bash
recodex patterns --since 30d
```

### Week 4: Improvement Candidate Queue

Goal:

- Generate improvement candidates.
- Support accept / reject / edit.

Deliverables:

```bash
recodex improvements propose --since 30d
recodex improvements list
recodex improvements show <id>
recodex improvements accept <id>
```

### Week 5: Exporters

Goal:

- Export AGENTS.md patch.
- Export skills.
- Export checklist.
- Export scripts.

Deliverables:

```bash
recodex export agents
recodex export skills
recodex export checklist
recodex export scripts
```

### Week 6: Workflow Integration

Goal:

- Support Codex after-session hook.
- Support before-task context.

Deliverables:

```bash
recodex before --project .
recodex after --session latest
recodex workflow install-codex-hooks
```

## 20. MVP Acceptance Criteria

1. Runs on Linux and macOS.
2. Reads Codex session transcripts.
3. Searches historical AI development records.
4. Generates single-session retrospectives.
5. Finds repeated issues from the last N days.
6. Generates at least five types of improvement candidates:
   - AGENTS.md
   - skill
   - checklist
   - script
   - CI rule
7. Every suggestion has evidence.
8. Every applying action requires human confirmation.
9. Redaction is enabled by default.
10. Original Codex files are not modified.

## 21. Version Roadmap

### v0.1: Codex Local Review

- Codex session scanning.
- SQLite index.
- CLI viewing.
- Search.

### v0.2: Retrospective

- Single-session retrospective.
- Error detection.
- User correction detection.
- Missing verification detection.

### v0.3: Improvement Engine

- Multi-session clustering.
- Improvement candidates.
- Review queue.

### v0.4: Exporters

- AGENTS.md exporter.
- Skill exporter.
- Checklist exporter.
- Script exporter.

### v0.5: Workflow Hooks

- Codex after-session hook.
- Before-task context.
- Automatic retrospective draft generation.

### v0.6: Cross-Agent

- Claude Code adapter.
- Cursor adapter.
- Git / GitHub adapter.
- CI logs adapter.

### v1.0: AI Dev Improvement Platform

- Web / TUI review.
- Project-level metrics.
- Improvement effect tracking.
- Team shared configuration.

## 22. Differentiation

Do not position the product as:

> Codex session viewer

Position it as:

> AI coding retrospective and improvement engine

Differentiation:

1. Not viewing history, but reviewing history.
2. Not summarizing chats, but generating improvement actions.
3. Not only generating skills, but supporting multiple improvement carriers.
4. Not one-off output, but a continuous loop.
5. Not letting AI decide alone, but applying changes after human review.

## 23. Recommended First Shape

Product name: `recodex`

Shape:

- Local CLI.
- SQLite.
- Markdown reports.

Primary users:

- Developers who frequently use Codex / Claude Code / Cursor.
- Developers who want to review AI development process and avoid repeated mistakes.
- Developers who want to turn personal experience into team AI workflows.

First data source:

- Codex session transcripts.

First outputs:

- Retrospective report.
- Improvement candidates.
- AGENTS.md patch.
- Skills.
- Checklists.
- Scripts.

Core commands:

```bash
recodex scan
recodex retro latest
recodex patterns --since 30d
recodex improvements propose
recodex improvements review
recodex export agents
recodex export skills
```

Final one-line plan:

> Build a Codex workflow profiler first: read `~/.codex/sessions`, structurally parse AI
> development processes, generate session retrospectives and cross-session improvement candidates,
> export AGENTS.md, skills, checklists, scripts, and CI suggestions after human review, then later
> integrate Codex hooks, Claude Code, Cursor, GitHub, and team collaboration.
