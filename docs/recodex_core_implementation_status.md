# recodex Core Refactor Implementation Status

This file tracks the implementation of `docs/recodex_core_analysis_and_artifact_architecture.md`.

## Completed Tasks

1. Facts foundation: transcript JSONL parsing, normalized events, byte offsets, evidence-ready source refs.
2. Core models: cost ledger, evidence refs, findings, improvement opportunities, artifact candidates.
3. Core diagnostics: deterministic cost analysis, findings, opportunity routing, artifact candidates.
4. Evidence mining outputs: cards, clusters, review queue, coverage report.
5. Reports/dashboard first screen: core summary, diagnostics, report JSON/HTML.
6. Artifact Router API: candidate preview/export with reviewed gate and path protection.
7. Review queue unification: mining review plus artifact candidate review states.
8. Skill Gate hardening: repeated evidence routes to skill; single-session gaps stay checklist/review.
9. Cross-session effect loop: `/artifacts/effectiveness` compares before/after report cost ledgers.
10. Golden Session eval runner: `recodex evals run --json`.
11. Deep Evidence Auditor: `recodex --deep`, `recodex report latest --deep`, and `recodex retro latest --deep` add `evidence_audit` to report JSON/HTML.
12. CLI/docs finalization: README command surface and this implementation status document.

## Verification Commands

```bash
PYTHONPATH=src python3 -m unittest tests.test_evidence_auditor tests.test_html_report tests.test_cli.CliSmokeTests.test_core_command_flow tests.test_cli.CliSmokeTests.test_evals_run_outputs_golden_metrics
uv run ruff check src/recodex/evidence_auditor.py src/recodex/evals.py src/recodex/cli.py src/recodex/html_report.py tests/test_evidence_auditor.py tests/test_evals.py tests/test_cli.py tests/test_html_report.py --select F,UP
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Main Operator Commands

```bash
recodex --deep
recodex report latest --deep --open
recodex mine --since 30d
recodex serve --dashboard-dir dashboard/dist
recodex evals run --json
```
