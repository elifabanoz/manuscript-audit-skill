"""Statistical screen tests use invented reports and private temporary runs."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from check_stats import check, evaluate
from prepare_run import prepare


class StatisticalScreenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="manuscript-stats-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manuscript = root / "paper.md"
        supplement = root / "appendix.md"
        guideline = root / "guideline.txt"
        manuscript.write_text(
            "# Results\n"
            "two-sided t(18) = 2.10, p = .050\n"
            "F(2, 20) = 10.00, p = .500\n"
            "χ²(1) = 12.00, p < .001\n"
            "two-tailed Pearson r(18) = .800, p = .500\n"
            "one-sided t(18) = 3.00, p = .500\n"
            "adjusted F(2, 20) = 10.00, p = .500\n"
            "not two-sided t(18) = 3.00, p = .500\n"
            "t(18) = 3.00, p = .500\n",
            encoding="utf-8",
        )
        supplement.write_text("two-sided t(18) = -1.00, p = .200\n", encoding="utf-8")
        guideline.write_text("Report statistical results.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript, guideline_text=guideline, supplementary=[supplement],
            guideline_url="https://example.org/authors", retrieved_at="2026-10-19T09:00:00Z",
            output_root=root / "runs", host="manual", model=[]))

    def test_explicit_patterns_and_exclusions(self):
        output = check(self.run)
        self.assertEqual([(item["statistic"], item["line_id"], item["status"])
                          for item in output["checks"]], [
            ("t", "L2", "compatible"),
            ("F", "L3", "needs_review"),
            ("chi_square", "L4", "compatible"),
            ("pearson_r", "L5", "needs_review"),
            ("t", "L1", "needs_review"),
        ])
        self.assertEqual([item.get("severity_proposed") for item in output["checks"]],
                         [None, "major", None, "major", "minor"])
        self.assertEqual(json.loads((self.run / "statistical-checks.json").read_text()), output)
        with self.assertRaises(FileExistsError):
            check(self.run)

    def test_invalid_and_boundary_inputs_remain_unresolved(self):
        from scipy import stats

        self.assertEqual(evaluate("t", "3.00", [18], "=", "1.2", stats)["status"], "unresolved")
        self.assertEqual(evaluate("F", "-1.00", [2, 20], "=", ".5", stats)["status"], "unresolved")
        self.assertEqual(evaluate("pearson_r", "1.20", [18], "=", ".5", stats)["status"], "unresolved")
        self.assertEqual(evaluate("chi_square", "3.84", [0], "=", ".05", stats)["status"], "unresolved")
        self.assertEqual(evaluate("t", "2.00", [18], "<", ".061", stats)["status"], "unresolved")

    def test_changed_manuscript_fails_before_output(self):
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            check(self.run)
        self.assertFalse((self.run / "statistical-checks.json").exists())


if __name__ == "__main__":
    unittest.main()
