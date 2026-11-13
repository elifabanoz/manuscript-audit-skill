"""Synthetic private label-contract checks; no model is trained."""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from labels import prepare, validate


class CalibrationLabelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="calibration-label-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run-1"
        self.run.mkdir()
        (self.run / "run-manifest.json").write_text(
            json.dumps({"created_at": "2026-11-13T12:00:00Z"}), encoding="utf-8")
        self.findings = [{"id": "F-1", "claim": "Private invented finding."},
                         {"id": "F-2", "claim": "Another private invented finding."}]
        self.write_findings()
        self.packet_file = self.root / "label-packet.json"
        self.labels_file = self.root / "labels.json"

    def write_findings(self):
        (self.run / "findings.json").write_text(json.dumps(self.findings), encoding="utf-8")

    def checked_prepare(self):
        with patch("labels.subprocess.run", return_value=SimpleNamespace(returncode=0)) as validator:
            result = prepare(self.run, self.packet_file)
        validator.assert_called_once()
        return result

    def checked_validate(self):
        with patch("labels.subprocess.run", return_value=SimpleNamespace(returncode=0)) as validator:
            result = validate(self.run, self.labels_file)
        validator.assert_called_once()
        return result

    def completed_labels(self):
        packet = json.loads(self.packet_file.read_text(encoding="utf-8"))
        labels = packet["label_template"]
        for index, reviewer in enumerate(labels["reviewers"]):
            reviewer["reviewer"] = f"Reviewer {index + 1}"
            reviewer["reviewed_at"] = "2026-11-13T13:00:00Z"
            reviewer["labels"] = [
                {"finding_id": "F-1", "assessment": "supported", "rationale": "Checked the private evidence."},
                {"finding_id": "F-2", "assessment": "unclear", "rationale": "Evidence remains incomplete."}]
        labels["reviewers"][1]["labels"][0]["assessment"] = "unsupported"
        labels["adjudication"] = {"adjudicator": "Third Reviewer",
                                  "reviewed_at": "2026-11-13T14:00:00Z",
                                  "labels": [
                                      {"finding_id": "F-1", "assessment": "supported",
                                       "rationale": "Adjudicated against the original."},
                                      {"finding_id": "F-2", "assessment": "unclear",
                                       "rationale": "No conclusive source evidence."}]}
        self.labels_file.write_text(json.dumps(labels), encoding="utf-8")
        return labels

    def test_requires_two_complete_reviews_and_adjudication_without_training(self):
        self.assertEqual(self.checked_prepare(), self.packet_file)
        packet = json.loads(self.packet_file.read_text(encoding="utf-8"))
        self.assertEqual(packet["finding_ids"], ["F-1", "F-2"])
        self.assertNotIn("Private invented finding", self.packet_file.read_text(encoding="utf-8"))
        self.labels_file.write_text(json.dumps(packet["label_template"]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.checked_validate()
        self.completed_labels()
        result = self.checked_validate()
        self.assertEqual(result["status"], "human_label_records_complete")
        self.assertEqual(result["reviewer_disagreements"], 1)
        self.assertEqual(result["adjudicated_counts"],
                         {"supported": 1, "unsupported": 0, "unclear": 1})
        self.assertNotIn("Private invented finding", json.dumps(result))
        self.assertNotIn("confidence_score", result)

    def test_rejects_stale_or_incomplete_labels(self):
        self.checked_prepare()
        labels = self.completed_labels()
        labels["reviewers"][1]["reviewer"] = "Reviewer 1"
        self.labels_file.write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "distinct names"):
            self.checked_validate()
        labels["reviewers"][1]["reviewer"] = "Reviewer 2"
        labels["reviewers"][0]["labels"].pop()
        self.labels_file.write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cover every finding"):
            self.checked_validate()
        labels = self.completed_labels()
        labels["adjudication"]["reviewed_at"] = "2026-11-13T12:30:00Z"
        self.labels_file.write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must follow both reviews"):
            self.checked_validate()
        self.completed_labels()
        self.findings[0]["claim"] = "Changed private finding."
        self.write_findings()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.checked_validate()


if __name__ == "__main__":
    unittest.main()
