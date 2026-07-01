# Security Policy

## Supported Versions

Security fixes target the latest released version and the `main` branch.

## Reporting a Vulnerability

Please report security issues privately to the maintainer instead of opening a public issue.
Use the GitHub repository owner contact channel until a dedicated security advisory workflow is
configured.

Include:

- affected version or commit
- vulnerable command, API endpoint, or Dashboard flow
- reproduction steps with sensitive data removed
- expected impact
- whether transcripts, API keys, reports, or local paths may be exposed

## Sensitive Data

recodex handles local AI coding transcripts, reports, provider settings, and API keys. Do not post
raw transcripts, `.env` files, provider responses, generated `.recodex` databases, or private
reports in public issues.

## Project Boundaries

recodex is local-first and does not provide a hosted backend. LLM report generation sends a
redacted analysis package to the provider configured by the user.

