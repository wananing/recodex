from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_dev_review.config import load_config
from ai_dev_review.privacy import redact_mapping, redact_text


class ConfigTests(unittest.TestCase):
    def test_default_config_is_local_first_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            codex_home = Path(temp) / "codex-home"
            root.mkdir()

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                config = load_config(root)

            self.assertEqual(config.sources.codex.sessions_dir, codex_home / "sessions")
            self.assertTrue(config.privacy.redact_secrets)
            self.assertTrue(config.privacy.redact_api_keys)
            self.assertTrue(config.privacy.redact_tokens)
            self.assertTrue(config.privacy.redact_home_path)
            self.assertTrue(config.analysis.local_only)
            self.assertEqual(config.analysis.llm_provider, "openai")
            self.assertIsNone(config.analysis.llm_model)
            self.assertIsNone(config.analysis.llm_api_key)
            self.assertIsNone(config.analysis.llm_api_key_env)
            self.assertIsNone(config.analysis.llm_base_url)
            self.assertEqual(config.outputs.reports_dir, root / ".ai-review" / "reports")
            self.assertEqual(config.outputs.agents_md, root / "AGENTS.md")
            self.assertEqual(config.outputs.skills_dir, root / ".agents" / "skills")
            self.assertEqual(config.outputs.checklists_dir, root / "docs" / "ai-checklists")
            self.assertEqual(config.outputs.scripts_dir, root / "scripts" / "ai")

    def test_global_config_loads_and_project_config_overrides_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            home = Path(temp) / "home"
            root.mkdir()
            (home / ".ai-review").mkdir(parents=True)
            (home / ".ai-review" / "config.toml").write_text(
                "\n".join(
                    [
                        "[sources.codex]",
                        'sessions_dir = "~/global-sessions"',
                        "",
                        "[privacy]",
                        "redact_tokens = false",
                        "",
                        "[analysis]",
                        "local_only = true",
                        'llm_provider = "mock"',
                        'llm_api_key_env = "AI_REVIEW_TEST_KEY"',
                    ]
                ),
                encoding="utf-8",
            )
            (root / ".ai-review.toml").write_text(
                "\n".join(
                    [
                        "[sources.codex]",
                        'sessions_dir = "./project-sessions"',
                        "",
                        "[privacy]",
                        "redact_emails = false",
                        "",
                        "[analysis]",
                        "local_only = false",
                        'llm_model = "gpt-test"',
                        'llm_provider = "volcengine"',
                        'llm_api_key = "ark-test-key"',
                        'llm_base_url = "https://ark.example.com/api/v3"',
                        "",
                        "[outputs]",
                        'reports_dir = "./review-reports"',
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                config = load_config(root)

            self.assertEqual(config.sources.codex.sessions_dir, root / "project-sessions")
            self.assertFalse(config.privacy.redact_tokens)
            self.assertFalse(config.privacy.redact_emails)
            self.assertFalse(config.analysis.local_only)
            self.assertEqual(config.analysis.llm_provider, "volcengine")
            self.assertEqual(config.analysis.llm_model, "gpt-test")
            self.assertEqual(config.analysis.llm_api_key, "ark-test-key")
            self.assertEqual(config.analysis.llm_api_key_env, "AI_REVIEW_TEST_KEY")
            self.assertEqual(config.analysis.llm_base_url, "https://ark.example.com/api/v3")
            self.assertEqual(config.outputs.reports_dir, root / "review-reports")


class PrivacyTests(unittest.TestCase):
    def test_redact_text_replaces_common_secrets_with_readable_markers(self) -> None:
        raw = "\n".join(
            [
                "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
                "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
                "DATABASE_URL=postgres://user:pass@localhost:5432/app",
                "JWT_SECRET=super-secret-value",
                "Contact dev@example.com for access.",
                "-----BEGIN OPENSSH PRIVATE KEY-----",
                "private-key-body",
                "-----END OPENSSH PRIVATE KEY-----",
            ]
        )

        redacted = redact_text(raw)

        self.assertIn("[REDACTED:API_KEY]", redacted)
        self.assertIn("[REDACTED:TOKEN]", redacted)
        self.assertIn("[REDACTED:AUTHORIZATION]", redacted)
        self.assertIn("[REDACTED:DATABASE_URL]", redacted)
        self.assertIn("[REDACTED:JWT_SECRET]", redacted)
        self.assertIn("[REDACTED:EMAIL]", redacted)
        self.assertIn("[REDACTED:SSH_PRIVATE_KEY]", redacted)
        self.assertNotIn("sk-proj-", redacted)
        self.assertNotIn("ghp_", redacted)
        self.assertNotIn("dev@example.com", redacted)
        self.assertNotIn("private-key-body", redacted)

    def test_redact_text_replaces_home_path(self) -> None:
        home = Path("/home/alice")
        redacted = redact_text(
            "Read /home/alice/project/.env and /home/alice/.ssh/id_rsa",
            home=home,
        )

        self.assertIn("[REDACTED:HOME]/project/.env", redacted)
        self.assertIn("[REDACTED:HOME]/.ssh/id_rsa", redacted)
        self.assertNotIn("/home/alice", redacted)

    def test_redact_mapping_redacts_nested_sensitive_values(self) -> None:
        redacted = redact_mapping(
            {
                "OPENAI_API_KEY": "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
                "metadata": {
                    "email": "dev@example.com",
                    "notes": ["Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"],
                },
            }
        )

        self.assertEqual(redacted["OPENAI_API_KEY"], "[REDACTED:API_KEY]")
        self.assertEqual(redacted["metadata"]["email"], "[REDACTED:EMAIL]")
        self.assertEqual(redacted["metadata"]["notes"][0], "Authorization: [REDACTED:AUTHORIZATION]")


if __name__ == "__main__":
    unittest.main()
