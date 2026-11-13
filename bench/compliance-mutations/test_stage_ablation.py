"""Stage rates require human labels and distinct, pinned reviewer checkpoints."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from score_stage_ablation import finalize, measure


class StageAblationTests(unittest.TestCase):
    def test_separates_defense_and_record_verification_by_class(self):
        rows = [
            {"case_id": "control-a", "kind": "control", "scored_rule_ids": ["r1"],
             "validated_flagged": False},
            {"case_id": "control-b", "kind": "control", "scored_rule_ids": ["r2", "r3"],
             "validated_flagged": True},
            {"case_id": "mutation-a", "kind": "mutation", "scored_rule_ids": ["r1"],
             "target_rule_id": "r1", "mutation_class": "structural", "validated_flagged": True},
            {"case_id": "mutation-b", "kind": "mutation", "scored_rule_ids": ["r2"],
             "target_rule_id": "r2", "mutation_class": "semantic", "validated_flagged": False},
        ]
        stages = {case_id: {"confirmed_rule_ids": rules, "snapshot_sha256": "a" * 64}
                  for case_id, rules in (("control-a", []), ("control-b", []),
                                         ("mutation-a", []), ("mutation-b", ["r2"]))}
        scored = {"schema_version": 5, "cases": rows}
        result = measure(scored, stages, {"mutation-a": "control-a", "mutation-b": "control-b"})
        self.assertEqual(result["mutations"]["defense_recall"], 0.5)
        self.assertEqual(result["mutations"]["verified_recall"], 0.5)
        self.assertEqual(result["controls"]["defense_false_positive_rate"], 0)
        self.assertEqual(result["controls"]["verified_false_positive_rate"], 0.5)
        self.assertEqual(result["by_mutation_class"]["structural"]["mutations"]["defense_recall"], 0)
        self.assertEqual(result["by_mutation_class"]["semantic"]["mutations"]["defense_recall"], 1)
        stages["control-a"]["confirmed_rule_ids"] = ["r1"]
        with self.assertRaisesRegex(ValueError, "paired control was flagged"):
            measure(scored, stages, {"mutation-a": "control-a", "mutation-b": "control-b"})

    def test_unapproved_labels_stop_before_stage_results(self):
        with tempfile.TemporaryDirectory(prefix="stage-ablation-test-") as directory:
            root = Path(directory)
            cases = root / "cases.json"
            labels = root / "human-labels.json"
            cases.write_text(json.dumps({"cases": []}), encoding="utf-8")
            labels.write_text("{}", encoding="utf-8")
            with patch("score_stage_ablation.score", return_value={"schema_version": 5, "cases": []}), \
                 patch("score_stage_ablation.verify_profile_agreements", return_value=[]), \
                 patch("score_stage_ablation.validate_labels", side_effect=ValueError("human labels pending")), \
                 patch("score_stage_ablation.subprocess.run") as stage_check:
                with self.assertRaisesRegex(ValueError, "human labels pending"):
                    finalize(cases, labels)
                stage_check.assert_not_called()
            self.assertFalse((root / "stage-ablation-scores.json").exists())

    def test_private_output_binds_all_stage_snapshots(self):
        with tempfile.TemporaryDirectory(prefix="stage-ablation-test-") as directory:
            root = Path(directory)
            cases_data = []
            for case_id, kind in (("control", "control"), ("mutation", "mutation")):
                run = root / case_id
                run.mkdir()
                (run / "defense-stage.json").write_text("{}", encoding="utf-8")
                (run / "raw-findings.json").write_text("[]", encoding="utf-8")
                cases_data.append({"case_id": case_id, "kind": kind, "run": str(run),
                                   "raw_findings": str(run / "raw-findings.json"),
                                   **({"control_case_id": "control"} if kind == "mutation" else {})})
            cases = root / "cases.json"
            labels = root / "human-labels.json"
            cases.write_text(json.dumps({"cases": cases_data}), encoding="utf-8")
            labels.write_text("{}", encoding="utf-8")
            scored = {"schema_version": 5, "data_freeze": "2026-10-20", "cases": [
                {"case_id": "control", "kind": "control", "scored_rule_ids": ["r1"],
                 "validated_flagged": False},
                {"case_id": "mutation", "kind": "mutation", "scored_rule_ids": ["r1"],
                 "target_rule_id": "r1", "mutation_class": "structural", "validated_flagged": True}]}
            stage_hash = hashlib.sha256(b"{}").hexdigest()
            stage = SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "confirmed_rule_ids": [], "snapshot_sha256": stage_hash}))
            with patch("score_stage_ablation.score", return_value=scored), \
                 patch("score_stage_ablation.verify_profile_agreements", return_value=[]), \
                 patch("score_stage_ablation.validate_labels", return_value={"labels_checked": 2}), \
                 patch("score_stage_ablation.subprocess.run", return_value=stage):
                output = finalize(cases, labels)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["human_review_status"], "approved")
            self.assertEqual(result["mutations"]["defense_recall"], 0)
            self.assertEqual(result["mutations"]["verified_recall"], 1)
            self.assertEqual(len(result["cases"]), 2)
            self.assertNotIn("phase4_findings_json", result)


if __name__ == "__main__":
    unittest.main()
