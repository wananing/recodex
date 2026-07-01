# Changelog

All notable changes to this project are tracked here.

## Unreleased

- Focus the product on one Dashboard-generated session efficiency report.
- Require an enabled LLM provider for Dashboard report generation.
- Remove user-facing `v2` wording from report generation and report pages.
- Hide legacy report type selection from the report list.
- Add open source maintenance docs and GitHub issue/PR templates.
- Add MIT license and CI checks for Python tests and Dashboard builds.
- Retire legacy local and hook-driven CLI report workflows and keep `recodex report` aligned with
  the Dashboard LLM-backed session report.

## 0.2.0

- Added the React Dashboard for importing sessions, selecting projects and sessions, configuring
  LLM providers, generating reports, and viewing report history.
- Added LLM-backed chat transcript analysis focused on the developer's collaboration process.
- Added report evidence audit, artifact suggestions, token usage reporting, and generated report
  metadata.
- Added support for Codex, Claude Code, and Cursor transcript sources.
