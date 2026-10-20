"""Reference checks use invented bibliography entries and no network."""

import argparse
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from check_dois import check
from check_reference_metadata import compare
from list_dois import inventory
from prepare_run import prepare


class ReferenceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-reference-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        manuscript = self.root / "paper.md"
        manuscript.write_text(
            "# References\nLovelace, A. (2024). Invented title. doi:10.1234/alpha\n"
            "Turing, A. (2020). Another title. doi:10.5678/beta\n", encoding="utf-8")
        guideline = self.root / "guideline.txt"
        guideline.write_text("List references.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript, extraction_record=None, source_record=[],
            guideline_text=guideline, guideline_url="https://journal.example/authors",
            retrieved_at="2026-10-19T09:00:00Z", supplementary=[],
            output_root=self.root / "runs", host="manual", model=[], data_freeze=None))
        inventory(self.run)

        def fake_open(request, timeout):
            self.assertEqual(timeout, 10)
            if request.full_url.endswith("/alpha"):
                message = {"DOI": "10.1234/alpha", "title": ["Invented title"],
                           "published": {"date-parts": [[2024, 1]]},
                           "author": [{"family": "Lovelace"}]}
            else:
                message = {"DOI": "10.5678/beta", "title": ["Different title"],
                           "published": {"date-parts": [[2021, 1]]},
                           "author": [{"family": "Other"}]}
            return BytesIO(json.dumps({"message": message}).encode("utf-8"))

        check(self.run, allow_network=True, opener=fake_open, pause=lambda _seconds: None)
        self.cases = self.root / "reference-cases.json"
        self.record = {"schema_version": 1, "references": [
            {"doi": "10.1234/alpha", "path": "manuscript.md", "start_line": 2, "end_line": 2,
             "stated_title": "Invented title", "stated_year": 2024, "first_author_family": "Lovelace"},
            {"doi": "10.5678/beta", "path": "manuscript.md", "start_line": 3, "end_line": 3,
             "stated_title": "Another title", "stated_year": 2020, "first_author_family": "Turing"}]}
        self.cases.write_text(json.dumps(self.record), encoding="utf-8")

    def test_compares_only_selected_reference_spans_as_provisional_leads(self):
        result = compare(self.run, self.cases)
        self.assertEqual([item["status"] for item in result["results"]],
                         ["metadata_consistent", "needs_review"])
        self.assertEqual(result["results"][0]["checks"],
                         {"title": "same", "year": "same", "first_author_family": "same"})
        self.assertEqual(result["results"][1]["checks"],
                         {"title": "review", "year": "review", "first_author_family": "review"})
        saved = (self.run / "reference-metadata-checks.json").read_text(encoding="utf-8")
        self.assertNotIn("Lovelace, A.", saved)
        with self.assertRaises(FileExistsError):
            compare(self.run, self.cases)

    def test_rejects_unbacked_case_and_stale_registry_metadata(self):
        self.record["references"][0]["stated_title"] = "Fabricated title"
        self.cases.write_text(json.dumps(self.record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Stated title is absent"):
            compare(self.run, self.cases)
        self.record["references"][0]["stated_title"] = "Invented title"
        self.cases.write_text(json.dumps(self.record), encoding="utf-8")
        metadata_path = self.run / "doi-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        original_inventory_hash = metadata["inventory_sha256"]
        metadata["inventory_sha256"] = "0" * 64
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            compare(self.run, self.cases)
        metadata["inventory_sha256"] = original_inventory_hash
        metadata["results"][0]["registered_doi"] = "10.1234/other"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "matching DOI provenance"):
            compare(self.run, self.cases)
        self.assertFalse((self.run / "reference-metadata-checks.json").exists())


if __name__ == "__main__":
    unittest.main()
