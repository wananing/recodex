from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from recodex.analysis_workflow import WorkflowLLMStage, extract_schema
from recodex.dashboard_services import _report_core_summary, _run_llm_workflow_stage
from recodex.dashboard_server import DashboardApp
from recodex.db import connect, insert_improvements, update_improvement_status
from recodex.llm import LLMResponseIncompleteError, MockProvider
from recodex.models import ImprovementDraft


class DashboardServerTests(unittest.TestCase):
    def test_dashboard_catalog_scan_lists_projects_before_full_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            sessions_dir = root / "sessions"
            sessions_dir.mkdir()
            _write_session(sessions_dir / "one.jsonl", "catalog-one", "Fix project A.", cwd="/work/project-a")
            _write_session(sessions_dir / "two.jsonl", "catalog-two", "Fix project B.", cwd="/work/project-b")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            scanned = _json(
                app.handle_post("/catalog/scan", {"source": "codex", "path": str(sessions_dir)})
            )
            self.assertEqual(scanned["scanned"], 2)
            self.assertEqual(scanned["cataloged"], 2)

            overview = _json(app.handle_get("/overview"))
            self.assertEqual(overview["sessions"], 0)
            self.assertEqual(overview["catalog_sessions"], 2)
            self.assertEqual(overview["catalog_projects"], 2)

            projects = _json(app.handle_get("/catalog/projects"))
            self.assertEqual(
                [project["project_path"] for project in projects["projects"]],
                ["/work/project-a", "/work/project-b"],
            )

            project_sessions = _json(app.handle_get("/catalog/sessions?project=/work/project-a"))
            self.assertEqual(len(project_sessions["sessions"]), 1)
            self.assertEqual(project_sessions["sessions"][0]["session_id"], "catalog-one")
            self.assertFalse(project_sessions["sessions"][0]["imported"])

            full_sessions = _json(app.handle_get("/sessions"))
            self.assertEqual(full_sessions["sessions"], [])

            imported = _json(
                app.handle_post("/catalog/import", {"source": "codex", "project": "/work/project-a"})
            )
            self.assertEqual(imported["selected"], 1)
            self.assertEqual(imported["imported"], 1)

            full_sessions = _json(app.handle_get("/sessions"))
            self.assertEqual([session["session_id"] for session in full_sessions["sessions"]], ["catalog-one"])
            project_sessions = _json(app.handle_get("/catalog/sessions?project=/work/project-a"))
            self.assertTrue(project_sessions["sessions"][0]["imported"])

    def test_dashboard_catalog_scan_supports_claude_code_project_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            claude_home = root / ".claude"
            project_path = root / "workspace-with-dash" / "my-app"
            project_path.mkdir(parents=True)
            codex_dir = root / "codex-sessions"
            codex_dir.mkdir()
            _write_session(codex_dir / "codex.jsonl", "codex-same-project", "Catalog from Codex.", cwd=str(project_path))
            project_dir = claude_home / "projects" / _encoded_claude_project_name(project_path)
            project_dir.mkdir(parents=True)
            session_path = project_dir / "claude-session.jsonl"
            _write_claude_session(session_path, "claude-catalog-1", "Audit this Claude Code session.")
            (claude_home / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
            (claude_home / "sessions").mkdir()
            (claude_home / "sessions" / "123.json").write_text(
                json.dumps({"pid": 123, "sessionId": "runtime-state", "cwd": str(project_path)}),
                encoding="utf-8",
            )
            (project_dir / "claude-session" / "subagents").mkdir(parents=True)
            (project_dir / "claude-session" / "subagents" / "agent-a.meta.json").write_text(
                '{"agent":"metadata"}',
                encoding="utf-8",
            )
            app = DashboardApp(db_path=db, dashboard_dir=None)

            codex_scanned = _json(
                app.handle_post("/catalog/scan", {"source": "codex", "path": str(codex_dir)})
            )
            self.assertEqual(codex_scanned["cataloged"], 1)
            scanned = _json(
                app.handle_post("/catalog/scan", {"source": "claude-code", "path": str(claude_home)})
            )
            self.assertEqual(scanned["scanned"], 1)
            self.assertEqual(scanned["cataloged"], 1)

            projects = _json(app.handle_get("/catalog/projects?source=claude-code"))["projects"]
            self.assertEqual([project["project_path"] for project in projects], [str(project_path)])
            self.assertEqual(projects[0]["sources"], ["claude-code"])

            project_sessions = _json(app.handle_get(f"/catalog/sessions?project={project_path}&source=claude-code"))
            self.assertEqual(project_sessions["sessions"][0]["session_id"], "claude-catalog-1")
            self.assertEqual(project_sessions["sessions"][0]["source"], "claude-code")

            imported = _json(
                app.handle_post("/catalog/import", {"source": "claude-code", "project": str(project_path)})
            )
            self.assertEqual(imported["imported"], 1)

            full_sessions = _json(app.handle_get("/sessions"))["sessions"]
            self.assertEqual([session["session_id"] for session in full_sessions], ["claude-catalog-1"])
            self.assertEqual(full_sessions[0]["source"], "claude-code")
            self.assertEqual(full_sessions[0]["project_path"], str(project_path))

    def test_dashboard_server_serves_spa_and_api_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<html><body>recodex dashboard</body></html>", encoding="utf-8")
            transcript = root / "session.jsonl"
            _write_session(transcript, "dashboard-1", "Import through dashboard API.")
            _seed_accepted_improvement(db)
            app = DashboardApp(db_path=db, dashboard_dir=dist)

            html = app.handle_get("/")
            self.assertEqual(html.status, HTTPStatus.OK)
            self.assertIn("recodex dashboard", html.body.decode("utf-8"))

            imported = _json(
                app.handle_post("/import/run", {"source": "codex", "path": str(transcript)})
            )
            self.assertEqual(imported["imported"], 1)
            self.assertEqual(imported["failed"], 0)

            sessions = _json(app.handle_get("/sessions"))
            self.assertEqual(sessions["sessions"][0]["session_id"], "dashboard-1")

            graph = _json(app.handle_get("/transcripts/dashboard-1/graph"))
            self.assertTrue(graph["events"])
            self.assertTrue(graph["raw_records"])
            source_ref = graph["events"][0]["source_ref"]
            lineage = _json(app.handle_get(f"/transcripts/dashboard-1/lineage?ref={source_ref}"))
            self.assertTrue(any(item["type"] == "raw_record" for item in lineage["upstream"]))

            source = _json(
                app.handle_post(
                    "/watch/add",
                    {"source": "codex", "path": str(root), "scope": "dashboard-test"},
                )
            )
            self.assertEqual(source["source"]["scope"], "dashboard-test")

            watched = _json(app.handle_post("/watch/run", {"id": source["source"]["id"]}))
            self.assertEqual(watched["results"][0]["skipped"], 1)

            skill_root = root / "skill-root"
            exported = _json(
                app.handle_post(
                    "/skills/export",
                    {"target": "custom", "out": str(skill_root), "on_conflict": "rename"},
                )
            )
            self.assertEqual(exported["target"], str(skill_root.resolve()))
            self.assertTrue((skill_root / "dashboard-skill" / "SKILL.md").exists())

    def test_dashboard_report_analysis_and_artifact_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            exports = root / "exports"
            transcript = root / "session.jsonl"
            _write_repeated_requirement_session(transcript, "dashboard-report-1")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            imported = _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            self.assertEqual(imported["imported"], 1)

            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {
                        "target": "dashboard-report-1",
                        "reports_dir": str(reports),
                        "llm": _mock_llm_settings(),
                    },
                )
            )
            self.assertEqual(generated["report"]["session_id"], "dashboard-report-1")
            self.assertTrue(Path(str(generated["report"]["html_path"])).exists())
            self.assertTrue(Path(str(generated["report"]["markdown_path"])).exists())
            self.assertTrue(Path(str(generated["report"]["json_path"])).exists())

            listed = _json(app.handle_get("/reports"))
            self.assertEqual(listed["reports"][0]["id"], generated["report"]["id"])

            report_id = str(generated["report"]["id"])
            html = _json(app.handle_get(f"/reports/{report_id}/html"))
            markdown = _json(app.handle_get(f"/reports/{report_id}/markdown"))
            raw_json = _json(app.handle_get(f"/reports/{report_id}/json"))
            self.assertIn("<!doctype html>", html["content"])
            self.assertIn("# AI Dev Session Retrospective", markdown["content"])
            self.assertIn("summary", raw_json["content"])
            report_payload = json.loads(str(raw_json["content"]))
            self.assertEqual(report_payload["evidence_audit"]["status"], "pass")
            self.assertIn("deep-audit", report_payload["meta"]["analysis_mode"])

            analysis = _json(
                app.handle_post(
                    "/analysis/run",
                    {"mode": "improvements", "since": "3650d", "reports_dir": str(reports)},
                )
            )
            self.assertGreaterEqual(analysis["created"], 1)

            improvements = _json(app.handle_get("/improvements"))
            improvement_id = improvements["improvements"][0]["id"]
            accepted = _json(app.handle_post(f"/improvements/{improvement_id}/accept", {}))
            self.assertEqual(accepted["improvement"]["status"], "accepted")

            skill_preview = _json(
                app.handle_get(f"/artifacts/preview?type=skill&improvement_id={improvement_id}")
            )
            self.assertEqual(skill_preview["artifact_type"], "skill")
            self.assertIn("SKILL.md", skill_preview["files"][0]["path"])
            self.assertIn("AGENTS.md", skill_preview["files"][0]["content"])

            md_preview = _json(
                app.handle_get(f"/artifacts/preview?type=markdown&improvement_id={improvement_id}")
            )
            self.assertEqual(md_preview["artifact_type"], "markdown")
            self.assertIn("Improvement Candidates", md_preview["files"][0]["content"])

            exported = _json(
                app.handle_post(
                    "/artifacts/export",
                    {
                        "type": "skill",
                        "improvement_id": improvement_id,
                        "target": "custom",
                        "out": str(exports / "skills"),
                        "on_conflict": "rename",
                    },
                )
            )
            self.assertTrue(Path(str(exported["paths"][0])).exists())

            rejected = _json(app.handle_post(f"/improvements/{improvement_id}/reject", {}))
            self.assertEqual(rejected["improvement"]["status"], "rejected")

            conn = connect(db)
            feedback_count = conn.execute("SELECT COUNT(*) AS count FROM analysis_examples").fetchone()["count"]
            self.assertGreaterEqual(feedback_count, 2)

    def test_dashboard_reports_list_exposes_core_diagnostic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "diagnostic-session.jsonl"
            _write_repeated_requirement_session(transcript, "dashboard-core-summary")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {
                        "target": "dashboard-core-summary",
                        "reports_dir": str(reports),
                        "llm": _mock_llm_settings(),
                    },
                )
            )

            generated_summary = generated["report"]["core_summary"]
            self.assertEqual(
                generated_summary["max_avoidable_cost"],
                "未发现明确可避免成本",
            )
            self.assertEqual(
                generated_summary["primary_improvement"],
                "发起任务时先要求列出最小相关验证、完成标准、未覆盖风险和收尾对照格式。",
            )
            self.assertEqual(
                generated_summary["primary_cause"],
                "任务启动时没有把原话、阶段目标和验证证据绑定成可更新清单。",
            )
            self.assertNotIn("user_advice", generated_summary["recommended_mechanisms"])
            self.assertEqual(
                generated_summary["opportunity_count"],
                len(generated_summary["top_opportunities"]),
            )

            listed = _json(app.handle_get("/reports"))["reports"]

            self.assertEqual(listed[0]["id"], generated["report"]["id"])
            self.assertEqual(listed[0]["core_summary"], generated_summary)

    def test_report_core_summary_prioritizes_llm_report_focus_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "focused-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "max_avoidable_cost": "额外轮次 3 次",
                            "primary_cause": "dashboard 未展示 LLM 结论。",
                            "primary_improvement": "优先对齐报告首屏。",
                        },
                        "findings": [{"id": "finding-1"}],
                        "improvement_opportunities": [
                            {
                                "title": "旧规则建议",
                                "recommended_mechanism": "agents_md",
                                "routing_reason": "fallback",
                                "suggested_target": "AGENTS.md",
                                "best_action": "写入项目指南。",
                            }
                        ],
                        "artifact_candidates": [
                            {
                                "id": "old-agent",
                                "mechanism": "agents_md",
                                "target_path": "AGENTS.md",
                                "status": "proposed",
                            }
                        ],
                        "report_focus": {
                            "source": "llm_chat_transcript",
                            "recommended_artifacts": [
                                {
                                    "id": "focus-checklist",
                                    "title": "dashboard 报告展示验收 checklist",
                                    "mechanism": "checklist",
                                    "target_path": "docs/dashboard-report-checklist.md",
                                    "status": "proposed",
                                },
                                {
                                    "id": "focus-skill",
                                    "title": "报告首屏验收技能",
                                    "mechanism": "skill",
                                    "target_path": "skills/report-review/SKILL.md",
                                    "status": "proposed",
                                },
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = _report_core_summary(report_path)

            self.assertEqual(summary["artifact_candidate_count"], 3)
            self.assertEqual(summary["recommended_mechanisms"][:2], ["checklist", "skill"])
            self.assertEqual(summary["top_artifact_candidates"][0]["mechanism"], "checklist")
            self.assertEqual(
                summary["top_artifact_candidates"][0]["target_path"],
                "docs/dashboard-report-checklist.md",
            )

    def test_dashboard_artifact_api_uses_report_artifact_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            exports = root / "exports"
            transcript = root / "candidate-session.jsonl"
            _write_artifact_candidate_session(transcript, "dashboard-artifact-candidate")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {
                        "target": "dashboard-artifact-candidate",
                        "reports_dir": str(reports),
                        "llm": _mock_llm_settings(),
                    },
                )
            )
            report_id = str(generated["report"]["id"])
            raw_json = _json(app.handle_get(f"/reports/{report_id}/json"))
            report_data = json.loads(str(raw_json["content"]))
            self.assertEqual(report_data["schema_version"], "recodex_core_report_v1")
            top_level_artifact = report_data["artifact_candidates"][0]
            self.assertIn("mechanism", top_level_artifact)
            self.assertIn("source_finding_ids", top_level_artifact)
            self.assertNotIn("artifact_type", top_level_artifact)
            self.assertNotIn("opportunity_id", top_level_artifact)
            self.assertEqual(
                report_data["efficiency_analysis"]["artifact_candidates"][0]["id"],
                top_level_artifact["id"],
            )
            self.assertEqual(
                report_data["artifact_review_queue"][0]["id"],
                top_level_artifact["id"],
            )

            review = _json(
                app.handle_get(f"/mining/review?reports_dir={reports}&report_id={report_id}")
            )
            artifact = review["artifact_candidates"][0]
            self.assertEqual(artifact["artifact_source"], "report_candidate")
            self.assertEqual(review["artifact_review_queue"][0]["id"], artifact["id"])

            preview = _json(
                app.handle_get(
                    f"/artifacts/preview?report_id={report_id}&artifact_id={artifact['id']}"
                )
            )

            self.assertEqual(preview["artifact_source"], "report_candidate")
            self.assertEqual(preview["artifact_type"], "checklist")
            self.assertEqual(preview["artifact_id"], artifact["id"])
            self.assertEqual(preview["files"][0]["path"], artifact["target_path"])
            self.assertIn("证据：", preview["files"][0]["content"])

            blocked = app.handle_post(
                "/artifacts/export",
                {
                    "report_id": report_id,
                    "artifact_id": artifact["id"],
                    "out": str(exports),
                },
            )
            self.assertEqual(blocked.status, HTTPStatus.BAD_REQUEST)
            self.assertIn("reviewed", blocked.body.decode("utf-8"))

            reviewed = _json(
                app.handle_post(
                    "/artifacts/review",
                    {
                        "report_id": report_id,
                        "artifact_id": artifact["id"],
                        "status": "accepted",
                    },
                )
            )
            self.assertEqual(reviewed["artifact"]["status"], "accepted")
            accepted_review = _json(app.handle_get(f"/mining/review?report_id={report_id}"))
            self.assertEqual(accepted_review["artifact_candidates"][0]["status"], "accepted")
            self.assertNotIn(
                artifact["id"],
                [item["id"] for item in accepted_review["artifact_review_queue"]],
            )
            report_path = Path(str(generated["report"]["json_path"]))
            reviewed_report = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.exists()
                else {}
            )
            reviewed_focus_artifact = next(
                item
                for item in reviewed_report["report_focus"]["recommended_artifacts"]
                if item["id"] == artifact["id"]
            )
            self.assertEqual(reviewed_focus_artifact["status"], "accepted")
            self.assertNotIn(
                artifact["id"],
                [item["id"] for item in reviewed_report["artifact_review_queue"]],
            )

            exported = _json(
                app.handle_post(
                    "/artifacts/export",
                    {
                        "report_id": report_id,
                        "artifact_id": artifact["id"],
                        "out": str(exports),
                        "reviewed": True,
                    },
                )
            )

            exported_path = Path(str(exported["paths"][0]))
            self.assertEqual(exported["artifact_source"], "report_candidate")
            self.assertTrue(exported_path.exists())
            self.assertEqual(exported_path, exports / Path(str(artifact["target_path"])))
            exported_content = exported_path.read_text(encoding="utf-8")
            self.assertIn(str(artifact["title"]), exported_content)
            self.assertIn("证据：", exported_content)

    def test_dashboard_artifact_candidate_rejects_unsafe_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "candidate-session.jsonl"
            _write_artifact_candidate_session(transcript, "dashboard-unsafe-candidate")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {
                        "target": "dashboard-unsafe-candidate",
                        "reports_dir": str(reports),
                        "llm": _mock_llm_settings(),
                    },
                )
            )
            report_id = str(generated["report"]["id"])
            report_path = Path(str(generated["report"]["json_path"]))
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
            artifact = report_data["artifact_candidates"][0]
            artifact["target_path"] = "../escape.md"
            report_path.write_text(json.dumps(report_data, ensure_ascii=False), encoding="utf-8")

            blocked = app.handle_get(
                f"/artifacts/preview?report_id={report_id}&artifact_id={artifact['id']}"
            )

            self.assertEqual(blocked.status, HTTPStatus.BAD_REQUEST)
            self.assertIn("Unsafe artifact", blocked.body.decode("utf-8"))

    def test_dashboard_artifact_effectiveness_compares_before_after_costs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            reports.mkdir()
            conn = connect(db)
            before_json = _write_effect_report(
                reports / "before.json",
                extra_turns=5,
                failed_commands=2,
                user_corrections=1,
                verification_followups=1,
            )
            after_json = _write_effect_report(
                reports / "after.json",
                extra_turns=1,
                failed_commands=0,
                user_corrections=0,
                verification_followups=0,
            )
            conn.execute(
                """
                INSERT INTO generated_reports (
                    id, kind, session_id, project_path, title,
                    html_path, markdown_path, json_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "report_before",
                    "session",
                    "before-session",
                    "/work/app",
                    "Before",
                    None,
                    None,
                    str(before_json),
                    "2026-05-28T01:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO artifact_exports (
                    artifact_type, improvement_id, target_path, status,
                    conflict_policy, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "checklist",
                    None,
                    str(root / "exports" / "docs" / "ai-coding-checklist.md"),
                    "ok",
                    "",
                    None,
                    "2026-05-28T02:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO generated_reports (
                    id, kind, session_id, project_path, title,
                    html_path, markdown_path, json_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "report_after",
                    "session",
                    "after-session",
                    "/work/app",
                    "After",
                    None,
                    None,
                    str(after_json),
                    "2026-05-28T03:00:00+00:00",
                ),
            )
            conn.commit()
            app = DashboardApp(db_path=db, dashboard_dir=None)

            effectiveness = _json(app.handle_get("/artifacts/effectiveness"))

            item = effectiveness["artifacts"][0]
            self.assertEqual(item["artifact_type"], "checklist")
            self.assertEqual(item["status"], "improved")
            self.assertEqual(item["before"]["report_count"], 1)
            self.assertEqual(item["after"]["report_count"], 1)
            self.assertEqual(item["delta"]["extra_turns"], -4)
            self.assertEqual(item["delta"]["failed_commands"], -2)

    def test_dashboard_workflow_analysis_returns_stage_details_and_report_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "dashboard-workflow-1", "Fix login and show verification.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            workflow = _json(
                app.handle_post(
                    "/analysis/run",
                    {"mode": "workflow", "target": "dashboard-workflow-1", "reports_dir": str(reports)},
                )
            )

            self.assertEqual(workflow["mode"], "workflow")
            self.assertEqual(workflow["report"]["kind"], "workflow")
            self.assertTrue(workflow["workflow"]["stages"])
            self.assertIn("deterministic_facts", workflow["workflow"])

            report_id = str(workflow["report"]["id"])
            raw_json = _json(app.handle_get(f"/reports/{report_id}/json"))
            self.assertIn('"analysis_mode": "llm-workflow"', raw_json["content"])
            self.assertIn('"stages"', raw_json["content"])

            conn = connect(db)
            prompt_count = conn.execute("SELECT COUNT(*) AS count FROM prompt_versions").fetchone()["count"]
            self.assertEqual(prompt_count, 1)

    def test_workflow_extract_retries_compact_payload_after_length_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "state.sqlite3")
            stage = WorkflowLLMStage(
                stage="extract",
                payload={
                    "session": {"session_id": "retry-session"},
                    "analysis_unit": {"id": "qunit_task_001"},
                    "qualitative_segments": [
                        {
                            "source_ref": "codex:retry-session:turn_1:event_1",
                            "text": "修复登录失败，并确认测试结果。",
                            "role": "user",
                            "codes": [{"code_id": "task_request"}],
                        }
                    ],
                    "qualitative_analysis": {"method": "codebook_qualitative_coding_v1"},
                },
                system="system",
                schema=extract_schema(),
                metadata={"task_type": "analysis_workflow_extract", "stage": "extract"},
                input_summary={"analysis_unit_id": "qunit_task_001"},
                max_output_tokens=2200,
            )
            calls: list[dict[str, object]] = []

            class FakeProvider:
                provider_name = "volcengine"

                def generate_json(self, **kwargs):
                    payload = json.loads(kwargs["messages"][-1]["content"])
                    calls.append({"tokens": kwargs["max_output_tokens"], "payload": payload})
                    if len(calls) == 1:
                        raise LLMResponseIncompleteError("Volcengine Ark", "length")
                    return {
                        "analysis_unit_id": "qunit_task_001",
                        "issues": [
                            {
                                "id": "issue_1",
                                "issue_type": "verification_gap",
                                "severity": "medium",
                                "evidence_refs": ["codex:retry-session:turn_1:event_1"],
                                "user_impact": "用户无法确认结果。",
                                "root_cause_hypothesis": "缺少验证闭环。",
                                "recommended_change": "补充验证状态。",
                                "confidence": 0.8,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }

            with patch("recodex.dashboard_services.provider_for_name", return_value=FakeProvider()):
                output = _run_llm_workflow_stage(
                    conn,
                    stage,
                    {
                        "enabled": True,
                        "provider": "volcengine",
                        "model": "ark-model",
                        "api_key": "ark-test",
                        "local_only": False,
                        "allow_cloud": True,
                    },
                )

            self.assertEqual(output.output["issues"][0]["id"], "issue_1")
            self.assertEqual(len(calls), 2)
            self.assertGreater(calls[1]["tokens"], calls[0]["tokens"])
            self.assertEqual(calls[1]["payload"]["retry_context"]["reason"], "length")  # type: ignore[index]
            self.assertEqual(calls[1]["payload"]["response_limits"]["max_issues"], 1)  # type: ignore[index]
            self.assertIn("compact_retry", output.warnings)

    def test_dashboard_llm_settings_and_mock_report_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "dashboard-llm-1", "Implement feature and verify tests.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            defaults = _json(app.handle_get("/settings/llm"))
            self.assertFalse(defaults["settings"]["enabled"])
            self.assertEqual(defaults["settings"]["provider"], "volcengine")

            saved = _json(
                app.handle_post(
                    "/settings/llm",
                    {
                        "enabled": True,
                        "provider": "mock",
                        "model": "mock-model",
                        "api_key": "local-secret",
                        "local_only": True,
                        "allow_cloud": False,
                    },
                )
            )
            self.assertTrue(saved["settings"]["enabled"])
            self.assertTrue(saved["settings"]["api_key_configured"])
            self.assertNotIn("api_key", saved["settings"])

            dashscope = _json(
                app.handle_post(
                    "/settings/llm",
                    {
                        "enabled": True,
                        "provider": "dashscope",
                        "model": "qwen-plus",
                        "api_key_env": "DASHSCOPE_API_KEY",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key": "dash-secret",
                        "local_only": False,
                        "allow_cloud": True,
                    },
                )
            )
            self.assertEqual(dashscope["settings"]["provider"], "dashscope")
            self.assertTrue(dashscope["settings"]["api_key_configured"])

            _json(
                app.handle_post(
                    "/settings/llm",
                    {
                        "enabled": True,
                        "provider": "mock",
                        "model": "mock-model",
                        "local_only": True,
                        "allow_cloud": False,
                    },
                )
            )
            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {"target": "dashboard-llm-1", "reports_dir": str(reports)},
                )
            )
            report_id = str(generated["report"]["id"])
            raw_json = _json(app.handle_get(f"/reports/{report_id}/json"))
            report = json.loads(str(raw_json["content"]))
            self.assertEqual(report["meta"]["analysis_mode"], "llm+rules+deep-audit")
            self.assertEqual(report["evidence_audit"]["status"], "pass")
            self.assertEqual(report["report_focus"]["title"], "验收条件没有在开工前固定")
            self.assertEqual(report["user_efficiency_analysis"]["subject"], "user_developer_workflow")

    def test_dashboard_report_generation_recovers_from_session_retro_length_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "dashboard-length-retry", "生成报告时 Ark 输出被截断也要恢复。")
            app = DashboardApp(db_path=db, dashboard_dir=None)
            calls: list[dict[str, object]] = []

            class LengthThenOkProvider:
                provider_name = "volcengine"

                def __init__(self) -> None:
                    self.last_usage: dict[str, object] = {}

                def generate_json(self, **kwargs):
                    payload = json.loads(kwargs["messages"][0]["content"])
                    calls.append({"tokens": kwargs["max_output_tokens"], "payload": payload})
                    if len(calls) == 1:
                        raise LLMResponseIncompleteError("Volcengine Ark", "length")
                    self.last_usage = {
                        "input_tokens": 1200,
                        "output_tokens": 600,
                        "total_tokens": 1800,
                    }
                    return MockProvider().generate_json(**kwargs)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            with patch("recodex.dashboard_services.provider_for_name", return_value=LengthThenOkProvider()):
                generated = _json(
                    app.handle_post(
                        "/reports/generate",
                        {
                            "target": "dashboard-length-retry",
                            "reports_dir": str(reports),
                            "llm": {
                                "enabled": True,
                                "provider": "volcengine",
                                "model": "ark-model",
                                "api_key": "ark-test",
                                "local_only": False,
                                "allow_cloud": True,
                            },
                        },
                    )
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["payload"]["retry_context"]["reason"], "length")  # type: ignore[index]
            report_id = str(generated["report"]["id"])
            raw_json = _json(app.handle_get(f"/reports/{report_id}/json"))
            report = json.loads(raw_json["content"])
            usage = report["token_usage"]["calls"][0]
            self.assertTrue(usage["retried"])
            self.assertIn("compact_retry", usage["warnings"])
            self.assertIn("compact_retry_reason:length", usage["warnings"])

    def test_dashboard_llm_settings_can_be_read_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "state.sqlite3"
            app = DashboardApp(db_path=db, dashboard_dir=None)
            _json(app.handle_get("/settings/llm"))

            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(lambda _: app.handle_get("/settings/llm"), range(24)))

            self.assertTrue(all(response.status == HTTPStatus.OK for response in responses))
            payloads = [json.loads(response.body.decode("utf-8")) for response in responses]
            self.assertTrue(all(payload["ok"] for payload in payloads))

    def test_dashboard_groups_sessions_by_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            project_a = root / "project-a"
            project_b = root / "project-b"
            transcript_a = root / "a.jsonl"
            transcript_b = root / "b.jsonl"
            _write_session(transcript_a, "project-a-session", "Fix project A.", cwd=str(project_a))
            _write_session(transcript_b, "project-b-session", "Fix project B.", cwd=str(project_b))
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(root)}))

            projects = _json(app.handle_get("/projects"))["projects"]
            self.assertEqual([project["project_path"] for project in projects], [str(project_a), str(project_b)])
            self.assertEqual(projects[0]["project_name"], "project-a")
            self.assertEqual(projects[0]["session_count"], 1)

            filtered = _json(app.handle_get(f"/sessions?project={str(project_a)}"))["sessions"]
            self.assertEqual([session["session_id"] for session in filtered], ["project-a-session"])
            self.assertEqual(filtered[0]["project_path"], str(project_a))

    def test_dashboard_searches_session_event_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            transcript = root / "session.jsonl"
            _write_session(transcript, "search-session", "Fix the frobnicator needle failure.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            results = _json(app.handle_get("/sessions/search?q=frobnicator"))["results"]

            self.assertEqual(results[0]["session"]["session_id"], "search-session")
            self.assertEqual(results[0]["matches"][0]["role"], "user")
            self.assertIn("frobnicator", results[0]["matches"][0]["text"])

    def test_dashboard_exposes_provider_capabilities_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            codex_home = root / "codex-home"
            project = root / "project-a"
            transcript = root / "session.jsonl"
            (codex_home / "sessions").mkdir(parents=True)
            (codex_home / "skills" / "review").mkdir(parents=True)
            (project / ".codex" / "skills" / "payment").mkdir(parents=True)
            (codex_home / "AGENTS.md").write_text("# Global Rules\nVerify changes.\n", encoding="utf-8")
            (codex_home / "config.toml").write_text(
                "[mcp_servers.files]\ncommand = \"python3\"\n",
                encoding="utf-8",
            )
            (codex_home / "skills" / "review" / "SKILL.md").write_text(
                "---\nname: review\n---\n# Review\n",
                encoding="utf-8",
            )
            (project / "AGENTS.md").write_text("# Project Rules\nUse pnpm.\n", encoding="utf-8")
            (project / ".codex" / "skills" / "payment" / "SKILL.md").write_text(
                "# Payment Skill\n",
                encoding="utf-8",
            )
            _write_session(transcript, "provider-session", "Use provider assets.", cwd=str(project))
            app = DashboardApp(db_path=db, dashboard_dir=None)

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
                providers = _json(app.handle_get("/providers"))["providers"]
                codex = next(provider for provider in providers if provider["id"] == "codex")
                self.assertTrue(codex["capabilities"]["has_sessions"])
                self.assertTrue(codex["capabilities"]["has_skills"])
                self.assertTrue(codex["capabilities"]["has_mcp_servers"])

                skills = _json(app.handle_get("/providers/codex/assets?type=skills"))["assets"]
                self.assertEqual({asset["name"] for asset in skills}, {"review", "Payment Skill"})

                instructions = _json(app.handle_get("/providers/codex/assets?type=instructions"))["assets"]
                self.assertEqual({asset["name"] for asset in instructions}, {"Global Rules", "Project Rules"})

                mcp = _json(app.handle_get("/providers/codex/assets?type=mcp"))["assets"]
                self.assertEqual(mcp[0]["name"], "MCP: files")

    def test_dashboard_exposes_evidence_mining_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            mining = reports / "evidence-mining"
            mining.mkdir(parents=True)
            (mining / "coverage_report.md").write_text(
                "\n".join(
                    [
                        "# Evidence Mining Coverage",
                        "",
                        "- Generated: 2026-06-17T00:00:00+00:00",
                        "- Sessions: 2",
                        "- Episodes: 4",
                        "- Analysis cards: 2",
                        "- Clusters: 1",
                        "- Ready for review clusters: 1",
                    ]
                ),
                encoding="utf-8",
            )
            (mining / "clusters.json").write_text(
                json.dumps(
                    [
                        {
                            "cluster_id": "cluster_validation",
                            "title": "改完代码后目标验证步骤不稳定",
                            "cluster_type": "validation_gap",
                            "common_pattern": "缺少目标测试复现。",
                            "frequency": 2,
                            "priority_score": 30,
                            "readiness": "ready_for_review",
                            "recommended_destinations": ["skill", "eval"],
                            "affected_repos": ["/repo-a"],
                            "card_ids": ["card_1"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (mining / "review_queue.json").write_text(
                json.dumps([{"cluster_id": "cluster_validation", "title": "review me"}]),
                encoding="utf-8",
            )
            (mining / "artifact_candidates.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "artifact_checklist_1",
                            "opportunity_id": "opp_validation",
                            "artifact_type": "checklist",
                            "target_path": "docs/ai-coding-checklist.md",
                            "proposed_content": "# Checklist\n",
                            "status": "proposed",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (mining / "cards.jsonl").write_text(
                json.dumps(
                    {
                        "card_id": "card_1",
                        "title": "CI 修复缺少复现",
                        "card_type": "validation_gap",
                        "observed_fact": "用户指出没有运行目标测试。",
                        "inferred_problem": "修复流程缺少目标验证。",
                        "candidate_destination": "skill",
                        "quality_score": 9.5,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            app = DashboardApp(db_path=db, dashboard_dir=None)

            review = _json(app.handle_get(f"/mining/review?reports_dir={reports}"))

            self.assertTrue(review["exists"])
            self.assertEqual(review["coverage"]["sessions"], 2)
            self.assertEqual(review["selected_cluster"]["cluster_id"], "cluster_validation")
            self.assertEqual(review["cards"][0]["card_id"], "card_1")
            self.assertEqual(review["artifact_candidates"][0]["id"], "artifact_checklist_1")
            self.assertEqual(review["artifact_candidates"][0]["artifact_source"], "mining_output")
            self.assertEqual(review["artifact_review_queue"][0]["id"], "artifact_checklist_1")

    def test_dashboard_report_latest_can_be_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            project_a = root / "project-a"
            project_b = root / "project-b"
            transcript_a = root / "a.jsonl"
            transcript_b = root / "b.jsonl"
            _write_session(
                transcript_a,
                "project-a-session",
                "Fix project A.",
                cwd=str(project_a),
                timestamp="2026-05-28T01:00:00+00:00",
            )
            _write_session(
                transcript_b,
                "project-b-session",
                "Fix project B.",
                cwd=str(project_b),
                timestamp="2026-05-28T02:00:00+00:00",
            )
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(root)}))
            generated = _json(
                app.handle_post(
                    "/reports/generate",
                    {
                        "target": "latest",
                        "project": str(project_a),
                        "reports_dir": str(reports),
                        "llm": _mock_llm_settings(),
                    },
                )
            )

            self.assertEqual(generated["report"]["session_id"], "project-a-session")
            self.assertEqual(generated["report"]["project_path"], str(project_a))

    def test_dashboard_analysis_jobs_report_status_until_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "job-session", "Fix the report status job.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            started = _json(
                app.handle_post(
                    "/analysis/jobs",
                    {"type": "analysis", "mode": "improvements", "since": "30d", "reports_dir": str(reports)},
                )
            )

            job = _wait_for_job(app, str(started["job"]["id"]))
            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["result"]["mode"], "improvements")
            self.assertIn("elapsed_ms", job)

    def test_dashboard_report_jobs_report_status_until_report_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "report-job-session", "Generate report job.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/settings/llm", _mock_llm_settings()))
            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            started = _json(
                app.handle_post(
                    "/analysis/jobs",
                    {"type": "report", "target": "report-job-session", "reports_dir": str(reports)},
                )
            )

            job = _wait_for_job(app, str(started["job"]["id"]))
            self.assertEqual(job["status"], "succeeded")
            report = job["result"]["report"]
            self.assertEqual(report["kind"], "session")
            self.assertEqual(report["session_id"], "report-job-session")
            self.assertTrue(Path(str(report["html_path"])).exists())
            self.assertTrue(Path(str(report["markdown_path"])).exists())
            self.assertTrue(Path(str(report["json_path"])).exists())

            raw_json = _json(app.handle_get(f"/reports/{report['id']}/json"))
            report_payload = json.loads(str(raw_json["content"]))
            self.assertEqual(report_payload["schema_version"], "recodex_core_report_v1")
            self.assertIn("deep-audit", report_payload["meta"]["analysis_mode"])
            self.assertNotEqual(report_payload["meta"]["analysis_mode"], "llm-workflow")

    def test_dashboard_report_job_requires_enabled_llm_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "fast-report-session", "Generate a quick report.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            started = _json(
                app.handle_post(
                    "/analysis/jobs",
                    {"type": "report", "target": "fast-report-session", "reports_dir": str(reports)},
                )
            )

            job = _wait_for_job(app, str(started["job"]["id"]))
            self.assertEqual(job["status"], "failed")
            self.assertIn("生成报告需要先配置并启用 LLM Provider", str(job.get("error")))

    def test_dashboard_report_job_uses_configured_llm_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            reports = root / "reports"
            transcript = root / "session.jsonl"
            _write_session(transcript, "llm-report-job-session", "Implement feature and verify tests.")
            app = DashboardApp(db_path=db, dashboard_dir=None)

            _json(
                app.handle_post(
                    "/settings/llm",
                    {
                        "enabled": True,
                        "provider": "mock",
                        "model": "mock-model",
                        "api_key": "local-secret",
                        "local_only": True,
                        "allow_cloud": False,
                    },
                )
            )
            _json(app.handle_post("/import/run", {"source": "codex", "path": str(transcript)}))
            started = _json(
                app.handle_post(
                    "/analysis/jobs",
                    {
                        "type": "report",
                        "target": "llm-report-job-session",
                        "reports_dir": str(reports),
                    },
                )
            )

            job = _wait_for_job(app, str(started["job"]["id"]))
            self.assertEqual(job["status"], "succeeded", job.get("error"))
            report = job["result"]["report"]
            self.assertEqual(report["session_id"], "llm-report-job-session")
            raw_json = _json(app.handle_get(f"/reports/{report['id']}/json"))
            report_payload = json.loads(str(raw_json["content"]))
            self.assertEqual(report_payload["schema_version"], "recodex_core_report_v1")
            self.assertEqual(report_payload["meta"]["analysis_mode"], "llm+rules+deep-audit")
            self.assertEqual(report_payload["report_focus"]["title"], "验收条件没有在开工前固定")
            self.assertEqual(
                report_payload["user_efficiency_analysis"]["subject"],
                "user_developer_workflow",
            )


def _json(response) -> dict[str, object]:
    self_status = getattr(response, "status", HTTPStatus.OK)
    if self_status != HTTPStatus.OK:
        raise AssertionError(response.body.decode("utf-8"))
    return json.loads(response.body.decode("utf-8"))


def _mock_llm_settings() -> dict[str, object]:
    return {
        "enabled": True,
        "provider": "mock",
        "model": "mock-model",
        "api_key": "local-secret",
        "local_only": True,
        "allow_cloud": False,
    }


def _wait_for_job(app: DashboardApp, job_id: str) -> dict[str, object]:
    deadline = time.time() + 5
    while time.time() < deadline:
        payload = _json(app.handle_get(f"/analysis/jobs/{job_id}"))
        job = payload["job"]
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def _write_session(
    path: Path,
    session_id: str,
    text: str,
    *,
    cwd: str | None = None,
    timestamp: str = "2026-05-28T01:00:00+00:00",
) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "session_id": session_id,
                "timestamp": timestamp,
                **({"cwd": cwd} if cwd else {}),
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_repeated_requirement_session(path: Path, session_id: str) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Build the dashboard."}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:01:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Use pnpm instead of npm for package manager commands.",
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:02:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Use pnpm instead of npm for package manager commands.",
                    }
                ],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def _write_diagnostic_session(path: Path, session_id: str) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix login test failure."}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:01:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "我会修改登录逻辑。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:02:00+00:00",
            "item": {
                "type": "exec",
                "command": "apply_patch <<PATCH",
                "exit_code": 0,
                "stdout": "Success. Updated the following files:\nsrc/login.py",
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:03:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "已修改 src/login.py，完成。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:04:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "不对，还没有跑指定的 pytest tests/test_login.py。"}
                ],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:05:00+00:00",
            "item": {
                "type": "exec",
                "command": "pytest tests/test_login.py",
                "exit_code": 0,
                "stdout": "1 passed",
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def _write_artifact_candidate_session(path: Path, session_id: str) -> None:
    rows = [
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:00:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "帮我修 CI failure。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:01:00+00:00",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "我已经修好了。"}],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:02:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "你还没看 CI 日志，也没跑失败的 test。先看日志，定位具体失败命令。",
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:03:00+00:00",
            "item": {
                "type": "exec",
                "command": "npm test",
                "exit_code": 0,
                "stdout": "Process exited with code 0",
            },
        },
        {
            "type": "response_item",
            "session_id": session_id,
            "timestamp": "2026-05-28T01:04:00+00:00",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "不是 npm test，CI 失败的是 pnpm test:payment。"}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def _write_effect_report(
    path: Path,
    *,
    extra_turns: int,
    failed_commands: int,
    user_corrections: int,
    verification_followups: int,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "meta": {"report_id": path.stem},
                "core_diagnostics": {
                    "cost_ledger": {
                        "extra_turns": extra_turns,
                        "failed_commands": failed_commands,
                        "user_corrections": user_corrections,
                        "verification_followups": verification_followups,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_claude_session(path: Path, session_id: str, text: str) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "permission-mode", "sessionId": session_id, "permissionMode": "default"}),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": session_id,
                        "timestamp": "2026-05-28T01:00:00+00:00",
                        "message": {"role": "user", "content": text},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": session_id,
                        "timestamp": "2026-05-28T01:01:00+00:00",
                        "message": {"role": "assistant", "content": "Reviewed."},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


def _encoded_claude_project_name(path: Path) -> str:
    return "-" + str(path).lstrip("/").replace("/", "-")


def _seed_accepted_improvement(db: Path) -> None:
    conn = connect(db)
    insert_improvements(
        conn,
        [
            ImprovementDraft(
                fingerprint="dashboard-skill-1",
                session_id=None,
                category="workflow",
                title="Dashboard Skill",
                evidence="Dashboard export needs accepted candidates.",
                recommendation="Export accepted dashboard skills.",
            )
        ],
    )
    update_improvement_status(conn, [1], "accepted")


if __name__ == "__main__":
    unittest.main()
