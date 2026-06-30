from __future__ import annotations

import unittest

from recodex.evals import run_golden_evals


class GoldenEvalTests(unittest.TestCase):
    def test_golden_evals_measure_routing_and_traceability(self) -> None:
        result = run_golden_evals()

        self.assertTrue(result["ok"])
        self.assertEqual(result["case_count"], 2)
        self.assertEqual(result["routing_accuracy"], 1.0)
        self.assertEqual(result["evidence_traceability"], 1.0)
        self.assertEqual(result["false_skill_promotions"], 0)


if __name__ == "__main__":
    unittest.main()
