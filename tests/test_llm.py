from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recodex.cli import main
from recodex.llm import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_DASHSCOPE_MODEL,
    DEFAULT_SILICONFLOW_BASE_URL,
    DEFAULT_SILICONFLOW_MODEL,
    DEFAULT_VOLCENGINE_BASE_URL,
    DEFAULT_VOLCENGINE_MODEL,
    LLMResponseIncompleteError,
    MockProvider,
    OpenAICompatibleProvider,
    SESSION_RETRO_MAX_OUTPUT_TOKENS,
    SESSION_RETRO_RETRY_MAX_OUTPUT_TOKENS,
    VolcengineProvider,
    build_session_retro_request,
    chat_completions_payload,
    default_model_for_provider,
    extract_chat_completion_text,
    extract_response_text,
    generate_session_retro_analysis,
    openai_responses_payload,
    parse_json_output_text,
    provider_for_name,
    session_retro_schema,
    volcengine_responses_payload,
)
from recodex.models import SessionRecord, TranscriptEvent


class LLMIntegrationTests(unittest.TestCase):
    def test_mock_provider_returns_structured_session_retro(self) -> None:
        provider = MockProvider()
        output = provider.generate_json(
            model="mock",
            system="Return JSON.",
            messages=[{"role": "user", "content": "verification_present=false event_1"}],
            schema=session_retro_schema(),
            temperature=0,
            max_output_tokens=1000,
            metadata={"task_type": "session_retro"},
        )

        self.assertIn("overall_assessment", output)
        self.assertIn("main_findings", output)
        self.assertTrue(output["main_findings"][0]["evidence_refs"])
        self.assertIn("chat_transcript_analysis", output)

    def test_session_retro_request_includes_raw_chat_without_tool_output(self) -> None:
        session = SessionRecord(
            session_id="llm-chat-session",
            source_path="/tmp/llm-chat.jsonl",
            started_at="2026-05-29T01:00:00+00:00",
            updated_at="2026-05-29T01:04:00+00:00",
            title="分析聊天记录",
            tool="codex",
            message_count=4,
            user_message_count=2,
            assistant_message_count=1,
            command_count=1,
            error_count=0,
            raw_preview="分析聊天记录",
            project_path="/work/aicoo",
        )
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="请分析这段聊天记录，重点看我反复纠正的点。",
                created_at="2026-05-29T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我会先看文字对话，再给出结论。",
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
            TranscriptEvent(
                session_id=session.session_id,
                event_index=3,
                role="user",
                kind="message",
                text="不要把工具输出当成聊天结论。",
                created_at="2026-05-29T01:03:00+00:00",
            ),
        ]

        request = build_session_retro_request(session, events, provider="mock", model="mock-model")
        payload = json.loads(request.messages[0]["content"])
        transcript = payload["raw_chat_transcript"]
        serialized = json.dumps(transcript, ensure_ascii=False)

        self.assertEqual(transcript["scope"], "user_and_assistant_chat_text_only")
        self.assertEqual([item["role"] for item in transcript["messages"]], ["user", "assistant", "user"])
        self.assertIn("请分析这段聊天记录", serialized)
        self.assertIn("我会先看文字对话", serialized)
        self.assertIn("不要把工具输出当成聊天结论", serialized)
        self.assertNotIn("SECRET_TOOL_OUTPUT", serialized)
        self.assertNotIn("command=pytest", serialized)

    def test_report_llm_mock_writes_report_and_job_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "transcript.jsonl"
            db = root / "state.sqlite3"
            reports = root / "reports"
            _write_session(transcript)

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(db),
                            "report",
                            "latest",
                            "--reports-dir",
                            str(reports),
                            "--llm",
                            "--llm-provider",
                            "mock",
                        ]
                    ),
                    0,
                )

            report_path = Path(output.getvalue().strip().splitlines()[-2])
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("## 9. 重点诊断", report)
            self.assertIn("验收条件没有在开工前固定", report)
            self.assertNotIn("LLM", report)
            self.assertNotRegex(report, r"R\\d{3}")

            conn = sqlite3.connect(db)
            job_count = conn.execute("SELECT COUNT(*) FROM llm_jobs").fetchone()[0]
            output_count = conn.execute("SELECT COUNT(*) FROM llm_outputs").fetchone()[0]
            usage_json = conn.execute("SELECT usage_json FROM llm_outputs").fetchone()[0]
            usage = json.loads(usage_json)
            self.assertEqual(job_count, 1)
            self.assertEqual(output_count, 1)
            self.assertEqual(usage["source"], "estimated")
            self.assertGreater(usage["input_tokens"], 0)
            self.assertGreater(usage["output_tokens"], 0)
            self.assertEqual(usage["current_run_total_tokens"], usage["total_tokens"])

    def test_cloud_llm_is_blocked_by_local_only_mode_without_allow_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "transcript.jsonl"
            db = root / "state.sqlite3"
            _write_session(transcript)

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = main(["--db", str(db), "report", "latest", "--llm"])

            self.assertEqual(status, 1)
            self.assertIn("local-only mode", output.getvalue())

    def test_openai_payload_uses_structured_outputs_schema(self) -> None:
        schema = session_retro_schema()
        payload = openai_responses_payload(
            model="gpt-5.5",
            system="system",
            messages=[{"role": "user", "content": "{}"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
            metadata={"task_type": "session_retro"},
        )

        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(payload["text"]["format"]["schema"], schema)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("thinking", payload)

    def test_volcengine_provider_aliases_and_default_model(self) -> None:
        self.assertIsInstance(provider_for_name("volcengine", api_key="ark-test"), VolcengineProvider)
        self.assertIsInstance(provider_for_name("ark", api_key="ark-test"), VolcengineProvider)
        self.assertIsInstance(provider_for_name("doubao", api_key="ark-test"), VolcengineProvider)
        self.assertEqual(default_model_for_provider("volcengine"), DEFAULT_VOLCENGINE_MODEL)

    def test_openai_compatible_provider_aliases_and_default_models(self) -> None:
        self.assertIsInstance(provider_for_name("dashscope", api_key="dash-test"), OpenAICompatibleProvider)
        self.assertIsInstance(provider_for_name("aliyun", api_key="dash-test"), OpenAICompatibleProvider)
        self.assertIsInstance(provider_for_name("siliconflow", api_key="sf-test"), OpenAICompatibleProvider)
        self.assertIsInstance(provider_for_name("openai-compatible", api_key="oa-test"), OpenAICompatibleProvider)
        self.assertEqual(default_model_for_provider("dashscope"), DEFAULT_DASHSCOPE_MODEL)
        self.assertEqual(default_model_for_provider("siliconflow"), DEFAULT_SILICONFLOW_MODEL)

    def test_volcengine_payload_uses_responses_api_schema_without_metadata(self) -> None:
        schema = session_retro_schema()
        payload = volcengine_responses_payload(
            model=DEFAULT_VOLCENGINE_MODEL,
            system="system",
            messages=[{"role": "user", "content": "{}"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
        )

        self.assertEqual(payload["model"], DEFAULT_VOLCENGINE_MODEL)
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(payload["text"]["format"]["schema"], schema)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("metadata", payload)

    def test_volcengine_provider_reads_ark_key_and_posts_to_default_base_url(self) -> None:
        schema = session_retro_schema()
        expected = MockProvider().generate_json(
            model="mock",
            system="system",
            messages=[{"role": "user", "content": "verification_present=false"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
            metadata={"task_type": "session_retro"},
        )
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "output_text": json.dumps(expected, ensure_ascii=False),
                        "usage": {
                            "input_tokens": 123,
                            "output_tokens": 45,
                            "total_tokens": 168,
                            "input_tokens_details": {"cached_tokens": 12},
                            "output_tokens_details": {"reasoning_tokens": 3},
                        },
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict(os.environ, {"ARK_API_KEY": "ark-test-key"}, clear=False):
            with patch("urllib.request.urlopen", fake_urlopen):
                provider = VolcengineProvider()
                output = provider.generate_json(
                    model=DEFAULT_VOLCENGINE_MODEL,
                    system="system",
                    messages=[{"role": "user", "content": "verification_present=false"}],
                    schema=schema,
                    temperature=0,
                    max_output_tokens=1000,
                    metadata={"task_type": "session_retro"},
                )

        self.assertEqual(output["overall_assessment"], expected["overall_assessment"])
        self.assertEqual(provider.last_usage["input_tokens"], 123)
        self.assertEqual(provider.last_usage["output_tokens"], 45)
        self.assertEqual(provider.last_usage["total_tokens"], 168)
        self.assertEqual(provider.last_usage["cached_tokens"], 12)
        self.assertEqual(provider.last_usage["reasoning_tokens"], 3)
        self.assertEqual(captured["url"], f"{DEFAULT_VOLCENGINE_BASE_URL}/responses")
        self.assertEqual(captured["authorization"], "Bearer ark-test-key")
        self.assertEqual(captured["payload"]["model"], DEFAULT_VOLCENGINE_MODEL)  # type: ignore[index]

    def test_volcengine_provider_accepts_fenced_json_response_text(self) -> None:
        schema = session_retro_schema()
        expected = MockProvider().generate_json(
            model="mock",
            system="system",
            messages=[{"role": "user", "content": "verification_present=false"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
            metadata={"task_type": "session_retro"},
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

            def read(self) -> bytes:
                text = "```json\n" + json.dumps(expected, ensure_ascii=False) + "\n```"
                return json.dumps({"output": [{"content": [{"type": "output_text", "text": text}]}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            return FakeResponse()

        with patch.dict(os.environ, {"ARK_API_KEY": "ark-test-key"}, clear=True):
            with patch("urllib.request.urlopen", fake_urlopen):
                output = VolcengineProvider().generate_json(
                    model=DEFAULT_VOLCENGINE_MODEL,
                    system="system",
                    messages=[{"role": "user", "content": "verification_present=false"}],
                    schema=schema,
                    temperature=0,
                    max_output_tokens=1000,
                    metadata={"task_type": "session_retro"},
                )

        self.assertEqual(output["overall_assessment"], expected["overall_assessment"])

    def test_json_output_parser_extracts_object_from_surrounding_text(self) -> None:
        parsed = parse_json_output_text(
            '结果如下：\n{"overall_assessment":"ok","main_findings":[]}\n请查收。',
            "provider",
        )

        self.assertEqual(parsed["overall_assessment"], "ok")

    def test_responses_parser_reports_incomplete_and_refusal_details(self) -> None:
        with self.assertRaisesRegex(LLMResponseIncompleteError, "incomplete.*max_output_tokens") as caught:
            extract_response_text(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                },
                provider_label="Volcengine Ark",
            )
        self.assertEqual(caught.exception.reason, "max_output_tokens")

        with self.assertRaisesRegex(RuntimeError, "refusal.*policy"):
            extract_response_text(
                {
                    "output": [
                        {
                            "content": [
                                {"type": "refusal", "refusal": "policy"},
                            ]
                        }
                    ]
                },
                provider_label="OpenAI",
            )

    def test_chat_completions_parser_reports_length_finish_reason(self) -> None:
        with self.assertRaisesRegex(LLMResponseIncompleteError, "incomplete.*length") as caught:
            extract_chat_completion_text(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "{\"overall_assessment\":\"truncated\""},
                        }
                    ]
                }
            )

        self.assertEqual(caught.exception.reason, "length")

    def test_session_retro_generation_compact_retries_after_length_incomplete(self) -> None:
        session = SessionRecord(
            session_id="retry-session",
            source_path="/tmp/retry-session.jsonl",
            started_at="2026-06-27T01:00:00+00:00",
            updated_at="2026-06-27T01:10:00+00:00",
            title="生成报告",
            tool="codex",
            message_count=3,
            user_message_count=2,
            assistant_message_count=1,
            command_count=0,
            error_count=0,
            raw_preview="生成报告",
            project_path="/work/recodex",
        )
        events = [
            TranscriptEvent(
                session_id=session.session_id,
                event_index=0,
                role="user",
                kind="message",
                text="生成 v2 报告，并重点分析 dashboard 看不到报告的问题。",
                created_at="2026-06-27T01:00:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=1,
                role="assistant",
                kind="message",
                text="我会检查报告链路并生成总结。",
                created_at="2026-06-27T01:01:00+00:00",
            ),
            TranscriptEvent(
                session_id=session.session_id,
                event_index=2,
                role="user",
                kind="message",
                text="不要因为输出太长就直接失败，要能恢复。",
                created_at="2026-06-27T01:02:00+00:00",
            ),
        ]
        request = build_session_retro_request(
            session,
            events,
            provider="volcengine",
            model=DEFAULT_VOLCENGINE_MODEL,
        )

        class LengthThenOkProvider:
            provider_name = "volcengine"

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []
                self.last_usage: dict[str, object] = {}

            def generate_json(self, **kwargs) -> dict[str, object]:
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise LLMResponseIncompleteError("Volcengine Ark", "length")
                self.last_usage = {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "source": "provider",
                }
                return MockProvider().generate_json(**kwargs)

        provider = LengthThenOkProvider()

        result = generate_session_retro_analysis(provider, request)

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[0]["max_output_tokens"], SESSION_RETRO_MAX_OUTPUT_TOKENS)
        self.assertEqual(provider.calls[1]["max_output_tokens"], SESSION_RETRO_RETRY_MAX_OUTPUT_TOKENS)
        self.assertIn("compact_retry", result.warnings)
        self.assertIn("compact_retry_reason:length", result.warnings)
        self.assertTrue(result.usage["retried"])
        self.assertEqual(result.usage["max_output_tokens"], SESSION_RETRO_RETRY_MAX_OUTPUT_TOKENS)
        retry_messages = provider.calls[1]["messages"]
        self.assertIsInstance(retry_messages, list)
        retry_payload = json.loads(retry_messages[0]["content"])  # type: ignore[index]
        self.assertEqual(retry_payload["response_limits"]["max_findings"], 2)
        self.assertEqual(retry_payload["retry_context"]["reason"], "length")

    def test_openai_compatible_payload_uses_chat_completions_json_mode(self) -> None:
        schema = session_retro_schema()
        payload = chat_completions_payload(
            model=DEFAULT_DASHSCOPE_MODEL,
            system="system",
            messages=[{"role": "user", "content": "{}"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
        )

        self.assertEqual(payload["model"], DEFAULT_DASHSCOPE_MODEL)
        self.assertEqual(payload["response_format"]["type"], "json_object")
        self.assertIn('"overall_assessment"', payload["messages"][0]["content"])

    def test_openai_compatible_provider_reads_provider_key_and_posts_to_chat_completions(self) -> None:
        schema = session_retro_schema()
        expected = MockProvider().generate_json(
            model="mock",
            system="system",
            messages=[{"role": "user", "content": "verification_present=false"}],
            schema=schema,
            temperature=0,
            max_output_tokens=1000,
            metadata={"task_type": "session_retro"},
        )
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [{"message": {"content": json.dumps(expected, ensure_ascii=False)}}],
                        "usage": {
                            "prompt_tokens": 111,
                            "completion_tokens": 22,
                            "total_tokens": 133,
                            "prompt_tokens_details": {"cached_tokens": 7},
                            "completion_tokens_details": {"reasoning_tokens": 5},
                        },
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dash-test-key"}, clear=True):
            with patch("urllib.request.urlopen", fake_urlopen):
                provider = provider_for_name("dashscope")
                output = provider.generate_json(
                    model=DEFAULT_DASHSCOPE_MODEL,
                    system="system",
                    messages=[{"role": "user", "content": "verification_present=false"}],
                    schema=schema,
                    temperature=0,
                    max_output_tokens=1000,
                    metadata={"task_type": "session_retro"},
                )

        self.assertEqual(output["overall_assessment"], expected["overall_assessment"])
        self.assertEqual(provider.last_usage["input_tokens"], 111)
        self.assertEqual(provider.last_usage["output_tokens"], 22)
        self.assertEqual(provider.last_usage["total_tokens"], 133)
        self.assertEqual(provider.last_usage["cached_tokens"], 7)
        self.assertEqual(provider.last_usage["reasoning_tokens"], 5)
        self.assertEqual(captured["url"], f"{DEFAULT_DASHSCOPE_BASE_URL}/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer dash-test-key")
        self.assertEqual(captured["payload"]["model"], DEFAULT_DASHSCOPE_MODEL)  # type: ignore[index]

    def test_siliconflow_provider_uses_default_base_url(self) -> None:
        provider = provider_for_name("siliconflow", api_key="sf-test-key")
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.base_url, DEFAULT_SILICONFLOW_BASE_URL)

    def test_volcengine_provider_requires_ark_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ARK_API_KEY"):
                VolcengineProvider().generate_json(
                    model=DEFAULT_VOLCENGINE_MODEL,
                    system="system",
                    messages=[{"role": "user", "content": "{}"}],
                    schema=session_retro_schema(),
                    temperature=0,
                    max_output_tokens=1000,
                    metadata={},
                )


def _write_session(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": "llm-session",
                        "timestamp": "2026-05-29T01:00:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "修复登录失败，但不要改无关模块。"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "exec_command",
                        "session_id": "llm-session",
                        "timestamp": "2026-05-29T01:01:00+00:00",
                        "arguments": {"cmd": "python app.py"},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "session_id": "llm-session",
                        "timestamp": "2026-05-29T01:02:00+00:00",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "已修改登录处理逻辑。"}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
