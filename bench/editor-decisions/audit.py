#!/usr/bin/env python3
"""Prepare and check private independent first-decision label audits."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from validate import CATEGORIES, STAGE, digest, private_file, read_record, validate


def case_inputs(manifest_file):
    validate(manifest_file)
    manifest = read_record(private_file(manifest_file))
    cases = []
    for case in manifest["cases"]:
        outcome_file = private_file(case["outcome_file"])
        outcome = read_record(outcome_file)
        cases.append({
            "case_id": case["case_id"],
            "outcome_sha256": digest(outcome_file),
            "decision_letter_sha256": digest(private_file(case["decision_letter_file"])),
            "primary_labeler": outcome["labeler"],
            "primary_category": outcome["category"],
            "primary_labeled_at": outcome["labeled_at"],
        })
    return cases


def prepare(manifest_file, output_file):
    manifest_file = private_file(manifest_file)
    output_file = Path(output_file)
    if not output_file.is_absolute():
        raise ValueError("Decision audit packet output must be absolute")
    output_file = private_file(output_file.parent, directory=True) / output_file.name
    cases = case_inputs(manifest_file)
    packet = {
        "schema_version": 1,
        "status": "pending_independent_review",
        "manifest_sha256": digest(manifest_file),
        "cases": [{key: case[key] for key in ("case_id", "outcome_sha256", "decision_letter_sha256")}
                  for case in cases],
        "audit_template": {
            "schema_version": 1,
            "manifest_sha256": digest(manifest_file),
            "auditor": "",
            "reviewed_at": "",
            "cases": [{"case_id": case["case_id"], "outcome_sha256": case["outcome_sha256"],
                       "decision_letter_sha256": case["decision_letter_sha256"],
                       "stage": "", "category": "", "prediction_lock_verified": None,
                       "rationale": ""} for case in cases],
        },
    }
    with output_file.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2)
        handle.write("\n")
    return output_file


def check(manifest_file, audit_file):
    manifest_file = private_file(manifest_file)
    audit_file = private_file(audit_file)
    audit = read_record(audit_file)
    cases = case_inputs(manifest_file)
    if (not isinstance(audit, dict) or set(audit) !=
            {"schema_version", "manifest_sha256", "auditor", "reviewed_at", "cases"}
            or audit["schema_version"] != 1 or audit["manifest_sha256"] != digest(manifest_file)
            or not isinstance(audit["auditor"], str) or not audit["auditor"].strip()
            or any(audit["auditor"].strip().casefold() == case["primary_labeler"].strip().casefold()
                   for case in cases)
            or not isinstance(audit["cases"], list) or len(audit["cases"]) != len(cases)):
        raise ValueError("Independent decision audit is incomplete or stale")
    try:
        reviewed_at = datetime.fromisoformat(audit["reviewed_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError("Independent decision audit needs a timestamp") from None
    if reviewed_at.tzinfo is None:
        raise ValueError("Independent decision audit needs a timezone")
    if any(reviewed_at < datetime.fromisoformat(case["primary_labeled_at"].replace("Z", "+00:00"))
           for case in cases):
        raise ValueError("Independent decision audit must follow primary labels")
    if any(not isinstance(item, dict) or not isinstance(item.get("case_id"), str)
           for item in audit["cases"]):
        raise ValueError("Independent decision audit cases are incomplete")
    supplied = {item["case_id"]: item for item in audit["cases"]}
    if len(supplied) != len(cases) or set(supplied) != {case["case_id"] for case in cases}:
        raise ValueError("Independent decision audit cases are incomplete")
    disagreements = 0
    for case in cases:
        item = supplied[case["case_id"]]
        if (set(item) != {"case_id", "outcome_sha256", "decision_letter_sha256", "stage",
                          "category", "prediction_lock_verified", "rationale"}
                or item["outcome_sha256"] != case["outcome_sha256"]
                or item["decision_letter_sha256"] != case["decision_letter_sha256"]):
            raise ValueError("Independent decision audit inputs are stale")
        if (not isinstance(item["stage"], str) or not isinstance(item["category"], str)
                or item["category"] not in CATEGORIES
                or item["stage"] != STAGE or type(item["prediction_lock_verified"]) is not bool
                or not isinstance(item["rationale"], str) or not item["rationale"].strip()):
            raise ValueError("Independent decision audit check is incomplete")
        disagreements += (item["category"] != case["primary_category"]
                          or not item["prediction_lock_verified"])
    return {"schema_version": 1,
            "status": "needs_adjudication" if disagreements else "recorded_agreement",
            "case_count": len(cases), "disagreements": disagreements,
            "manifest_sha256": digest(manifest_file), "audit_sha256": digest(audit_file),
            "note": "Recorded agreement does not prove reviewer identity, model provenance, or decision-label correctness."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            print(prepare(args.manifest, args.record))
        else:
            print(json.dumps(check(args.manifest, args.record), indent=2))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
