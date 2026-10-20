"""Checks for human-label completion before reporting reviewed rates."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from finalize_labels import finalize, validate_labels
from prepare_label_review import make_json_template
from score import sha256


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
            item["verdict"] = "compliant" if item["case_id"] == "control-1" else "noncompliant"
            item["evidence_location"] = "paper.md:L1-L20"
            item["rationale"] = "The heading marker was checked against the cited rule."
            if item["case_id"] == "mutation-1":
                item["single_change"] = True
                item["mutation_class"] = "structural"
    labels["adjudication"]["adjudicator_id"] = "human-reviewer-2"
    labels["adjudication"]["approved"] = True
    labels["adjudication"]["labels"] = copy.deepcopy(labels["reviews"][0]["labels"])
    return labels


class HumanLabelTests(unittest.TestCase):
    def test_template_requires_two_filled_reviews_and_adjudication(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases = Path(temporary) / "cases.json"
            cases.write_text("{}", encoding="utf-8")
            with patch("prepare_label_review.score", return_value=SCORED):
                template = make_json_template(cases)
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
            with patch("finalize_labels.score", return_value=SCORED):
                output = finalize(cases, labels_path)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["human_review_status"], "approved")
                self.assertEqual(result["human_labels_sha256"], sha256(labels_path))
                with self.assertRaises(FileExistsError):
                    finalize(cases, labels_path)


if __name__ == "__main__":
    unittest.main()
