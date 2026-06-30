from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from recodex.provider_assets import discover_providers, list_provider_assets


class ProviderAssetsTests(unittest.TestCase):
    def test_codex_provider_discovers_capabilities_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_home = root / "codex-home"
            project = root / "repo"
            (codex_home / "sessions").mkdir(parents=True)
            (codex_home / "skills" / "ci-fix").mkdir(parents=True)
            (codex_home / "rules").mkdir(parents=True)
            project.mkdir()
            (project / ".codex" / "skills" / "payment").mkdir(parents=True)

            (codex_home / "AGENTS.md").write_text(
                "# Global Codex Rules\nAlways verify.\n",
                encoding="utf-8",
            )
            (codex_home / "config.toml").write_text(
                """
[mcp_servers.filesystem]
command = "npx"

[profiles.default]
model = "gpt-test"
""".strip(),
                encoding="utf-8",
            )
            (codex_home / "skills" / "ci-fix" / "SKILL.md").write_text(
                """
---
name: ci-fix
description: Repair CI failures from logs.
---
# CI Fix
""".strip(),
                encoding="utf-8",
            )
            (codex_home / "rules" / "no-env.md").write_text(
                "# Do not edit .env\n",
                encoding="utf-8",
            )
            (project / "AGENTS.md").write_text("# Project Rules\nUse pnpm.\n", encoding="utf-8")
            (project / ".codex" / "skills" / "payment" / "SKILL.md").write_text(
                "# Payment Skill\nRun payment tests first.\n",
                encoding="utf-8",
            )

            providers = discover_providers(
                codex_home=codex_home,
                project_roots=(project,),
            )
            codex = next(provider for provider in providers if provider.provider_id == "codex")
            self.assertTrue(codex.detected)
            self.assertTrue(codex.capabilities.has_sessions)
            self.assertTrue(codex.capabilities.has_instructions)
            self.assertTrue(codex.capabilities.has_config)
            self.assertTrue(codex.capabilities.has_skills)
            self.assertTrue(codex.capabilities.has_rules)
            self.assertTrue(codex.capabilities.has_mcp_servers)

            skills = list_provider_assets(
                "codex",
                "skills",
                codex_home=codex_home,
                project_roots=(project,),
            )
            self.assertEqual({skill.name for skill in skills}, {"ci-fix", "Payment Skill"})
            self.assertTrue(any(skill.scope == "project" for skill in skills))

            instructions = list_provider_assets(
                "codex",
                "instructions",
                codex_home=codex_home,
                project_roots=(project,),
            )
            self.assertEqual(
                {instruction.name for instruction in instructions},
                {"Global Codex Rules", "Project Rules"},
            )

            mcp = list_provider_assets("codex", "mcp", codex_home=codex_home)
            self.assertEqual(mcp[0].name, "MCP: filesystem")
            self.assertEqual(mcp[0].metadata["command"], "npx")


if __name__ == "__main__":
    unittest.main()
