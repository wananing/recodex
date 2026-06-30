from __future__ import annotations

import unittest

from recodex.models import SessionRecord, TranscriptEvent
from recodex.qualitative_coding import build_session_qualitative_analysis


class QualitativeCodingTests(unittest.TestCase):
    def test_long_user_prompt_is_split_into_meaning_units_before_coding(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session.session_id,
                1,
                "user",
                "message",
                "Description\nUI Scope\nSkills page.\nProposal\nExport confirmed skills.\n"
                "Acceptance Criteria\nA user can export SKILL.md."
                "Description\nUI Scope\nIngress page.\nProposal\nAdd watch sources and import context.\n"
                "Acceptance Criteria\nA user can create a watch source.",
                "2026-06-15T01:01:00+00:00",
            )
        ]

        analysis = build_session_qualitative_analysis(session, events)

        self.assertEqual(len(analysis["segments"]), 2)
        self.assertEqual(analysis["segments"][0]["source_ref"], "qda-session:event_1:unit_1")
        self.assertEqual(analysis["segments"][1]["source_ref"], "qda-session:event_1:unit_2")
        self.assertIn("Skills page", analysis["segments"][0]["text"])
        self.assertIn("Ingress page", analysis["segments"][1]["text"])

    def test_session_qualitative_analysis_codes_only_user_input_segments(self) -> None:
        session = _session()
        analysis = build_session_qualitative_analysis(session, _events(session.session_id))

        self.assertEqual(analysis["method"], "codebook_qualitative_coding_v1")
        self.assertEqual(analysis["session"]["session_id"], "qda-session")
        self.assertEqual(
            [segment["text"] for segment in analysis["segments"]],
            [
                "把报告功能加上去，分析报告功能，然后预览skill，md，一键导入这些，你帮我规划一下",
                "不要把报告页单独拿出来，要跟dashboard一体的",
                "LLM analysis failed: Volcengine Ark response did not contain valid JSON output.",
                "聊天记录导入功能写的不太行，你搜索一下网上开源的工具抄一下",
            ],
        )
        self.assertTrue(all(segment["role"] == "user" for segment in analysis["segments"]))
        self.assertTrue(all("source_ref" in segment for segment in analysis["segments"]))

        codes_by_text = {segment["text"]: {code["code_id"] for code in segment["codes"]} for segment in analysis["segments"]}
        self.assertIn("reporting_experience", codes_by_text["把报告功能加上去，分析报告功能，然后预览skill，md，一键导入这些，你帮我规划一下"])
        self.assertIn("artifact_workflow", codes_by_text["把报告功能加上去，分析报告功能，然后预览skill，md，一键导入这些，你帮我规划一下"])
        self.assertIn("user_correction", codes_by_text["不要把报告页单独拿出来，要跟dashboard一体的"])
        self.assertIn("ui_integration", codes_by_text["不要把报告页单独拿出来，要跟dashboard一体的"])
        self.assertIn("llm_reliability", codes_by_text["LLM analysis failed: Volcengine Ark response did not contain valid JSON output."])
        self.assertIn("import_quality", codes_by_text["聊天记录导入功能写的不太行，你搜索一下网上开源的工具抄一下"])

        segment_refs = {segment["source_ref"] for segment in analysis["segments"]}
        theme_ids = {theme["theme_id"] for theme in analysis["themes"]}
        self.assertIn("reporting_workflow", theme_ids)
        self.assertIn("llm_analysis_reliability", theme_ids)
        self.assertIn("context_ingestion_quality", theme_ids)
        self.assertTrue(
            all(ref in segment_refs for theme in analysis["themes"] for ref in theme["evidence_refs"])
        )
        self.assertTrue(all(theme["validation"]["status"] == "supported" for theme in analysis["themes"]))

    def test_business_report_language_is_not_recodex_reporting_workflow(self) -> None:
        session = _session()
        events = [
            TranscriptEvent(
                session.session_id,
                1,
                "user",
                "message",
                "根据日期、用户编码获取订单报表和礼包报表，请求报文里有 beginDate/endDate/userCode，这是测试接口地址。",
                "2026-06-15T01:01:00+00:00",
            ),
            TranscriptEvent(
                session.session_id,
                2,
                "user",
                "message",
                "这个批次超时报告可以新增一个报告，xxjbo 的定时如果我不需要就可以不触发。",
                "2026-06-15T01:02:00+00:00",
            ),
        ]

        analysis = build_session_qualitative_analysis(session, events)

        codes_by_text = {segment["text"]: {code["code_id"] for code in segment["codes"]} for segment in analysis["segments"]}
        for codes in codes_by_text.values():
            self.assertNotIn("reporting_experience", codes)
            self.assertNotIn("user_correction", codes)
        self.assertNotIn("reporting_workflow", {theme["theme_id"] for theme in analysis["themes"]})


def _session() -> SessionRecord:
    return SessionRecord(
        session_id="qda-session",
        source_path="/tmp/qda-session.jsonl",
        started_at="2026-06-15T01:00:00+00:00",
        updated_at="2026-06-15T01:20:00+00:00",
        title="报告和导入优化",
        tool="codex",
        message_count=6,
        user_message_count=5,
        assistant_message_count=1,
        command_count=1,
        error_count=1,
        raw_preview="把报告功能加上去",
        project_path="/work/recodex",
    )


def _events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id,
            0,
            "user",
            "message",
            "<environment_context><cwd>/work/recodex</cwd></environment_context>",
            "2026-06-15T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            1,
            "user",
            "message",
            "把报告功能加上去，分析报告功能，然后预览skill，md，一键导入这些，你帮我规划一下",
            "2026-06-15T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            2,
            "assistant",
            "message",
            "我会先设计报告页面。",
            "2026-06-15T01:02:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            3,
            "tool",
            "exec_command",
            "pytest failed with AssertionError",
            "2026-06-15T01:03:00+00:00",
            {"command": "pytest"},
        ),
        TranscriptEvent(
            session_id,
            4,
            "user",
            "message",
            "不要把报告页单独拿出来，要跟dashboard一体的",
            "2026-06-15T01:04:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            5,
            "user",
            "message",
            "LLM analysis failed: Volcengine Ark response did not contain valid JSON output.",
            "2026-06-15T01:05:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            6,
            "user",
            "message",
            "聊天记录导入功能写的不太行，你搜索一下网上开源的工具抄一下",
            "2026-06-15T01:06:00+00:00",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
