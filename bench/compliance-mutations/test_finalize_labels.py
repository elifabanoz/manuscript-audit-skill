"""Checks for human-label completion before reporting reviewed rates."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from finalize_labels import finalize, validate_labels, verify_profile_agreements
from prepare_label_review import make_json_template
from score import scope_review_sha256, sha256


SCORED = {"schema_version": 2, "data_freeze": "2026-10-20", "cases": [
    {"case_id": "control-1", "kind": "control", "scored_rule_ids": ["required_heading"],
     "target_rule_id": None},
    {"case_id": "mutation-1", "kind": "mutation", "scored_rule_ids": ["required_heading"],
     "target_rule_id": "required_heading", "mutation_class": "structural"}]}


def complete(template):
    labels = copy.deepcopy(template)
    for number, review in enumerate(labels["reviews"], start=1):
        review["reviewer_id"] = f"human-reviewer-{number}"
        review["reviewed_at"] = "2026-10-20"
        for item in review["labels"]:
            target = item["case_id"] == "mutation-1" and item["rule_id"] == "required_heading"
            item["verdict"] = "noncompliant" if target else "compliant"
            item["evidence_location"] = "paper.md:L1-L20"
            item["rationale"] = "The heading marker was checked against the cited rule."
            if target:
                item["single_change"] = True
                item["mutation_class"] = "structural"
    labels["adjudication"]["adjudicator_id"] = "human-reviewer-2"
    labels["adjudication"]["approved"] = True
    labels["adjudication"]["labels"] = copy.deepcopy(labels["reviews"][0]["labels"])
    return labels


class HumanLabelTests(unittest.TestCase):
    def test_version_five_requires_both_reviewers_to_approve_scope_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            scored = copy.deepcopy(SCORED)
            scored["schema_version"] = 5
            for index, row in enumerate(scored["cases"]):
                row["input_sha256"] = {"scope_record": f"{index + 1:064x}"}
            with patch("prepare_label_review.score", return_value=scored):
                labels = complete(make_json_template(cases))
            self.assertEqual(labels["schema_version"], 4)
            with self.assertRaisesRegex(ValueError, "scoring scope"):
                validate_labels(scored, labels, sha256(cases))
            scope_hash = scope_review_sha256(scored)
            for record in (*labels["reviews"], labels["adjudication"]):
                record["scope_review_sha256"] = scope_hash
            result = validate_labels(scored, labels, sha256(cases))
            self.assertEqual(result["scope_review_sha256"], scope_hash)
            labels["reviews"][1]["scope_review_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "scoring scope"):
                validate_labels(scored, labels, sha256(cases))

    def test_version_five_requires_labels_for_every_mutation_rule(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            scored = copy.deepcopy(SCORED)
            scored["schema_version"] = 5
            for index, row in enumerate(scored["cases"]):
                row["scored_rule_ids"] = ["required_heading", "secondary_rule"]
                row["input_sha256"] = {"scope_record": f"{index + 1:064x}"}
            with patch("prepare_label_review.score", return_value=scored):
                labels = complete(make_json_template(cases))
            scope_hash = scope_review_sha256(scored)
            for record in (*labels["reviews"], labels["adjudication"]):
                record["scope_review_sha256"] = scope_hash
            self.assertEqual(len(labels["reviews"][0]["labels"]), 4)
            review = validate_labels(scored, labels, sha256(cases))
            self.assertEqual(review["labels_checked"], 4)
            self.assertEqual(review["off_target_labels_checked"], 1)
            missing = copy.deepcopy(labels)
            missing["reviews"][0]["labels"].pop()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_labels(scored, missing, sha256(cases))
            invalid = copy.deepcopy(labels)
            off_target = next(item for item in invalid["adjudication"]["labels"]
                              if item["case_id"] == "mutation-1" and item["rule_id"] == "secondary_rule")
            off_target["verdict"] = "noncompliant"
            with self.assertRaisesRegex(ValueError, "off-target.*not human-labeled compliant"):
                validate_labels(scored, invalid, sha256(cases))
            malformed = copy.deepcopy(labels)
            off_target = next(item for item in malformed["reviews"][0]["labels"]
                              if item["case_id"] == "mutation-1" and item["rule_id"] == "secondary_rule")
            off_target["single_change"] = True
            with self.assertRaisesRegex(ValueError, "off-target.*mutation metadata"):
                validate_labels(scored, malformed, sha256(cases))

    def test_human_rates_require_an_agreed_profile_matching_scored_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            benchmark = {"cases": [{"case_id": "control-1", "kind": "control", "run": str(run)}]}
            scored = {"cases": [{"case_id": "control-1", "kind": "control",
                                  "input_sha256": {"manifest": "a" * 64, "profile": "b" * 64}}]}
            record = {"issn": "1234-567X", "manifest_sha256": "a" * 64,
                      "profile_sha256": "b" * 64,
                      "comparison_record": "profile-comparison-v2-aaaaaaaaaaaa-bbbbbbbbbbbb.json",
                      "comparison_sha256": "c" * 64}
            response = SimpleNamespace(returncode=0, stdout=json.dumps(record), stderr="")
            with patch("finalize_labels.subprocess.run", return_value=response):
                result = verify_profile_agreements(benchmark, scored)
            self.assertEqual(result[0]["comparison_sha256"], "c" * 64)
            adjudicated = {**record,
                           "adjudication_record": "profile-adjudication-v1-aaaaaaaaaaaa-bbbbbbbbbbbb.json",
                           "adjudication_sha256": "d" * 64}
            with patch("finalize_labels.subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(adjudicated), stderr="")):
                self.assertEqual(verify_profile_agreements(benchmark, scored)[0]["adjudication_sha256"], "d" * 64)
            incomplete = {**adjudicated, "adjudication_sha256": None}
            with patch("finalize_labels.subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(incomplete), stderr="")):
                with self.assertRaisesRegex(ValueError, "adjudication evidence is incomplete"):
                    verify_profile_agreements(benchmark, scored)
            with patch("finalize_labels.subprocess.run", return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="No agreed comparison")):
                with self.assertRaisesRegex(ValueError, "agreed profile is required"):
                    verify_profile_agreements(benchmark, scored)
            changed = {**record, "profile_sha256": "d" * 64}
            with patch("finalize_labels.subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(changed), stderr="")):
                with self.assertRaisesRegex(ValueError, "differs from scored inputs"):
                    verify_profile_agreements(benchmark, scored)

    def test_template_requires_two_filled_reviews_and_adjudication(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            with patch("prepare_label_review.score", return_value=SCORED):
                template = make_json_template(cases)
            self.assertEqual(template["schema_version"], 2)
            self.assertEqual(len(template["reviews"]), 2)
            self.assertEqual(len(template["reviews"][0]["labels"]), 2)
            with self.assertRaisesRegex(ValueError, "name or identifier"):
                validate_labels(SCORED, template, sha256(cases))
            approved = complete(template)
            result = validate_labels(SCORED, approved, sha256(cases))
            self.assertEqual(result["labels_checked"], 2)
            self.assertEqual(result["disagreements"], 0)

    def test_rejects_missing_and_invalid_ground_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            with patch("prepare_label_review.score", return_value=SCORED):
                original = complete(make_json_template(cases))
            variants = []
            missing = copy.deepcopy(original)
            missing["reviews"][0]["labels"].pop()
            variants.append((missing, "incomplete"))
            invalid_control = copy.deepcopy(original)
            invalid_control["adjudication"]["labels"][0]["verdict"] = "noncompliant"
            variants.append((invalid_control, "not human-labeled compliant"))
            invalid_mutation = copy.deepcopy(original)
            invalid_mutation["adjudication"]["labels"][1]["single_change"] = False
            variants.append((invalid_mutation, "single violation"))
            same_reviewer = copy.deepcopy(original)
            same_reviewer["reviews"][1]["reviewer_id"] = "HUMAN-REVIEWER-1"
            variants.append((same_reviewer, "must differ"))
            for value, error in variants:
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    validate_labels(SCORED, value, sha256(cases))
            with self.assertRaisesRegex(ValueError, "manifest"):
                validate_labels(SCORED, original, "b" * 64)
            changed_inputs = copy.deepcopy(SCORED)
            changed_inputs["cases"][1]["proposal_record_sha256"] = "a" * 64
            with self.assertRaisesRegex(ValueError, "scored input files"):
                validate_labels(changed_inputs, original, sha256(cases))

    def test_disagreements_require_documented_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            with patch("prepare_label_review.score", return_value=SCORED):
                labels = complete(make_json_template(cases))
            labels["reviews"][1]["labels"][1]["verdict"] = "unclear"
            with self.assertRaisesRegex(ValueError, "disagreements"):
                validate_labels(SCORED, labels, sha256(cases))
            labels["adjudication"]["disagreement_resolution"] = "The second reading confirmed the missing marker."
            self.assertEqual(validate_labels(SCORED, labels, sha256(cases))["disagreements"], 1)

    def test_final_output_is_private_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = root / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            with patch("prepare_label_review.score", return_value=SCORED):
                labels = complete(make_json_template(cases))
            labels_path = root / "human-labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            with patch("finalize_labels.score", return_value=SCORED), patch(
                    "finalize_labels.verify_profile_agreements", return_value=[{"control_case_id": "control-1"}]):
                output = finalize(cases, labels_path)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["human_review_status"], "approved")
                self.assertEqual(result["human_labels_sha256"], sha256(labels_path))
                self.assertEqual(result["profile_agreements"], [{"control_case_id": "control-1"}])
                with self.assertRaises(FileExistsError):
                    finalize(cases, labels_path)

    def test_off_target_rate_requires_approved_labels_for_every_scored_rule(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = root / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            scored = copy.deepcopy(SCORED)
            scored["schema_version"] = 5
            for index, row in enumerate(scored["cases"]):
                row["scored_rule_ids"] = ["required_heading", "secondary_rule"]
                row["input_sha256"] = {"scope_record": f"{index + 1:064x}"}
            scored["cases"][1]["off_target_raw_rule_ids"] = ["secondary_rule"]
            scored["cases"][1]["off_target_validated_rule_ids"] = []
            with patch("prepare_label_review.score", return_value=scored):
                labels = complete(make_json_template(cases))
            scope_hash = scope_review_sha256(scored)
            for record in (*labels["reviews"], labels["adjudication"]):
                record["scope_review_sha256"] = scope_hash
            labels_path = root / "human-labels.json"
            labels_path.write_text(json.dumps(labels), encoding="utf-8")
            with patch("finalize_labels.score", return_value=scored), patch(
                    "finalize_labels.verify_profile_agreements", return_value=[{"control_case_id": "control-1"}]):
                output = finalize(cases, labels_path)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["human_review"]["off_target_labels_checked"], 1)
            rates = result["human_reviewed_off_target"]
            self.assertEqual(rates["rule_opportunities"], 1)
            self.assertEqual(rates["raw_false_positive_rate"], 1)
            self.assertEqual(rates["validated_false_positive_rate"], 0)
            self.assertEqual(rates["by_mutation_class"]["structural"]["raw_flagged_rules"], 1)
            invalid = copy.deepcopy(labels)
            off_target = next(item for item in invalid["adjudication"]["labels"]
                              if item["case_id"] == "mutation-1" and item["rule_id"] == "secondary_rule")
            off_target["verdict"] = "noncompliant"
            labels_path.write_text(json.dumps(invalid), encoding="utf-8")
            with patch("finalize_labels.score", return_value=scored), patch(
                    "finalize_labels.verify_profile_agreements", return_value=[{"control_case_id": "control-1"}]):
                with self.assertRaisesRegex(ValueError, "off-target.*not human-labeled compliant"):
                    finalize(cases, labels_path, root / "invalid-scores.json")
            self.assertFalse((root / "invalid-scores.json").exists())


if __name__ == "__main__":
    unittest.main()
