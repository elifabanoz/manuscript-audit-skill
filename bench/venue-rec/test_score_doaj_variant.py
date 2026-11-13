"""Synthetic checks for pinned DOAJ-augmented venue comparisons."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score_doaj_variant import score_manifest


class DoajVariantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="doaj-variant-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preprint = self.root / "masked.md"
        self.preprint.write_text("# Private preprint text\n", encoding="utf-8")
        self.grouped = self.root / "grouped.json"
        self.doaj = self.root / "doaj.json"
        shared = {"schema_version": 4, "strategy": "grouped", "query": "invented topic",
                  "field_id": 17, "published_after": "2024-01-01", "excluded_work_id": "W9",
                  "openalex_work_filter": "type:article,ids.openalex:!W9,primary_location.source.type:journal",
                  "group_page_sha256": ["a" * 64], "source_response_sha256": ["b" * 64],
                  "group_pages_seen": 1, "group_pages_complete": False,
                  "matching_works_total": 30, "candidate_limit": 50}
        self.base_record = {**shared, "candidates": [
            {"source_id": "S1", "issns": ["0010-485X"], "evidence": ["openalex_grouped_works"]},
            {"source_id": "S2", "issns": ["2475-9066"], "evidence": ["openalex_grouped_works"]}]}
        self.doaj_record = {**shared, "doaj_subject": "Computer Science", "doaj_response_sha256": "c" * 64,
                            "candidates": [
                                {"source_id": "S2", "issns": ["2475-9066"], "doaj_id": "doaj-2",
                                 "evidence": ["openalex_grouped_works", "doaj_subject"]},
                                {"source_id": "S1", "issns": ["0010-485X"],
                                 "evidence": ["openalex_grouped_works"]},
                                {"source_id": None, "issn_l": None, "issns": ["1532-4435"],
                                 "doaj_id": "doaj-3", "evidence": ["doaj_subject"]}]}
        self.write_records()
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "data_freeze": "2026-11-13",
                                             "cases": [{"case_id": "paper-1", "field": "Computer Science",
                                                        "published_issn": "2475-9066",
                                                        "target_work_id": "W9",
                                                        "preprint_file": str(self.preprint),
                                                        "grouped_record": str(self.grouped),
                                                        "doaj_record": str(self.doaj)}]}),
                                 encoding="utf-8")

    def write_records(self):
        self.grouped.write_text(json.dumps(self.base_record), encoding="utf-8")
        self.doaj.write_text(json.dumps(self.doaj_record), encoding="utf-8")

    def test_scores_paired_shortlists_without_releasing_preprint(self):
        result = score_manifest(self.manifest)
        self.assertEqual(result["status"], "provisional_unreviewed")
        self.assertEqual(result["cases"][0]["grouped_rank"], 2)
        self.assertEqual(result["cases"][0]["doaj_augmented_rank"], 1)
        self.assertEqual(result["overall"]["grouped"]["mrr"], 0.5)
        self.assertEqual(result["by_field"]["Computer Science"]["doaj_augmented"]["hit_at_1"], 1)
        self.assertNotIn("Private preprint text", json.dumps(result))
        script = Path(__file__).with_name("score_doaj_variant.py")
        completed = subprocess.run([sys.executable, str(script), str(self.manifest)],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(Path(completed.stdout.strip()).read_text(encoding="utf-8")), result)
        self.assertNotEqual(subprocess.run([sys.executable, str(script), str(self.manifest)],
                                           capture_output=True).returncode, 0)

    def test_rejects_changed_grouped_snapshot(self):
        self.doaj_record["group_page_sha256"] = ["0" * 64]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "different OpenAlex snapshots"):
            score_manifest(self.manifest)

    def test_rejects_missing_doaj_identity_or_hash(self):
        self.doaj_record["candidates"][0].pop("doaj_id")
        self.write_records()
        with self.assertRaisesRegex(ValueError, "unsupported evidence"):
            score_manifest(self.manifest)
        self.doaj_record["candidates"][0]["doaj_id"] = "doaj-2"
        self.doaj_record["doaj_response_sha256"] = None
        self.write_records()
        with self.assertRaisesRegex(ValueError, "pinned subject"):
            score_manifest(self.manifest)

    def test_does_not_accept_unpinned_openalex_candidate(self):
        self.doaj_record["candidates"][2]["evidence"].append("openalex_grouped_works")
        self.write_records()
        with self.assertRaisesRegex(ValueError, "unsupported evidence"):
            score_manifest(self.manifest)

    def test_rejects_duplicate_doaj_journal_identity(self):
        self.doaj_record["candidates"][2]["doaj_id"] = "doaj-2"
        self.write_records()
        with self.assertRaisesRegex(ValueError, "repeats a journal identifier"):
            score_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
