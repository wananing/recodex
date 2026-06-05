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
    DEFAULT_VOLCENGINE_BASE_URL,
    DEFAULT_VOLCENGINE_MODEL,
    MockProvider,
    VolcengineProvider,
    default_model_for_provider,
    openai_responses_payload,
    provider_for_name,
    session_retro_schema,
    volcengine_responses_payload,
)


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

    def test_retro_llm_mock_writes_report_and_job_output(self) -> None:
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
                            "retro",
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

            report_path = Path(output.getvalue().strip().splitlines()[-1])
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("## 9. 重点诊断", report)
            self.assertIn("修改后缺少验证闭环", report)
            self.assertNotIn("LLM", report)
            self.assertNotRegex(report, r"R\\d{3}")

            conn = sqlite3.connect(db)
            job_count = conn.execute("SELECT COUNT(*) FROM llm_jobs").fetchone()[0]
            output_count = conn.execute("SELECT COUNT(*) FROM llm_outputs").fetchone()[0]
            self.assertEqual(job_count, 1)
            self.assertEqual(output_count, 1)

    def test_cloud_llm_is_blocked_by_local_only_mode_without_allow_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / "transcript.jsonl"
            db = root / "state.sqlite3"
            _write_session(transcript)

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = main(["--db", str(db), "retro", "latest", "--llm"])

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

    def test_volcengine_provider_aliases_and_default_model(self) -> None:
        self.assertIsInstance(provider_for_name("volcengine", api_key="ark-test"), VolcengineProvider)
        self.assertIsInstance(provider_for_name("ark", api_key="ark-test"), VolcengineProvider)
        self.assertIsInstance(provider_for_name("doubao", api_key="ark-test"), VolcengineProvider)
        self.assertEqual(default_model_for_provider("volcengine"), DEFAULT_VOLCENGINE_MODEL)

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
                return json.dumps({"output_text": json.dumps(expected, ensure_ascii=False)}).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.dict(os.environ, {"ARK_API_KEY": "ark-test-key"}, clear=False):
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
        self.assertEqual(captured["url"], f"{DEFAULT_VOLCENGINE_BASE_URL}/responses")
        self.assertEqual(captured["authorization"], "Bearer ark-test-key")
        self.assertEqual(captured["payload"]["model"], DEFAULT_VOLCENGINE_MODEL)  # type: ignore[index]

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
