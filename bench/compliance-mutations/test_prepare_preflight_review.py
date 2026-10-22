"""Preflight packets use invented manuscripts and private directories only."""

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from propose_text_mutation import propose
from prepare_preflight_review import prepare
from prepare_run import prepare as prepare_run


class PreflightReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="preflight-review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manuscript = self.root / "paper.md"
        guideline = self.root / "guideline.txt"
        manuscript.write_text("# Summary\nThe tool helps students learn safely.\n# References\nPublic source.\n",
                              encoding="utf-8")
        guideline.write_text("Explain who can use the tool.\nInclude References.\n",
                             encoding="utf-8")
        self.run = prepare_run(argparse.Namespace(
            manuscript=manuscript, guideline_text=guideline, supplementary=[],
            guideline_url="https://example.org/authors", retrieved_at="2026-10-19T09:00:00Z",
            output_root=self.root / "runs", host="manual", model=[]))
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        source_ref = next(iter(manifest["sources"]))
        profile = {"issn": "1234-567X", "title": "Example Journal",
                   "publisher": "Example Publisher", "article_types": ["article"],
                   "sources": manifest["sources"], "rules": [
                       {"rule_id": "audience", "kind": "semantic", "status": "verified",
                        "requirement": "Explain the audience for the tool.",
                        "quote": "Explain who can use the tool.", "source_ref": source_ref},
                       {"rule_id": "references", "kind": "deterministic", "status": "verified",
                        "check": {"metric": "heading_count", "heading": "References", "op": ">=", "value": 1},
                        "quote": "Include References.", "source_ref": source_ref}]}
        (self.run / "journal-profile.json").write_text(json.dumps(profile), encoding="utf-8")
        recipe = {"schema_version": 1, "article_type": "article", "rule_id": "audience",
                  "mutation_class": "semantic", "original": "The tool helps students learn safely.",
                  "replacement": "The tool exists."}
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
        self.proposal = propose(self.run, recipe_path, self.root / "proposals")

    def test_packet_precedes_agent_records_and_scored_cases(self):
        self.assertFalse((self.run / "findings.json").exists())
        self.assertFalse((self.run / "coverage-ledger.json").exists())
        packet = prepare(self.run, "article", [self.proposal])
        self.assertIn("Reviewer 1 verdict", packet)
        self.assertIn("Reviewer 2 verdict", packet)
        self.assertIn("Adjudicated decision", packet)
        self.assertIn("Explain who can use the tool", packet)
        self.assertIn(hashlib.sha256((self.run / "run-manifest.json").read_bytes()).hexdigest(), packet)
        self.assertNotIn("The tool helps students learn safely.", packet)
        self.assertNotIn("benchmark result: 1", packet)

    def test_changed_proposal_or_source_is_rejected(self):
        record = json.loads(self.proposal.read_text(encoding="utf-8"))
        (self.proposal.parent / record["file"]).write_text("Altered mutation", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "proposal manuscript differs"):
            prepare(self.run, "article", [self.proposal])
        (self.proposal.parent / record["file"]).write_text(
            "# Summary\nThe tool exists.\n# References\nPublic source.\n", encoding="utf-8")
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        source = next(iter(manifest["sources"].values()))
        (self.run / source["path"]).write_text("Changed source", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source-backed profile"):
            prepare(self.run, "article", [self.proposal])

    def test_unreviewed_control_without_proposals_is_allowed(self):
        packet = prepare(self.run, "article", [])
        self.assertIn("No proposals supplied", packet)
        with self.assertRaisesRegex(ValueError, "Article type is absent"):
            prepare(self.run, "different", [])


if __name__ == "__main__":
    unittest.main()
