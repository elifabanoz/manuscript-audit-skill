"""Private, exact-string preprint leakage review checks."""

import json
import tempfile
import unittest
from pathlib import Path

from prepare_leakage_review import prepare


class LeakageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="leakage-review-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preprint = self.root / "masked.md"
        self.output = self.root / "leakage-review.json"
        self.preprint.write_text("# Synthetic study\nAccepted in Sample Journal.\n"
                                 "DOI: 10.1234/example\nISSN 2475-9066\n"
                                 "Internal W123 record\nPrivate body text stays here.\n",
                                 encoding="utf-8")

    def test_flags_exact_clues_without_copying_preprint_text(self):
        path = prepare(self.preprint, "W123", "2475-9066", ["Sample Journal"], self.output,
                       "10.1234/example")
        packet = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_human_review")
        self.assertEqual(packet["signals"]["venue_name_1"]["line_numbers"], [2])
        self.assertEqual(packet["signals"]["target_doi"]["line_numbers"], [3])
        self.assertEqual(packet["signals"]["published_issn"]["line_numbers"], [4])
        self.assertEqual(packet["signals"]["target_work_id"]["line_numbers"], [5])
        self.assertNotIn("Private body text", path.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            prepare(self.preprint, "W123", "2475-9066", ["Sample Journal"], self.output)

    def test_zero_hits_remain_pending(self):
        self.preprint.write_text("# Synthetic study\n", encoding="utf-8")
        packet = json.loads(prepare(self.preprint, "W123", "2475-9066", ["Sample Journal"],
                                    self.output).read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_human_review")
        self.assertTrue(all(item["line_count"] == 0 for item in packet["signals"].values()))

    def test_rejects_invalid_targets_and_repository_output(self):
        with self.assertRaisesRegex(ValueError, "OpenAlex"):
            prepare(self.preprint, "W0", "2475-9066", ["Sample Journal"], self.output)
        with self.assertRaisesRegex(ValueError, "journal names"):
            prepare(self.preprint, "W123", "2475-9066", ["X"], self.output)
        repo_output = Path(__file__).resolve().parent / "leakage-review.json"
        with self.assertRaisesRegex(ValueError, "outside Git"):
            prepare(self.preprint, "W123", "2475-9066", ["Sample Journal"], repo_output)


if __name__ == "__main__":
    unittest.main()
