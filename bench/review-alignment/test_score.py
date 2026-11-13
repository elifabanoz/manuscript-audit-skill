"""Synthetic private review-label scoring checks."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_label_review import prepare
from score import score_manifest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReviewAlignmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-alignment-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run-1"
        (self.run / "sources").mkdir(parents=True)
        (self.run / "manuscript.md").write_text("# Invented manuscript\n", encoding="utf-8")
        source = self.run / "sources" / "guideline.txt"
        source.write_text("Example journal instructions.\n", encoding="utf-8")
        source_record = {"url": "https://journal.example/authors", "retrieved_at": "2026-11-13T10:00:00Z",
                         "sha256": digest(source), "path": "sources/guideline.txt"}
        manifest = {"schema_version": 1, "created_at": "2026-11-13T10:00:00Z", "host": "manual",
                    "models": [], "skill_sha256": "a" * 64, "prompt_hashes": {"SKILL.md": "a" * 64},
                    "config_hash": None, "data_freeze": None, "cross_host": False,
                    "manuscript": {"path": "manuscript.md", "sha256": digest(self.run / "manuscript.md")},
                    "supplementary": [], "sources": {"src_guideline": source_record}}
        profile = {"issn": "1234-567X", "title": "Example Journal", "publisher": "Example",
                   "article_types": ["original_research"], "rules": [],
                   "sources": manifest["sources"]}
        for name, record in (("run-manifest.json", manifest), ("journal-profile.json", profile),
                             ("coverage-ledger.json", []), ("findings.json", [])):
            (self.run / name).write_text(json.dumps(record), encoding="utf-8")
        self.report = self.root / "peer-report.txt"
        self.report.write_text("Private peer issue text.\n", encoding="utf-8")
        self.labels = self.root / "labels.json"
        self.label_record = {"schema_version": 1, "peer_report_sha256": digest(self.report),
                             "findings_sha256": digest(self.run / "findings.json"),
                             "reviewer": "Human Reviewer", "reviewed_at": "2026-11-13T11:00:00Z",
                             "manuscript_version": "initial_submission",
                             "issues": [{"issue_id": "P-1", "in_scope": True,
                                         "matched_finding_ids": [], "rationale": "No matching review finding."}]}
        self.write_labels()
        self.manifest = self.root / "cases.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "cases": [
            {"case_id": "case-1", "run_directory": str(self.run),
             "peer_report_file": str(self.report), "labels_file": str(self.labels)}]}), encoding="utf-8")

    def write_labels(self):
        self.labels.write_text(json.dumps(self.label_record), encoding="utf-8")

    def test_prepares_private_hash_bound_human_label_packet(self):
        packet_path = self.root / "label-packet.json"
        self.assertEqual(prepare(self.run, self.report, packet_path), packet_path)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["status"], "pending_human_labels")
        self.assertEqual(packet["run_manifest_sha256"], digest(self.run / "run-manifest.json"))
        self.assertEqual(packet["peer_report_sha256"], digest(self.report))
        self.assertEqual(packet["findings_sha256"], digest(self.run / "findings.json"))
        self.assertEqual(packet["confirmed_review_findings"], [])
        self.assertEqual(packet["label_template"]["issues"], [])
        self.assertEqual(packet["label_template"]["reviewer"], "")
        self.assertNotIn("Private peer issue text", packet_path.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            prepare(self.run, self.report, packet_path)
        self.labels.write_text(json.dumps(packet["label_template"]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Human labels are missing or stale"):
            score_manifest(self.manifest)

    def test_rejects_stale_run_before_preparing_packet(self):
        (self.run / "manuscript.md").write_text("# Changed manuscript\n", encoding="utf-8")
        packet_path = self.root / "stale-packet.json"
        with self.assertRaisesRegex(ValueError, "failed validation"):
            prepare(self.run, self.report, packet_path)
        self.assertFalse(packet_path.exists())

    def test_packet_lists_review_findings_without_report_text(self):
        findings = [{"id": "F-1", "category": "review", "verdict": "confirmed",
                     "review_class": "methodology"},
                    {"id": "F-2", "category": "review", "verdict": "needs_validation"},
                    {"id": "F-3", "category": "compliance", "verdict": "confirmed"}]
        (self.run / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        packet_path = self.root / "finding-packet.json"
        with patch("prepare_label_review.validate_run"):
            prepare(self.run, self.report, packet_path)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["confirmed_review_findings"],
                         [{"finding_id": "F-1", "review_class": "methodology"}])
        self.assertEqual(packet["pending_review_findings"], ["F-2"])
        self.assertNotIn("Private peer issue text", packet_path.read_text(encoding="utf-8"))

    def test_scores_only_complete_human_labels_from_validated_private_run(self):
        result = score_manifest(self.manifest)
        self.assertEqual(result["status"], "local_human_label_records")
        self.assertEqual(result["overall"]["peer_issue_recall"], 0)
        self.assertNotIn("Private peer issue text", json.dumps(result))
        script = Path(__file__).with_name("score.py")
        completed = subprocess.run([sys.executable, str(script), str(self.manifest)],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(Path(completed.stdout.strip()).read_text(encoding="utf-8")), result)
        self.assertNotEqual(subprocess.run([sys.executable, str(script), str(self.manifest)],
                                           capture_output=True).returncode, 0)

    def test_rejects_stale_labels_and_unconfirmed_matches(self):
        self.label_record["peer_report_sha256"] = "0" * 64
        self.write_labels()
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            score_manifest(self.manifest)
        self.label_record["peer_report_sha256"] = digest(self.report)
        self.label_record["issues"][0]["matched_finding_ids"] = ["F-1"]
        self.write_labels()
        with self.assertRaisesRegex(ValueError, "confirmed review finding"):
            score_manifest(self.manifest)

    def test_counts_adjudicated_matches_without_treating_unmentioned_as_false(self):
        findings = [{"id": "F-1", "category": "review", "verdict": "confirmed"},
                    {"id": "F-2", "category": "review", "verdict": "confirmed"}]
        (self.run / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        self.label_record["findings_sha256"] = digest(self.run / "findings.json")
        self.label_record["issues"][0]["matched_finding_ids"] = ["F-1"]
        self.write_labels()
        with patch("score.validate_run"):
            result = score_manifest(self.manifest)
        self.assertEqual(result["overall"]["peer_issue_recall"], 1)
        self.assertEqual(result["overall"]["confirmed_findings_unmentioned"], 1)
        self.assertEqual(result["overall"]["unmentioned_assessment_status"], "pending")
        self.assertIn("not false positives", result["note"])
        self.label_record["unmentioned_findings"] = [
            {"finding_id": "F-2", "assessment": "supported", "rationale": "Checked against the manuscript."}]
        self.write_labels()
        with patch("score.validate_run"):
            assessed = score_manifest(self.manifest)
        self.assertEqual(assessed["overall"]["unmentioned_assessment_status"], "complete")
        self.assertEqual(assessed["overall"]["unmentioned_assessments"]["supported"], 1)
        self.label_record["unmentioned_findings"] = []
        self.write_labels()
        with patch("score.validate_run"), self.assertRaisesRegex(ValueError, "Every unmentioned"):
            score_manifest(self.manifest)
        self.label_record["unmentioned_findings"] = None
        self.write_labels()
        with patch("score.validate_run"), self.assertRaisesRegex(ValueError, "must be an array"):
            score_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
