"""Synthetic checks for pinned citation-augmented venue comparisons."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score_citation_variant import score_manifest


class CitationVariantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="citation-variant-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preprint = self.root / "masked.md"
        self.preprint.write_text("# Private preprint text\n", encoding="utf-8")
        self.grouped = self.root / "grouped.json"
        self.citation = self.root / "citation.json"
        self.combined = self.root / "combined.json"
        shared = {"schema_version": 4, "strategy": "grouped", "query": "invented topic",
                  "field_id": 17, "published_after": "2024-01-01", "excluded_work_id": "W9",
                  "openalex_work_filter": "type:article,ids.openalex:!W9,primary_location.source.type:journal",
                  "group_page_sha256": ["a" * 64], "source_response_sha256": ["b" * 64],
                  "group_pages_seen": 1, "group_pages_complete": False,
                  "matching_works_total": 30, "candidate_limit": 50}
        self.base_record = {**shared, "candidates": [
            {"source_id": "S1", "issns": ["0010-485X"], "evidence": ["openalex_grouped_works"]},
            {"source_id": "S2", "issns": ["2475-9066"], "evidence": ["openalex_grouped_works"]}]}
        self.citation_record = {"schema_version": 1, "provider": "OpenAlex",
                                "excluded_work_id": "W9", "citing_filter":
                                "cites:W1,type:article,ids.openalex:!W9",
                                "candidates": [
                                    {"source_id": "S2", "issns": ["2475-9066"]},
                                    {"source_id": "S3", "issns": ["1532-4435"]}]}
        self.combined_record = {**shared, "citation_excluded_work_id": "W9", "candidates": [
            {"source_id": "S2", "issns": ["2475-9066"],
             "evidence": ["openalex_grouped_works", "citation_graph"]},
            {"source_id": "S1", "issns": ["0010-485X"], "evidence": ["openalex_grouped_works"]},
            {"source_id": "S3", "issns": ["1532-4435"], "evidence": ["citation_graph"]}]}
        self.write_records()
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "data_freeze": "2026-11-13",
                                             "cases": [{"case_id": "paper-1", "field": "Computer Science",
                                                        "published_issn": "2475-9066",
                                                        "target_work_id": "W9",
                                                        "preprint_file": str(self.preprint),
                                                        "grouped_record": str(self.grouped),
                                                        "citation_record": str(self.citation),
                                                        "combined_record": str(self.combined)}]}),
                                 encoding="utf-8")

    def write_records(self):
        self.grouped.write_text(json.dumps(self.base_record), encoding="utf-8")
        self.citation.write_text(json.dumps(self.citation_record), encoding="utf-8")
        self.combined_record["citation_record_sha256"] = hashlib.sha256(self.citation.read_bytes()).hexdigest()
        self.combined.write_text(json.dumps(self.combined_record), encoding="utf-8")

    def test_scores_paired_shortlists_without_releasing_preprint(self):
        result = score_manifest(self.manifest)
        self.assertEqual(result["status"], "provisional_unreviewed")
        self.assertEqual(result["cases"][0]["grouped_rank"], 2)
        self.assertEqual(result["cases"][0]["citation_augmented_rank"], 1)
        self.assertEqual(result["overall"]["grouped"]["mrr"], 0.5)
        self.assertEqual(result["by_field"]["Computer Science"]["citation_augmented"]["hit_at_1"], 1)
        self.assertNotIn("Private preprint text", json.dumps(result))
        script = Path(__file__).with_name("score_citation_variant.py")
        completed = subprocess.run([sys.executable, str(script), str(self.manifest)],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(Path(completed.stdout.strip()).read_text(encoding="utf-8")), result)
        self.assertNotEqual(subprocess.run([sys.executable, str(script), str(self.manifest)],
                                           capture_output=True).returncode, 0)

    def test_rejects_changed_grouped_snapshot(self):
        self.combined_record["group_page_sha256"] = ["c" * 64]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "different OpenAlex snapshots"):
            score_manifest(self.manifest)
        self.combined_record["group_page_sha256"] = ["a" * 64]
        self.combined_record["candidate_limit"] = 20
        self.write_records()
        with self.assertRaisesRegex(ValueError, "different OpenAlex snapshots"):
            score_manifest(self.manifest)

    def test_rejects_stale_citation_or_missing_exclusion(self):
        self.citation_record["excluded_work_id"] = "W8"
        self.write_records()
        with self.assertRaisesRegex(ValueError, "target exclusion"):
            score_manifest(self.manifest)
        self.citation_record["excluded_work_id"] = "W9"
        self.write_records()
        self.combined_record["citation_record_sha256"] = "0" * 64
        self.combined.write_text(json.dumps(self.combined_record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            score_manifest(self.manifest)

    def test_rejects_claimed_citation_evidence_absent_from_source(self):
        self.citation_record["candidates"] = [self.citation_record["candidates"][1]]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "evidence absent"):
            score_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
