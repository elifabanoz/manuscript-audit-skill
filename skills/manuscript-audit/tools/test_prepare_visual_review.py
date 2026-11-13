"""Private visual packets pin invented markers without asserting visual defects."""

import argparse
import base64
import json
import tempfile
import unittest
from pathlib import Path

from prepare_run import prepare as prepare_run
from prepare_visual_review import check, prepare


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="visual-review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manuscript = self.root / "paper.md"
        manuscript.write_text(
            "# Results\n![plot](plot.png)\nTable 1: Summary\n| A | B |\n"
            "| --- | --- |\n| 1 | 2 |\n```md\n![fake](fake.png)\n```\n", encoding="utf-8")
        guideline = self.root / "guideline.txt"
        guideline.write_text("List figures and tables.\n", encoding="utf-8")
        self.run = prepare_run(argparse.Namespace(
            manuscript=manuscript, extraction_record=None, source_record=[],
            guideline_text=guideline, guideline_url="https://journal.example/authors",
            retrieved_at="2026-10-19T09:00:00Z", supplementary=[],
            output_root=self.root / "runs", host="manual", model=[], data_freeze=None))
        self.image = self.root / "plot.png"
        self.image.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/gN8AAAAASUVORK5CYII="))
        self.recipe = {"schema_version": 1, "items": [
            {"item_id": "V-1", "kind": "figure", "path": "manuscript.md", "line_id": "L2",
             "quote": "![plot](plot.png)", "asset_file": str(self.image)},
            {"item_id": "V-2", "kind": "table", "path": "manuscript.md",
             "start_line": "L3", "end_line": "L6", "quote": "Table 1: Summary"}]}
        self.recipe_file = self.root / "visual-recipe.json"
        self.write_recipe()

    def write_recipe(self):
        self.recipe_file.write_text(json.dumps(self.recipe), encoding="utf-8")

    def test_packet_rechecks_private_assets_and_table_span(self):
        output = self.root / "visual-packet.json"
        self.assertEqual(prepare(self.run, self.recipe_file, output), output)
        packet = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_human_visual_review")
        self.assertEqual([item["kind"] for item in packet["items"]], ["figure", "table"])
        self.assertNotIn("defect", packet["items"][0])
        self.assertEqual(check(output)["status"], "private_inputs_current")
        self.image.write_bytes(self.image.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            check(output)

    def test_fenced_marker_and_unsupported_asset_are_rejected(self):
        self.recipe["items"][0]["line_id"] = "L8"
        self.recipe["items"][0]["quote"] = "![fake](fake.png)"
        self.write_recipe()
        with self.assertRaisesRegex(ValueError, "not an observed Markdown image marker"):
            prepare(self.run, self.recipe_file, self.root / "fenced.json")
        self.recipe["items"][0]["line_id"] = "L2"
        self.recipe["items"][0]["quote"] = "![plot](plot.png)"
        self.image.write_bytes(b"not really a PNG")
        self.write_recipe()
        with self.assertRaisesRegex(ValueError, "supported image signature"):
            prepare(self.run, self.recipe_file, self.root / "invalid-image.json")


if __name__ == "__main__":
    unittest.main()
