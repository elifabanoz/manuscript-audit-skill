"""Citation claim packets bind invented private quotes without deciding support."""

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_citation_claim import prepare
from prepare_run import prepare as prepare_run


class CitationClaimPacketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="citation-claim-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manuscript = self.root / "paper.md"
        manuscript.write_text(
            "# Introduction\nThe cited study proves every case improves [1].\n"
            "# References\n[1] Invented study. doi:10.1234/alpha\n", encoding="utf-8")
        guideline = self.root / "guideline.txt"
        guideline.write_text("List references.\n", encoding="utf-8")
        self.run = prepare_run(argparse.Namespace(
            manuscript=manuscript, extraction_record=None, source_record=[],
            guideline_text=guideline, guideline_url="https://journal.example/authors",
            retrieved_at="2026-10-19T09:00:00Z", supplementary=[],
            output_root=self.root / "runs", host="manual", model=[], data_freeze=None))
        self.source = self.root / "source.txt"
        self.source.write_text("The sample included only six cases.\n", encoding="utf-8")
        self.recipe_file = self.root / "recipe.json"
        self.recipe = {"schema_version": 1, "cited_doi": "10.1234/alpha",
                       "claim": {"path": "manuscript.md", "line_id": "L2",
                                 "quote": "The cited study proves every case improves [1]."},
                       "source": {"file": str(self.source), "quote": "The sample included only six cases."}}
        self.write_recipe()

    def write_recipe(self):
        self.recipe_file.write_text(json.dumps(self.recipe), encoding="utf-8")

    def test_prepares_private_packet_without_a_semantic_verdict(self):
        output = self.root / "packet.json"
        self.assertEqual(prepare(self.run, self.recipe_file, output), output)
        packet = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_human_citation_review")
        self.assertEqual(packet["claim"]["line_id"], "L2")
        self.assertEqual(packet["source"]["file_sha256"], hashlib.sha256(
            self.source.read_bytes()).hexdigest())
        self.assertNotIn("supported", packet)
        with self.assertRaises(FileExistsError):
            prepare(self.run, self.recipe_file, output)

    def test_rejects_quote_mismatch_and_uninventoried_doi(self):
        self.recipe["source"]["quote"] = "The sample included twelve cases."
        self.write_recipe()
        with self.assertRaisesRegex(ValueError, "Cited source quote"):
            prepare(self.run, self.recipe_file, self.root / "missing-source.json")
        self.recipe["source"]["quote"] = "The sample included only six cases."
        self.recipe["cited_doi"] = "10.1234/not-in-manuscript"
        self.write_recipe()
        with self.assertRaisesRegex(ValueError, "absent from the supplied manuscript"):
            prepare(self.run, self.recipe_file, self.root / "missing-doi.json")

    def test_rejects_manuscript_as_cited_source(self):
        self.recipe["source"] = {"file": str(self.run / "manuscript.md"),
                                 "quote": "The cited study proves every case improves [1]."}
        self.write_recipe()
        with self.assertRaisesRegex(ValueError, "cannot be the submitted manuscript"):
            prepare(self.run, self.recipe_file, self.root / "self-source.json")


if __name__ == "__main__":
    unittest.main()
