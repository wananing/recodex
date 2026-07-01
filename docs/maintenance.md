# Project Maintenance

This document keeps the public maintenance direction clear for contributors and future releases.

## Product Scope

recodex currently maintains one primary workflow:

1. Import local AI coding sessions.
2. Configure a user-owned LLM provider.
3. Select a project and session on the Dashboard home page.
4. Generate a session efficiency report.
5. Review the report and decide what to preserve as project knowledge.

The maintained report is the LLM-backed session efficiency report. It analyzes how the developer
worked with the AI coding tool and gives next-session guidance. It should not drift back into
multiple report products, generic transcript viewing, or local rules-only summaries.

## Maintenance Priorities

- Keep onboarding short: import, configure LLM, generate report.
- Keep privacy explicit: local transcripts stay local except for the redacted LLM analysis package.
- Keep report language reader-facing: write "next time..." instead of talking about "the user" as
  a third party.
- Keep generated recommendations actionable and tied to evidence.
- Keep Dashboard UI focused on the report workflow.

## Release Checklist

Before publishing a release:

- Run `make test` locally, then confirm CI passes on GitHub.
- Run `make dashboard-build` locally for Dashboard changes.
- Update `CHANGELOG.md`.
- Confirm README quick start still matches the Dashboard.
- Check that no generated `.recodex` files, transcripts, reports, or API keys are staged.
- Tag with `vX.Y.Z` and create a GitHub release with the main user-facing changes.

## Repository Hygiene

- Keep docs aligned with the single-report architecture.
- Prefer small, reviewable PRs.
- Avoid broad refactors when updating UI copy or report contracts.
- Add tests when report generation, LLM behavior, storage, or importers change.
- Use screenshots for Dashboard changes.

## CLI Maintenance Decision

- Maintained: `serve`, `scan`, `import`, `watch`, `sessions`, `search`, `doctor`, `storage`,
  `privacy`, `open`, and `report`.
- `report` is only a headless entry for the same LLM-backed session efficiency report shown in
  the Dashboard.
- Retired: `latest`, `quickstart`, `retro`, `patterns`, `history`, `after`, and
  `workflow install-codex-hooks`. These commands must print migration guidance instead of
  generating local rules-only, hook-driven, or aggregate reports.
- Daily report generation belongs on the Dashboard home page.
