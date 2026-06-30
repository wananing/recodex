from __future__ import annotations

import json
import unittest

from recodex.analysis_workflow import (
    MAX_LLM_EXTRACT_UNITS,
    WorkflowStageOutput,
    build_evidence_packs,
    extract_schema,
    normalize_trace,
    run_analysis_workflow,
    segment_episodes,
    workflow_result_to_report_data,
)
from recodex.models import SessionRecord, TranscriptEvent


class LLMWorkflowTests(unittest.TestCase):
    def test_normalize_trace_extracts_turn_event_command_file_and_prepass_facts(self) -> None:
        session = _session()
        trace = normalize_trace(session, _events())

        events = trace["events"]
        facts = trace["deterministic_facts"]

        self.assertGreaterEqual(len(trace["turns"]), 2)
        self.assertTrue(all("source_ref" in event for event in events))  # type: ignore[union-attr]
        self.assertTrue(any(event["phase"] == "failure_retry" for event in events))  # type: ignore[index,union-attr]
        self.assertTrue(any(event["phase"] == "user_correction" for event in events))  # type: ignore[index,union-attr]
        self.assertEqual(facts["command_failure_count"], 1)  # type: ignore[index]
        self.assertEqual(facts["test_run_count"], 1)  # type: ignore[index]
        self.assertGreaterEqual(facts["user_correction_count"], 1)  # type: ignore[index]
        self.assertIn("src/login.py", facts["files_touched"])  # type: ignore[index,operator]

    def test_episode_segmentation_uses_user_goal_turns_not_phase_chunks(self) -> None:
        session = _session()
        trace = normalize_trace(session, _events())
        normalized_events = [
            _event_from_trace(event)
            for event in trace["events"]  # type: ignore[index]
        ]

        episodes = segment_episodes(session, normalized_events)
        phases = [episode.phase for episode in episodes]
        episode_event_refs = [
            {event_id for event_id in episode.event_ids}
            for episode in episodes
        ]
        command_event_ids = {
            event["id"]
            for event in trace["events"]  # type: ignore[index]
            if event.get("command")
        }

        self.assertIn("user_request", phases)
        self.assertIn("user_correction", phases)
        self.assertLessEqual(len(episodes), 3)
        self.assertTrue(any(command_event_ids.intersection(event_ids) for event_ids in episode_event_refs))

    def test_evidence_pack_contains_source_refs_excerpts_commands_and_files(self) -> None:
        session = _session()
        trace = normalize_trace(session, _events())
        normalized_events = [_event_from_trace(event) for event in trace["events"]]  # type: ignore[index]
        episodes = segment_episodes(session, normalized_events)
        packs = build_evidence_packs(session, episodes, normalized_events)

        command_packs = [pack for pack in packs if pack.commands]

        self.assertTrue(command_packs)
        self.assertTrue(command_packs[0].source_refs)
        self.assertTrue(command_packs[0].raw_excerpts)
        self.assertIn("src/login.py", command_packs[0].file_refs)

    def test_extract_schema_bounds_llm_output_size(self) -> None:
        schema = extract_schema()
        issue_schema = schema["properties"]["issues"]["items"]

        self.assertEqual(schema["properties"]["issues"]["maxItems"], 3)
        self.assertEqual(schema["properties"]["observations"]["maxItems"], 3)
        self.assertEqual(issue_schema["properties"]["evidence_refs"]["maxItems"], 4)
        self.assertLessEqual(issue_schema["properties"]["user_impact"]["maxLength"], 240)
        self.assertLessEqual(issue_schema["properties"]["recommended_change"]["maxLength"], 320)

    def test_workflow_payload_prioritizes_user_inputs_over_context(self) -> None:
        session = _session(session_id="user-focus-session", message_count=5, command_count=1, error_count=0)
        seen_extract_payloads: list[dict[str, object]] = []

        def runner(stage):
            if stage.stage == "extract":
                payload = stage.payload
                seen_extract_payloads.append(payload)  # type: ignore[arg-type]
                self.assertEqual(set(payload["session"].keys()), {"session_id"})  # type: ignore[index]
                self.assertNotIn("evidence_pack", payload)
                self.assertNotIn("episode", payload)
                self.assertEqual(payload["analysis_focus"]["primary"], "qualitative_analysis.segments")  # type: ignore[index]
                qualitative = payload["qualitative_analysis"]  # type: ignore[index]
                self.assertEqual(qualitative["method"], "codebook_qualitative_coding_v1")  # type: ignore[index]
                segments = payload["qualitative_segments"]  # type: ignore[index]
                refs = [segment["source_ref"] for segment in segments]  # type: ignore[index]
                self.assertTrue(all(str(ref).startswith("codex:user-focus-session:") for ref in refs))
                serialized = str(payload)
                self.assertNotIn("Read this large repo guide", serialized)
                self.assertNotIn("process exited with code 0", serialized)
                self.assertTrue(all(segment["role"] == "user" for segment in segments))  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": payload["analysis_unit"]["id"],  # type: ignore[index]
                        "issues": [
                            {
                                "id": f"{payload['analysis_unit']['id']}_issue",  # type: ignore[index]
                                "issue_type": "user_intent_gap",
                                "severity": "medium",
                                "evidence_refs": list(refs)[:1],
                                "user_impact": "用户真实诉求容易被上下文噪音覆盖。",
                                "root_cause_hypothesis": "报告没有把纯用户输入作为主分析对象。",
                                "recommended_change": "先基于 user_inputs 建立用户诉求时间线。",
                                "confidence": 0.83,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                self.assertEqual(set(stage.payload["session"].keys()), {"session_id"})  # type: ignore[index]
                self.assertGreaterEqual(stage.max_output_tokens, 7000)
                self.assertIn("qualitative_analysis", stage.payload)
                self.assertNotIn("evidence_packs", stage.payload)
                issues = stage.payload["issues"]  # type: ignore[index]
                refs = [ref for issue in issues for ref in issue["evidence_refs"]]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "clusters": [
                            {
                                "id": "cluster_user_focus",
                                "title": "用户输入优先",
                                "pattern": "报告应先围绕用户输入组织。",
                                "pattern_type": "user_intent",
                                "severity": "medium",
                                "confidence": 0.8,
                                "issue_ids": [issue["id"] for issue in issues],  # type: ignore[index]
                                "evidence_refs": refs,
                                "impact": "LLM 会分析 AGENTS 或 IDE context 而不是用户请求。",
                                "recommended_change": "把 user_inputs 放到 LLM payload 顶层。",
                                "skill_candidate_allowed": False,
                                "skill_gate_reason": "single-session workflow recommendation",
                            }
                        ],
                        "discarded_issue_ids": [],
                    }
                )
            if stage.stage == "validate":
                self.assertEqual(set(stage.payload["session"].keys()), {"session_id"})  # type: ignore[index]
                clusters = stage.payload["clusters"]  # type: ignore[index]
                issues = stage.payload["issues"]  # type: ignore[index]
                for source_ref in stage.payload["source_refs"]:  # type: ignore[index]
                    self.assertIn("text", source_ref)
                    self.assertIn("segment_id", source_ref)
                    self.assertIn("code_ids", source_ref)
                    self.assertNotIn("command", source_ref)
                    self.assertNotIn("file_refs", source_ref)
                return WorkflowStageOutput(
                    {
                        "validated_issues": [
                            {
                                "id": issue["id"],
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "has user input evidence",
                                "evidence_refs": issue["evidence_refs"],
                            }
                            for issue in issues
                        ],
                        "validated_clusters": [
                            {
                                "id": clusters[0]["id"],  # type: ignore[index]
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "cluster cites user input refs",
                                "evidence_refs": clusters[0]["evidence_refs"],  # type: ignore[index]
                            }
                        ],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                self.assertEqual(set(stage.payload["session"].keys()), {"session_id"})  # type: ignore[index]
                user_intent = stage.payload["user_intent"]  # type: ignore[index]
                self.assertEqual(user_intent["primary_request"], "修复登录失败，重点看用户输入。")  # type: ignore[index]
                self.assertIn("qualitative_analysis", stage.payload)
                self.assertIn("qualitative_segments", stage.payload)
                self.assertNotIn("evidence_pack_summaries", stage.payload)
                return WorkflowStageOutput(
                    {
                        "headline": "user focused workflow",
                        "overall": "report is centered on user inputs",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _events_with_context_noise(), stage_runner=runner)

        self.assertEqual(workflow["user_intent"]["primary_request"], "修复登录失败，重点看用户输入。")
        self.assertEqual(workflow["deterministic_facts"]["user_input_count"], 2)
        self.assertIn("qualitative_analysis", workflow)
        self.assertEqual(workflow["qualitative_analysis"]["method"], "codebook_qualitative_coding_v1")
        self.assertGreaterEqual(workflow["llm_coverage"]["skipped_context_packs"], 1)
        self.assertTrue(seen_extract_payloads)
        for payload in seen_extract_payloads:
            focus = payload["analysis_focus"]  # type: ignore[index]
            self.assertEqual(focus["primary"], "qualitative_analysis.segments")  # type: ignore[index]
            self.assertEqual(focus["supporting"], ["qualitative_theme", "codebook", "audit_trail"])  # type: ignore[index]
            self.assertTrue(payload["qualitative_segments"])  # type: ignore[index]

    def test_workflow_runs_extract_cluster_validate_report_with_structured_outputs(self) -> None:
        session = _session()
        seen_stages: list[str] = []

        def runner(stage):
            seen_stages.append(stage.stage)
            if stage.stage == "extract":
                refs = [segment["source_ref"] for segment in stage.payload["qualitative_segments"]]  # type: ignore[index]
                unit_id = stage.payload["analysis_unit"]["id"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": unit_id,
                        "issues": [
                            {
                                "id": f"{unit_id}_issue_1",
                                "issue_type": "skipped_verification",
                                "severity": "high",
                                "evidence_refs": list(refs)[:1],
                                "user_impact": "用户无法确认是否完成。",
                                "root_cause_hypothesis": "没有把完成状态绑定到验证证据。",
                                "recommended_change": "把验证命令和结果放入报告门禁。",
                                "confidence": 0.88,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                issues = stage.payload["issues"]  # type: ignore[index]
                refs = [ref for issue in issues for ref in issue["evidence_refs"]]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "clusters": [
                            {
                                "id": "cluster_1",
                                "title": "验证闭环缺失",
                                "pattern": "任务完成判断缺少验证门禁。",
                                "pattern_type": "verification_gap",
                                "severity": "high",
                                "confidence": 0.82,
                                "issue_ids": [issue["id"] for issue in issues],  # type: ignore[index]
                                "evidence_refs": refs[:3],
                                "impact": "返工风险上升。",
                                "recommended_change": "报告合成前先跑 validator。",
                                "skill_candidate_allowed": len(issues) >= 2,
                                "skill_gate_reason": "needs repeated evidence and human confirmation",
                            }
                        ],
                        "discarded_issue_ids": [],
                    }
                )
            if stage.stage == "validate":
                clusters = stage.payload["clusters"]  # type: ignore[index]
                issues = stage.payload["issues"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "validated_issues": [
                            {
                                "id": issue["id"],
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "has source_ref",
                                "evidence_refs": issue["evidence_refs"],
                            }
                            for issue in issues
                        ],
                        "validated_clusters": [
                            {
                                "id": cluster["id"],
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "cluster is issue-backed",
                                "evidence_refs": cluster["evidence_refs"],
                            }
                            for cluster in clusters
                        ],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "workflow done",
                        "overall": "validated clusters only",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": stage.payload["skill_candidates"],  # type: ignore[index]
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _events(), stage_runner=runner)

        self.assertEqual(seen_stages[-3:], ["cluster", "validate", "report"])
        self.assertIn("extract", seen_stages)
        self.assertTrue(workflow["validated_clusters"])
        self.assertEqual(workflow["report"]["clusters"][0]["id"], "cluster_1")
        self.assertIn("deterministic_facts", workflow)
        report = workflow_result_to_report_data(
            session,
            workflow,
            report_id="rep_test_user_focus",
            generated_at="2026-06-13T02:00:00+00:00",
        )
        self.assertEqual(report["user_intent"]["primary_request"], "修复 src/login.py 的登录失败。")
        self.assertEqual(report["metrics"]["user_inputs"], 2)
        self.assertGreaterEqual(report["metrics"]["qualitative_segments"], 2)
        self.assertEqual([item["user_input_text"] for item in report["evidence"]], ["修复 src/login.py 的登录失败。", "不对，我的意思是不要改认证外的逻辑。"])
        self.assertTrue(all(item["role"] == "user" for item in report["evidence"]))
        self.assertTrue(all("pytest" not in item["content"] for item in report["evidence"]))
        self.assertIn("user_intent", report["workflow"])
        self.assertIn("qualitative_analysis", report["workflow"])

    def test_workflow_falls_back_to_deterministic_report_when_report_llm_fails(self) -> None:
        session = _session()

        def runner(stage):
            if stage.stage == "extract":
                refs = [segment["source_ref"] for segment in stage.payload["qualitative_segments"]]  # type: ignore[index]
                unit_id = stage.payload["analysis_unit"]["id"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": unit_id,
                        "issues": [
                            {
                                "id": f"{unit_id}_issue",
                                "issue_type": "verification_gap",
                                "severity": "medium",
                                "evidence_refs": list(refs)[:1],
                                "user_impact": "用户无法确认是否完成。",
                                "root_cause_hypothesis": "缺少报告阶段兜底。",
                                "recommended_change": "report LLM 失败时使用已验证模式合成报告。",
                                "confidence": 0.8,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                issues = stage.payload["issues"]  # type: ignore[index]
                refs = [ref for issue in issues for ref in issue["evidence_refs"]]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "clusters": [
                            {
                                "id": "cluster_fallback",
                                "title": "报告阶段需要兜底",
                                "pattern": "report LLM timeout should not fail validated analysis.",
                                "pattern_type": "report_resilience",
                                "severity": "medium",
                                "confidence": 0.8,
                                "issue_ids": [issue["id"] for issue in issues],  # type: ignore[index]
                                "evidence_refs": refs[:2],
                                "impact": "用户拿不到已完成的分析结果。",
                                "recommended_change": "使用 deterministic report fallback。",
                                "skill_candidate_allowed": False,
                                "skill_gate_reason": "fallback behavior",
                            }
                        ],
                        "discarded_issue_ids": [],
                    }
                )
            if stage.stage == "validate":
                cluster = stage.payload["clusters"][0]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [
                            {
                                "id": cluster["id"],
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "supported before report stage",
                                "evidence_refs": cluster["evidence_refs"],
                            }
                        ],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                raise RuntimeError("The read operation timed out")
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _events(), stage_runner=runner)

        self.assertEqual(workflow["report"]["verification"]["status"], "fallback")
        self.assertEqual(workflow["report"]["clusters"][0]["id"], "cluster_fallback")
        self.assertEqual(workflow["stages"][-1]["stage"], "report")
        self.assertEqual(workflow["stages"][-1]["status"], "fallback")
        self.assertIn("report_fallback", workflow["stages"][-1]["warnings"])

    def test_extract_llm_json_failure_does_not_abort_workflow(self) -> None:
        session = _session()
        seen_stages: list[str] = []

        def runner(stage):
            seen_stages.append(stage.stage)
            if stage.stage == "extract":
                raise RuntimeError("Volcengine Ark response did not contain valid JSON output.")
            if stage.stage in {"cluster", "validate"}:
                raise AssertionError(f"{stage.stage} should be skipped when extraction produced no issues")
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "LLM 分阶段分析已完成",
                        "overall": "本次分析先把用户输入转成定性编码单元。",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _events(), stage_runner=runner)
        stage_status = [(stage["stage"], stage["status"]) for stage in workflow["stages"]]

        self.assertIn(("extract", "fallback"), stage_status)
        self.assertIn(("cluster", "skipped"), stage_status)
        self.assertIn(("validate", "skipped"), stage_status)
        self.assertIn("report", seen_stages)
        self.assertEqual(workflow["issues"], [])
        self.assertTrue(workflow["validated_clusters"])
        self.assertTrue(any("extract_fallback" in stage["warnings"] for stage in workflow["stages"]))

    def test_cluster_llm_json_failure_does_not_abort_workflow(self) -> None:
        session = _session()

        def runner(stage):
            if stage.stage == "extract":
                refs = [segment["source_ref"] for segment in stage.payload["qualitative_segments"]]  # type: ignore[index]
                unit_id = stage.payload["analysis_unit"]["id"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": unit_id,
                        "issues": [
                            {
                                "id": f"{unit_id}_issue",
                                "issue_type": "verification_gap",
                                "severity": "medium",
                                "evidence_refs": list(refs)[:1],
                                "user_impact": "用户无法确认结果。",
                                "root_cause_hypothesis": "缺少验证闭环。",
                                "recommended_change": "补充验证状态。",
                                "confidence": 0.8,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                raise RuntimeError("Volcengine Ark response did not contain valid JSON output.")
            if stage.stage == "validate":
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "LLM 分阶段分析已完成",
                        "overall": "本次分析先把用户输入转成定性编码单元。",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _events(), stage_runner=runner)
        stage_status = [(stage["stage"], stage["status"]) for stage in workflow["stages"]]

        self.assertIn(("cluster", "fallback"), stage_status)
        self.assertIn(("validate", "skipped"), stage_status)
        self.assertTrue(workflow["validated_clusters"])
        self.assertTrue(any("cluster_fallback" in stage["warnings"] for stage in workflow["stages"]))

    def test_workflow_limits_llm_extract_calls_for_large_episode_sets(self) -> None:
        session = _session(session_id="large-wf-session", message_count=80, command_count=40, error_count=8)
        seen_stages: list[str] = []

        def runner(stage):
            seen_stages.append(stage.stage)
            if stage.stage == "extract":
                refs = [segment["source_ref"] for segment in stage.payload["qualitative_segments"]]  # type: ignore[index]
                unit_id = stage.payload["analysis_unit"]["id"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": unit_id,
                        "issues": [
                            {
                                "id": f"{unit_id}_issue",
                                "issue_type": "workflow_signal",
                                "severity": "medium",
                                "evidence_refs": list(refs)[:1],
                                "user_impact": "long workflows can timeout.",
                                "root_cause_hypothesis": "too many episode-level LLM calls.",
                                "recommended_change": "budget extract calls by evidence signal.",
                                "confidence": 0.8,
                                "missing_evidence": [],
                            }
                        ],
                    }
                )
            if stage.stage == "cluster":
                issues = stage.payload["issues"]  # type: ignore[index]
                self.assertEqual(stage.payload["analysis_coverage"]["llm_extract_units"], MAX_LLM_EXTRACT_UNITS)  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "clusters": [
                            {
                                "id": "cluster_budget",
                                "title": "extract 调用需要预算",
                                "pattern": "long sessions should not call LLM once per episode.",
                                "pattern_type": "workflow_performance",
                                "severity": "medium",
                                "confidence": 0.8,
                                "issue_ids": [issue["id"] for issue in issues],  # type: ignore[index]
                                "evidence_refs": [ref for issue in issues for ref in issue["evidence_refs"]][:3],  # type: ignore[index]
                                "impact": "frontend waits too long.",
                                "recommended_change": "select high-signal packs.",
                                "skill_candidate_allowed": True,
                                "skill_gate_reason": "supported by many packs",
                            }
                        ],
                        "discarded_issue_ids": [],
                    }
                )
            if stage.stage == "validate":
                clusters = stage.payload["clusters"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [
                            {
                                "id": clusters[0]["id"],  # type: ignore[index]
                                "status": "supported",
                                "confidence": 0.8,
                                "reason": "budgeted workflow still cites refs",
                                "evidence_refs": clusters[0]["evidence_refs"],  # type: ignore[index]
                            }
                        ],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                coverage = stage.payload["analysis_coverage"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "headline": "budgeted workflow done",
                        "overall": f"selected {coverage['llm_extract_units']} of {coverage['total_extract_units']} qualitative units",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _many_events(520), stage_runner=runner)

        self.assertEqual(seen_stages.count("extract"), MAX_LLM_EXTRACT_UNITS)
        self.assertGreater(workflow["llm_coverage"]["total_extract_units"], MAX_LLM_EXTRACT_UNITS)
        self.assertEqual(workflow["llm_coverage"]["llm_extract_units"], MAX_LLM_EXTRACT_UNITS)
        self.assertEqual(workflow["llm_coverage"]["llm_extract_packs"], MAX_LLM_EXTRACT_UNITS)
        self.assertGreater(workflow["llm_coverage"]["skipped_extract_units"], 0)

    def test_workflow_outputs_auditable_evidence_layers_not_business_product_clusters(self) -> None:
        session = _business_report_session()

        def runner(stage):
            if stage.stage == "extract":
                refs = [segment["source_ref"] for segment in stage.payload["qualitative_segments"]]  # type: ignore[index]
                unit_id = stage.payload["analysis_unit"]["id"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": unit_id,
                        "issues": [
                            {
                                "id": f"{unit_id}_product_issue",
                                "issue_type": "product_functionality_gap",
                                "severity": "medium",
                                "evidence_refs": list(refs)[:2],
                                "user_impact": "Users need flexible business report scheduling.",
                                "root_cause_hypothesis": "Business reporting features are hardcoded.",
                                "recommended_change": "Add custom switches for scheduled business reports.",
                                "confidence": 0.9,
                                "missing_evidence": [],
                            }
                        ],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                issues = stage.payload["issues"]  # type: ignore[index]
                refs = [ref for issue in issues for ref in issue["evidence_refs"]]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "clusters": [
                            {
                                "id": "cluster_product_reporting",
                                "title": "Reporting system flexibility and usability deficiency",
                                "pattern": "The business reporting system needs flexible scheduling.",
                                "pattern_type": "product_functionality_gap",
                                "severity": "medium",
                                "confidence": 0.9,
                                "issue_ids": [issue["id"] for issue in issues],  # type: ignore[index]
                                "evidence_refs": refs,
                                "impact": "Business users cannot control report triggering.",
                                "recommended_change": "Add switches for order and gift report schedules.",
                                "skill_candidate_allowed": True,
                                "skill_gate_reason": "LLM incorrectly treated business requirements as workflow improvement.",
                            }
                        ],
                        "discarded_issue_ids": [],
                    }
                )
            if stage.stage == "validate":
                clusters = stage.payload["clusters"]  # type: ignore[index]
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [
                            {
                                "id": cluster["id"],
                                "status": "supported",
                                "confidence": 0.9,
                                "reason": "LLM says supported",
                                "evidence_refs": cluster["evidence_refs"],
                            }
                            for cluster in clusters
                        ],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "bad product report",
                        "overall": "bad product report",
                        "clusters": stage.payload["validated_clusters"],  # type: ignore[index]
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _business_report_events(), stage_runner=runner)

        self.assertIn("evidence_windows", workflow)
        self.assertIn("micro_claims", workflow)
        self.assertIn("analysis_cards", workflow)
        self.assertIn("card_verifications", workflow)
        self.assertIn("pattern_clusters", workflow)
        self.assertTrue(
            all(card.get("evidence_claim_ids") and card.get("evidence_event_ids") for card in workflow["analysis_cards"])
        )
        card_types = [card.get("card_type") for card in workflow["analysis_cards"]]
        self.assertNotIn("wrong_command", card_types)
        self.assertNotIn("validation_gap", card_types)
        serialized_clusters = json.dumps(workflow.get("validated_clusters", []), ensure_ascii=False)
        self.assertNotIn("Reporting system flexibility", serialized_clusters)
        self.assertNotIn("product_functionality_gap", serialized_clusters)

        report = workflow_result_to_report_data(
            session,
            workflow,
            report_id="rep_business_report",
            generated_at="2026-06-16T01:00:00+00:00",
        )
        serialized_report = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("140.143.206.118", serialized_report)
        self.assertNotIn("29898", serialized_report)
        self.assertIn("证据卡", report["summary"]["headline"])

    def test_successful_tool_outputs_and_context_do_not_create_failure_cards(self) -> None:
        session = _session(session_id="successful-tool-noise", message_count=6, command_count=2, error_count=0)

        def runner(stage):
            if stage.stage == "extract":
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": stage.payload["analysis_unit"]["id"],  # type: ignore[index]
                        "issues": [],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                return WorkflowStageOutput({"clusters": [], "discarded_issue_ids": []})
            if stage.stage == "validate":
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "LLM 分阶段分析已完成",
                        "overall": "本次分析先把用户输入转成定性编码单元，再进行 issue 提取、聚类、验证和报告合成。",
                        "clusters": [],
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _successful_tool_noise_events(), stage_runner=runner)
        card_types = [card.get("card_type") for card in workflow["analysis_cards"]]
        windows_text = json.dumps(workflow["evidence_windows"], ensure_ascii=False)
        report = workflow_result_to_report_data(
            session,
            workflow,
            report_id="rep_successful_tool_noise",
            generated_at="2026-06-18T01:00:00+00:00",
        )

        self.assertIn("user_correction", card_types)
        self.assertNotIn("wrong_command", card_types)
        self.assertNotIn("validation_gap", card_types)
        self.assertNotIn("<permissions", windows_text)
        self.assertEqual(workflow["deterministic_facts"]["command_failure_count"], 0)
        self.assertIn("证据卡", report["summary"]["headline"])

    def test_failed_chunk_output_creates_wrong_command_card_without_command_metadata(self) -> None:
        session = _session(session_id="failed-chunk-output", message_count=3, command_count=1, error_count=1)

        def runner(stage):
            if stage.stage == "extract":
                return WorkflowStageOutput(
                    {
                        "analysis_unit_id": stage.payload["analysis_unit"]["id"],  # type: ignore[index]
                        "issues": [],
                        "observations": [],
                    }
                )
            if stage.stage == "cluster":
                return WorkflowStageOutput({"clusters": [], "discarded_issue_ids": []})
            if stage.stage == "validate":
                return WorkflowStageOutput(
                    {
                        "validated_issues": [],
                        "validated_clusters": [],
                        "human_queue": [],
                        "rejected_ids": [],
                        "warnings": [],
                    }
                )
            if stage.stage == "report":
                return WorkflowStageOutput(
                    {
                        "headline": "LLM 分阶段分析已完成",
                        "overall": "本次分析先把用户输入转成定性编码单元。",
                        "clusters": [],
                        "suggestions": [],
                        "skill_drafts": [],
                        "verification": {"status": "supported"},
                        "flow": [],
                    }
                )
            raise AssertionError(stage.stage)

        workflow = run_analysis_workflow(session, _failed_chunk_events(), stage_runner=runner)
        card_types = [card.get("card_type") for card in workflow["analysis_cards"]]

        self.assertIn("wrong_command", card_types)
        self.assertTrue(any(window.get("center_signal_type") == "wrong_command" for window in workflow["evidence_windows"]))


def _session(
    *,
    session_id: str = "wf-session",
    message_count: int = 7,
    command_count: int = 2,
    error_count: int = 1,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path="/tmp/wf-session.jsonl",
        started_at="2026-06-13T01:00:00+00:00",
        updated_at="2026-06-13T01:10:00+00:00",
        title="修复登录失败",
        tool="codex",
        message_count=message_count,
        user_message_count=2,
        assistant_message_count=2,
        command_count=command_count,
        error_count=error_count,
        raw_preview="修复登录失败",
        project_path="/work/app",
    )


def _business_report_session() -> SessionRecord:
    return SessionRecord(
        session_id="business-report-session",
        source_path="/tmp/business-report-session.jsonl",
        started_at="2026-06-16T01:00:00+00:00",
        updated_at="2026-06-16T01:20:00+00:00",
        title="小时级定时请求方案",
        tool="codex",
        message_count=10,
        user_message_count=5,
        assistant_message_count=5,
        command_count=3,
        error_count=0,
        raw_preview="梳理每天凌晨发送和接收数据流程",
        project_path="/work/business-service",
    )


def _events() -> list[TranscriptEvent]:
    return [
        TranscriptEvent("wf-session", 0, "user", "message", "修复 src/login.py 的登录失败。", "2026-06-13T01:00:00+00:00"),
        TranscriptEvent("wf-session", 1, "assistant", "message", "我先查看代码并制定计划。", "2026-06-13T01:01:00+00:00"),
        TranscriptEvent(
            "wf-session",
            2,
            "tool",
            "exec_command",
            "process exited with code 0 output from src/login.py",
            "2026-06-13T01:02:00+00:00",
            {"command": "sed -n '1,120p' src/login.py"},
        ),
        TranscriptEvent(
            "wf-session",
            3,
            "tool",
            "exec_command",
            "pytest tests/test_login.py failed with AssertionError in src/login.py",
            "2026-06-13T01:03:00+00:00",
            {"command": "pytest tests/test_login.py"},
        ),
        TranscriptEvent("wf-session", 4, "assistant", "message", "已修改 src/login.py。", "2026-06-13T01:04:00+00:00"),
        TranscriptEvent("wf-session", 5, "user", "message", "不对，我的意思是不要改认证外的逻辑。", "2026-06-13T01:05:00+00:00"),
        TranscriptEvent("wf-session", 6, "assistant", "message", "最终只保留登录修复并说明验证。", "2026-06-13T01:06:00+00:00"),
    ]


def _business_report_events() -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            "business-report-session",
            0,
            "user",
            "message",
            "bitnei-service/src/main/java/com/acme/task/BatteryHealthTask.java 现在系统每天凌晨发送一次数据，然后接收一次数据，先梳理整个流程。",
            "2026-06-16T01:00:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            1,
            "assistant",
            "message",
            "我先查看定时任务和调用链。",
            "2026-06-16T01:01:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            2,
            "tool",
            "exec_command",
            "process exited with code 0 output from BatteryHealthTask.java",
            "2026-06-16T01:02:00+00:00",
            {"command": "sed -n '1,200p' bitnei-service/src/main/java/com/acme/task/BatteryHealthTask.java"},
        ),
        TranscriptEvent(
            "business-report-session",
            3,
            "user",
            "message",
            "根据日期、用户编码获取订单报表 http://140.143.206.118:29898/car-api/car/video/getOrderReportByTimeAndCom 请求报文 userCode=chediandian，这是测试接口地址。",
            "2026-06-16T01:04:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            4,
            "user",
            "message",
            "刚刚偏题了，我们继续研究按天请求改成每小时一次请求。",
            "2026-06-16T01:05:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            5,
            "assistant",
            "message",
            "我会回到小时级定时方案。",
            "2026-06-16T01:06:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            6,
            "user",
            "message",
            "这个批次超时报告可以新增一个报告，xxjbo 的定时如果我不需要就可以不触发。",
            "2026-06-16T01:07:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            7,
            "user",
            "message",
            "再次确认整个小时级方案。",
            "2026-06-16T01:08:00+00:00",
        ),
        TranscriptEvent(
            "business-report-session",
            8,
            "tool",
            "exec_command",
            "process exited with code 0",
            "2026-06-16T01:09:00+00:00",
            {
                "command": "curl -s -i -X POST 'http://140.143.206.118:29898/car-api/car/video/getOrderReportByTimeAndCom'",
            },
        ),
    ]


def _successful_tool_noise_events() -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            "successful-tool-noise",
            0,
            "developer",
            "message",
            "<permissions instructions> Filesystem sandboxing defines which files can be read or written. `sandbox_mode` is `workspace-write`.",
            "2026-06-18T01:00:00+00:00",
        ),
        TranscriptEvent(
            "successful-tool-noise",
            1,
            "user",
            "message",
            "先梳理 bitnei-service 的定时链路。",
            "2026-06-18T01:01:00+00:00",
        ),
        TranscriptEvent(
            "successful-tool-noise",
            2,
            "tool",
            "exec_command",
            "Chunk ID: abc123\nWall time: 0.0000 seconds\nProcess exited with code 0\nOutput: log.error(\"电池报告处理异常\", e);",
            "2026-06-18T01:02:00+00:00",
            {"command": "sed -n '1,120p' bitnei-service/src/main/java/com/acme/ScheduleService.java"},
        ),
        TranscriptEvent(
            "successful-tool-noise",
            3,
            "tool",
            "message",
            "Chunk ID: def456\nWall time: 0.0000 seconds\nProcess exited with code 0\nOutput: HTTP/1.1 200 OK",
            "2026-06-18T01:03:00+00:00",
        ),
        TranscriptEvent(
            "successful-tool-noise",
            4,
            "user",
            "message",
            "刚刚偏题了，回到按天请求改成每小时一次请求。",
            "2026-06-18T01:04:00+00:00",
        ),
    ]


def _failed_chunk_events() -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            "failed-chunk-output",
            0,
            "user",
            "message",
            "跑一下相关测试。",
            "2026-06-18T01:00:00+00:00",
        ),
        TranscriptEvent(
            "failed-chunk-output",
            1,
            "unknown",
            "text",
            "Chunk ID: bad123\nWall time: 0.0000 seconds\nProcess exited with code 1\nOutput: ERROR: JAVA_HOME is not set.",
            "2026-06-18T01:01:00+00:00",
        ),
    ]


def _many_events(count: int) -> list[TranscriptEvent]:
    events: list[TranscriptEvent] = []
    for index in range(count):
        if index % 2 == 0:
            events.append(
                TranscriptEvent(
                    "large-wf-session",
                    index,
                    "user",
                    "message",
                    f"Fix issue slice {index}.",
                    "2026-06-13T01:00:00+00:00",
                )
            )
        else:
            failed = index % 10 == 1
            events.append(
                TranscriptEvent(
                    "large-wf-session",
                    index,
                    "tool",
                    "exec_command",
                    f"pytest tests/test_{index}.py {'failed AssertionError' if failed else 'passed'} in src/file_{index}.py",
                    "2026-06-13T01:00:00+00:00",
                    {"command": f"pytest tests/test_{index}.py", "exit_code": 1 if failed else 0},
                )
            )
    return events


def _events_with_context_noise() -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            "user-focus-session",
            0,
            "user",
            "message",
            "# AGENTS.md instructions for /workspace/project\n\nRead this large repo guide before work.",
            "2026-06-13T01:00:00+00:00",
        ),
        TranscriptEvent(
            "user-focus-session",
            1,
            "user",
            "message",
            "修复登录失败，重点看用户输入。",
            "2026-06-13T01:01:00+00:00",
        ),
        TranscriptEvent(
            "user-focus-session",
            2,
            "tool",
            "exec_command",
            "process exited with code 0 output from src/login.py",
            "2026-06-13T01:02:00+00:00",
            {"command": "sed -n '1,120p' src/login.py"},
        ),
        TranscriptEvent(
            "user-focus-session",
            3,
            "assistant",
            "message",
            "已按登录失败方向处理。",
            "2026-06-13T01:03:00+00:00",
        ),
        TranscriptEvent(
            "user-focus-session",
            4,
            "user",
            "message",
            "不要分析 AGENTS，报告重点放我的请求。",
            "2026-06-13T01:04:00+00:00",
        ),
    ]


def _event_from_trace(payload: object):
    from recodex.analysis_workflow import _event_from_payload

    return _event_from_payload(payload)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
