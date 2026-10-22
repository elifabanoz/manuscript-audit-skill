"""Single-span proposals use invented manuscripts and private directories."""

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from propose_text_mutation import propose
from prepare_run import prepare


class TextProposalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="text-proposal-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manuscript = self.root / "paper.md"
        guideline = self.root / "guideline.txt"
        manuscript.write_text(
            "---\ntitle: Invented paper\ndate: 01 December 2023\n---\n# Summary\n"
            "The tool helps students learn safely.\n"
            "# References\nA public reference.\n", encoding="utf-8")
        guideline.write_text("Explain who can use the tool.\nUse day month year.\nInclude References.\n",
                             encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript, guideline_text=guideline, supplementary=[],
            guideline_url="https://example.org/authors", retrieved_at="2026-10-19T09:00:00Z",
            output_root=self.root / "runs", host="manual", model=[]))
        manifest = json.loads((self.run / "run-manifest.json").read_text())
        source_ref = next(iter(manifest["sources"]))
        self.profile = {"issn": "1234-567X", "title": "Example Journal",
                        "publisher": "Example Publisher", "article_types": ["article"],
                        "sources": manifest["sources"], "rules": [
                            {"rule_id": "audience", "kind": "semantic", "status": "verified",
                             "requirement": "Explain the audience for the tool.",
                             "quote": "Explain who can use the tool.", "source_ref": source_ref},
                            {"rule_id": "metadata_date_format", "kind": "semantic", "status": "verified",
                             "requirement": "Use a written day month year date.",
                             "quote": "Use day month year.", "source_ref": source_ref},
                            {"rule_id": "references", "kind": "deterministic", "status": "verified",
                             "check": {"metric": "heading_count", "heading": "References", "op": ">=", "value": 1},
                             "quote": "Include References.", "source_ref": source_ref}]}
        self.save_profile()
        self.recipe = {"schema_version": 1, "article_type": "article", "rule_id": "audience",
                       "mutation_class": "semantic",
                       "original": "The tool helps students learn safely.",
                       "replacement": "The tool exists."}
        self.recipe_path = self.root / "recipe.json"
        self.save_recipe()

    def save_profile(self):
        (self.run / "journal-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def save_recipe(self):
        self.recipe_path.write_text(json.dumps(self.recipe), encoding="utf-8")

    def test_proposal_changes_one_line_without_claiming_a_label(self):
        path = propose(self.run, self.recipe_path, self.root / "proposals")
        record = json.loads(path.read_text(encoding="utf-8"))
        control = (self.run / "manuscript.md").read_bytes()
        mutation = (path.parent / record["file"]).read_bytes()
        self.assertEqual(mutation, control.replace(b"The tool helps students learn safely.",
                                                   b"The tool exists."))
        self.assertEqual(record["status"], "unreviewed_proposal")
        self.assertEqual(record["mutation_class_proposed"], "semantic")
        self.assertEqual(record["edit_line_id"], "L6")
        self.assertEqual(record["manuscript_sha256"], hashlib.sha256(mutation).hexdigest())
        self.assertFalse((self.run / "deterministic-checks.json").exists())

    def test_ambiguous_or_structural_edits_are_rejected(self):
        self.recipe["original"] = "The tool"
        self.recipe["replacement"] = "The application"
        (self.run / "manuscript.md").write_text(
            (self.run / "manuscript.md").read_text() + "The tool is also referenced here.\n",
            encoding="utf-8")
        manifest_path = self.run / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["manuscript"]["sha256"] = hashlib.sha256((self.run / "manuscript.md").read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.save_recipe()
        with self.assertRaisesRegex(ValueError, "exactly once"):
            propose(self.run, self.recipe_path, self.root / "proposals")
        self.recipe["original"] = "# Summary"
        self.recipe["replacement"] = "# Overview"
        self.save_recipe()
        with self.assertRaisesRegex(ValueError, "Markdown heading"):
            propose(self.run, self.recipe_path, self.root / "proposals")
        self.assertFalse((self.root / "proposals").exists())

    def test_unverified_rule_and_repository_paths_are_rejected(self):
        self.profile["rules"][0]["status"] = "needs_validation"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "not verified"):
            propose(self.run, self.recipe_path, self.root / "proposals")
        self.profile["rules"][0]["status"] = "verified"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            propose(self.run, self.recipe_path, Path(__file__).parent)
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            propose(self.run, Path(__file__), self.root / "proposals")

    def test_verified_date_format_can_change_only_the_date_value(self):
        self.recipe.update(rule_id="metadata_date_format", mutation_class="numerical_format",
                           original="01 December 2023", replacement="2023-12-01")
        self.save_recipe()
        path = propose(self.run, self.recipe_path, self.root / "proposals")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["mutation_class_proposed"], "numerical_format")
        self.assertEqual(record["edit_line_id"], "L3")
        self.assertIn("date: 2023-12-01", (path.parent / record["file"]).read_text())
        self.recipe["original"] = "date: 01 December 2023"
        self.save_recipe()
        with self.assertRaisesRegex(ValueError, "only the metadata date value"):
            propose(self.run, self.recipe_path, self.root / "proposals")

    def test_changed_control_and_unmet_deterministic_rule_are_rejected(self):
        self.profile["rules"][2]["check"]["heading"] = "Missing section"
        self.save_profile()
        with self.assertRaisesRegex(ValueError, "unmet or unresolved deterministic"):
            propose(self.run, self.recipe_path, self.root / "proposals")
        self.profile["rules"][2]["check"]["heading"] = "References"
        self.save_profile()
        (self.run / "manuscript.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            propose(self.run, self.recipe_path, self.root / "proposals")


if __name__ == "__main__":
    unittest.main()
