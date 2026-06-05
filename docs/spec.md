# Spec: recodex

## Objective

Build a local CLI that helps frequent Codex, Claude Code, and Cursor users
review AI-assisted development sessions, identify repeated failure patterns,
and turn personal lessons into reusable team workflow assets.

The first supported data source is Codex session transcripts. The first
deliverable is a working local MVP that scans transcripts into SQLite and emits
Markdown reports and exportable workflow artifacts.

## Tech Stack

- Python 3.10+
- Standard-library CLI with `argparse`
- Standard-library SQLite with `sqlite3`
- Markdown files for human-readable outputs
- No runtime dependencies for the first MVP

## Commands

```bash
PYTHONPATH=src python3 -m recodex scan [paths...]
PYTHONPATH=src python3 -m recodex retro latest
PYTHONPATH=src python3 -m recodex patterns --since 30d
PYTHONPATH=src python3 -m recodex improvements propose
PYTHONPATH=src python3 -m recodex improvements review
PYTHONPATH=src python3 -m recodex export agents
PYTHONPATH=src python3 -m recodex export skills
PYTHONPATH=src python3 -m unittest discover -s tests
```

After package installation:

```bash
recodex scan [paths...]
recodex retro latest
recodex patterns --since 30d
recodex improvements propose
recodex improvements review
recodex export agents
recodex export skills
```

## Project Structure

```text
src/recodex/
  cli.py          CLI entrypoint and command routing
  db.py           SQLite schema and persistence
  transcripts.py  Codex transcript discovery and tolerant parsing
  analysis.py     Heuristics for retrospectives and improvements
  reports.py      Markdown rendering and export writers
  paths.py        Local state path resolution
tests/            Unit tests for parser and CLI flows
docs/spec.md      Product and engineering specification
```

## Code Style

Prefer small, explicit functions with typed dataclasses at boundaries.

```python
def count_terms(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)
```

Conventions:

- Keep runtime code dependency-free until the data model stabilizes.
- Store UTC timestamps as ISO-8601 strings.
- Treat transcript schemas as unstable input and parse defensively.
- Keep generated Markdown readable when opened directly in an editor.

## Testing Strategy

Use standard-library `unittest` for the MVP so tests run without installing
dependencies. Add parser tests for representative JSONL and plain-text inputs,
and CLI smoke tests for scan/report/export commands.

## Boundaries

- Always: preserve original transcript files; write generated state under
  `.recodex/` unless configured otherwise; make scanner re-runnable.
- Ask first: adding runtime dependencies; changing the default state location;
  writing patches directly into a user's `AGENTS.md`.
- Never: upload transcripts to a remote service; store secrets in reports;
  destructively edit source transcripts.

## Success Criteria

- A new `recodex/` directory exists with a runnable CLI project.
- `scan` ingests Codex-like transcript files into SQLite.
- `retro latest` writes a retrospective Markdown report.
- `patterns --since 30d` writes an aggregate Markdown report.
- `improvements propose` creates reviewable candidates.
- `export agents` writes an AGENTS.md patch suggestion.
- `export skills` writes starter skill, checklist, and script artifacts.
- Unit tests verify parser and command smoke paths.

## Open Questions

- Which exact Codex transcript locations should be treated as canonical in your
  environment?
- Should Cursor and Claude Code transcripts be added as first-class sources
  after the Codex scanner is useful?
- Should reports keep full excerpts, or only short evidence snippets for
  privacy?
