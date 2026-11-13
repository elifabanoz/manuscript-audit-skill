"""Checks for the private journal candidate review packet."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_candidate_review import prepare


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="candidate-review-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.discovery = self.root / "candidates.json"
        self.record = {"schema_version": 4, "query": "invented topic", "retrieved_at": "2026-11-13T00:00:00Z",
                       "candidates": [
                           {"source_id": "https://openalex.org/S12", "display_name": "Example Journal",
                            "issn_l": "2475-9066", "issns": ["2475-9066"],
                            "evidence": ["openalex_grouped_works"], "matching_works": 20},
                           {"source_id": None, "display_name": "Another Journal",
                            "issn_l": None, "issns": ["0010-485X"],
                            "evidence": ["doaj_subject"], "web_results_unverified": ["https://example.org"]}]}
        self.discovery.write_text(json.dumps(self.record), encoding="utf-8")

    def test_packet_pins_discovery_and_marks_checks_pending(self):
        path = prepare(self.discovery)
        packet = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_independent_review")
        self.assertEqual(packet["discovery_record_sha256"], hashlib.sha256(self.discovery.read_bytes()).hexdigest())
        self.assertEqual([item["rank"] for item in packet["candidates"]], [1, 2])
        self.assertEqual(packet["candidates"][1]["issns"], ["0010-485X"])
        self.assertEqual(packet["candidates"][0]["scope_check"], "pending")
        self.assertNotIn("matching_works", path.read_text(encoding="utf-8"))
        self.assertNotIn("web_results_unverified", path.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            prepare(self.discovery)

    def test_rejects_ambiguous_identity_and_invalid_limit(self):
        self.record["candidates"][1]["issns"] = ["2475-9066"]
        self.discovery.write_text(json.dumps(self.record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            prepare(self.discovery)
        with self.assertRaisesRegex(ValueError, "--limit"):
            prepare(self.discovery, 0)

    def test_rejects_repository_input(self):
        repository_readme = Path(__file__).resolve().parents[3] / "README.md"
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            prepare(repository_readme)


if __name__ == "__main__":
    unittest.main()
