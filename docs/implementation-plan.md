# recodex Implementation Plan

## Lead Objective

Build the MVP as a paced workflow-analysis loop:

```text
scan -> sessions/search -> retro -> patterns -> propose -> review -> export
```

The implementation should favor small verified slices over a broad rewrite. Typer,
Rich, Pydantic, Jinja2, LLM analysis, hooks, TUI, and web UI stay out of the first
integration unless a later slice explicitly needs them.

## Functional Breakdown

### P0: Codex Local Index

Goal: make local Codex sessions searchable and inspectable.

Commands:

```bash
recodex init
recodex scan
recodex import ./session.jsonl
recodex sessions list
recodex sessions show latest
recodex search "test failed"
```

Acceptance:

- Reads `$CODEX_SESSIONS_DIR`, `$CODEX_HOME/sessions`, and `~/.codex/sessions`.
- Does not modify original transcript files.
- Stores normalized sessions/events in SQLite.
- Supports FTS5 search with LIKE fallback.
- Search results include source session and short evidence snippets.

### P1: Single Session Retrospective

Goal: produce evidence-backed retrospectives for a single session.

Commands:

```bash
recodex retro latest
recodex retro <session-id>
```

Acceptance:

- Report contains Task Goal, Outcome, Timeline, What Went Well, What Went Wrong,
  User Interventions, Reusable Lessons, and Improvement Candidates.
- Report uses short evidence snippets, not full transcript dumps.
- Missing verification and user corrections are explicitly surfaced when detected.

### P2: Pattern Mining

Goal: find repeated issues across recent sessions.

Commands:

```bash
recodex patterns --since 30d
```

Acceptance:

- Detects command failures, repeated error terms, repeated commands, missing
  verification, sandbox friction, and user corrections.
- Output is a Markdown report under `.recodex/reports`.

### P3: Improvement Queue

Goal: convert repeated problems into reviewable improvement candidates.

Commands:

```bash
recodex improvements propose --since 30d
recodex improvements list
recodex improvements show <id>
recodex improvements accept <id>
recodex improvements reject <id>
```

Acceptance:

- Candidates include type, problem, recommendation, confidence, impact, effort,
  status, and evidence.
- Applying changes remains manual or explicitly confirmed.
- Existing `improvements review --accept/--reject` remains compatible until the
  subcommands replace it.

### P4: Exporters

Goal: export accepted or proposed candidates into workflow artifacts.

Commands:

```bash
recodex export agents
recodex export skills
recodex export checklist
recodex export scripts
recodex export ci
```

Acceptance:

- AGENTS.md export is a patch suggestion, not an automatic edit.
- Skill export writes a valid `SKILL.md` directory.
- Checklist export writes Markdown.
- Script export writes a shell script suggestion with executable bit where safe.
- CI export writes a GitHub Actions suggestion.

### P5: Privacy and Config

Goal: keep transcript handling private and explicit by default.

Commands:

```bash
recodex privacy scan latest
recodex retro latest --redact
recodex retro latest --local-only
```

Acceptance:

- Project config: `.recodex.toml`.
- Global config: `~/.recodex/config.toml`.
- Default redaction covers common API keys, tokens, Authorization headers,
  database URLs, JWT secrets, emails, home paths, SSH keys, cookies, and `.env`
  style values.
- Redaction happens before report/export rendering when enabled.

## Worker Goals

### Worker A: Storage and Search

Ownership:

- `src/recodex/db.py`
- `src/recodex/models.py`
- `tests/test_storage.py`

Goal:

- Add schema-compatible storage for design-draft sessions/events.
- Add FTS5 search with LIKE fallback.
- Add `search_events`, `get_session`, and `count_sessions`.
- Keep current scan/retro/patterns tests green.

Validation:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

### Worker B: Privacy and Config

Ownership:

- `src/recodex/config.py`
- `src/recodex/privacy.py`
- `tests/test_privacy_config.py`

Goal:

- Add project/global TOML config loading with dependency-free fallback behavior.
- Add safe local storage defaults.
- Add reusable redaction helpers.

Validation:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

### Worker C: Reports and Export Rendering

Ownership:

- `src/recodex/reports.py`
- `tests/test_reports.py`

Goal:

- Align retrospective Markdown structure with the design draft.
- Add independent checklist, script, and CI export renderers.
- Keep all evidence as short snippets.

Validation:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

### Worker D: Codex Transcript Normalization

Ownership:

- `src/recodex/transcripts.py`
- `src/recodex/models.py`
- `tests/test_codex_adapter.py`

Goal:

- Improve Codex JSONL parsing without breaking existing models.
- Support `$CODEX_SESSIONS_DIR`.
- Extract command/stdout/stderr/exit-code-like facts when present.
- Add user-correction signal detection.

Validation:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Lead Integration Rules

- Merge only one completed worker slice at a time.
- After each integration, run the full test suite.
- Do not start Typer/Rich/Pydantic migration until the standard-library MVP
  covers P0-P4.
- Do not add LLM calls until evidence, privacy, and review queue behavior are
  stable.
- Do not auto-apply AGENTS.md, skills, scripts, or CI files without an explicit
  user command.

## Review Gates

Each worker slice must satisfy:

- Tests pass.
- Existing commands remain compatible.
- No transcript source files are modified.
- No sensitive raw transcript dumps are introduced into reports.
- Public helper names are clear enough for CLI integration.

Final MVP gate:

```bash
make test
PYTHONPATH=src python3 -m recodex scan --dry-run
PYTHONPATH=src python3 -m recodex --help
```
