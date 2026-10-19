"""DOI inventory tests use invented manuscript text only."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from build_coverage import corpus_hash
from list_dois import inventory
from prepare_run import prepare


class DoiInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-doi-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        supplement = root / "appendix.md"
        guideline = root / "guideline.txt"
        manuscript.write_text(
            "# References\nFirst: https://doi.org/10.1234/Alpha.\n"
            "Second (10.5678/beta(1)). Duplicate 10.1234/alpha.\n",
            encoding="utf-8",
        )
        supplement.write_text("Supporting citation: doi:10.5678/beta(1).\n", encoding="utf-8")
        guideline.write_text("References are required.\n", encoding="utf-8")
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

    def test_lists_distinct_mentions_with_file_and_line_evidence(self):
        result = inventory(self.run)
        self.assertEqual([item["doi"] for item in result["dois"]], ["10.1234/Alpha", "10.5678/beta(1)"])
        self.assertEqual(result["dois"][0]["doi_url"], "https://doi.org/10.1234/Alpha")
        self.assertEqual(result["dois"][0]["occurrences"], [
            {"path": "manuscript.md", "line_id": "L2"},
            {"path": "manuscript.md", "line_id": "L3"},
        ])
        self.assertEqual(result["dois"][1]["occurrences"], [
            {"path": "manuscript.md", "line_id": "L3"},
            {"path": "supplementary/supp-1.md", "line_id": "L1"},
        ])
        manifest = json.loads((self.run / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(result["corpus_sha256"], corpus_hash(manifest))
        self.assertEqual(json.loads((self.run / "doi-inventory.json").read_text(encoding="utf-8")), result)
        with self.assertRaises(FileExistsError):
            inventory(self.run)

    def test_rejects_changed_manuscript_without_writing_output(self):
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            inventory(self.run)
        self.assertFalse((self.run / "doi-inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
