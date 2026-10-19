"""Deterministic checks use only invented manuscripts and guideline text."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from check_deterministic import check, measure_heading_count
from prepare_run import prepare


class DeterministicCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-check-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "example.md"
        guideline = root / "guideline.txt"
        manuscript.write_text(
            "# Abstract\nOne two three.\n# Keywords\nalpha, beta, gamma\n# Introduction\nAlpha beta.\n## Methods\nGamma delta.\n# References\nReference text.\n",
            encoding="utf-8",
        )
        guideline.write_text("Abstract limit: 2 words.\nMain text limit: 5 words.\nMaximum 2 keywords.\n", encoding="utf-8")
        args = argparse.Namespace(
            manuscript=manuscript,
            guideline_text=guideline,
            supplementary=[],
            guideline_url="https://example.org/authors",
            retrieved_at="2026-10-19T09:00:00Z",
            output_root=root / "output",
            host="manual",
            model=[],
        )
        self.run = prepare(args)
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        source_ref = next(iter(manifest["sources"]))
        self.profile = {
            "issn": "1234-567X",
            "title": "Example Journal",
            "publisher": "Example Publisher",
            "article_types": ["original_research"],
            "sources": manifest["sources"],
            "rules": [
                {"rule_id": "abstract_limit", "kind": "deterministic", "check": {"metric": "abstract_words", "op": "<=", "value": 2}, "quote": "Abstract limit: 2 words.", "source_ref": source_ref},
                {"rule_id": "main_limit", "kind": "deterministic", "check": {"metric": "main_text_words", "op": "<=", "value": 5}, "quote": "Main text limit: 5 words.", "source_ref": source_ref},
                {"rule_id": "keyword_limit", "kind": "deterministic", "check": {"metric": "keyword_count", "op": "<=", "value": 2}, "quote": "Maximum 2 keywords.", "source_ref": source_ref},
            ],
        }
        self.save_profile()

    def save_profile(self):
        (self.run / "journal-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def test_measures_explicit_sections_without_confirming_a_finding(self):
        output = check(self.run, "original_research", "Introduction", "References")
        self.assertEqual([item["status"] for item in output["checks"]], ["triggered", "met", "triggered"])
        self.assertEqual([item.get("measured_value") for item in output["checks"]], [3, 4, 3])
        self.assertEqual(json.loads((self.run / "deterministic-checks.json").read_text(encoding="utf-8")), output)

    def test_unknown_main_boundaries_remain_unresolved(self):
        output = check(self.run, "original_research")
        self.assertEqual(output["checks"][1]["status"], "unresolved")

    def test_heading_count_is_provisional_and_ignores_fenced_code(self):
        self.assertEqual(measure_heading_count("# Methods\n```md\n# Data Availability\n```\n", "Data Availability"), 0)
        self.assertEqual(measure_heading_count("# Methods\n~~~\n# Methods\n~~~\n", "Methods"), 1)
        source_ref = next(iter(self.profile["sources"]))
        self.profile["rules"].append({
            "rule_id": "abstract_heading",
            "kind": "deterministic",
            "check": {"metric": "heading_count", "heading": "Abstract", "op": ">=", "value": 1},
            "quote": "Abstract limit: 2 words.",
            "source_ref": source_ref,
        })
        self.save_profile()
        output = check(self.run, "original_research")
        self.assertEqual(output["checks"][-1]["status"], "met")
        self.assertEqual(output["checks"][-1]["measured_value"], 1)
        self.assertEqual(output["checks"][-1]["heading"], "Abstract")

    def test_markdown_link_needs_checked_extraction(self):
        manuscript = self.run / "manuscript.md"
        manuscript.write_text(manuscript.read_text(encoding="utf-8").replace("One two three.", "One [two](https://example.org) three."), encoding="utf-8")
        manifest_file = self.run / "run-manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        import hashlib
        manifest["manuscript"]["sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        output = check(self.run, "original_research", "Introduction", "References")
        self.assertEqual(output["checks"][0]["status"], "unresolved")

    def test_ambiguous_keyword_separator_remains_unresolved(self):
        manuscript = self.run / "manuscript.md"
        manuscript.write_text(manuscript.read_text(encoding="utf-8").replace("alpha, beta, gamma", "alpha; beta, gamma"), encoding="utf-8")
        manifest_file = self.run / "run-manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        import hashlib
        manifest["manuscript"]["sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        output = check(self.run, "original_research")
        self.assertEqual(output["checks"][2]["status"], "unresolved")

    def test_source_mismatch_and_tampering_are_rejected(self):
        self.profile["sources"][next(iter(self.profile["sources"]))]["url"] = "https://example.org/other"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "sources differ"):
            check(self.run, "original_research")
        self.profile["sources"][next(iter(self.profile["sources"]))]["url"] = "https://example.org/authors"
        self.save_profile()
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            check(self.run, "original_research")

    def test_article_type_must_be_in_profile(self):
        with self.assertRaisesRegex(ValueError, "absent from the journal profile"):
            check(self.run, "review")


if __name__ == "__main__":
    unittest.main()
