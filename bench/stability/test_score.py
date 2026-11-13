"""Synthetic structural repeatability checks without agent runs or manuscripts."""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from score import score


class StabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="stability-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runs = [self.root / f"run-{number}" for number in range(1, 4)]
        self.manifest = {"created_at": "2026-11-13T13:00:00Z", "host": "manual", "models": [],
                         "skill_sha256": "a" * 64, "prompt_hashes": {"SKILL.md": "a" * 64},
                         "config_hash": None, "data_freeze": None, "cross_host": False,
                         "manuscript": {"path": "manuscript.md", "sha256": "b" * 64},
                         "supplementary": [], "sources": {"s": {"url": "https://journal.example/authors",
                                                              "retrieved_at": "2026-11-13T12:00:00Z",
                                                              "sha256": "c" * 64, "path": "sources/s.txt"}}}
        self.profile = {"issn": "1234-567X", "rules": [], "sources": self.manifest["sources"]}
        self.ledger = [{"unit_id": "U-1", "kind": "review", "section": "Results",
                        "review_class": "methods", "target_issn": "1234-567X",
                        "status": "covered", "source_hash": "d" * 64}]
        self.finding = {"id": "F-1", "verdict": "confirmed", "category": "review",
                        "target_issn": "1234-567X", "review_class": "methods",
                        "evidence": [{"type": "manuscript_quote", "path": "manuscript.md",
                                      "line_id": "L12", "quote": "Invented observation."}]}
        for index, run in enumerate(self.runs):
            run.mkdir()
            self.write(run, "run-manifest.json", self.manifest)
            self.write(run, "journal-profile.json", self.profile)
            self.write(run, "coverage-ledger.json", self.ledger)
            self.write(run, "findings.json", [self.finding] if index < 2 else [])
        self.suite = self.root / "suite.json"
        self.write(self.root, "suite.json", {"schema_version": 1,
                                              "run_directories": [str(run) for run in self.runs]})

    @staticmethod
    def write(root, name, value):
        (root / name).write_text(json.dumps(value), encoding="utf-8")

    def checked_score(self):
        with patch("score.subprocess.run", return_value=SimpleNamespace(returncode=0)) as validator:
            result = score(self.suite)
        self.assertEqual(validator.call_count, 3)
        return result

    def test_counts_same_class_and_line_without_claiming_correctness(self):
        result = self.checked_score()
        self.assertEqual(result["minimum_runs_for_retention"], 2)
        self.assertEqual(result["anchored_union"], 1)
        self.assertEqual(result["anchors"][0]["present_runs"], 2)
        self.assertEqual(result["retained_anchor_count"], 1)
        self.assertNotIn("Invented observation", json.dumps(result))
        self.assertNotIn("accuracy", result)

    def test_rejects_scope_drift_and_incomplete_coverage(self):
        changed = dict(self.manifest)
        changed["models"] = ["other-model"]
        self.write(self.runs[1], "run-manifest.json", changed)
        with self.assertRaisesRegex(ValueError, "differ in inputs"):
            self.checked_score()
        self.write(self.runs[1], "run-manifest.json", self.manifest)
        pending = [dict(self.ledger[0], status="pending")]
        self.write(self.runs[1], "coverage-ledger.json", pending)
        with self.assertRaisesRegex(ValueError, "must be covered"):
            self.checked_score()

    def test_rejects_anchor_collision_and_reports_unanchored(self):
        self.write(self.runs[0], "findings.json", [self.finding, dict(self.finding, id="F-2")])
        with self.assertRaisesRegex(ValueError, "multiple confirmed findings"):
            self.checked_score()
        unanchored = dict(self.finding, evidence=[{"type": "manuscript_absence",
                                                   "searched": ["manuscript.md"], "patterns": ["x"]}])
        self.write(self.runs[0], "findings.json", [unanchored])
        result = self.checked_score()
        self.assertEqual(result["excluded_without_one_quote"], 1)


if __name__ == "__main__":
    unittest.main()
