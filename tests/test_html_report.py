from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recodex.html_report import (
    _efficiency_actions,
    _efficiency_issues,
    _efficiency_suggestions,
    _normalize_core_diagnostics,
    build_project_report_data,
    build_session_report_data,
    render_report_html,
    write_report_bundle,
)
from recodex.models import SessionRecord, TranscriptEvent


class HtmlReportTests(unittest.TestCase):
    def test_session_report_data_has_display_schema(self) -> None:
        session = _session()
        events = _events(session.session_id)

        report = build_session_report_data(session, events)

        for key in (
            "meta",
            "summary",
            "metrics",
            "flow",
            "issues",
            "context_frontload",
            "intervention",
            "verification",
            "suggestions",
            "artifacts",
            "evidence",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["meta"]["project"], "/work/aicoo")  # type: ignore[index]
        self.assertEqual(report["meta"]["source"], "codex")  # type: ignore[index]
        self.assertFalse(report["metrics"]["verification_found"])  # type: ignore[index]
        self.assertTrue(report["issues"])
        self.assertTrue(report["suggestions"])
        self.assertEqual(report["evidence"][0]["content"], "帮我修复 token refresh。password=<redacted>")  # type: ignore[index]
        self.assertNotIn("我修改了认证逻辑", report["evidence"][0]["content"])  # type: ignore[index]

    def test_user_requested_verification_is_not_counted_as_verified(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="After code changes, run pnpm test:dashboard.",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="I updated the dashboard and it is done.",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="You did not run pnpm test:dashboard.",
                created_at="2026-05-29T01:02:00+00:00",
            ),
        ]

        report = build_session_report_data(session, events)

        self.assertFalse(report["metrics"]["verification_found"])  # type: ignore[index]
        self.assertEqual(report["verification"]["overall"], "验证闭环不足")  # type: ignore[index]
        self.assertEqual(
            report["task_outcome"]["result"],  # type: ignore[index]
            "completed_with_verification_gap",
        )
        self.assertFalse(report["task_outcome"]["accepted_and_evidenced"])  # type: ignore[index]
        self.assertEqual(report["findings"][0]["problem_type"], "verification_debt")  # type: ignore[index]

    def test_command_verification_is_counted_as_verified(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="tool",
                kind="exec_command",
                text="command=pnpm test:dashboard\nProcess exited with code 0",
                created_at="2026-05-29T01:00:00+00:00",
                metadata={"command": "pnpm test:dashboard", "exit_code": 0},
            )
        ]

        report = build_session_report_data(session, events)

        self.assertTrue(report["metrics"]["verification_found"])  # type: ignore[index]
        self.assertEqual(report["verification"]["overall"], "验证闭环存在")  # type: ignore[index]

    def test_html_report_embeds_json_without_fetching_sidecar_file(self) -> None:
        session = _session()
        events = _events(session.session_id)
        report = build_session_report_data(session, events)

        html = render_report_html(report)

        self.assertIn("<!doctype html>", html.lower())
        self.assertIn('<script id="report-data" type="application/json">', html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("\\u003c/script\\u003e", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("验收证据不足", html)
        self.assertNotIn("规则经验库对照", html)

    def test_session_report_keeps_core_diagnostics_but_uses_v2_summary(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events)
        core = report["core_diagnostics"]  # type: ignore[index]
        opportunities = report["improvement_opportunities"]  # type: ignore[index]

        self.assertGreaterEqual(core["cost_ledger"]["user_corrections"], 1)  # type: ignore[index]
        self.assertTrue(core["evidence_refs"])  # type: ignore[index]
        self.assertTrue(core["findings"])  # type: ignore[index]
        self.assertTrue(opportunities)
        self.assertEqual(report["summary"]["primary_improvement"], opportunities[0]["title"])  # type: ignore[index]
        mechanisms = {item["recommended_mechanism"] for item in opportunities}
        self.assertIn("checklist", mechanisms)
        self.assertNotIn("skill", mechanisms)

        html = render_report_html(report)

        self.assertIn("最大可避免成本", html)
        self.assertIn("首要改进", html)
        self.assertIn("关键问题回答", html)
        self.assertIn("问题 → 改进 → 沉淀建议", html)
        self.assertIn("沉淀建议", html)

    def test_session_report_promotes_core_contract_to_top_level_schema(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events)

        self.assertEqual(report["schema_version"], "recodex_core_report_v1")
        self.assertEqual(report["evidence_audit"]["mode"], "light")
        self.assertIn("audit", report["meta"]["analysis_mode"])
        for key in (
            "task_outcome",
            "cost_ledger",
            "findings",
            "improvement_opportunities",
            "artifact_candidates",
            "artifact_review_queue",
            "core_answers",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["cost_ledger"], report["efficiency_analysis"]["cost_ledger"])
        self.assertLessEqual(len(report["findings"]), 3)
        self.assertLessEqual(len(report["improvement_opportunities"]), 3)
        self.assertLessEqual(len(report["artifact_candidates"]), 3)
        self.assertTrue(all("problem_type" in item for item in report["findings"]))
        self.assertTrue(all("category" not in item for item in report["findings"]))
        self.assertTrue(all("card_type" not in item for item in report["findings"]))
        self.assertTrue(all("mechanism" in item for item in report["artifact_candidates"]))
        self.assertTrue(all("source_finding_ids" in item for item in report["artifact_candidates"]))
        self.assertTrue(all("artifact_type" not in item for item in report["artifact_candidates"]))
        self.assertTrue(all("opportunity_id" not in item for item in report["artifact_candidates"]))
        serialized_report = json.dumps(report, ensure_ascii=False)
        self.assertNotIn('"category"', serialized_report)
        self.assertNotIn('"card_type"', serialized_report)
        self.assertEqual(
            len({item["title"] for item in report["findings"]}),
            len(report["findings"]),
        )
        self.assertEqual(
            len({item["title"] for item in report["improvement_opportunities"]}),
            len(report["improvement_opportunities"]),
        )
        self.assertEqual(
            report["summary"]["primary_improvement"],
            report["improvement_opportunities"][0]["title"],
        )
        self.assertEqual(
            report["core_answers"]["what_should_be_preserved_as_artifact"],
            report["artifact_candidates"][0]["mechanism"],
        )
        self.assertEqual(
            [item["id"] for item in report["artifact_review_queue"]],
            [item["id"] for item in report["artifact_candidates"] if item["status"] in {"proposed", "ready_for_review"}],
        )
        finding_ids = {item["id"] for item in report["findings"]}
        for opportunity in report["improvement_opportunities"]:
            self.assertTrue(set(opportunity["source_finding_ids"]).issubset(finding_ids))

    def test_session_report_metrics_use_v2_cost_ledger_values(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events)
        ledger = report["cost_ledger"]  # type: ignore[index]
        metrics = report["metrics"]  # type: ignore[index]

        for key in (
            "extra_turns",
            "failed_commands",
            "repeated_commands",
            "repeated_file_reads",
            "user_corrections",
        ):
            self.assertEqual(metrics[key], ledger.get(key) or 0)

    def test_deep_session_report_includes_evidence_audit(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events, deep=True)

        audit = report["evidence_audit"]  # type: ignore[index]
        self.assertEqual(audit["status"], "pass")  # type: ignore[index]
        self.assertIn("deep-audit", report["meta"]["analysis_mode"])  # type: ignore[index]

        html = render_report_html(report)

        self.assertIn("证据检查", html)
        self.assertIn("可追溯率", html)

    def test_session_report_resolves_core_evidence_to_chat_analysis(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events)

        analysis = report["conversation_analysis"]  # type: ignore[index]
        self.assertTrue(analysis)
        joined = json.dumps(analysis, ensure_ascii=False)
        self.assertIn("不是 npm test，CI 失败的是 pnpm test:payment。", joined)
        self.assertIn("聊天证据", joined)

        html = render_report_html(report)

        self.assertIn("证据中的聊天片段", html)
        self.assertIn("不是 npm test，CI 失败的是 pnpm test:payment。", html)

    def test_session_report_includes_llm_chat_transcript_analysis(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="我需要分析原始聊天文字，不要工具执行结果。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我会把聊天文字单独送给 LLM 分析。",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="tool",
                kind="exec_command",
                text="command=pytest\nSECRET_TOOL_OUTPUT\nProcess exited with code 1",
                created_at="2026-05-29T01:02:00+00:00",
                metadata={"command": "pytest", "exit_code": 1},
            ),
        ]
        analysis = {
            "overall_assessment": "已生成结构化复盘。",
            "main_findings": [],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "chat_transcript_analysis": {
                "summary": "LLM 已基于纯聊天文字识别出用户要求保留原始对话语义。",
                "key_observations": ["用户明确要求不要把工具执行结果混入聊天分析。"],
                "friction_points": ["报告输入链路需要区分聊天文字和工具证据。"],
                "evidence_refs": ["event_0", "event_1"],
            },
        }

        report = build_session_report_data(session, events, analysis)

        chat_analysis = report["chat_transcript_analysis"]  # type: ignore[index]
        serialized = json.dumps(chat_analysis, ensure_ascii=False)
        self.assertEqual(chat_analysis["method"]["scope"], "raw_user_and_assistant_chat_text")  # type: ignore[index]
        self.assertEqual(chat_analysis["source"], "llm")  # type: ignore[index]
        self.assertIn("纯聊天文字", serialized)
        self.assertIn("event_0", serialized)
        self.assertNotIn("SECRET_TOOL_OUTPUT", serialized)
        self.assertNotIn("command=pytest", serialized)

        html = render_report_html(report)

        self.assertIn("聊天与提效分析", html)
        self.assertIn("用户明确要求不要把工具执行结果混入聊天分析。", html)

    def test_session_report_uses_llm_chat_focus_for_primary_summary(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="按照新架构重构报告页面。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我先完成后端报告结构。",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="dashboard 里怎么看不到新报告效果？",
                created_at="2026-05-29T01:02:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="你这个报告还是旧思路。",
                created_at="2026-05-29T01:03:00+00:00",
            ),
        ]
        analysis = {
            "overall_assessment": "用户能否在 dashboard 看到新架构，是本次重构验收的关键。",
            "main_findings": [
                {
                    "title": "前端展示链路未对齐重构成果",
                    "category": "context_gap",
                    "severity": "high",
                    "confidence": 0.95,
                    "problem": "后端报告结构已变化，但 dashboard 首屏仍按旧字段展示，用户感知不到新架构效果。",
                    "evidence_refs": ["event_2", "event_3"],
                    "impact": "用户会质疑重构是否真正完成。",
                    "recommendation": "把 dashboard 报告首屏和列表摘要改为优先展示 LLM 聊天分析主结论。",
                    "suggested_artifacts": ["checklist"],
                }
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [
                {
                    "title": "dashboard 报告展示验收 checklist",
                    "artifact_type": "checklist",
                    "priority": "high",
                    "effort": "low",
                    "why": "防止后端报告已改但用户在 dashboard 看不到新结论。",
                    "evidence_refs": ["event_2", "event_3"],
                }
            ],
            "chat_transcript_analysis": {
                "summary": "用户持续追问 dashboard 是否能看到新报告效果。",
                "key_observations": ["用户验收入口是 dashboard 报告页。"],
                "friction_points": ["后端能力和前端展示没有同步对齐。"],
                "evidence_refs": ["event_0", "event_2", "event_3"],
            },
        }

        report = build_session_report_data(session, events, analysis)

        focus = report["report_focus"]  # type: ignore[index]
        self.assertEqual(focus["source"], "llm_chat_transcript")  # type: ignore[index]
        self.assertEqual(report["summary"]["top_focus"], "前端展示链路未对齐重构成果")  # type: ignore[index]
        self.assertIn("dashboard 首屏仍按旧字段展示", report["summary"]["primary_cause"])  # type: ignore[index]
        self.assertIn("dashboard 报告首屏", report["summary"]["primary_improvement"])  # type: ignore[index]
        self.assertEqual(
            report["core_answers"]["why_it_happened"],  # type: ignore[index]
            report["summary"]["primary_cause"],  # type: ignore[index]
        )
        self.assertEqual(
            report["core_answers"]["highest_leverage_change"],  # type: ignore[index]
            report["summary"]["primary_improvement"],  # type: ignore[index]
        )
        self.assertEqual(focus["recommended_artifacts"][0]["mechanism"], "checklist")  # type: ignore[index]
        self.assertIn("dashboard 报告展示验收 checklist", json.dumps(focus, ensure_ascii=False))

    def test_session_report_prefers_structured_chat_findings_and_audits_focus(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="看下 core 的任务相关内容。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我先梳理任务主链路。",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="现在完整实现这个路线产品文档，不要只做最小版本。",
                created_at="2026-05-29T01:02:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="对接上了吗？我配置好了你测试一下。",
                created_at="2026-05-29T01:03:00+00:00",
            ),
        ]
        analysis = {
            "overall_assessment": "聊天主线显示需求边界和验证闭环是核心问题。",
            "main_findings": [
                {
                    "title": "旧规则主导的工具浪费结论",
                    "category": "tool_waste",
                    "severity": "critical",
                    "confidence": 0.99,
                    "problem": "旧主线不应覆盖聊天结论。",
                    "evidence_refs": ["event_1"],
                    "impact": "报告主线跑偏。",
                    "recommendation": "不要使用旧主线。",
                    "suggested_artifacts": ["agents_md"],
                }
            ],
            "chat_findings": [
                {
                    "title": "需求扩展缺少阶段边界",
                    "problem": "用户从查看任务链路扩展到完整产品实现，但会话没有先确认阶段目标。",
                    "cause": "每次扩展需求前缺少边界确认和验收标准。",
                    "impact": "交付范围不断扩大，验证闭环被推迟。",
                    "recommendation": "在进入完整实现前确认阶段目标、非目标和验收清单。",
                    "severity": "high",
                    "confidence": 0.91,
                    "evidence_refs": ["event_0", "event_2", "event_3"],
                    "artifact_type": "checklist",
                    "artifact_title": "需求边界确认 checklist",
                    "artifact_target_path": "docs/requirement-boundary-checklist.md",
                }
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [
                {
                    "title": "旧 AGENTS 建议",
                    "artifact_type": "agents_md",
                    "priority": "medium",
                    "effort": "low",
                    "why": "这个候选不应排在 chat finding 前面。",
                    "evidence_refs": ["event_1"],
                }
            ],
            "chat_transcript_analysis": {
                "summary": "用户请求从查看任务链路扩展到完整实现，并追问对接和测试状态。",
                "key_observations": ["用户明确要求完整实现。"],
                "friction_points": ["需求扩展前缺少阶段边界。"],
                "evidence_refs": ["event_0", "event_2", "event_3"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        self.assertEqual(report["summary"]["top_focus"], "需求扩展缺少阶段边界")  # type: ignore[index]
        self.assertIn("缺少边界确认", report["summary"]["primary_cause"])  # type: ignore[index]
        self.assertIn("确认阶段目标", report["summary"]["primary_improvement"])  # type: ignore[index]
        focus = report["report_focus"]  # type: ignore[index]
        self.assertEqual(focus["source_finding_id"], "chat_finding_1")  # type: ignore[index]
        self.assertEqual(
            focus["recommended_artifacts"][0]["target_path"],  # type: ignore[index]
            "docs/requirement-boundary-checklist.md",
        )
        audit = report["evidence_audit"]  # type: ignore[index]
        audited_targets = [item["target"] for item in audit["audited_objects"]]  # type: ignore[index]
        self.assertIn("report.report_focus", audited_targets)
        self.assertIn("report.report_focus.recommended_artifacts[0]", audited_targets)
        self.assertEqual(audit["status"], "pass")  # type: ignore[index]

    def test_session_report_routes_multiple_chat_findings_to_reviewable_artifacts(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="先看任务链路。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我先梳理任务链路。",
                created_at="2026-05-29T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="完整实现路线产品，先拆里程碑。",
                created_at="2026-05-29T01:02:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="交付时告诉我哪些是真实现哪些是 mock。",
                created_at="2026-05-29T01:03:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=4,
                role="user",
                kind="message",
                text="完成后按真实场景测试。",
                created_at="2026-05-29T01:04:00+00:00",
            ),
        ]
        analysis = {
            "overall_assessment": "聊天主线显示多个可沉淀改进。",
            "main_findings": [],
            "chat_findings": [
                {
                    "title": "需求演进缺少里程碑拆分",
                    "problem": "需求从调研扩展到完整实现，未先拆分阶段。",
                    "cause": "缺少里程碑确认入口。",
                    "impact": "用户难以及时看到阶段成果。",
                    "recommendation": "先拆分里程碑，再进入实现。",
                    "severity": "high",
                    "confidence": 0.94,
                    "evidence_refs": ["event_0", "event_2"],
                    "artifact_type": "prompt_template",
                    "artifact_title": "需求里程碑拆分引导 prompt",
                    "artifact_target_path": "prompts/requirement_milestone_split.md",
                },
                {
                    "title": "mock 与真实实现未显式交接",
                    "problem": "交付状态没有明确区分 mock provider 和真实接入。",
                    "cause": "缺少交付状态模板。",
                    "impact": "用户可能误判上线准备度。",
                    "recommendation": "交付时列出真实实现、mock 实现和替换条件。",
                    "severity": "medium",
                    "confidence": 0.88,
                    "evidence_refs": ["event_3"],
                    "artifact_type": "checklist",
                    "artifact_title": "mock 与真实 provider 交接清单",
                    "artifact_target_path": "checklists/mock_real_provider_handoff.md",
                },
                {
                    "title": "功能验证没有覆盖真实场景",
                    "problem": "验证停留在编译通过，未覆盖真实使用路径。",
                    "cause": "缺少场景验收清单。",
                    "impact": "用户会在使用时才发现问题。",
                    "recommendation": "交付前补真实场景验收清单。",
                    "severity": "medium",
                    "confidence": 0.86,
                    "evidence_refs": ["event_4"],
                    "artifact_type": "checklist",
                    "artifact_title": "真实场景验证清单",
                    "artifact_target_path": "docs/acceptance/real_scene_validation.md",
                },
                {
                    "title": "低优先级观察不应挤掉前三个产物",
                    "problem": "低优先级观察不应优先沉淀。",
                    "cause": "排序不稳定。",
                    "impact": "产物列表变噪。",
                    "recommendation": "只保留前三个高价值产物。",
                    "severity": "low",
                    "confidence": 0.8,
                    "evidence_refs": ["event_1"],
                    "artifact_type": "agents_md",
                    "artifact_title": "低优先级规则",
                    "artifact_target_path": "AGENTS.md",
                },
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "chat_transcript_analysis": {
                "summary": "用户多次提出阶段拆分、mock 交接和真实场景验证要求。",
                "key_observations": [
                    "编译、构建验证充分。",
                    "用户明确要求完整实现。",
                ],
                "friction_points": [
                    "功能验证仅到编译级别，未覆盖真实使用场景。",
                    "交付状态缺少 mock 与真实实现说明。",
                ],
                "evidence_refs": ["event_0", "event_2", "event_3", "event_4"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        focus = report["report_focus"]  # type: ignore[index]
        artifacts = focus["recommended_artifacts"]  # type: ignore[index]
        self.assertEqual(len(artifacts), 3)
        self.assertEqual(
            [artifact["source_finding_id"] for artifact in artifacts],
            ["chat_finding_1", "chat_finding_2", "chat_finding_3"],
        )
        self.assertEqual(
            artifacts[0]["target_path"],
            "docs/prompts/requirement-milestone-split.md",
        )
        self.assertEqual(
            artifacts[1]["target_path"],
            "docs/checklists/mock-real-provider-handoff.md",
        )
        self.assertEqual(
            artifacts[2]["target_path"],
            "docs/acceptance/real-scene-validation.md",
        )
        self.assertNotIn("报告首屏必须展示", json.dumps(artifacts, ensure_ascii=False))
        self.assertIn(
            "编译/构建验证较充分，但真实场景验证仍不足。",
            focus["key_observations"],  # type: ignore[index]
        )

        audit = report["evidence_audit"]  # type: ignore[index]
        focus_audit = next(
            item
            for item in audit["audited_objects"]  # type: ignore[index]
            if item["target"] == "report.report_focus"
        )
        self.assertTrue(focus_audit["evidence_quotes"])
        self.assertIn(
            "先看任务链路",
            json.dumps(focus_audit["evidence_quotes"], ensure_ascii=False),
        )
        artifact_audits = [
            item
            for item in audit["audited_objects"]  # type: ignore[index]
            if str(item["target"]).startswith("report.report_focus.recommended_artifacts")
        ]
        self.assertEqual(len(artifact_audits), 3)
        self.assertTrue(all(item["evidence_quotes"] for item in artifact_audits))

    def test_session_report_routes_outcome_artifacts_before_environment_precheck(self) -> None:
        session = _session("outcome-artifact-ranking")
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=index,
                role="user" if index != 1 else "assistant",
                kind="message",
                text=text,
                created_at=f"2026-05-29T01:{index:02d}:00+00:00",
            )
            for index, text in enumerate(
                [
                    "先看任务链路。",
                    "我先梳理任务链路。",
                    "完整实现路线产品，先拆里程碑。",
                    "交付时告诉我已完成、未完成、占位和 mock 边界。",
                    "编译先失败在本机 Java 版本不对。",
                    "完成后按真实场景测试，别只编译通过。",
                ]
            )
        ]
        analysis = {
            "overall_assessment": "聊天主线显示多个改进点。",
            "main_findings": [],
            "chat_findings": [
                {
                    "title": "需求演进缺少里程碑拆分",
                    "problem": "多阶段需求未先拆分交付阶段。",
                    "recommendation": "先拆分里程碑，再进入实现。",
                    "severity": "medium",
                    "confidence": 0.9,
                    "evidence_refs": ["event_2"],
                    "artifact_type": "prompt_template",
                    "artifact_title": "需求里程碑拆分引导 prompt",
                    "artifact_target_path": "prompts/requirement_milestone_split.md",
                },
                {
                    "title": "环境预校验缺失",
                    "problem": "编译前未确认 Java 版本。",
                    "recommendation": "开始开发前运行环境预检查脚本。",
                    "severity": "low",
                    "confidence": 0.95,
                    "evidence_refs": ["event_4"],
                    "artifact_type": "script",
                    "artifact_title": "项目开发环境预检查脚本",
                    "artifact_target_path": "scripts/env_precheck.sh",
                },
                {
                    "title": "交付状态主动告知缺失",
                    "problem": "交付时未明确已完成、未完成、占位和 mock 边界。",
                    "recommendation": "每次功能交付时主动输出实现清单。",
                    "severity": "low",
                    "confidence": 0.85,
                    "evidence_refs": ["event_3"],
                    "artifact_type": "checklist",
                    "artifact_title": "功能交付状态清单",
                    "artifact_target_path": "checklists/feature_delivery_checklist.md",
                },
                {
                    "title": "功能测试验证不足",
                    "problem": "只完成编译级验证，未覆盖真实使用场景。",
                    "recommendation": "新增功能完成后按真实场景测试。",
                    "severity": "medium",
                    "confidence": 0.86,
                    "evidence_refs": ["event_5"],
                    "artifact_type": "checklist",
                    "artifact_title": "真实场景验收清单",
                    "artifact_target_path": "checklists/feature_test_checklist.md",
                },
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "chat_transcript_analysis": {
                "summary": "需要同时沉淀里程碑、交付状态和真实场景验收。",
                "key_observations": [],
                "friction_points": [],
                "evidence_refs": ["event_2", "event_3", "event_5"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        artifacts = report["report_focus"]["recommended_artifacts"]  # type: ignore[index]
        self.assertEqual(
            [artifact["source_finding_id"] for artifact in artifacts],
            ["chat_finding_1", "chat_finding_4", "chat_finding_3"],
        )
        self.assertNotIn("env_precheck", json.dumps(artifacts, ensure_ascii=False))
        self.assertIn("### 触发条件", artifacts[0]["proposed_content"])
        self.assertIn("### 阶段拆分模板", artifacts[0]["proposed_content"])
        self.assertIn("- [ ] 覆盖真实用户路径", artifacts[1]["proposed_content"])
        self.assertIn("- [ ] 已完成", artifacts[2]["proposed_content"])
        self.assertIn("mock", artifacts[2]["proposed_content"].lower())
        effect = report["effect_observation"]  # type: ignore[index]
        self.assertIn("success_indicators", effect)
        self.assertIn("里程碑", json.dumps(effect["success_indicators"], ensure_ascii=False))
        self.assertIn("真实场景", json.dumps(effect["success_indicators"], ensure_ascii=False))
        self.assertIn("交付状态", json.dumps(effect["success_indicators"], ensure_ascii=False))

    def test_session_report_merges_chat_and_user_efficiency_signals_into_v2_chain(self) -> None:
        session = _session("recodex-self-refactor")
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="recodex/docs/recodex_core_analysis_and_artifact_architecture.md 根据这个文档重构。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="tool",
                kind="exec_command",
                text="command=sed -n '1,220p' README.md\nProcess exited with code 0",
                created_at="2026-05-29T01:01:00+00:00",
                metadata={"command": "sed -n '1,220p' README.md", "exit_code": 0},
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="tool",
                kind="exec_command",
                text="command=sed -n '220,330p' README.md\nProcess exited with code 0",
                created_at="2026-05-29T01:02:00+00:00",
                metadata={"command": "sed -n '220,330p' README.md", "exit_code": 0},
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="你列一下完整实现的任务列表，然后一个一个实现。",
                created_at="2026-05-29T01:03:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=4,
                role="user",
                kind="message",
                text="是否重构完成？是否有为开发的占位，模拟等内容？",
                created_at="2026-05-29T01:04:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=5,
                role="user",
                kind="message",
                text="你这个报告是按照最新的核心思路出的吗，我怎么感觉跟之前一样",
                created_at="2026-05-29T01:05:00+00:00",
            ),
        ]
        analysis = {
            "overall_assessment": "重构完成但用户可见验证不足。",
            "main_findings": [],
            "chat_findings": [
                {
                    "title": "重构成果用户验证缺失",
                    "problem": "重构完成后未主动引导用户验证核心功能效果。",
                    "recommendation": "功能完成后展示效果、入口和前后差异，邀请用户确认。",
                    "severity": "high",
                    "confidence": 0.9,
                    "evidence_refs": ["event_5"],
                    "artifact_type": "checklist",
                    "artifact_title": "重构交付验证检查清单",
                    "artifact_target_path": "recodex/docs/checklists/refactor_delivery_checklist.md",
                },
                {
                    "title": "用户任务拆解需求响应缺失",
                    "problem": "用户要求列出完整任务列表再逐个实现，助手未响应该需求。",
                    "recommendation": "接收到任务列表需求时，先输出任务拆解并经用户确认。",
                    "severity": "medium",
                    "confidence": 0.85,
                    "evidence_refs": ["event_3"],
                    "artifact_type": "skill",
                    "artifact_title": "用户需求优先级处理技能",
                    "artifact_target_path": "recodex/skills/user_demand_priority_handling.md",
                },
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "chat_transcript_analysis": {
                "summary": "用户关注重构效果、任务拆解和完成度验证。",
                "key_observations": [],
                "friction_points": [],
                "evidence_refs": ["event_0", "event_3", "event_4", "event_5"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        finding_titles = [finding["title"] for finding in report["findings"]]  # type: ignore[index]
        self.assertEqual(
            finding_titles[:3],
            [
                "验收方式和完成边界没有前置",
                "需求完整度没有被持续追踪",
                "稳定项目知识被反复重新发现",
            ],
        )
        artifact_targets = [
            artifact["target_path"]
            for artifact in report["artifact_candidates"]  # type: ignore[index]
        ]
        self.assertEqual(
            artifact_targets[:3],
            [
                "recodex/docs/checklists/refactor_delivery_checklist.md",
                "docs/implementation-ledger.md",
                "AGENTS.md",
            ],
        )
        self.assertEqual(report["improvement_opportunities"][0]["title"], "前置验收方式和完成边界")  # type: ignore[index]
        self.assertEqual(report["improvement_opportunities"][1]["title"], "建立需求完成度账本")  # type: ignore[index]
        self.assertEqual(
            [
                action["title"]
                for action in report["efficiency_actions"][:3]  # type: ignore[index]
            ],
            [
                "前置验收方式和完成边界",
                "建立需求完成度账本",
                "把稳定项目事实前置到 AGENTS.md",
            ],
        )
        user_efficiency = report["user_efficiency_analysis"]  # type: ignore[index]
        self.assertEqual(user_efficiency["subject"], "user_developer_workflow")  # type: ignore[index]
        self.assertIn("聊天记录和效率诊断", user_efficiency["summary"])  # type: ignore[index]
        self.assertIn("验收方式", user_efficiency["top_guidance"][0]["title"])  # type: ignore[index]
        report_text = json.dumps(
            {
                "summary": report["summary"],  # type: ignore[index]
                "core_answers": report["core_answers"],  # type: ignore[index]
                "findings": report["findings"],  # type: ignore[index]
                "efficiency_actions": report["efficiency_actions"],  # type: ignore[index]
                "user_efficiency_analysis": user_efficiency,
            },
            ensure_ascii=False,
        )
        self.assertNotIn("主动向用户展示", report_text)
        self.assertNotIn("邀请用户确认", report_text)
        self.assertIn("交付前完成真实场景验证", json.dumps(report["effect_observation"], ensure_ascii=False))  # type: ignore[index]
        self.assertIn("任务列表", json.dumps(report["effect_observation"], ensure_ascii=False))  # type: ignore[index]
        self.assertEqual(report["evidence_audit"]["status"], "pass")  # type: ignore[index]

    def test_session_report_keeps_refactor_start_guidance_user_facing(self) -> None:
        session = _session("refactor-start-checklist")
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="按最新架构重构整个 dashboard。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="user",
                kind="message",
                text="你先把范围、验收和任务列表说清楚。",
                created_at="2026-05-29T01:01:00+00:00",
            ),
        ]
        analysis = {
            "overall_assessment": "重构类需求前置检查项缺失。",
            "main_findings": [],
            "chat_findings": [
                {
                    "title": "重构类需求前置检查项缺失",
                    "problem": "用户发起重构需求时没有标准的前置信息要求。",
                    "cause": "没有约定重构类需求的必填输入项。",
                    "impact": "每次重构任务都存在需求模糊风险。",
                    "recommendation": "制作重构类需求提报checklist，用户发起需求时按清单填写完整信息。",
                    "severity": "high",
                    "confidence": 0.9,
                    "evidence_refs": ["event_0", "event_1"],
                    "artifact_type": "checklist",
                    "artifact_title": "重构类需求提报检查清单",
                    "artifact_target_path": "recodex/docs/refactor_requirement_checklist.md",
                }
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [
                {
                    "title": "重构类需求提报检查清单",
                    "artifact_type": "checklist",
                    "priority": "high",
                    "effort": "low",
                    "why": "规范重构类需求输入。",
                    "evidence_refs": ["event_0"],
                }
            ],
            "chat_transcript_analysis": {
                "summary": "用户要求先固定重构范围和验收方式。",
                "key_observations": ["用户明确要求范围和验收先说清。"],
                "friction_points": ["重构开工输入需要清单化。"],
                "evidence_refs": ["event_0", "event_1"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        self.assertEqual(report["findings"][0]["title"], "重构任务缺少开工前清单")  # type: ignore[index]
        self.assertEqual(report["efficiency_actions"][0]["title"], "建立重构任务开工清单")  # type: ignore[index]
        self.assertIn("范围、非目标、交付物", report["efficiency_actions"][0]["next_action"])  # type: ignore[index]
        self.assertNotIn("失败命令", report["efficiency_actions"][0]["next_action"])  # type: ignore[index]
        self.assertIn("聊天与提效分析显示", report["summary"]["headline"])  # type: ignore[index]
        self.assertEqual(report["llm_retro"]["improvement_candidates"][0]["title"], "重构任务开工清单")  # type: ignore[index]

    def test_session_report_promotes_late_chat_finding_refs_into_sample(self) -> None:
        session = _session("late-chat-ref")
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=index,
                role="user",
                kind="message",
                text=(
                    "这是普通聊天消息"
                    if index != 18
                    else "late evidence: 真实场景验证仍然没有完成。"
                ),
                created_at=f"2026-05-29T01:{index:02d}:00+00:00",
            )
            for index in range(20)
        ]
        analysis = {
            "overall_assessment": "聊天后段包含关键证据。",
            "main_findings": [],
            "chat_findings": [
                {
                    "title": "真实场景验证证据在后段",
                    "problem": "关键验收风险出现在较晚聊天消息中。",
                    "recommendation": "把后段证据纳入报告审计。",
                    "severity": "high",
                    "confidence": 0.9,
                    "evidence_refs": ["event_18"],
                    "artifact_type": "checklist",
                    "artifact_title": "真实场景验证清单",
                }
            ],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "chat_transcript_analysis": {
                "summary": "后段聊天包含验收风险。",
                "key_observations": [],
                "friction_points": [],
                "evidence_refs": ["event_0"],
            },
        }

        report = build_session_report_data(session, events, analysis, deep=True)

        chat_analysis = report["chat_transcript_analysis"]  # type: ignore[index]
        self.assertIn("event_18", chat_analysis["evidence_refs"])  # type: ignore[index]
        self.assertIn(
            "late evidence",
            json.dumps(
                chat_analysis["transcript_sample"],  # type: ignore[index]
                ensure_ascii=False,
            ),
        )
        artifact_audit = next(
            item
            for item in report["evidence_audit"]["audited_objects"]  # type: ignore[index]
            if item["target"] == "report.report_focus.recommended_artifacts[0]"
        )
        self.assertEqual(artifact_audit["status"], "supported")
        self.assertIn(
            "late evidence",
            json.dumps(artifact_audit["evidence_quotes"], ensure_ascii=False),
        )

    def test_session_report_surfaces_efficiency_actions_not_only_findings(self) -> None:
        session = _session()
        events = _diagnostic_events(session.session_id)

        report = build_session_report_data(session, events)

        actions = report["efficiency_actions"]  # type: ignore[index]
        self.assertTrue(actions)
        first = actions[0]
        for key in (
            "title",
            "trigger",
            "next_action",
            "expected_efficiency_gain",
            "suggested_target",
            "evidence_summary",
            "source_finding",
        ):
            self.assertIn(key, first)
            self.assertTrue(first[key])
        self.assertNotEqual(first["title"], first["source_finding"])
        self.assertIn("下次", first["next_action"])

        html = render_report_html(report)

        self.assertIn("聊天与提效分析", html)
        self.assertIn("下次", html)

    def test_session_report_replays_user_message_efficiency_diagnosis_process(self) -> None:
        session = _session()
        events = _efficiency_diagnosis_events(session.session_id)

        report = build_session_report_data(session, events)

        diagnosis = report["efficiency_diagnosis"]  # type: ignore[index]
        process = diagnosis["process"]  # type: ignore[index]
        signals = diagnosis["signal_summary"]  # type: ignore[index]
        problems = diagnosis["efficiency_problems"]  # type: ignore[index]
        actions = report["efficiency_actions"]  # type: ignore[index]

        self.assertEqual(diagnosis["method"]["scope"], "pure_user_messages")  # type: ignore[index]
        self.assertGreaterEqual(diagnosis["message_count"], 10)  # type: ignore[index]
        self.assertEqual(
            [step["step"] for step in process],
            [
                "extract_user_messages",
                "classify_efficiency_signals",
                "rank_efficiency_problems",
                "select_representative_evidence",
                "route_to_reusable_actions",
            ],
        )
        signal_by_id = {item["id"]: item for item in signals}
        self.assertGreaterEqual(signal_by_id["completeness_tracking"]["count"], 3)
        self.assertGreaterEqual(signal_by_id["real_environment_validation"]["count"], 3)
        self.assertGreaterEqual(signal_by_id["contract_coupling"]["count"], 3)
        self.assertIn("需求完整度", problems[0]["title"])
        self.assertIn("实现矩阵", problems[0]["recommended_action"])
        self.assertIn("建立需求完成度账本", actions[0]["title"])
        self.assertIn("下次", actions[0]["next_action"])
        self.assertIn("不要最小实现，要完整实现", json.dumps(actions, ensure_ascii=False))

        html = render_report_html(report)

        self.assertIn("效率诊断过程", html)
        self.assertIn("提取纯用户消息", html)
        self.assertIn("需求完整度没有被持续追踪", html)
        self.assertIn("真实环境验证", html)

    def test_session_report_includes_token_usage_from_llm_analysis(self) -> None:
        session = _session()
        events = _events(session.session_id)
        analysis = {
            "overall_assessment": "已生成结构化复盘。",
            "main_findings": [],
            "what_went_well": [],
            "next_time_suggestions": [],
            "improvement_candidates": [],
            "_recodex_token_usage": {
                "calls": [
                    {
                        "task_type": "session_retro",
                        "provider": "volcengine",
                        "model": "doubao",
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "total_tokens": 100,
                        "current_run_total_tokens": 100,
                        "source": "provider",
                    }
                ],
                "totals": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "total_tokens": 100,
                    "current_run_total_tokens": 100,
                    "cached_calls": 0,
                    "estimated_calls": 0,
                    "provider_reported_calls": 1,
                },
            },
        }

        report = build_session_report_data(session, events, analysis)

        self.assertEqual(report["token_usage"]["totals"]["total_tokens"], 100)  # type: ignore[index]
        self.assertEqual(report["token_usage"]["calls"][0]["provider"], "volcengine")  # type: ignore[index]

        html = render_report_html(report)

        self.assertIn("Token 消耗", html)
        self.assertIn("本次新增", html)
        self.assertIn("session_retro / volcengine / doubao", html)

    def test_report_display_dedupes_v2_titles(self) -> None:
        analysis = {
            "findings": [
                {"title": "重复问题", "confidence": 0.9, "evidence_refs": ["ev_1"]},
                {"title": "重复问题", "confidence": 0.6, "evidence_refs": ["ev_2"]},
                {"title": "另一个问题", "confidence": 0.7, "evidence_refs": ["ev_3"]},
            ],
            "artifact_candidates": [
                {"title": "重复建议", "confidence": 0.8},
                {"title": "重复建议", "confidence": 0.4},
                {"title": "另一个建议", "confidence": 0.6},
            ],
        }

        self.assertEqual(
            [issue["title"] for issue in _efficiency_issues(analysis)],
            ["重复问题", "另一个问题"],
        )
        self.assertEqual(
            [suggestion["title"] for suggestion in _efficiency_suggestions(analysis)],
            ["重复建议", "另一个建议"],
        )

    def test_core_normalization_removes_source_finding_refs_outside_reported_top_findings(self) -> None:
        core = {
            "cost_ledger": {},
            "coverage": {},
            "findings": [
                {"id": f"finding_{index}", "title": f"问题 {index}", "evidence_refs": [f"ev_{index}"]}
                for index in range(5)
            ],
            "improvement_opportunities": [
                {
                    "id": "opp_1",
                    "title": "机会",
                    "source_finding_ids": [f"finding_{index}" for index in range(5)],
                    "evidence_refs": ["ev_1"],
                }
            ],
            "artifact_candidates": [],
        }

        normalized = _normalize_core_diagnostics(core)
        finding_ids = {item["id"] for item in normalized["findings"]}

        self.assertEqual(finding_ids, {"finding_0", "finding_1", "finding_2"})
        self.assertEqual(
            set(normalized["improvement_opportunities"][0]["source_finding_ids"]),
            finding_ids,
        )

    def test_efficiency_action_without_source_finding_uses_opportunity_cause(self) -> None:
        actions = _efficiency_actions(
            {
                "cost_ledger": {"repeated_file_reads": 4},
                "findings": [
                    {
                        "id": "finding_1",
                        "title": "不相关 finding",
                        "evidence_refs": ["ev_a"],
                    }
                ],
                "improvement_opportunities": [
                    {
                        "id": "opp_agents",
                        "title": "前置稳定项目知识",
                        "cause": "稳定项目知识没有进入默认可检索上下文。",
                        "best_action": "把稳定项目事实写入 AGENTS.md。",
                        "recommended_mechanism": "agents_md",
                        "suggested_target": "AGENTS.md",
                        "source_finding_ids": [],
                        "evidence_refs": ["ev_b", "ev_c"],
                    }
                ],
            },
            [],
        )

        self.assertEqual(actions[0]["source_finding"], "稳定项目知识没有进入默认可检索上下文。")
        self.assertEqual(actions[0]["suggested_target"], "AGENTS.md")
        self.assertEqual(actions[0]["evidence_refs"], ["ev_b", "ev_c"])

    def test_write_report_bundle_writes_json_and_single_file_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            report = build_session_report_data(_session(), _events("html-session"))

            json_path, html_path = write_report_bundle(directory, "report", report)

            self.assertEqual(json_path, directory / "report.json")
            self.assertEqual(html_path, directory / "report.html")
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["meta"]["project"], "/work/aicoo")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("report-data", html)
            self.assertIn("recodex", html)

    def test_project_report_includes_aggregate_core_diagnostics_with_v2_summary(self) -> None:
        sessions = [_session("project-s1"), _session("project-s2")]
        events_by_session = {
            session.session_id: _diagnostic_events(session.session_id)
            for session in sessions
        }

        report = build_project_report_data(
            "/work/aicoo",
            sessions,
            events_by_session,
            [],
            "30d",
        )
        core = report["core_diagnostics"]  # type: ignore[index]
        opportunities = report["improvement_opportunities"]  # type: ignore[index]

        self.assertGreaterEqual(core["cost_ledger"]["user_corrections"], 2)  # type: ignore[index]
        self.assertTrue(core["findings"])  # type: ignore[index]
        self.assertTrue(opportunities)
        self.assertEqual(report["summary"]["primary_improvement"], opportunities[0]["title"])  # type: ignore[index]


def _session(session_id: str = "html-session") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/rollout-{session_id}.jsonl",
        started_at="2026-05-29T01:00:00+00:00",
        updated_at="2026-05-29T01:12:00+00:00",
        title="修复 aicoo 登录问题",
        tool="codex",
        message_count=4,
        user_message_count=1,
        assistant_message_count=2,
        command_count=0,
        error_count=0,
        raw_preview="修复 aicoo 登录问题",
        project_path="/work/aicoo",
    )


def _events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id=session_id,
            event_index=0,
            role="user",
            kind="message",
            text="帮我修复 token refresh。password=secret-value",
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=1,
            role="assistant",
            kind="message",
            text="我修改了认证逻辑，但最终没有运行测试 </script><script>alert(1)</script>",
            created_at="2026-05-29T01:10:00+00:00",
        ),
    ]


def _diagnostic_events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id=session_id,
            event_index=0,
            role="user",
            kind="message",
            text="帮我修 CI failure。",
            created_at="2026-05-29T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=1,
            role="assistant",
            kind="message",
            text="我已经修好了。",
            created_at="2026-05-29T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=2,
            role="user",
            kind="message",
            text="你还没看 CI 日志，也没跑失败的 test。先看日志，定位具体失败命令。",
            created_at="2026-05-29T01:02:00+00:00",
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=3,
            role="tool",
            kind="exec_command",
            text="command=npm test\nProcess exited with code 0",
            created_at="2026-05-29T01:03:00+00:00",
            metadata={"command": "npm test", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id=session_id,
            event_index=4,
            role="user",
            kind="message",
            text="不是 npm test，CI 失败的是 pnpm test:payment。",
            created_at="2026-05-29T01:04:00+00:00",
        ),
    ]


def _efficiency_diagnosis_events(session_id: str) -> list[TranscriptEvent]:
    texts = [
        "不要最小实现，要完整实现",
        "还有哪些没完成的，占位、临时、示例都列出来",
        "这条任务线构建了吗",
        "启动前后端，我要在局域网用手机看",
        "我部署到正式也是这个问题，是为啥",
        "Safari 拍照节点没办法唤起",
        "看下 agent ws 和 device ws 有啥区别",
        "这个改动会不会影响设备通道",
        "别影响 device ws 链路",
        "你不应该只针对天气这一点",
        "场景问题要规范化，普通 qa 和路线规划都要看",
        "整理一下 git，做几个 commit",
    ]
    return [
        TranscriptEvent(
            session_id=session_id,
            event_index=index,
            role="user",
            kind="message",
            text=text,
            created_at=f"2026-05-29T01:{index:02d}:00+00:00",
        )
        for index, text in enumerate(texts)
    ]


if __name__ == "__main__":
    unittest.main()
