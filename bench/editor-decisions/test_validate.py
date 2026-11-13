"""Synthetic checks for private first-decision record contracts."""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from audit import check as check_audit, prepare as prepare_audit
from validate import digest, validate


class DecisionContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="editor-decision-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run-1"
        self.run.mkdir()
        self.run_manifest_file = self.run / "run-manifest.json"
        self.run_manifest_file.write_text(json.dumps({"created_at": "2026-01-01T09:00:00Z"}), encoding="utf-8")
        (self.run / "journal-profile.json").write_text(json.dumps({"issn": "1234-567X"}), encoding="utf-8")
        self.letter = self.root / "decision-letter.txt"
        self.letter.write_text("Private invented decision text.\n", encoding="utf-8")
        self.prediction_file = self.root / "prediction.json"
        self.prediction = {"schema_version": 1, "run_manifest_sha256": digest(self.run_manifest_file),
                           "target_issn": "1234-567X", "stage": "first_editorial_decision",
                           "category": "major_revision", "recorded_at": "2026-01-01T10:00:00Z",
                           "predictor_id": "synthetic-local-check"}
        self.outcome_file = self.root / "outcome.json"
        self.outcome = {"schema_version": 1, "target_issn": "1234-567X",
                        "stage": "first_editorial_decision", "category": "minor_revision",
                        "decision_at": "2026-02-01T10:00:00Z", "letter_sha256": digest(self.letter),
                        "labeler": "Invented Human", "labeled_at": "2026-02-02T10:00:00Z"}
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "cases": [{
            "case_id": "case-1", "run_directory": str(self.run),
            "prediction_file": str(self.prediction_file), "outcome_file": str(self.outcome_file),
            "decision_letter_file": str(self.letter)}]}), encoding="utf-8")
        self.write_records()

    def write_records(self):
        self.prediction_file.write_text(json.dumps(self.prediction), encoding="utf-8")
        self.outcome_file.write_text(json.dumps(self.outcome), encoding="utf-8")

    def check(self):
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            return validate(self.manifest)

    def test_validates_structure_without_scoring_accuracy_or_copying_letter(self):
        result = self.check()
        self.assertEqual(result["status"], "records_structurally_valid")
        self.assertEqual(len(result["cases"]), 1)
        self.assertNotIn("accuracy", result)
        self.assertNotIn("Private invented decision text", json.dumps(result))

    def test_rejects_late_prediction_and_stale_evidence(self):
        self.prediction["recorded_at"] = "2026-02-02T10:00:00Z"
        self.write_records()
        with self.assertRaisesRegex(ValueError, "before the first decision"):
            self.check()
        self.prediction["recorded_at"] = "2026-01-01T10:00:00Z"
        self.letter.write_text("Changed private decision.\n", encoding="utf-8")
        self.write_records()
        with self.assertRaisesRegex(ValueError, "Outcome record is incomplete or stale"):
            self.check()

    def test_rejects_unknown_category_and_unvalidated_run(self):
        self.prediction["category"] = "very_likely_accept"
        self.write_records()
        with self.assertRaisesRegex(ValueError, "Prediction record is incomplete"):
            self.check()
        self.prediction["category"] = "major_revision"
        self.write_records()
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, "run failed validation"):
                validate(self.manifest)

    def test_independent_audit_rechecks_blind_category_and_lock(self):
        packet_file = self.root / "audit-packet.json"
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            prepare_audit(self.manifest, packet_file)
        packet = json.loads(packet_file.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_independent_review")
        self.assertNotIn("minor_revision", json.dumps(packet))
        self.assertNotIn("Private invented decision text", json.dumps(packet))
        audit = packet["audit_template"]
        audit_file = self.root / "audit.json"
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                check_audit(self.manifest, audit_file)
        audit["auditor"] = "Second Human"
        audit["reviewed_at"] = "2026-02-03T10:00:00Z"
        audit["cases"][0].update({"stage": "first_editorial_decision", "category": "minor_revision",
                                  "prediction_lock_verified": True, "rationale": "Checked the private records."})
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(check_audit(self.manifest, audit_file)["status"], "recorded_agreement")
        audit["cases"][0]["category"] = "major_revision"
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(check_audit(self.manifest, audit_file)["status"], "needs_adjudication")
        audit["auditor"] = "Invented Human"
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                check_audit(self.manifest, audit_file)
        audit["auditor"] = "Second Human"
        audit["reviewed_at"] = "2026-02-01T10:00:00Z"
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, "must follow primary labels"):
                check_audit(self.manifest, audit_file)
        audit["reviewed_at"] = "2026-02-03T10:00:00Z"
        audit_file.write_text(json.dumps(audit), encoding="utf-8")
        self.outcome["labeler"] = "Changed Primary Labeler"
        self.write_records()
        with patch("validate.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, "stale"):
                check_audit(self.manifest, audit_file)


if __name__ == "__main__":
    unittest.main()
