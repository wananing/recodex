from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from recodex.cli import main
from recodex.db import connect, insert_improvements, update_improvement_status
from recodex.exports.skill import write_skill_md_exports
from recodex.models import ImprovementDraft


class SkillExportTests(unittest.TestCase):
    def test_skill_export_writes_one_skill_md_per_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            paths = write_skill_md_exports(
                out,
                [
                    {
                        "id": 7,
                        "title": "Deploy Service",
                        "recommendation": "Run health checks after deployment.",
                        "evidence": "Deployment failed before health check.",
                    }
                ],
            )

            self.assertEqual(paths, [out / "skills" / "deploy-service" / "SKILL.md"])
            text = paths[0].read_text(encoding="utf-8")
            self.assertIn("name: Deploy Service", text)
            self.assertIn("Run health checks", text)
            self.assertTrue((out / "skills" / ".recodex-export.json").exists())

    def test_conflict_skip_does_not_overwrite_unmanaged_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            existing = out / "skills" / "deploy-service" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("manual", encoding="utf-8")

            paths = write_skill_md_exports(
                out,
                [
                    {
                        "id": 7,
                        "title": "Deploy Service",
                        "recommendation": "Run health checks after deployment.",
                        "evidence": "Deployment failed before health check.",
                    }
                ],
                on_conflict="skip",
            )

            self.assertEqual(paths, [])
            self.assertEqual(existing.read_text(encoding="utf-8"), "manual")

    def test_cli_skill_export_requires_accepted_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            transcript = root / "session.jsonl"
            exports = root / "exports"
            reports = root / "reports"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "skill-cli-1",
                                "timestamp": "2026-05-28T01:00:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "Fix sandbox failure."}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "skill-cli-1",
                                "timestamp": "2026-05-28T01:01:00+00:00",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "A sandbox permission error failed the test run.",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "skill-cli-1",
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
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "session_id": "skill-cli-1",
                                "timestamp": "2026-05-28T01:03:00+00:00",
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
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(main(["--db", str(db), "scan", str(transcript)]), 0)
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(db),
                        "improvements",
                        "propose",
                        "--since",
                        "3650d",
                        "--reports-dir",
                        str(reports),
                    ]
                ),
                0,
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(["--db", str(db), "export", "skills", "--exports-dir", str(exports)]),
                    1,
                )
            self.assertIn("No accepted improvement candidates", output.getvalue())
            self.assertFalse((exports / "skills").exists())

            self.assertEqual(main(["--db", str(db), "improvements", "accept", "1"]), 0)
            self.assertEqual(
                main(["--db", str(db), "export", "skills", "--exports-dir", str(exports)]),
                0,
            )
            self.assertTrue(any((exports / "skills").glob("*/SKILL.md")))

    def test_cli_skill_export_target_project_uses_project_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            _seed_accepted_improvement(db)

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--db", str(db), "export", "skills", "--target", "project"]), 0)
            finally:
                os.chdir(previous_cwd)

            skill_path = root / ".agents" / "skills" / "deploy-service" / "SKILL.md"
            self.assertTrue(skill_path.exists())
            self.assertIn(str((root / ".agents" / "skills").resolve()), output.getvalue())

    def test_cli_skill_export_remembers_last_out_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "state.sqlite3"
            out = root / "manual-skills"
            _seed_accepted_improvement(db)

            self.assertEqual(main(["--db", str(db), "export", "skills", "--out", str(out)]), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(db), "export", "skills", "--target", "last"]), 0)

            self.assertTrue((out / "deploy-service" / "SKILL.md").exists())
            self.assertIn(f"Skill export target: {out.resolve()}", output.getvalue())


def _seed_accepted_improvement(db: Path) -> None:
    conn = connect(db)
    insert_improvements(
        conn,
        [
            ImprovementDraft(
                fingerprint="skill-dest-1",
                session_id=None,
                category="workflow",
                title="Deploy Service",
                evidence="Deployment failed before health check.",
                recommendation="Run health checks after deployment.",
            )
        ],
    )
    update_improvement_status(conn, [1], "accepted")


if __name__ == "__main__":
    unittest.main()
