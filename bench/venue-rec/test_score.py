"""Synthetic, private inputs for the venue recommendation scorer."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score import private_file, score_manifest


class VenueScoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="venue-score-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preprint = self.root / "masked-preprint.md"
        self.preprint.write_text("# Masked preprint\n", encoding="utf-8")
        self.discovery = self.root / "candidates.json"
        self.discovery.write_text(json.dumps({
            "schema_version": 4, "query": "invented topic",
            "candidates": [
                {"source_id": "S1", "issns": ["0010-485X"], "evidence": ["openalex_grouped_works"]},
                {"source_id": "S2", "issns": ["2475-9066"], "evidence": ["citation_graph"]},
                {"source_id": "S3", "issns": ["1532-4435"], "evidence": ["doaj_subject"]},
            ],
        }), encoding="utf-8")
        self.baseline = self.root / "embedding.json"
        self.baseline.write_text(json.dumps({
            "schema_version": 1, "method": "embedding_only",
            "preprint_sha256": self.hash(self.preprint),
            "discovery_record_sha256": self.hash(self.discovery),
            "ranked_keys": ["S3", "S1", "S2"],
        }), encoding="utf-8")
        self.manifest = self.root / "cases.json"
        self.save_manifest()

    @staticmethod
    def hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps({
            "schema_version": 1, "data_freeze": "2026-11-13",
            "cases": [{"case_id": "paper-1", "field": "Computer Science",
                       "published_issn": "2475-9066", "preprint_file": str(self.preprint),
                       "discovery_record": str(self.discovery),
                       "embedding_baseline": str(self.baseline)}],
        }), encoding="utf-8")

    def test_scores_known_published_venue_without_releasing_preprint(self):
        result = score_manifest(self.manifest)
        self.assertEqual(result["status"], "provisional_unreviewed")
        self.assertEqual(result["cases"][0]["discovery_rank"], 2)
        self.assertEqual(result["cases"][0]["embedding_baseline_rank"], 3)
        self.assertEqual(result["overall"]["discovery"]["hit_at_1"], 0)
        self.assertEqual(result["overall"]["discovery"]["hit_at_5"], 1)
        self.assertEqual(result["overall"]["discovery"]["mrr"], 0.5)
        self.assertEqual(result["by_field"]["Computer Science"]["embedding_baseline"]["mrr"], 1 / 3)
        self.assertNotIn("Masked preprint", json.dumps(result))
        self.assertEqual(result["benchmark_manifest_sha256"], self.hash(self.manifest))
        script = Path(__file__).with_name("score.py")
        completed = subprocess.run([sys.executable, str(script), str(self.manifest)],
                                   capture_output=True, text=True, check=True)
        saved = json.loads(Path(completed.stdout.strip()).read_text(encoding="utf-8"))
        self.assertEqual(saved, result)
        self.assertNotEqual(subprocess.run([sys.executable, str(script), str(self.manifest)],
                                           capture_output=True).returncode, 0)

    def test_rejects_stale_or_different_baseline_pool(self):
        baseline = json.loads(self.baseline.read_text(encoding="utf-8"))
        baseline["ranked_keys"] = ["S1", "S1", "S2"]
        self.baseline.write_text(json.dumps(baseline), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pinned candidate pool"):
            score_manifest(self.manifest)
        baseline["ranked_keys"] = ["S1", "S2", "S3"]
        baseline["preprint_sha256"] = "0" * 64
        self.baseline.write_text(json.dumps(baseline), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pinned candidate pool"):
            score_manifest(self.manifest)

    def test_rejects_ambiguous_journal_identity_and_git_inputs(self):
        discovery = json.loads(self.discovery.read_text(encoding="utf-8"))
        discovery["candidates"][2]["issns"] = ["2475-9066"]
        self.discovery.write_text(json.dumps(discovery), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ISSNs overlap"):
            score_manifest(self.manifest)
        repository_readme = Path(__file__).resolve().parents[2] / "README.md"
        with self.assertRaisesRegex(ValueError, "outside Git"):
            private_file(repository_readme)

    def test_rejects_duplicate_preprint_as_a_second_case(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        second = {**manifest["cases"][0], "case_id": "paper-2", "field": "Physics"}
        manifest["cases"].append(second)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate preprint"):
            score_manifest(self.manifest)

    def test_reports_missing_published_venue_separately_by_field(self):
        other_preprint = self.root / "other-preprint.md"
        other_preprint.write_text("# Another masked preprint\n", encoding="utf-8")
        other_baseline = self.root / "other-embedding.json"
        baseline = json.loads(self.baseline.read_text(encoding="utf-8"))
        baseline["preprint_sha256"] = self.hash(other_preprint)
        other_baseline.write_text(json.dumps(baseline), encoding="utf-8")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["cases"].append({"case_id": "paper-2", "field": "Physics",
                                  "published_issn": "0000-0000",
                                  "preprint_file": str(other_preprint),
                                  "discovery_record": str(self.discovery),
                                  "embedding_baseline": str(other_baseline)})
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        result = score_manifest(self.manifest)
        self.assertIsNone(result["cases"][1]["discovery_rank"])
        self.assertEqual(result["overall"]["discovery"]["hit_at_5"], 0.5)
        self.assertEqual(result["overall"]["discovery"]["mrr"], 0.25)
        self.assertEqual(result["by_field"]["Physics"]["discovery"]["hit_at_10"], 0)


if __name__ == "__main__":
    unittest.main()
