"""z/p consistency tests use invented text and no real research results."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from check_z_p import check, evaluate
from prepare_run import prepare
from screenable_lines import screenable_lines


class ZPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-z-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        supplement = root / "appendix.md"
        bibliography = root / "references.bib"
        guideline = root / "guideline.txt"
        manuscript.write_text(
            "---\n"
            "example: two-sided z = 3.00, p = .500\n"
            "---\n"
            "# Results\n"
            "two-sided z = 1.96, p = .050\n"
            "two-sided z = 3.00, p = .500\n"
            "two-tailed z = 4.00, p < .001\n"
            "two-sided z = 2.00, p < .001\n"
            "one-sided z = 3.00, p = .500\n"
            "not two-sided z = 3.00, p = .500\n"
            "~~~text\n"
            "two-sided z = 3.00, p = .500\n"
            "~~~\n"
            "    two-sided z = 3.00, p = .500\n"
            "<!-- two-sided z = 3.00, p = .500 -->\n",
            encoding="utf-8",
        )
        supplement.write_text("two-sided z = 2.00, p = .046\n", encoding="utf-8")
        bibliography.write_text("@misc{x, note={two-sided z = 3.00, p = .500}}\n", encoding="utf-8")
        guideline.write_text("Report statistical tests.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript,
            guideline_text=guideline,
            supplementary=[supplement, bibliography],
            guideline_url="https://example.org/authors",
            retrieved_at="2026-10-19T09:00:00Z",
            output_root=root / "output",
            host="manual",
            model=[],
        ))

    def test_records_only_explicit_two_sided_pairs(self):
        output = check(self.run)
        self.assertEqual([item["status"] for item in output["checks"]],
                         ["compatible", "needs_review", "compatible", "needs_review", "unresolved", "compatible"])
        self.assertEqual([(item["path"], item["line_id"]) for item in output["checks"]], [
            ("manuscript.md", "L5"), ("manuscript.md", "L6"),
            ("manuscript.md", "L7"), ("manuscript.md", "L8"),
            ("manuscript.md", "L14"),
            ("supplementary/supp-1.md", "L1"),
        ])
        self.assertEqual(json.loads((self.run / "z-checks.json").read_text(encoding="utf-8")), output)
        with self.assertRaises(FileExistsError):
            check(self.run)

    def test_rounding_overlap_and_invalid_p_remain_conservative(self):
        self.assertEqual(evaluate("1.96", "=", ".050")["status"], "compatible")
        self.assertEqual(evaluate("3.00", "=", ".500")["status"], "needs_review")
        self.assertEqual(evaluate("2.00", "<", ".045")["status"], "unresolved")
        self.assertEqual(evaluate("2.00", "=", "1.2")["status"], "unresolved")

    def test_unclosed_metadata_keeps_candidate_context_visible(self):
        lines = list(screenable_lines("manuscript.md", "---\nexample: two-sided z = 3.00, p = .500\n"))
        self.assertEqual(lines, [(2, "example: two-sided z = 3.00, p = .500",
                                  "ambiguous_frontmatter")])

    def test_rejects_changed_input(self):
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            check(self.run)
        self.assertFalse((self.run / "z-checks.json").exists())


if __name__ == "__main__":
    unittest.main()
