"""GRIM tests use invented single-item integer-scale results only."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from check_grim import check, evaluate
from prepare_run import prepare


class GrimTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-grim-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        supplement = root / "appendix.md"
        guideline = root / "guideline.txt"
        manuscript.write_text("# Results\nReported mean 2.34.\n", encoding="utf-8")
        supplement.write_text("Reported mean 2.31.\n", encoding="utf-8")
        guideline.write_text("Report descriptive statistics.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript,
            guideline_text=guideline,
            supplementary=[supplement],
            guideline_url="https://example.org/authors",
            retrieved_at="2026-10-19T09:00:00Z",
            output_root=root / "output",
            host="manual",
            model=[],
        ))
        self.cases_path = root / "cases.json"
        self.cases = [
            {"case_id": "M1", "path": "manuscript.md", "line_id": "L2", "reported_mean": "2.34", "sample_size": 13, "scale_min": 1, "scale_max": 5},
            {"case_id": "M2", "path": "supplementary/supp-1.md", "line_id": "L1", "reported_mean": "2.31", "sample_size": 13, "scale_min": 1, "scale_max": 5},
        ]
        self.save_cases()

    def save_cases(self):
        self.cases_path.write_text(json.dumps(self.cases), encoding="utf-8")

    def test_flags_only_arithmetic_infeasibility(self):
        output = check(self.run, self.cases_path)
        self.assertEqual([item["status"] for item in output["checks"]], ["needs_review", "compatible"])
        self.assertEqual(output["checks"][1]["possible_integer_sum_range"], [30, 30])
        self.assertEqual(json.loads((self.run / "grim-checks.json").read_text(encoding="utf-8")), output)
        with self.assertRaises(FileExistsError):
            check(self.run, self.cases_path)

    def test_rounding_interval_includes_ties_conservatively(self):
        case = dict(self.cases[0], reported_mean="2.35", sample_size=20)
        self.assertEqual(evaluate(case)["status"], "compatible")

    def test_rejects_unbound_or_invalid_cases(self):
        self.cases[0]["reported_mean"] = "2.39"
        self.save_cases()
        with self.assertRaisesRegex(ValueError, "absent from its cited line"):
            check(self.run, self.cases_path)
        self.cases[0]["reported_mean"] = "2.34"
        self.cases[0]["sample_size"] = True
        self.save_cases()
        with self.assertRaisesRegex(ValueError, "bounded integer"):
            check(self.run, self.cases_path)
        self.assertFalse((self.run / "grim-checks.json").exists())

    def test_rejects_changed_source(self):
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            check(self.run, self.cases_path)
        self.assertFalse((self.run / "grim-checks.json").exists())


if __name__ == "__main__":
    unittest.main()
