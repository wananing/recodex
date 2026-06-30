from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recodex.evidence_mining import run_evidence_mining, write_mining_outputs
from recodex.models import SessionRecord, TranscriptEvent


class EvidenceMiningTests(unittest.TestCase):
    def test_user_corrections_become_evidence_claims_cards_and_not_ready_cluster(self) -> None:
        session = _session("s1")
        result = run_evidence_mining([session], {session.session_id: _ci_events("s1")})

        self.assertGreaterEqual(len(result.evidence_windows), 1)
        compact = "\n".join(window.compact_text for window in result.evidence_windows)
        self.assertIn("[USER_CORRECTION]", compact)
        self.assertIn("pnpm test:payment", compact)

        self.assertTrue(result.micro_claims)
        self.assertTrue(all(claim.supporting_event_ids for claim in result.micro_claims))
        self.assertTrue(all("应该沉淀" not in claim.claim for claim in result.micro_claims))

        self.assertTrue(result.analysis_cards)
        card = result.analysis_cards[0]
        self.assertIn(card.card_type, {"validation_gap", "wrong_command", "user_correction"})
        self.assertTrue(card.evidence_claim_ids)
        self.assertTrue(card.evidence_event_ids)
        self.assertIn(
            card.candidate_destination, {"skill", "eval", "repo_agents_md", "global_agents_md"}
        )

        verifications = {item.card_id: item for item in result.card_verifications}
        self.assertIn(card.card_id, verifications)
        self.assertIn(verifications[card.card_id].verdict, {"pass", "weaken"})

        self.assertTrue(result.pattern_clusters)
        cluster = result.pattern_clusters[0]
        self.assertEqual(cluster.frequency, 1)
        self.assertEqual(cluster.readiness, "needs_more_evidence")
        self.assertNotEqual(cluster.readiness, "ready_for_draft")
        self.assertGreaterEqual(result.cost_ledger.user_corrections, 1)
        self.assertGreaterEqual(result.cost_ledger.verification_followups, 1)
        self.assertTrue(result.evidence_refs)
        self.assertTrue(result.findings)
        self.assertTrue(result.improvement_opportunities)

        mechanisms = {
            opportunity.recommended_mechanism
            for opportunity in result.improvement_opportunities
            if opportunity.title == "降低完成验证转移成本"
        }
        self.assertIn("checklist", mechanisms)
        self.assertNotIn("skill", mechanisms)
        routing_reasons = [
            opportunity.routing_reason
            for opportunity in result.improvement_opportunities
            if opportunity.title == "降低完成验证转移成本"
        ]
        self.assertTrue(any("skill gate held" in reason for reason in routing_reasons))

    def test_repeated_cards_promote_cluster_to_review_queue(self) -> None:
        sessions = [_session("s1"), _session("s2")]
        events_by_session = {
            "s1": _ci_events("s1"),
            "s2": _ci_events("s2"),
        }

        result = run_evidence_mining(sessions, events_by_session)

        ready_clusters = [
            cluster
            for cluster in result.pattern_clusters
            if cluster.frequency >= 2 and cluster.readiness == "ready_for_review"
        ]
        self.assertTrue(ready_clusters)
        self.assertTrue(result.review_queue)
        self.assertEqual(result.coverage["sessions"], 2)
        self.assertGreaterEqual(result.coverage["analysis_cards"], 2)

    def test_skill_gate_promotes_repeated_ready_validation_gaps(self) -> None:
        sessions = [_session("s1"), _session("s2"), _session("s3")]
        events_by_session = {session.session_id: _ci_events(session.session_id) for session in sessions}

        result = run_evidence_mining(sessions, events_by_session)

        validation_opportunities = [
            opportunity
            for opportunity in result.improvement_opportunities
            if opportunity.title == "降低完成验证转移成本"
        ]
        self.assertTrue(validation_opportunities)
        self.assertTrue(
            all(opportunity.recommended_mechanism == "skill" for opportunity in validation_opportunities)
        )
        self.assertTrue(
            all("skill gate passed" in opportunity.routing_reason for opportunity in validation_opportunities)
        )
        self.assertTrue(any(candidate.artifact_type == "skill" for candidate in result.artifact_candidates))

    def test_write_mining_outputs_writes_mvp_files(self) -> None:
        session = _session("s1")
        result = run_evidence_mining([session], {session.session_id: _ci_events("s1")})

        with tempfile.TemporaryDirectory() as temp:
            paths = write_mining_outputs(result, Path(temp))

            self.assertTrue(paths["cards"].exists())
            self.assertTrue(paths["clusters"].exists())
            self.assertTrue(paths["cost_ledger"].exists())
            self.assertTrue(paths["evidence_refs"].exists())
            self.assertTrue(paths["findings"].exists())
            self.assertTrue(paths["opportunities"].exists())
            self.assertTrue(paths["artifact_candidates"].exists())
            self.assertTrue(paths["review_queue"].exists())
            self.assertTrue(paths["coverage_report"].exists())

            cards = [
                json.loads(line)
                for line in paths["cards"].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(cards), len(result.analysis_cards))
            self.assertIn("evidence_event_ids", cards[0])
            opportunities = json.loads(paths["opportunities"].read_text(encoding="utf-8"))
            self.assertEqual(len(opportunities), len(result.improvement_opportunities))
            self.assertIn("recommended_mechanism", opportunities[0])
            self.assertIn("routing_reason", opportunities[0])
            coverage = paths["coverage_report"].read_text(encoding="utf-8")
            self.assertIn("Top Opportunities", coverage)
            self.assertIn("Top Clusters", coverage)


def _session(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        source_path=f"/tmp/{session_id}.jsonl",
        started_at="2026-06-15T01:00:00+00:00",
        updated_at="2026-06-15T01:10:00+00:00",
        title="修复 CI failure",
        tool="codex",
        message_count=6,
        user_message_count=3,
        assistant_message_count=2,
        command_count=1,
        error_count=1,
        raw_preview="修复 CI failure",
        project_path="/work/app",
    )


def _ci_events(session_id: str) -> list[TranscriptEvent]:
    return [
        TranscriptEvent(
            session_id,
            0,
            "user",
            "message",
            "帮我修 CI failure。",
            "2026-06-15T01:00:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            1,
            "assistant",
            "message",
            "我已经修好了。",
            "2026-06-15T01:01:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            2,
            "user",
            "message",
            "你还没看 CI 日志，也没跑失败的 test。先看日志，定位具体失败命令。",
            "2026-06-15T01:02:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            3,
            "tool",
            "exec_command",
            "command=npm test\nProcess exited with code 0",
            "2026-06-15T01:03:00+00:00",
            {"command": "npm test", "exit_code": 0},
        ),
        TranscriptEvent(
            session_id,
            4,
            "user",
            "message",
            "不是 npm test，CI 失败的是 pnpm test:payment。",
            "2026-06-15T01:04:00+00:00",
        ),
        TranscriptEvent(
            session_id,
            5,
            "assistant",
            "message",
            "我会改为查看 CI 日志并运行 pnpm test:payment。",
            "2026-06-15T01:05:00+00:00",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
