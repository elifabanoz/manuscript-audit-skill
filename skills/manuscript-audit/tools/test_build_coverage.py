"""Coverage generation tests use invented content only."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from build_coverage import build
from prepare_run import prepare


class CoverageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-coverage-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        guideline = root / "guideline.txt"
        supplement = root / "appendix.md"
        manuscript.write_text("# Introduction\nExample text.\n", encoding="utf-8")
        guideline.write_text("A short abstract is required.\n", encoding="utf-8")
        supplement.write_text("Supporting detail.\n", encoding="utf-8")
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
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        source_ref = next(iter(manifest["sources"]))
        self.profile = {
            "issn": "1234-567X",
            "title": "Example Journal",
            "publisher": "Example Publisher",
            "article_types": ["original_research", "review"],
            "sources": manifest["sources"],
            "rules": [
                {"rule_id": "abstract", "kind": "semantic", "requirement": "Include an abstract", "quote": "A short abstract is required.", "source_ref": source_ref, "applies_to": ["original_research"]},
                {"rule_id": "review_abstract", "kind": "semantic", "requirement": "Include an abstract", "quote": "A short abstract is required.", "source_ref": source_ref, "applies_to": ["review"]},
            ],
        }
        self.save_profile()

    def save_profile(self):
        (self.run / "journal-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def test_builds_only_applicable_pending_units_and_does_not_overwrite(self):
        ledger = build(self.run, "original_research")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["rule_id"], "abstract")
        self.assertEqual(ledger[0]["status"], "pending")
        self.assertEqual(json.loads((self.run / "coverage-ledger.json").read_text(encoding="utf-8")), ledger)
        with self.assertRaises(FileExistsError):
            build(self.run, "original_research")

    def test_rejects_changed_supplement_before_building(self):
        (self.run / "supplementary" / "supp-1.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            build(self.run, "original_research")
        self.assertFalse((self.run / "coverage-ledger.json").exists())

    def test_rejects_unbound_profile_source(self):
        self.profile["sources"][next(iter(self.profile["sources"]))]["url"] = "https://example.org/other"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "sources differ"):
            build(self.run, "original_research")

    def test_builds_explicit_section_review_matrix(self):
        ledger = build(self.run, "original_research", ["Introduction"], ["methods", "claim_evidence"])
        self.assertEqual([(item["kind"], item.get("review_class"), item["section"]) for item in ledger],
                         [("compliance", None, "all-supplied-files"),
                          ("review", "methods", "Introduction"),
                          ("review", "claim_evidence", "Introduction")])
        self.assertEqual(len({item["unit_id"] for item in ledger}), 3)

    def test_review_sections_require_unique_existing_heading(self):
        with self.assertRaisesRegex(ValueError, "unique Markdown heading"):
            build(self.run, "original_research", ["Missing"], ["methods"])
        self.assertFalse((self.run / "coverage-ledger.json").exists())


if __name__ == "__main__":
    unittest.main()
