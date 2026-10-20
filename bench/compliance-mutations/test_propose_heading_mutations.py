"""Mutation proposals use invented manuscripts and stay outside Git."""

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from propose_heading_mutations import propose
from prepare_run import prepare


class HeadingProposalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="heading-proposals-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manuscript = self.root / "paper.md"
        guideline = self.root / "guideline.txt"
        manuscript.write_text("# Summary\nOne sentence.\n# References\nA reference.\n", encoding="utf-8")
        guideline.write_text("Include Summary.\nInclude References.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript, guideline_text=guideline, supplementary=[],
            guideline_url="https://example.org/authors", retrieved_at="2026-10-19T09:00:00Z",
            output_root=self.root / "runs", host="manual", model=[]))
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        source_ref = next(iter(manifest["sources"]))
        self.profile = {"issn": "1234-567X", "title": "Example Journal",
                        "publisher": "Example Publisher", "article_types": ["article"],
                        "sources": manifest["sources"], "rules": [
                            {"rule_id": "summary", "kind": "deterministic",
                             "check": {"metric": "heading_count", "heading": "Summary", "op": ">=", "value": 1},
                             "quote": "Include Summary.", "source_ref": source_ref},
                            {"rule_id": "references", "kind": "deterministic",
                             "check": {"metric": "heading_count", "heading": "References", "op": ">=", "value": 1},
                             "quote": "Include References.", "source_ref": source_ref}]}
        self.save_profile()

    def save_profile(self):
        (self.run / "journal-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def test_proposals_remove_only_one_marker_and_remain_unreviewed(self):
        path = propose(self.run, self.root / "proposals", "article")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "unreviewed_proposals")
        self.assertEqual(len(record["mutations"]), 2)
        self.assertFalse((self.run / "deterministic-checks.json").exists())
        control = (self.run / "manuscript.md").read_bytes()
        expected = (control.replace(b"# Summary", b"Summary", 1),
                    control.replace(b"# References", b"References", 1))
        for item, content in zip(record["mutations"], expected):
            actual = (path.parent / item["file"]).read_bytes()
            self.assertEqual(actual, content)
            self.assertEqual(item["manuscript_sha256"], hashlib.sha256(actual).hexdigest())
            self.assertEqual(item["mutation_class_proposed"], "structural")

    def test_ambiguous_target_and_repo_output_are_rejected(self):
        duplicate = dict(self.profile["rules"][0], rule_id="summary_also")
        self.profile["rules"].append(duplicate)
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "cannot support"):
            propose(self.run, self.root / "proposals", "article", ["summary"])
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            propose(self.run, Path(__file__).parent, "article", ["references"])

    def test_control_with_unmet_verified_rule_cannot_be_proposed(self):
        self.profile["rules"][1]["check"]["heading"] = "Missing section"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "Control has unmet"):
            propose(self.run, self.root / "proposals", "article", ["summary"])


if __name__ == "__main__":
    unittest.main()
