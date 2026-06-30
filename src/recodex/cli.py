from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
import webbrowser
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import mechanism_for_improvement_category, propose_improvements
from .config import load_config
from .dashboard_server import create_dashboard_server
from .db import (
    count_catalog_entries,
    count_sessions,
    connect,
    find_cached_llm_output,
    get_events,
    get_improvement,
    get_session,
    get_setting,
    insert_llm_job,
    insert_llm_output,
    insert_improvements,
    list_catalog_entries,
    list_catalog_projects,
    list_improvements,
    list_sessions,
    save_catalog_entries,
    save_transcript,
    search_events,
    set_setting,
    update_llm_job_status,
    update_improvement_fields,
    update_improvement_status,
)
from .evidence_mining import MIN_SIGNAL_SCORE, run_evidence_mining, write_mining_outputs
from .evals import run_golden_evals
from .llm import (
    SESSION_RETRO_MAX_OUTPUT_TOKENS,
    build_session_retro_request,
    default_model_for_provider,
    generate_session_retro_analysis,
    llm_cached_usage_payload,
    llm_token_usage_report,
    llm_usage_has_tokens,
    normalize_provider_name,
    parse_llm_usage_json,
    provider_for_name,
    validate_session_retro_output,
)
from .html_report import (
    build_project_report_data,
    build_session_report_data,
    write_report_bundle,
    write_report_html,
    write_report_json,
)
from .importers import get_importer, importer_names
from .exports.skill import write_skill_md_exports_to_root
from .paths import db_path, exports_dir, reports_dir
from .privacy import redact_text
from .reports import (
    improvements_report_path,
    patterns_report_path,
    render_agents_patch,
    render_improvements,
    render_patterns,
    render_retro,
    render_retro_with_findings,
    retro_report_path,
    write_checklist_export,
    write_ci_rule_export,
    write_scripts_export,
    write_skill_exports,
    write_text,
)
from .storage import (
    archive_raw_session_files,
    collect_storage_stats,
    format_size,
    index_raw_session_files,
    largest_storage_files,
    parse_age_days,
    recent_storage_files,
    restore_raw_session_file,
    storage_archive_dir,
    storage_roots,
    vacuum_storage,
)
from .sync import sync_import_paths
from .transcripts import catalog_transcript_file, default_transcript_roots, discover_files, parse_transcript_file
from .watch import (
    add_watch_source,
    delete_watch_source,
    get_watch_source,
    list_watch_events,
    list_watch_sources,
    run_enabled_watch_sources,
    run_watch_source,
    update_watch_source,
)


COMMAND_NAMES = frozenset(
    {
        "init",
        "latest",
        "open",
        "history",
        "doctor",
        "serve",
        "scan",
        "quickstart",
        "report",
        "import",
        "watch",
        "sessions",
        "search",
        "retro",
        "patterns",
        "mine",
        "improvements",
        "export",
        "storage",
        "privacy",
        "before",
        "after",
        "workflow",
        "evals",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_default_to_latest_args(argv))
    return args.handler(args)


def _default_to_latest_args(argv: Sequence[str] | None) -> list[str]:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return ["latest"]
    if any(arg in {"-h", "--help"} for arg in args):
        return args

    index = 0
    while index < len(args):
        token = args[index]
        if token in COMMAND_NAMES:
            return args
        if token == "--db":
            index += 2
            continue
        if token.startswith("--db="):
            index += 1
            continue
        return args[:index] + ["latest"] + args[index:]
    return args + ["latest"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recodex",
        description="Review AI development sessions.",
        epilog="Run `recodex` without a subcommand to generate and open the latest local HTML report.",
    )
    parser.add_argument("--db", help="SQLite database path. Defaults to .recodex/recodex.sqlite3.")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="Initialize local recodex state.")
    init.add_argument("--project", default=".", help="Project directory for .recodex.toml.")
    init.add_argument("--sessions-dir", action="append", help="Codex sessions directory to catalog.")
    init.add_argument("--limit", type=int, help="Maximum transcript files to catalog.")
    init.add_argument("--select", type=int, help="Project number to process after cataloging.")
    init.add_argument("--process-limit", type=int, help="Maximum selected-project transcript files to scan.")
    init.add_argument("--no-prompt", action="store_true", help="Do not prompt for interactive project selection.")
    init.set_defaults(handler=cmd_init)

    latest = subparsers.add_parser("latest", help="Generate and open the latest Codex session report.")
    latest.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    latest.add_argument("--since", default="3650d", help="Window used to find the latest session.")
    latest.add_argument("--reports-dir", help="Directory for generated reports.")
    latest.add_argument("--no-open", action="store_true", help="Generate the report without opening a browser.")
    latest.add_argument("--terminal", action="store_true", help="Print the terminal summary without opening a browser.")
    latest.add_argument("--json", action="store_true", help="Generate only report.json for the latest session.")
    latest.add_argument("--deep", action="store_true", help="Include deterministic evidence audit in the generated report.")
    latest.set_defaults(handler=cmd_latest)

    open_cmd = subparsers.add_parser("open", help="Open a generated report.")
    open_cmd.add_argument("target", nargs="?", default="latest", help="Report id or latest.")
    open_cmd.add_argument("--reports-dir", help="Directory containing generated reports.")
    open_cmd.set_defaults(handler=cmd_open)

    history = subparsers.add_parser("history", help="Summarize repeated patterns across recent sessions.")
    history.add_argument("--since", default="30d", help="Window such as 30d, 2w, 12h, or ISO datetime.")
    history.add_argument("--reports-dir", help="Directory for Markdown reports.")
    history.set_defaults(handler=cmd_patterns)

    doctor = subparsers.add_parser("doctor", help="Inspect Codex session storage and recodex state.")
    doctor.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    doctor.add_argument("--archive-dir", help="recodex archive directory.")
    doctor.set_defaults(handler=cmd_storage_stats)

    serve = subparsers.add_parser("serve", help="Serve the local React dashboard and JSON API.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host.")
    serve.add_argument("--port", type=int, default=8000, help="Bind port.")
    serve.add_argument("--dashboard-dir", help="Built dashboard dist directory.")
    serve.add_argument("--open", action="store_true", help="Open the dashboard URL in a browser.")
    serve.set_defaults(handler=cmd_serve)

    source_choices = ("auto", *importer_names())

    scan = subparsers.add_parser("scan", help="Scan AI coding transcript files into SQLite.")
    scan.add_argument("paths", nargs="*", help="Transcript files or directories.")
    scan.add_argument("--source", choices=source_choices, default="auto", help="Transcript source type.")
    scan.add_argument("--limit", type=int, help="Maximum number of files to scan.")
    scan.add_argument("--dry-run", action="store_true", help="Only print discovered files.")
    scan.set_defaults(handler=cmd_scan)

    quickstart = subparsers.add_parser("quickstart", help="Scan a few recent sessions and write reports.")
    quickstart.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    quickstart.add_argument("--since", default="7d", help="Window such as 7d, 2w, 12h, or ISO datetime.")
    quickstart.add_argument("--limit", type=int, default=5, help="Maximum recent sessions to parse.")
    quickstart.add_argument("--reports-dir", help="Directory for Markdown reports.")
    quickstart.add_argument("--exports-dir", help="Directory for generated artifacts.")
    quickstart.set_defaults(handler=cmd_quickstart)

    import_cmd = subparsers.add_parser("import", help="Import one transcript file or directory.")
    import_cmd.add_argument("--source", choices=source_choices, default="auto", help="Transcript source type.")
    import_cmd.add_argument("paths", nargs="+", help="Transcript files or directories.")
    import_cmd.set_defaults(handler=cmd_scan)

    watch = subparsers.add_parser("watch", help="Manage incremental import watch sources.")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    watch_add = watch_sub.add_parser("add", help="Add or update a watch source.")
    watch_add.add_argument("--source", choices=source_choices, default="codex", help="Transcript source type.")
    watch_add.add_argument("--path", required=True, help="File or directory to watch.")
    watch_add.add_argument("--scope", help="Optional logical scope for imported context.")
    watch_add.add_argument("--disabled", action="store_true", help="Create the source disabled.")
    watch_add.set_defaults(handler=cmd_watch_add)
    watch_list = watch_sub.add_parser("list", help="List watch sources.")
    watch_list.set_defaults(handler=cmd_watch_list)
    watch_status = watch_sub.add_parser("status", help="Show watch source sync status.")
    watch_status.add_argument("--events", type=int, default=3, help="Recent sync events per source.")
    watch_status.set_defaults(handler=cmd_watch_status)
    watch_edit = watch_sub.add_parser("edit", help="Edit a watch source.")
    watch_edit.add_argument("id", type=int)
    watch_edit.add_argument("--source", choices=source_choices, help="Transcript source type.")
    watch_edit.add_argument("--path", help="File or directory to watch.")
    watch_edit.add_argument("--scope", help="Optional logical scope for imported context.")
    watch_enabled = watch_edit.add_mutually_exclusive_group()
    watch_enabled.add_argument("--enable", action="store_true", help="Enable this source.")
    watch_enabled.add_argument("--disable", action="store_true", help="Disable this source.")
    watch_edit.set_defaults(handler=cmd_watch_edit)
    watch_delete = watch_sub.add_parser("delete", help="Delete a watch source.")
    watch_delete.add_argument("id", type=int)
    watch_delete.set_defaults(handler=cmd_watch_delete)
    watch_remove = watch_sub.add_parser("remove", help="Delete a watch source.")
    watch_remove.add_argument("id", type=int)
    watch_remove.set_defaults(handler=cmd_watch_delete)
    watch_run = watch_sub.add_parser("run", help="Run one sync pass for enabled watch sources.")
    watch_run.add_argument("--id", type=int, help="Run one watch source by id.")
    watch_run.set_defaults(handler=cmd_watch_run)

    sessions = subparsers.add_parser("sessions", help="Inspect indexed sessions.")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sessions_list = sessions_sub.add_parser("list", help="List indexed sessions.")
    sessions_list.add_argument("--since", help="Window such as 30d, 2w, 12h, or ISO datetime.")
    sessions_list.set_defaults(handler=cmd_sessions_list)
    sessions_show = sessions_sub.add_parser("show", help="Show a session summary.")
    sessions_show.add_argument("session", help="Session id or latest.")
    sessions_show.set_defaults(handler=cmd_sessions_show)

    search = subparsers.add_parser("search", help="Search indexed transcript events.")
    search.add_argument("query", help="Search query.")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(handler=cmd_search)

    retro = subparsers.add_parser("retro", help="Generate retrospective reports.")
    retro.add_argument("target", nargs="?", default="latest", help="Session id, latest, or omitted.")
    retro.add_argument("--since", help="Generate retrospectives for sessions in a time window.")
    retro.add_argument("--reports-dir", help="Directory for Markdown reports.")
    retro.add_argument("--redact", action="store_true", default=True, help="Redact sensitive output.")
    retro.add_argument("--local-only", action="store_true", help="Do not call remote analysis providers.")
    retro.add_argument("--llm", action="store_true", help="Run optional structured LLM analysis.")
    retro.add_argument(
        "--llm-provider",
        help="LLM provider: openai, openai-compatible, dashscope, siliconflow, volcengine, or mock.",
    )
    retro.add_argument("--llm-model", help="LLM model. Defaults to config model or provider default.")
    retro.add_argument("--allow-cloud", action="store_true", help="Allow cloud LLM calls for this command.")
    retro.add_argument("--deep", action="store_true", help="Include deterministic evidence audit in the generated report.")
    retro.add_argument("--open", action="store_true", help="Open the generated HTML report in a browser.")
    retro.set_defaults(handler=cmd_retro)

    report = subparsers.add_parser("report", help="Generate a static HTML report for one session.")
    report.add_argument("target", nargs="?", default="latest", help="Session id, latest, or omitted.")
    report.add_argument("--reports-dir", help="Directory for generated reports.")
    report.add_argument("--local-only", action="store_true", help="Do not call remote analysis providers.")
    report.add_argument("--llm", action="store_true", help="Run optional structured LLM analysis.")
    report.add_argument(
        "--llm-provider",
        help="LLM provider: openai, openai-compatible, dashscope, siliconflow, volcengine, or mock.",
    )
    report.add_argument("--llm-model", help="LLM model. Defaults to config model or provider default.")
    report.add_argument("--allow-cloud", action="store_true", help="Allow cloud LLM calls for this command.")
    report.add_argument("--deep", action="store_true", help="Include deterministic evidence audit in the generated report.")
    report.add_argument("--open", action="store_true", help="Open the generated HTML report in a browser.")
    report.set_defaults(handler=cmd_report)

    patterns = subparsers.add_parser("patterns", help="Generate aggregate pattern reports.")
    patterns.add_argument("--since", default="30d", help="Window such as 30d, 2w, 12h, or ISO datetime.")
    patterns.add_argument("--reports-dir", help="Directory for Markdown reports.")
    patterns.set_defaults(handler=cmd_patterns)

    mine = subparsers.add_parser(
        "mine",
        help="Mine auditable evidence cards and pattern clusters.",
    )
    mine.add_argument(
        "--since",
        default="30d",
        help="Window such as 30d, 2w, 12h, or ISO datetime.",
    )
    mine.add_argument("--reports-dir", help="Base directory for generated reports.")
    mine.add_argument(
        "--output-dir",
        help=(
            "Directory for cards.jsonl, clusters.json, review_queue.json, "
            "and coverage_report.md."
        ),
    )
    mine.add_argument("--min-signal-score", type=float, default=MIN_SIGNAL_SCORE)
    mine.set_defaults(handler=cmd_mine)

    improvements = subparsers.add_parser("improvements", help="Manage improvement candidates.")
    improvements_sub = improvements.add_subparsers(dest="improvements_command", required=True)
    propose = improvements_sub.add_parser("propose", help="Propose improvement candidates.")
    propose.add_argument("--since", default="30d", help="Window such as 30d, 2w, 12h, or ISO datetime.")
    propose.add_argument("--reports-dir", help="Directory for Markdown reports.")
    propose.set_defaults(handler=cmd_improvements_propose)
    review = improvements_sub.add_parser("review", help="List or update improvement candidates.")
    review.add_argument("--status", help="Filter by status, for example proposed or accepted.")
    review.add_argument("--accept", type=int, action="append", default=[], help="Mark candidate id accepted.")
    review.add_argument("--reject", type=int, action="append", default=[], help="Mark candidate id rejected.")
    review.add_argument("--limit", type=int, default=50, help="Maximum candidates to show.")
    review.set_defaults(handler=cmd_improvements_review)
    improvements_list = improvements_sub.add_parser("list", help="List improvement candidates.")
    improvements_list.add_argument("--status", help="Filter by status.")
    improvements_list.add_argument("--limit", type=int, default=50)
    improvements_list.set_defaults(handler=cmd_improvements_list)
    improvements_show = improvements_sub.add_parser("show", help="Show one improvement candidate.")
    improvements_show.add_argument("id", type=int)
    improvements_show.set_defaults(handler=cmd_improvements_show)
    improvements_accept = improvements_sub.add_parser("accept", help="Accept one improvement candidate.")
    improvements_accept.add_argument("id", type=int)
    improvements_accept.set_defaults(handler=cmd_improvements_accept)
    improvements_reject = improvements_sub.add_parser("reject", help="Reject one improvement candidate.")
    improvements_reject.add_argument("id", type=int)
    improvements_reject.set_defaults(handler=cmd_improvements_reject)
    improvements_edit = improvements_sub.add_parser("edit", help="Edit one improvement candidate.")
    improvements_edit.add_argument("id", type=int)
    improvements_edit.add_argument("--title")
    improvements_edit.add_argument("--category")
    improvements_edit.add_argument("--evidence")
    improvements_edit.add_argument("--recommendation")
    improvements_edit.add_argument("--status")
    improvements_edit.set_defaults(handler=cmd_improvements_edit)
    improvements_apply = improvements_sub.add_parser("apply", help="Apply one candidate as generated artifact.")
    improvements_apply.add_argument("id", type=int)
    improvements_apply.add_argument("--exports-dir", help="Directory for generated artifacts.")
    improvements_apply.set_defaults(handler=cmd_improvements_apply)

    export = subparsers.add_parser("export", help="Export workflow artifacts.")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    agents = export_sub.add_parser("agents", help="Write an AGENTS.md patch suggestion.")
    agents.add_argument("--exports-dir", help="Directory for exported artifacts.")
    agents.set_defaults(handler=cmd_export_agents)
    skills = export_sub.add_parser("skills", help="Write accepted improvements as SKILL.md artifacts.")
    skills.add_argument("--exports-dir", help="Directory for exported artifacts.")
    skills.add_argument("--out", help="Direct skill root directory, for example ~/.codex/skills.")
    skills.add_argument(
        "--target",
        choices=["project", "codex", "cursor", "last"],
        help="Common skill export destination shortcut.",
    )
    skills.add_argument(
        "--on-conflict",
        choices=["skip", "overwrite", "rename"],
        default="rename",
        help="How to handle an existing skill directory not managed by recodex.",
    )
    skills.set_defaults(handler=cmd_export_skills)
    checklist = export_sub.add_parser("checklist", help="Write a checklist export.")
    checklist.add_argument("--exports-dir", help="Directory for exported artifacts.")
    checklist.set_defaults(handler=cmd_export_checklist)
    scripts = export_sub.add_parser("scripts", help="Write a shell script export.")
    scripts.add_argument("--exports-dir", help="Directory for exported artifacts.")
    scripts.set_defaults(handler=cmd_export_scripts)
    ci = export_sub.add_parser("ci", help="Write a CI rule export.")
    ci.add_argument("--exports-dir", help="Directory for exported artifacts.")
    ci.set_defaults(handler=cmd_export_ci)

    storage = subparsers.add_parser("storage", help="Manage Codex transcript storage.")
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_stats = storage_sub.add_parser("stats", help="Show hot path, index, and archive stats.")
    storage_stats.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    storage_stats.add_argument("--archive-dir", help="AI review archive directory.")
    storage_stats.set_defaults(handler=cmd_storage_stats)
    storage_top = storage_sub.add_parser("top", help="List the largest transcript files.")
    storage_top.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    storage_top.add_argument("--limit", type=int, default=50)
    storage_top.set_defaults(handler=cmd_storage_top)
    storage_index = storage_sub.add_parser("index", help="Incrementally index raw session files.")
    storage_index.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    storage_index.add_argument(
        "--incremental",
        action="store_true",
        default=True,
        help="Skip unchanged files based on path, size, and mtime. This is the default.",
    )
    storage_index.add_argument("--full", action="store_true", help="Re-read metadata for all files.")
    storage_index.set_defaults(handler=cmd_storage_index)
    storage_archive = storage_sub.add_parser("archive", help="Move old sessions out of Codex hot path.")
    storage_archive.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    storage_archive.add_argument("--archive-dir", help="AI review archive directory.")
    storage_archive.add_argument("--older-than", required=True, help="Age such as 30d or 8w.")
    storage_archive.add_argument("--dry-run", action="store_true", help="Print planned moves only.")
    storage_archive.add_argument("--limit", type=int, help="Maximum files to archive.")
    storage_archive.set_defaults(handler=cmd_storage_archive)
    storage_restore = storage_sub.add_parser("restore", help="Restore an archived session file.")
    storage_restore.add_argument("session_id")
    storage_restore.add_argument("--sessions-dir", action="append", help="Codex sessions directory.")
    storage_restore.add_argument("--archive-dir", help="recodex archive directory.")
    storage_restore.set_defaults(handler=cmd_storage_restore)
    storage_vacuum = storage_sub.add_parser("vacuum", help="Optimize and vacuum the SQLite index.")
    storage_vacuum.set_defaults(handler=cmd_storage_vacuum)

    privacy = subparsers.add_parser("privacy", help="Inspect privacy risks in indexed sessions.")
    privacy_sub = privacy.add_subparsers(dest="privacy_command", required=True)
    privacy_scan = privacy_sub.add_parser("scan", help="Scan one session for redacted sensitive text.")
    privacy_scan.add_argument("target", nargs="?", default="latest", help="Session id or latest.")
    privacy_scan.add_argument("--limit", type=int, default=20, help="Maximum findings to print.")
    privacy_scan.set_defaults(handler=cmd_privacy_scan)

    before = subparsers.add_parser("before", help="Print context to use before an AI coding task.")
    before.add_argument("--project", default=".", help="Project directory.")
    before.set_defaults(handler=cmd_before)

    after = subparsers.add_parser("after", help="Run after-session review actions.")
    after.add_argument("--session", default="latest", help="Session id or latest.")
    after.add_argument("--reports-dir", help="Directory for Markdown reports.")
    after.set_defaults(handler=cmd_after)

    workflow = subparsers.add_parser("workflow", help="Install workflow helper artifacts.")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    hooks = workflow_sub.add_parser("install-codex-hooks", help="Write a Codex after-session hook helper.")
    hooks.add_argument("--exports-dir", help="Directory for generated artifacts.")
    hooks.set_defaults(handler=cmd_workflow_install_codex_hooks)

    evals = subparsers.add_parser("evals", help="Run recodex golden-session evaluations.")
    evals_sub = evals.add_subparsers(dest="evals_command", required=True)
    evals_run = evals_sub.add_parser("run", help="Run built-in golden evals.")
    evals_run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    evals_run.set_defaults(handler=cmd_evals_run)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    project = Path(args.project).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    config_path = project / ".recodex.toml"
    if not config_path.exists():
        write_text(config_path, _default_project_config(project))
    roots = [Path(value) for value in args.sessions_dir] if args.sessions_dir else default_transcript_roots()
    cataloged = catalog_sessions(conn, roots, limit=args.limit)
    print(
        f"Initialized {db_path(args.db)} with {count_sessions(conn)} indexed session(s), "
        f"{count_catalog_entries(conn)} cataloged session(s)."
    )
    print(f"Cataloged {cataloged} transcript file(s) without full scan.")
    print(config_path)
    projects = list_catalog_projects(conn)
    print_catalog_projects(projects)
    selected = args.select
    if selected is None and not args.no_prompt and sys.stdin.isatty() and projects:
        selected = prompt_project_selection(len(projects))
    if selected is not None:
        return process_catalog_project(conn, projects, selected, args.process_limit)
    return 0


def catalog_sessions(conn, roots: list[Path], *, limit: int | None) -> int:
    if not roots:
        return 0
    files = discover_files(roots)
    if limit is not None:
        files = files[:limit]
    entries = []
    failed = 0
    for file in files:
        try:
            entries.append(catalog_transcript_file(file))
        except OSError:
            failed += 1
    saved = save_catalog_entries(conn, entries)
    if failed:
        print(f"Catalog failed files: {failed}")
    return saved


def print_catalog_projects(projects) -> None:
    if not projects:
        print("No Codex session projects found.")
        return
    print("Cataloged projects:")
    for index, row in enumerate(projects, start=1):
        latest = row["latest_at"] or "unknown"
        print(
            f"[{index}] {redact_text(row['project_path'])} "
            f"({row['session_count']} session(s), latest={latest})"
        )


def prompt_project_selection(project_count: int) -> int | None:
    raw = input("Select a project number to process now, or press Enter to skip: ").strip()
    if not raw:
        return None
    try:
        selected = int(raw)
    except ValueError:
        print("Invalid selection.")
        return None
    if selected < 1 or selected > project_count:
        print("Invalid selection.")
        return None
    return selected


def process_catalog_project(conn, projects, selected: int, process_limit: int | None) -> int:
    if selected < 1 or selected > len(projects):
        print(f"Invalid project selection: {selected}")
        return 1
    project_path = projects[selected - 1]["project_path"]
    rows = list_catalog_entries(conn, project_path=project_path, limit=process_limit)
    print(f"Selected project [{selected}]: {redact_text(project_path)}")
    scanned = 0
    failed = 0
    for row in rows:
        try:
            parsed = parse_transcript_file(Path(row["source_path"]))
            if parsed.session.message_count > 0:
                save_transcript(conn, parsed)
                scanned += 1
        except OSError as exc:
            failed += 1
            print(f"Failed to scan {redact_text(row['source_path'])}: {exc}")
    print(f"Scanned {scanned} transcript file(s) for selected project.")
    if failed:
        print(f"Failed files: {failed}")
    return 0 if scanned or not rows else 1


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        importer = get_importer(getattr(args, "source", "auto"))
    except ValueError as exc:
        print(exc)
        return 1
    roots = [Path(value) for value in args.paths] if args.paths else list(importer.default_roots)
    if not roots:
        print(f"No transcript paths provided and no default {importer.name} transcript directory was found.")
        return 1

    files = importer.discover(roots)
    limit = getattr(args, "limit", None)
    dry_run = getattr(args, "dry_run", False)
    if limit is not None:
        files = files[:limit]
    if dry_run:
        for file in files:
            print(file)
        print(f"Discovered {len(files)} transcript file(s).")
        return 0

    conn = connect(db_path(args.db))
    report = sync_import_paths(conn, importer, roots, limit=limit)
    print(f"Scanned {report.imported} transcript file(s) into {db_path(args.db)}.")
    if report.skipped:
        print(f"Skipped files: {report.skipped}")
    if report.failed:
        print(f"Failed files: {report.failed}")
        for error in report.errors:
            print(f"Failed to scan {error}")
    return 0 if report.imported or report.skipped or not files else 1


def cmd_serve(args: argparse.Namespace) -> int:
    dashboard_dir = Path(args.dashboard_dir).expanduser() if args.dashboard_dir else None
    server = create_dashboard_server(
        db_path=db_path(args.db),
        dashboard_dir=dashboard_dir,
        host=args.host,
        port=args.port,
    )
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"Serving recodex dashboard at {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped recodex dashboard.")
    finally:
        server.server_close()
    return 0


def cmd_watch_add(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    try:
        source = add_watch_source(
            conn,
            source=args.source,
            path=Path(args.path),
            scope=args.scope,
            enabled=not args.disabled,
        )
    except ValueError as exc:
        print(exc)
        return 1
    print(f"Watch source #{source.id} {_watch_state(source)} {source.source} {source.path}")
    if source.scope:
        print(f"scope: {source.scope}")
    return 0


def cmd_watch_list(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    sources = list_watch_sources(conn)
    if not sources:
        print("No watch sources configured.")
        return 0
    for source in sources:
        print(_format_watch_source(source))
    return 0


def cmd_watch_status(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    sources = list_watch_sources(conn)
    if not sources:
        print("No watch sources configured.")
        return 0
    for source in sources:
        print(_format_watch_source(source))
        if source.last_error:
            print(f"  error: {source.last_error}")
        for event in list_watch_events(conn, source.id, limit=args.events):
            print(f"  event {event['created_at']}: {event['message']}")
    return 0


def cmd_watch_edit(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    enabled = True if args.enable else False if args.disable else None
    try:
        source = update_watch_source(
            conn,
            args.id,
            source=args.source,
            path=Path(args.path) if args.path else None,
            scope=args.scope,
            enabled=enabled,
        )
    except ValueError as exc:
        print(exc)
        return 1
    if source is None:
        print(f"No watch source found for #{args.id}.")
        return 1
    print(f"Updated watch source #{source.id}.")
    print(_format_watch_source(source))
    return 0


def cmd_watch_delete(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    if not delete_watch_source(conn, args.id):
        print(f"No watch source found for #{args.id}.")
        return 1
    print(f"Deleted watch source #{args.id}.")
    return 0


def cmd_watch_run(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    if args.id:
        source = get_watch_source(conn, args.id)
        if source is None:
            print(f"No watch source found for #{args.id}.")
            return 1
        if not source.enabled:
            print(f"Watch source #{args.id} is disabled.")
            return 1
        results = [(source, run_watch_source(conn, source))]
    else:
        results = run_enabled_watch_sources(conn)
    if not results:
        print("No enabled watch sources configured.")
        return 0
    failed = 0
    for source, report in results:
        if report.failed:
            failed += 1
        print(
            f"Watch source #{source.id}: scanned={report.scanned} "
            f"imported={report.imported} skipped={report.skipped} failed={report.failed}"
        )
        for error in report.errors:
            print(f"  error: {error}")
    return 1 if failed else 0


def _format_watch_source(source) -> str:
    scope = source.scope or "default"
    last_sync = source.last_sync_at or "never"
    return (
        f"#{source.id} [{_watch_state(source)}] {source.source} {source.path} "
        f"scope={scope} last_sync={last_sync} imported={source.last_imported} "
        f"skipped={source.last_skipped} failed={source.last_failed}"
    )


def _watch_state(source) -> str:
    return "enabled" if source.enabled else "disabled"


def cmd_quickstart(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        print("--limit must be greater than 0.")
        return 1
    try:
        since = parse_since_datetime(args.since)
    except ValueError as exc:
        print(exc)
        return 1
    roots = storage_roots(args.sessions_dir)
    files = recent_storage_files(roots, since, args.limit)
    if not files:
        print(f"No Codex JSONL session files found since {args.since}.")
        return 0

    conn = connect(db_path(args.db))
    report_dir = reports_dir(args.reports_dir)
    export_base = exports_dir(args.exports_dir)
    sessions = []
    projects: dict[str, dict[str, object]] = {}
    failed = 0
    for file in files:
        try:
            parsed = parse_transcript_file(file)
        except OSError as exc:
            failed += 1
            print(f"Failed to scan {redact_text(str(file))}: {exc}")
            continue
        if parsed.session.message_count <= 0:
            continue
        save_transcript(conn, parsed)
        events = list(parsed.events)
        sessions.append(parsed.session)
        project_key = _session_project_key(parsed.session)
        project = projects.setdefault(
            project_key,
            {"sessions": [], "events": {}, "retro_paths": [], "retro_html_paths": []},
        )
        project["sessions"].append(parsed.session)  # type: ignore[union-attr]
        project["events"][parsed.session.session_id] = events  # type: ignore[index]
        project_dir = _quickstart_project_dir(report_dir, project_key)
        path = retro_report_path(project_dir, parsed.session)
        write_text(path, render_retro(parsed.session, events))
        _json_path, html_path = _write_session_html_report(path, parsed.session, events)
        project["retro_paths"].append(path)  # type: ignore[union-attr]
        project["retro_html_paths"].append(html_path)  # type: ignore[union-attr]

    if not sessions:
        print(f"No parseable sessions found since {args.since}.")
        return 1 if failed else 0

    project_outputs = []
    created = skipped = 0
    for project_key in sorted(projects):
        project = projects[project_key]
        project_sessions = project["sessions"]  # type: ignore[assignment]
        project_events = project["events"]  # type: ignore[assignment]
        project_dir = _quickstart_project_dir(report_dir, project_key)
        patterns_path = patterns_report_path(project_dir, args.since)
        write_text(patterns_path, render_patterns(project_sessions, project_events, args.since))
        drafts = propose_improvements(project_sessions, project_events)
        project_created, project_skipped = insert_improvements(conn, drafts)
        created += project_created
        skipped += project_skipped
        improvements_path = improvements_report_path(project_dir)
        write_text(improvements_path, _render_quickstart_drafts(project_key, drafts))
        report_data = build_project_report_data(project_key, project_sessions, project_events, drafts, args.since)
        report_json_path, report_html_path = write_report_bundle(project_dir, "report", report_data)
        project_export_dir = _quickstart_project_export_dir(export_base, project_key)
        export_paths = _write_quickstart_exports(project_export_dir, drafts)
        project_outputs.append(
            {
                "project": project_key,
                "project_dir": project_dir,
                "export_dir": project_export_dir,
                "export_paths": export_paths,
                "session_count": len(project_sessions),
                "retro_paths": project["retro_paths"],
                "retro_html_paths": project["retro_html_paths"],
                "patterns_path": patterns_path,
                "improvements_path": improvements_path,
                "report_json_path": report_json_path,
                "report_html_path": report_html_path,
            }
        )
    index_path = report_dir / "quickstart-index.md"
    write_text(index_path, _render_quickstart_index(project_outputs, args.since, len(sessions)))

    print(f"Quickstart scanned {len(sessions)} session(s) from the last {args.since}.")
    if failed:
        print(f"Failed files: {failed}")
    print(f"Improvement candidates: created={created}, skipped={skipped}")
    print("Projects:")
    for output in project_outputs:
        print(f"Project: {redact_text(str(output['project']))}")
        print(f"  Reports: {output['project_dir']}")
        print("  Retrospectives:")
        for path in output["retro_paths"]:
            print(f"  - {path}")
        print(f"  Patterns: {output['patterns_path']}")
        print(f"  Improvements: {output['improvements_path']}")
        print(f"  Report JSON: {output['report_json_path']}")
        print(f"  Report HTML: {output['report_html_path']}")
        print(f"  Exports: {output['export_dir']}")
        for path in output["export_paths"]:
            print(f"  - {path}")
    print(f"Index: {index_path}")
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    try:
        since = parse_since_datetime(args.since)
    except ValueError as exc:
        print(exc)
        return 1

    roots = storage_roots(args.sessions_dir)
    files = recent_storage_files(roots, since, 1)
    if not files:
        print(f"No Codex JSONL session files found since {args.since}.")
        return 0

    file = files[0]
    try:
        parsed = parse_transcript_file(file)
    except OSError as exc:
        print(f"Failed to scan {redact_text(str(file))}: {exc}")
        return 1
    if parsed.session.message_count <= 0:
        print(f"Latest Codex session has no parseable messages: {redact_text(str(file))}")
        return 1

    conn = connect(db_path(args.db))
    save_transcript(conn, parsed)
    events = list(parsed.events)
    session_dir = _latest_session_report_dir(reports_dir(args.reports_dir), parsed.session)
    report_data = build_session_report_data(parsed.session, events, deep=args.deep)
    if args.json:
        report_json_path = write_report_json(session_dir / "report.json", report_data)
        print(report_json_path)
        return 0

    report_json_path, report_html_path = write_report_bundle(session_dir, "report", report_data)
    report_md_path = write_text(session_dir / "report.md", render_retro(parsed.session, events))

    print("[ok] Found latest Codex session")
    print("[ok] Quick analysis completed")
    print("[ok] Generated report.html")
    if not args.no_open and not args.terminal:
        _open_html_report(report_html_path)
        print("[ok] Opened report in browser")
    print("")
    print(f"Report: {report_html_path}")
    print(f"Report JSON: {report_json_path}")
    print(f"Report Markdown: {report_md_path}")
    issues = report_data.get("issues") if isinstance(report_data, dict) else None
    if isinstance(issues, list) and issues:
        print("")
        print("Key findings:")
        for issue in issues[:3]:
            if isinstance(issue, dict):
                print(f"- {redact_text(str(issue.get('title') or 'Untitled finding'))}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    report_dir = reports_dir(args.reports_dir)
    if args.target != "latest":
        candidate = report_dir / args.target / "report.html"
        if not candidate.exists():
            print(f"No report found for `{args.target}` under {report_dir}.")
            return 1
        _open_html_report(candidate)
        print(candidate)
        return 0

    matches = sorted(
        report_dir.glob("*/report.html"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    if not matches:
        print(f"No generated reports found under {report_dir}. Run `recodex` first.")
        return 1
    _open_html_report(matches[0])
    print(matches[0])
    return 0


def _session_project_key(session) -> str:
    return session.project_path or session.cwd or "(unknown)"


def _latest_session_report_dir(report_dir: Path, session) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(session.session_id)).strip("-")
    return report_dir / (safe_id or "latest-session")


def _write_session_html_report(
    markdown_path: Path,
    session,
    events,
    analysis: dict[str, object] | None = None,
    *,
    deep: bool = False,
) -> tuple[Path, Path]:
    report_data = build_session_report_data(session, events, analysis, deep=deep)
    json_path = write_report_json(markdown_path.with_suffix(".json"), report_data)
    html_path = write_report_html(markdown_path.with_suffix(".html"), report_data)
    return json_path, html_path


def _open_html_report(path: Path) -> None:
    opened = webbrowser.open(path.resolve().as_uri())
    if not opened:
        print(f"Could not open browser automatically: {path}")


def _quickstart_project_dir(report_dir: Path, project_key: str) -> Path:
    return report_dir / "projects" / _project_slug(project_key)


def _mine_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir:
        return Path(args.output_dir).expanduser()
    return reports_dir(args.reports_dir) / "evidence-mining"


def _quickstart_project_export_dir(export_base: Path, project_key: str) -> Path:
    return export_base / "quickstart" / "projects" / _project_slug(project_key)


def _project_slug(project_key: str) -> str:
    if project_key == "(unknown)":
        return "unknown-project"
    name = Path(project_key).name or "project"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "project"
    digest = hashlib.sha256(project_key.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{digest}"


def _write_quickstart_exports(project_export_dir: Path, drafts) -> list[Path]:
    rows = _draft_rows(drafts)
    paths = [
        write_text(project_export_dir / "AGENTS.patch.md", render_agents_patch(rows)),
        write_checklist_export(project_export_dir, rows),
        write_scripts_export(project_export_dir, rows),
        write_ci_rule_export(project_export_dir, rows),
    ]
    paths.extend(write_skill_exports(project_export_dir, rows))
    return paths


def _draft_rows(drafts) -> list[dict[str, object]]:
    rows = []
    for index, draft in enumerate(drafts, start=1):
        rows.append(
            {
                "id": index,
                "status": "proposed",
                "category": draft.category,
                "title": draft.title,
                "session_id": draft.session_id,
                "evidence": draft.evidence,
                "recommendation": draft.recommendation,
            }
        )
    return rows


def _render_quickstart_drafts(project_key: str, drafts) -> str:
    lines = [
        "# Improvement Candidates",
        "",
        f"- Project: `{redact_text(project_key)}`",
        f"- Generated: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}",
        f"- Candidates: {len(drafts)}",
        "",
    ]
    if not drafts:
        lines.extend(["No candidates detected for this project.", ""])
        return "\n".join(lines)

    for index, draft in enumerate(drafts, start=1):
        lines.extend(
            [
                f"## #{index} {redact_text(draft.title)}",
                "",
                "- Status: `proposed`",
                f"- Mechanism: `{mechanism_for_improvement_category(draft.category)}`",
                f"- Session: `{draft.session_id or 'project-aggregate'}`",
                "",
                "Evidence:",
                "",
                f"> {redact_text(draft.evidence).replace(chr(10), chr(10) + '> ')}",
                "",
                "Recommendation:",
                "",
                redact_text(draft.recommendation),
                "",
            ]
        )
    return "\n".join(lines)


def _render_quickstart_index(project_outputs, since_label: str, session_count: int) -> str:
    lines = [
        "# Quickstart Index",
        "",
        f"- Generated: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}",
        f"- Window: `{since_label}`",
        f"- Sessions: {session_count}",
        f"- Projects: {len(project_outputs)}",
        "",
    ]
    for output in project_outputs:
        lines.extend(
            [
                f"## {redact_text(str(output['project']))}",
                "",
                f"- Sessions: {output['session_count']}",
                f"- Reports: `{output['project_dir']}`",
                f"- Exports: `{output['export_dir']}`",
                f"- Patterns: `{output['patterns_path']}`",
                f"- Improvements: `{output['improvements_path']}`",
                f"- Report JSON: `{output['report_json_path']}`",
                f"- Report HTML: `{output['report_html_path']}`",
                "",
                "Retrospectives:",
                "",
            ]
        )
        for path in output["retro_paths"]:
            lines.append(f"- `{path}`")
        lines.extend(["", "Exported Artifacts:", ""])
        for path in output["export_paths"]:
            lines.append(f"- `{path}`")
        lines.append("")
    return "\n".join(lines)


def cmd_sessions_list(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    since = parse_since(args.since) if args.since else None
    sessions = list_sessions(conn, since)
    if not sessions:
        print("No sessions found. Run `recodex scan` first.")
        return 0
    for session in sessions:
        print(
            f"{session.session_id}\t{session.updated_at or 'unknown'}\t"
            f"messages={session.message_count}\terrors={session.error_count}\t{redact_text(session.title)}"
        )
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    session = get_session(conn, args.session)
    if session is None:
        print(f"No session found for `{args.session}`.")
        return 1
    events = get_events(conn, session.session_id)
    print(f"Session: {session.session_id}")
    print(f"Title: {redact_text(session.title)}")
    print(f"Source: {redact_text(session.source_path)}")
    print(f"Window: {session.started_at or 'unknown'} -> {session.updated_at or 'unknown'}")
    print(f"Messages: {session.message_count}")
    print(f"Commands: {session.command_count}")
    print(f"Errors: {session.error_count}")
    print("Events:")
    for event in events[:12]:
        print(f"- {event.event_index} {event.role}/{event.kind}: {_excerpt(redact_text(event.text))}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    rows = search_events(conn, args.query, args.limit)
    if not rows:
        print("No matching events found.")
        return 0
    for row in rows:
        print(f"{row['session_id']}#{row['event_index']}\t{row['role']}/{row['kind']}\t{_excerpt(redact_text(row['text']))}")
    return 0


def cmd_retro(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    if args.since:
        if args.llm:
            print("LLM analysis currently supports a single session target, not --since.")
            return 1
        since = parse_since(args.since)
        sessions = list_sessions(conn, since)
        if not sessions:
            print(f"No sessions found since {args.since}.")
            return 0
        paths = []
        for session in sessions:
            events = get_events(conn, session.session_id)
            path = retro_report_path(reports_dir(args.reports_dir), session)
            write_text(path, render_retro(session, events))
            _write_session_html_report(path, session, events, deep=getattr(args, "deep", False))
            paths.append(path)
        index_path = reports_dir(args.reports_dir) / "retro-index.md"
        write_text(index_path, _render_retro_index(paths, args.since))
        print(index_path)
        return 0

    session = get_session(conn, args.target)
    if session is None:
        print("No sessions found. Run `recodex scan` first.")
        return 1
    events = get_events(conn, session.session_id)
    path = retro_report_path(reports_dir(args.reports_dir), session)
    analysis = None
    if args.llm:
        try:
            analysis = run_llm_session_retro(conn, session, events, args)
        except RuntimeError as exc:
            print(str(exc))
            return 1
        write_text(path, render_retro_with_findings(session, events, analysis))
    else:
        write_text(path, render_retro(session, events))
    _json_path, html_path = _write_session_html_report(path, session, events, analysis, deep=getattr(args, "deep", False))
    if getattr(args, "open", False):
        _open_html_report(html_path)
    print(html_path)
    print(path)
    return 0


def cmd_retro_latest(args: argparse.Namespace) -> int:
    args.target = "latest"
    args.since = None
    return cmd_retro(args)


def cmd_report(args: argparse.Namespace) -> int:
    args.since = None
    return cmd_retro(args)


def run_llm_session_retro(conn, session, events, args: argparse.Namespace) -> dict[str, object]:
    config = load_config(Path.cwd())
    provider_name = normalize_provider_name(str(args.llm_provider or config.analysis.llm_provider or "openai"))
    if provider_name != "mock" and (config.analysis.local_only or args.local_only) and not args.allow_cloud:
        raise RuntimeError(
            "Cloud LLM calls are blocked by local-only mode. "
            "Use --llm-provider mock for local testing, pass --allow-cloud, or set analysis.local_only=false."
        )
    model = args.llm_model or config.analysis.llm_model or config.analysis.model or default_model_for_provider(provider_name)
    api_key = config.analysis.llm_api_key
    if not api_key and config.analysis.llm_api_key_env:
        api_key = os.environ.get(config.analysis.llm_api_key_env)
    request = build_session_retro_request(
        session,
        events,
        provider=provider_name,
        model=model,
    )
    cached = find_cached_llm_output(
        conn,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        input_hash=request.input_hash,
    )
    if cached is not None:
        cached_usage_json = parse_llm_usage_json(cached["usage_json"])
        if not llm_usage_has_tokens(cached_usage_json):
            cached = None
    if cached is not None:
        output = json.loads(cached["output_json"])
        cleaned, _warnings = validate_session_retro_output(output)
        cached_usage = llm_cached_usage_payload(
            request,
            cached_usage=cached_usage_json,
            max_output_tokens=SESSION_RETRO_MAX_OUTPUT_TOKENS,
            warnings=_warnings,
        )
        cleaned["_recodex_token_usage"] = llm_token_usage_report([cached_usage])
        return cleaned

    job_id = f"job_{uuid.uuid4().hex[:24]}"
    insert_llm_job(
        conn,
        job_id=job_id,
        task_type=request.task_type,
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        rulebase_version="rulebase_v1",
        input_hash=request.input_hash,
        status="running",
    )
    try:
        provider = provider_for_name(
            provider_name,
            api_key=api_key,
            base_url=config.analysis.llm_base_url,
        )
        result = generate_session_retro_analysis(provider, request)
        cleaned = result.output
        usage = result.usage
        insert_llm_output(
            conn,
            output_id=f"out_{uuid.uuid4().hex[:24]}",
            job_id=job_id,
            output_json=json.dumps(cleaned, ensure_ascii=False),
            usage_json=json.dumps(usage, ensure_ascii=False),
            validation_status="ok" if not result.warnings else "ok_with_warnings",
        )
        update_llm_job_status(conn, job_id=job_id, status="ok")
        cleaned["_recodex_token_usage"] = llm_token_usage_report([usage])
        return cleaned
    except Exception as exc:
        update_llm_job_status(conn, job_id=job_id, status="error", error=str(exc))
        raise RuntimeError(f"LLM analysis failed: {exc}") from exc


def cmd_patterns(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    since = parse_since(args.since)
    sessions = list_sessions(conn, since)
    events_by_session = {
        session.session_id: get_events(conn, session.session_id)
        for session in sessions
    }
    path = patterns_report_path(reports_dir(args.reports_dir), args.since)
    write_text(path, render_patterns(sessions, events_by_session, args.since))
    print(path)
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    since = parse_since(args.since)
    sessions = list_sessions(conn, since)
    if not sessions:
        print(f"No sessions found since {args.since}.")
        return 0

    events_by_session = {
        session.session_id: get_events(conn, session.session_id)
        for session in sessions
    }
    output_dir = _mine_output_dir(args)
    result = run_evidence_mining(
        sessions,
        events_by_session,
        min_signal_score=float(args.min_signal_score),
    )
    paths = write_mining_outputs(result, output_dir)
    print(
        "Mined "
        f"{result.coverage['analysis_cards']} card(s), "
        f"{result.coverage['clusters']} cluster(s), "
        f"{result.coverage['ready_for_review_clusters']} ready for review."
    )
    for path in paths.values():
        print(path)
    return 0


def cmd_improvements_propose(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    since = parse_since(args.since)
    sessions = list_sessions(conn, since)
    events_by_session = {session.session_id: get_events(conn, session.session_id) for session in sessions}
    drafts = propose_improvements(sessions, events_by_session)
    created, skipped = insert_improvements(conn, drafts)
    rows = list_improvements(conn, limit=100)
    path = improvements_report_path(reports_dir(args.reports_dir))
    write_text(path, render_improvements(rows))
    print(f"Created {created} candidate(s), skipped {skipped} duplicate(s).")
    print(path)
    return 0


def cmd_improvements_review(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    accepted = update_improvement_status(conn, args.accept, "accepted")
    rejected = update_improvement_status(conn, args.reject, "rejected")
    if accepted:
        print(f"Accepted {accepted} candidate(s).")
    if rejected:
        print(f"Rejected {rejected} candidate(s).")
    rows = list_improvements(conn, status=args.status, limit=args.limit)
    if not rows:
        print("No improvement candidates found.")
        return 0
    for row in rows:
        mechanism = mechanism_for_improvement_category(row["category"])
        print(f"#{row['id']} [{row['status']}] {mechanism}: {row['title']}")
    return 0


def cmd_improvements_list(args: argparse.Namespace) -> int:
    return _print_improvements(args, status=args.status, limit=args.limit)


def cmd_improvements_show(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    row = get_improvement(conn, args.id)
    if row is None:
        print(f"No improvement candidate found for #{args.id}.")
        return 1
    print(render_improvements([row]))
    return 0


def cmd_improvements_accept(args: argparse.Namespace) -> int:
    return _set_single_improvement_status(args, "accepted")


def cmd_improvements_reject(args: argparse.Namespace) -> int:
    return _set_single_improvement_status(args, "rejected")


def cmd_improvements_edit(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    changed = update_improvement_fields(
        conn,
        args.id,
        title=args.title,
        category=args.category,
        evidence=args.evidence,
        recommendation=args.recommendation,
        status=args.status,
    )
    if not changed:
        print(f"No improvement candidate found for #{args.id}.")
        return 1
    print(f"Edited #{args.id}.")
    return 0


def cmd_improvements_apply(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    row = get_improvement(conn, args.id)
    if row is None:
        print(f"No improvement candidate found for #{args.id}.")
        return 1
    base = exports_dir(args.exports_dir)
    paths = _write_candidate_artifact(base, row)
    update_improvement_status(conn, [args.id], "applied")
    for path in paths:
        print(path)
    return 0


def cmd_export_agents(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    rows = list_improvements(conn, status="accepted", limit=20)
    if not rows:
        rows = list_improvements(conn, status="proposed", limit=20)
    path = exports_dir(args.exports_dir) / "AGENTS.patch.md"
    write_text(path, render_agents_patch(rows))
    print(path)
    return 0


def cmd_export_skills(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    rows = list_improvements(conn, status="accepted", limit=20)
    if not rows:
        print("No accepted improvement candidates to export. Run `recodex improvements accept <id>` first.")
        return 1
    skill_root, error = _resolve_skill_export_root(conn, args)
    if error:
        print(error)
        return 1
    paths = write_skill_md_exports_to_root(
        skill_root,
        rows,
        on_conflict=args.on_conflict,
    )
    set_setting(conn, "last_skill_export_dir", str(skill_root))
    for path in paths:
        print(path)
    print(f"Skill export target: {skill_root}")
    return 0


def _resolve_skill_export_root(conn, args: argparse.Namespace) -> tuple[Path, str | None]:
    if (args.out or args.target) and args.exports_dir:
        return Path(), "Use either --exports-dir or --out/--target, not both."
    if args.out and args.target:
        return Path(), "Use either --out or --target, not both."
    if args.out:
        return Path(args.out).expanduser().resolve(), None
    target = args.target
    if target == "last":
        previous = get_setting(conn, "last_skill_export_dir")
        if not previous:
            return Path(), "No previous skill export directory recorded for this database."
        return Path(previous).expanduser().resolve(), None
    if target == "project":
        return load_config(Path.cwd()).outputs.skills_dir, None
    if target == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        return (codex_home / "skills").resolve(), None
    if target == "cursor":
        return (Path.cwd() / ".cursor" / "rules").resolve(), None
    return (exports_dir(args.exports_dir) / "skills").resolve(), None


def cmd_export_checklist(args: argparse.Namespace) -> int:
    path = write_checklist_export(exports_dir(args.exports_dir), _export_rows(args))
    print(path)
    return 0


def cmd_export_scripts(args: argparse.Namespace) -> int:
    path = write_scripts_export(exports_dir(args.exports_dir), _export_rows(args))
    print(path)
    return 0


def cmd_export_ci(args: argparse.Namespace) -> int:
    path = write_ci_rule_export(exports_dir(args.exports_dir), _export_rows(args))
    print(path)
    return 0


def cmd_storage_stats(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    roots = storage_roots(args.sessions_dir)
    archive_dir = storage_archive_dir(args.archive_dir)
    stats = collect_storage_stats(conn, roots, archive_dir)
    print("Codex sessions:")
    print(f"  path: {', '.join(str(path) for path in stats.roots) or '(none)'}")
    print(f"  files: {stats.file_count}")
    print(f"  total size: {format_size(stats.total_size)}")
    largest = str(stats.largest_path) if stats.largest_path else "(none)"
    print(f"  largest file: {format_size(stats.largest_size)} {redact_text(largest)}")
    print(f"  files > 10MB: {stats.files_over_10mb}")
    print(f"  files older than 30d: {stats.files_older_than_30d}")
    print("AI Review index:")
    print(f"  indexed sessions: {stats.indexed_sessions}")
    print(f"  summaries: {stats.summaries}")
    print(f"  archive size: {format_size(stats.archive_size)}")
    print(f"  hot path size: {format_size(stats.hot_index_size)}")
    return 0


def cmd_storage_top(args: argparse.Namespace) -> int:
    roots = storage_roots(args.sessions_dir)
    rows = largest_storage_files(roots, args.limit)
    if not rows:
        print("No Codex JSONL session files found.")
        return 0
    for path, size in rows:
        print(f"{format_size(size)}\t{redact_text(str(path))}")
    return 0


def cmd_storage_index(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    roots = storage_roots(args.sessions_dir)
    result = index_raw_session_files(conn, roots, incremental=not args.full)
    print(
        "Indexed raw session files: "
        f"discovered={result.discovered}, created={result.created}, "
        f"updated={result.updated}, skipped={result.skipped}, "
        f"missing={result.missing}, failed={result.failed}"
    )
    return 0 if result.failed == 0 else 1


def cmd_storage_archive(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    roots = storage_roots(args.sessions_dir)
    archive_dir = storage_archive_dir(args.archive_dir)
    try:
        older_than_days = parse_age_days(args.older_than)
    except ValueError as exc:
        print(exc)
        return 1
    index_result = index_raw_session_files(conn, roots, incremental=True)
    result = archive_raw_session_files(
        conn,
        roots,
        archive_dir,
        older_than_days=older_than_days,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    mode = "Dry run" if args.dry_run else "Archived"
    print(
        f"{mode}: candidates={result.candidates}, moved={result.moved}, "
        f"failed={result.failed}, index_failed={index_result.failed}"
    )
    for source, target in result.paths[:50]:
        print(f"{redact_text(str(source))} -> {redact_text(str(target))}")
    if len(result.paths) > 50:
        print(f"... {len(result.paths) - 50} more file(s)")
    return 0 if result.failed == 0 and index_result.failed == 0 else 1


def cmd_storage_restore(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    roots = storage_roots(args.sessions_dir)
    if not roots:
        print("No Codex sessions directory found. Pass --sessions-dir.")
        return 1
    result = restore_raw_session_file(
        conn,
        args.session_id,
        roots[0],
        storage_archive_dir(args.archive_dir),
    )
    print(result.message)
    if result.path is not None:
        print(result.path)
    return 0 if result.restored else 1


def cmd_storage_vacuum(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    vacuum_storage(conn)
    print(f"Vacuumed {db_path(args.db)}.")
    return 0


def cmd_privacy_scan(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    session = get_session(conn, args.target)
    if session is None:
        print(f"No session found for `{args.target}`.")
        return 1
    events = get_events(conn, session.session_id)
    print(f"Privacy scan for `{session.session_id}`")
    findings = 0
    for event in events:
        redacted = redact_text(event.text, home=Path.home())
        if redacted != event.text:
            findings += 1
            if findings > args.limit:
                print(f"- Output truncated after {args.limit} finding(s).")
                break
            print(f"- {event.session_id}#{event.event_index}: {_excerpt(redacted)}")
    if not findings:
        print("- No redaction targets detected.")
    return 0


def cmd_before(args: argparse.Namespace) -> int:
    config = load_config(Path(args.project))
    conn = connect(db_path(args.db))
    rows = list_improvements(conn, status="accepted", limit=8)
    if not rows:
        rows = list_improvements(conn, status="proposed", limit=8)
    print("# Relevant AI Dev Context")
    print("")
    print("## Project Rules")
    print("- Run relevant verification before claiming completion.")
    print("- Keep unrelated files out of the final change.")
    print(f"- Reports directory: `{config.outputs.reports_dir}`")
    print("")
    print("## Recent Improvement Candidates")
    if rows:
        for row in rows:
            print(f"- [{row['status']}] {row['title']}: {_excerpt(row['recommendation'])}")
    else:
        print("- No candidates yet. Run `recodex scan` and `recodex improvements propose`.")
    print("")
    print("## Suggested Checklist")
    print("- Identify files and commands before editing.")
    print("- Run focused tests or build checks.")
    print("- Summarize commands run and residual risks.")
    return 0


def cmd_after(args: argparse.Namespace) -> int:
    conn = connect(db_path(args.db))
    session = get_session(conn, args.session)
    if session is None:
        print(f"No session found for `{args.session}`.")
        return 1
    events = get_events(conn, session.session_id)
    report_path = retro_report_path(reports_dir(args.reports_dir), session)
    write_text(report_path, render_retro(session, events))
    drafts = propose_improvements([session], {session.session_id: events})
    created, skipped = insert_improvements(conn, drafts)
    print(report_path)
    print(f"Created {created} candidate(s), skipped {skipped} duplicate(s).")
    return 0


def cmd_workflow_install_codex_hooks(args: argparse.Namespace) -> int:
    directory = exports_dir(args.exports_dir) / "workflow"
    script = directory / "codex-after-session.sh"
    write_text(script, _codex_after_session_script())
    script.chmod(0o755)
    print(script)
    print("Add this helper to your Codex after-session hook configuration after review.")
    return 0


def cmd_evals_run(args: argparse.Namespace) -> int:
    result = run_golden_evals()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Golden evals")
        print(f"- cases: {result['case_count']}")
        print(f"- routing accuracy: {result['routing_accuracy']}")
        print(f"- evidence traceability: {result['evidence_traceability']}")
        print(f"- false skill promotions: {result['false_skill_promotions']}")
    return 0 if result["ok"] else 1


def _print_improvements(args: argparse.Namespace, status: str | None, limit: int) -> int:
    conn = connect(db_path(args.db))
    rows = list_improvements(conn, status=status, limit=limit)
    if not rows:
        print("No improvement candidates found.")
        return 0
    for row in rows:
        mechanism = mechanism_for_improvement_category(row["category"])
        print(f"#{row['id']} [{row['status']}] {mechanism}: {row['title']}")
    return 0


def _set_single_improvement_status(args: argparse.Namespace, status: str) -> int:
    conn = connect(db_path(args.db))
    changed = update_improvement_status(conn, [args.id], status)
    if not changed:
        print(f"No improvement candidate found for #{args.id}.")
        return 1
    print(f"Marked #{args.id} as {status}.")
    return 0


def _export_rows(args: argparse.Namespace):
    conn = connect(db_path(args.db))
    rows = list_improvements(conn, status="accepted", limit=20)
    if not rows:
        rows = list_improvements(conn, status="proposed", limit=20)
    return rows


def _write_candidate_artifact(directory: Path, row) -> list[Path]:
    category = str(row["category"]).lower()
    if "agent" in category:
        return [write_text(directory / "AGENTS.patch.md", render_agents_patch([row]))]
    if "skill" in category:
        return write_skill_exports(directory, [row])
    if "checklist" in category:
        return [write_checklist_export(directory, [row])]
    if "script" in category:
        return [write_scripts_export(directory, [row])]
    if "ci" in category:
        return [write_ci_rule_export(directory, [row])]
    return [write_text(directory / "applied" / f"candidate-{row['id']}.md", render_improvements([row]))]


def _render_retro_index(paths: list[Path], since_label: str) -> str:
    lines = [f"# Retrospectives Since {since_label}", ""]
    for path in paths:
        lines.append(f"- {path}")
    lines.append("")
    return "\n".join(lines)


def _default_project_config(project: Path) -> str:
    return "\n".join(
        [
            "[project]",
            f'name = "{project.name}"',
            'root = "."',
            "",
            "[sources.codex]",
            "enabled = true",
            'sessions_dir = "~/.codex/sessions"',
            "",
            "[privacy]",
            "redact_secrets = true",
            "redact_env_files = true",
            "redact_home_path = true",
            "",
            "[analysis]",
            "local_only = true",
            "max_session_tokens = 80000",
            '# llm_provider = "volcengine"',
            '# llm_model = "doubao-seed-2-0-lite-260215"',
            '# llm_api_key_env = "ARK_API_KEY"',
            "",
            "[outputs]",
            'agents_md = "./AGENTS.md"',
            'skills_dir = "./.agents/skills"',
            'checklists_dir = "./docs/ai-checklists"',
            'scripts_dir = "./scripts/ai"',
            'reports_dir = "./.recodex/reports"',
            "",
        ]
    )


def _codex_after_session_script() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Codex hook helper for recodex.",
            "# It accepts Codex hook JSON on stdin and falls back to latest session.",
            'payload="$(cat || true)"',
            'session_id="$(printf "%s" "$payload" | python3 -c \'import json,sys; '
            'data=sys.stdin.read(); '
            'print((json.loads(data).get("session_id") if data.strip() else "") or "latest")\' 2>/dev/null || printf latest)"',
            'recodex after --session "${session_id:-latest}"',
            "",
        ]
    )


def _excerpt(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def parse_since(value: str) -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    lowered = value.strip().lower()
    if lowered.endswith("d") and lowered[:-1].isdigit():
        return (now - timedelta(days=int(lowered[:-1]))).isoformat()
    if lowered.endswith("w") and lowered[:-1].isdigit():
        return (now - timedelta(weeks=int(lowered[:-1]))).isoformat()
    if lowered.endswith("h") and lowered[:-1].isdigit():
        return (now - timedelta(hours=int(lowered[:-1]))).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def parse_since_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(parse_since(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
