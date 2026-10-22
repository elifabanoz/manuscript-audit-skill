#!/usr/bin/env python3
"""Finalize private benchmark rates only after complete human labels."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path

from score import MUTATION_CLASSES, outside_git, score, scored_inputs_sha256, sha256


PROFILE_AGREEMENT_VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-profile-agreement.cjs"
VERDICTS = {"compliant", "noncompliant", "unclear"}
LABEL_KEYS = {"case_id", "rule_id", "verdict", "evidence_location", "rationale",
              "single_change", "mutation_class"}


def expected_labels(scored):
    return {(row["case_id"], rule_id): row for row in scored["cases"]
            for rule_id in (row["scored_rule_ids"] if row["kind"] == "control"
                            else [row["target_rule_id"]])}


def checked_labels(labels, expected, reviewer, final=False):
    if not isinstance(labels, list):
        raise ValueError(f"{reviewer}: labels must be an array")
    found = {}
    for item in labels:
        if not isinstance(item, dict) or set(item) != LABEL_KEYS:
            raise ValueError(f"{reviewer}: each label must have the required fields")
        if not isinstance(item["case_id"], str) or not isinstance(item["rule_id"], str):
            raise ValueError(f"{reviewer}: case and rule IDs must be strings")
        key = (item["case_id"], item["rule_id"])
        if key not in expected or key in found:
            raise ValueError(f"{reviewer}: unknown or duplicate case/rule label {key}")
        row = expected[key]
        if (not isinstance(item["verdict"], str) or item["verdict"] not in VERDICTS
                or not isinstance(item["evidence_location"], str) or not item["evidence_location"].strip()
                or not isinstance(item["rationale"], str) or not item["rationale"].strip()):
            raise ValueError(f"{reviewer}: {key} needs a verdict, evidence location, and reason")
        if row["kind"] == "control":
            if item["single_change"] is not None or item["mutation_class"] is not None:
                raise ValueError(f"{reviewer}: {key} is a control, not a mutation")
            if final and item["verdict"] != "compliant":
                raise ValueError(f"{reviewer}: control {key} is not human-labeled compliant")
        else:
            if (item["single_change"] is not None and type(item["single_change"]) is not bool) \
                    or (item["mutation_class"] is not None and (
                        not isinstance(item["mutation_class"], str)
                        or item["mutation_class"] not in MUTATION_CLASSES)):
                raise ValueError(f"{reviewer}: {key} has invalid mutation labels")
            if final and (item["verdict"] != "noncompliant" or item["single_change"] is not True):
                raise ValueError(f"{reviewer}: mutation {key} lacks a verified single violation")
            if final and item["mutation_class"] not in MUTATION_CLASSES:
                raise ValueError(f"{reviewer}: mutation {key} lacks a verified class")
            if final and row.get("mutation_class") and item["mutation_class"] != row["mutation_class"]:
                raise ValueError(f"{reviewer}: mutation {key} class differs from the case manifest")
        found[key] = item
    if set(found) != set(expected):
        missing = sorted(set(expected) - set(found))
        raise ValueError(f"{reviewer}: labels are incomplete; missing {missing}")
    return found


def validate_labels(scored, labels, manifest_hash):
    if (not isinstance(labels, dict) or set(labels) != {"schema_version", "benchmark_manifest_sha256",
                                                      "scored_inputs_sha256", "reviews", "adjudication"}
            or labels["schema_version"] != 2 or labels["benchmark_manifest_sha256"] != manifest_hash):
        raise ValueError("Human labels do not match this benchmark manifest")
    if labels["scored_inputs_sha256"] != scored_inputs_sha256(scored):
        raise ValueError("Human labels do not match the scored input files")
    reviews = labels["reviews"]
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("Exactly two independent human review records are required")
    expected = expected_labels(scored)
    reviewers = []
    reviewed = []
    for number, record in enumerate(reviews, start=1):
        if not isinstance(record, dict) or set(record) != {"reviewer_id", "reviewed_at", "labels"}:
            raise ValueError(f"Reviewer {number} record has an unexpected format")
        identifier = record["reviewer_id"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Reviewer {number} needs a name or identifier")
        try:
            date.fromisoformat(record["reviewed_at"])
        except (TypeError, ValueError):
            raise ValueError(f"Reviewer {number} needs an ISO review date") from None
        reviewers.append(identifier.strip())
        reviewed.append(checked_labels(record["labels"], expected, identifier.strip()))
    if reviewers[0].casefold() == reviewers[1].casefold():
        raise ValueError("The two human reviewer identifiers must differ")
    adjudication = labels["adjudication"]
    if (not isinstance(adjudication, dict)
            or set(adjudication) != {"adjudicator_id", "approved", "labels", "disagreement_resolution"}
            or not isinstance(adjudication["adjudicator_id"], str)
            or not adjudication["adjudicator_id"].strip()
            or adjudication["approved"] is not True):
        raise ValueError("Human adjudication is incomplete or not approved")
    final = checked_labels(adjudication["labels"], expected, "Adjudication", final=True)
    def decision(item):
        return item["verdict"], item["single_change"], item["mutation_class"]
    disagreements = [key for key in expected
                     if decision(reviewed[0][key]) != decision(reviewed[1][key])
                     or decision(final[key]) != decision(reviewed[0][key])]
    if disagreements and (not isinstance(adjudication["disagreement_resolution"], str)
                          or not adjudication["disagreement_resolution"].strip()):
        raise ValueError("Reviewer disagreements need an evidence-based resolution")
    return {"reviewer_ids": reviewers, "adjudicator_id": adjudication["adjudicator_id"].strip(),
            "labels_checked": len(final), "disagreements": len(disagreements)}


def verify_profile_agreements(benchmark, scored):
    scored_controls = {row["case_id"]: row for row in scored["cases"] if row["kind"] == "control"}
    agreements = []
    for case in benchmark["cases"]:
        if case["kind"] != "control":
            continue
        case_id = case["case_id"]
        run = Path(case["run"]).resolve()
        outside_git(run)
        check = subprocess.run(["node", str(PROFILE_AGREEMENT_VALIDATOR), str(run)],
                               capture_output=True, text=True)
        if check.returncode:
            raise ValueError(f"{case_id}: agreed profile is required: {check.stderr.strip()}")
        record = json.loads(check.stdout)
        inputs = scored_controls[case_id]["input_sha256"]
        if (record.get("profile_sha256") != inputs["profile"]
                or record.get("manifest_sha256") != inputs["manifest"]
                or not isinstance(record.get("comparison_record"), str)
                or not re.fullmatch(r"profile-comparison-v2-[a-f0-9-]+\.json", record["comparison_record"])
                or not isinstance(record.get("comparison_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["comparison_sha256"])):
            raise ValueError(f"{case_id}: agreed profile evidence differs from scored inputs")
        agreements.append({"control_case_id": case_id, "issn": record["issn"],
                           "profile_sha256": record["profile_sha256"],
                           "comparison_record": record["comparison_record"],
                           "comparison_sha256": record["comparison_sha256"]})
    if len(agreements) != len(scored_controls):
        raise ValueError("Profile agreement coverage differs from scored controls")
    return agreements


def finalize(cases_path, labels_path, output_path=None):
    outside_git(cases_path)
    outside_git(labels_path)
    cases_raw = cases_path.read_bytes()
    benchmark = json.loads(cases_raw)
    manifest_hash = hashlib.sha256(cases_raw).hexdigest()
    scored = score(benchmark)
    if sha256(cases_path) != manifest_hash:
        raise ValueError("Benchmark manifest changed during human-label validation")
    profile_agreements = verify_profile_agreements(benchmark, scored)
    labels_raw = labels_path.read_bytes()
    labels = json.loads(labels_raw)
    review = validate_labels(scored, labels, manifest_hash)
    labels_hash = hashlib.sha256(labels_raw).hexdigest()
    if sha256(labels_path) != labels_hash:
        raise ValueError("Human labels changed during validation")
    output = output_path or cases_path.parent / "human-reviewed-scores.json"
    outside_git(output)
    result = {**scored, "benchmark_manifest_sha256": manifest_hash,
              "scored_inputs_sha256": scored_inputs_sha256(scored),
              "profile_agreements": profile_agreements,
              "human_review_status": "approved", "human_labels_sha256": labels_hash,
              "human_review": review}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Private benchmark case manifest")
    parser.add_argument("labels", type=Path, help="Private completed human-label JSON")
    parser.add_argument("--output", type=Path, help="Private output path")
    args = parser.parse_args()
    try:
        print(finalize(args.cases, args.labels, args.output))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
