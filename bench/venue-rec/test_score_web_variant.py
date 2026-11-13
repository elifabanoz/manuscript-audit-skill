"""Synthetic checks for paired web-augmented venue comparison."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score_web_variant import score_manifest


class WebVariantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="web-variant-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.preprint = self.root / "masked.md"
        self.preprint.write_text("# Private preprint text\n", encoding="utf-8")
        self.grouped = self.root / "grouped.json"
        self.web = self.root / "web.json"
        self.web_input = self.root / "web-input.json"
        self.web_input.write_text(json.dumps({"schema_version": 1, "candidates": [
            {"issn": "2475-9066", "result_url": "https://search.example/result"}]}), encoding="utf-8")
        self.input_hash = hashlib.sha256(self.web_input.read_bytes()).hexdigest()
        shared = {"schema_version": 4, "strategy": "grouped", "query": "invented topic",
                  "field_id": 17, "published_after": "2024-01-01", "excluded_work_id": "W9",
                  "openalex_work_filter": "type:article,ids.openalex:!W9,primary_location.source.type:journal",
                  "group_page_sha256": ["a" * 64], "source_response_sha256": ["b" * 64],
                  "group_pages_seen": 1, "group_pages_complete": False,
                  "matching_works_total": 30, "candidate_limit": 50}
        self.base_record = {**shared, "candidates": [
            {"source_id": "S1", "issns": ["0010-485X"], "evidence": ["openalex_grouped_works"]},
            {"source_id": "S2", "issns": ["2475-9066"], "evidence": ["openalex_grouped_works"]}]}
        self.web_record = {**shared, "web_input_sha256": self.input_hash,
                           "web_submitted_count": 1, "web_verified_count": 1,
                           "web_registry_checks": [{"issn": "2475-9066", "registry_checks": [
                               {"provider": "OpenAlex", "status": "matched", "response_sha256": "c" * 64}]}],
                           "candidates": [
                               {"source_id": "S2", "issns": ["2475-9066"],
                                "evidence": ["openalex_grouped_works", "web_registry_verified"],
                                "web_results_unverified": ["https://search.example/result"],
                                "web_registry_matches": [{"issn": "2475-9066", "provider": "OpenAlex",
                                                          "response_sha256": "c" * 64}]},
                               {"source_id": "S1", "issns": ["0010-485X"],
                                "evidence": ["openalex_grouped_works"]}]}
        self.write_records()
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "data_freeze": "2026-11-13",
                                             "cases": [{"case_id": "paper-1", "field": "Computer Science",
                                                        "published_issn": "2475-9066", "target_work_id": "W9",
                                                        "preprint_file": str(self.preprint),
                                                        "grouped_record": str(self.grouped),
                                                        "web_input_file": str(self.web_input),
                                                        "web_record": str(self.web)}]}), encoding="utf-8")

    def write_records(self):
        self.grouped.write_text(json.dumps(self.base_record), encoding="utf-8")
        self.web.write_text(json.dumps(self.web_record), encoding="utf-8")

    def test_scores_paired_shortlists_privately(self):
        result = score_manifest(self.manifest)
        self.assertEqual(result["status"], "provisional_unreviewed")
        self.assertEqual(result["cases"][0]["grouped_rank"], 2)
        self.assertEqual(result["cases"][0]["web_augmented_rank"], 1)
        self.assertNotIn("Private preprint text", json.dumps(result))
        script = Path(__file__).with_name("score_web_variant.py")
        completed = subprocess.run([sys.executable, str(script), str(self.manifest)],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(Path(completed.stdout.strip()).read_text(encoding="utf-8")), result)
        self.assertNotEqual(subprocess.run([sys.executable, str(script), str(self.manifest)],
                                           capture_output=True).returncode, 0)

    def test_rejects_input_or_registry_drift(self):
        self.web_record["web_input_sha256"] = "0" * 64
        self.write_records()
        with self.assertRaisesRegex(ValueError, "pinned input"):
            score_manifest(self.manifest)
        self.web_record["web_input_sha256"] = self.input_hash
        self.web_record["web_registry_checks"][0]["registry_checks"][0]["response_sha256"] = "0" * 64
        self.write_records()
        with self.assertRaisesRegex(ValueError, "not pinned"):
            score_manifest(self.manifest)

    def test_rejects_changed_grouped_snapshot_and_fake_evidence(self):
        self.web_record["source_response_sha256"] = ["0" * 64]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "different OpenAlex snapshots"):
            score_manifest(self.manifest)
        self.web_record["source_response_sha256"] = ["b" * 64]
        self.web_record["candidates"][1]["evidence"] = ["openalex_grouped_works", "web_registry_verified"]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "incomplete registry evidence"):
            score_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
