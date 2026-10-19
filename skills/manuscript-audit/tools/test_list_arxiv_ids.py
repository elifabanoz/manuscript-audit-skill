"""arXiv inventory tests use invented manuscript text only."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from list_arxiv_ids import inventory
from prepare_run import prepare


class ArxivInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-arxiv-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        supplement = root / "appendix.md"
        guideline = root / "guideline.txt"
        manuscript.write_text(
            "# References\narXiv:2404.01234v2 and arXiv:hep-ph/9912345.\n"
            "Again arxiv:2404.01234v2. Bad arXiv:2499.01234.\n",
            encoding="utf-8",
        )
        supplement.write_text("Related: arXiv:hep-ph/9912345.\n", encoding="utf-8")
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

    def test_inventories_modern_and_legacy_ids_with_locations(self):
        output = inventory(self.run)
        self.assertEqual([item["arxiv_id"] for item in output["arxiv_ids"]],
                         ["2404.01234v2", "hep-ph/9912345"])
        self.assertEqual(output["arxiv_ids"][1]["url"], "https://arxiv.org/abs/hep-ph/9912345")
        self.assertEqual(output["arxiv_ids"][1]["occurrences"], [
            {"path": "manuscript.md", "line_id": "L2"},
            {"path": "supplementary/supp-1.md", "line_id": "L1"},
        ])
        self.assertEqual(json.loads((self.run / "arxiv-inventory.json").read_text(encoding="utf-8")), output)
        with self.assertRaises(FileExistsError):
            inventory(self.run)

    def test_rejects_changed_input(self):
        (self.run / "supplementary" / "supp-1.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            inventory(self.run)
        self.assertFalse((self.run / "arxiv-inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
