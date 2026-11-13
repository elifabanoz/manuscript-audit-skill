#!/usr/bin/env python3
"""Prepare and check a private second-review record for peer-issue labels."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from score import digest, private_path, read_json, score_manifest


def case_inputs(manifest_path):
    manifest = read_json(private_path(manifest_path))
    score_manifest(manifest_path)
    cases = []
    for case in manifest["cases"]:
        labels_path = private_path(case["labels_file"])
        labels = read_json(labels_path)
        cases.append({
            "case_id": case["case_id"],
            "labels_sha256": digest(labels_path),
            "primary_reviewer": labels["reviewer"],
            "issue_ids": [item["issue_id"] for item in labels["issues"]],
            "unmentioned_finding_ids": [item["finding_id"] for item in
                                        labels.get("unmentioned_findings", [])],
        })
    return cases


def prepare(manifest_path, output_path):
    manifest_path = private_path(manifest_path)
    output_path = Path(output_path)
    if not output_path.is_absolute():
        raise ValueError("Audit packet output must be absolute")
    output_path = private_path(output_path.parent, directory=True) / output_path.name
    cases = case_inputs(manifest_path)
    packet = {
        "schema_version": 1,
        "status": "pending_independent_review",
        "manifest_sha256": digest(manifest_path),
        "cases": cases,
        "audit_template": {
            "schema_version": 1,
            "manifest_sha256": digest(manifest_path),
            "auditor": "",
            "reviewed_at": "",
            "cases": [{"case_id": case["case_id"], "labels_sha256": case["labels_sha256"],
                       "issue_checks": [], "unmentioned_finding_checks": []} for case in cases],
        },
    }
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2)
        handle.write("\n")
    return output_path


def check(manifest_path, audit_path):
    manifest_path = private_path(manifest_path)
    audit = read_json(private_path(audit_path))
    cases = case_inputs(manifest_path)
    if (not isinstance(audit, dict) or set(audit) !=
            {"schema_version", "manifest_sha256", "auditor", "reviewed_at", "cases"}
            or audit["schema_version"] != 1 or audit["manifest_sha256"] != digest(manifest_path)
            or not isinstance(audit["auditor"], str) or not audit["auditor"].strip()
            or any(audit["auditor"].strip().casefold() == case["primary_reviewer"].strip().casefold()
                   for case in cases)
            or not isinstance(audit["cases"], list) or len(audit["cases"]) != len(cases)):
        raise ValueError("Independent label audit is incomplete or stale")
    try:
        reviewed_at = datetime.fromisoformat(audit["reviewed_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError("Independent audit needs a timestamp") from None
    if reviewed_at.tzinfo is None:
        raise ValueError("Independent audit needs a timezone")
    if any(not isinstance(item, dict) or not isinstance(item.get("case_id"), str)
           for item in audit["cases"]):
        raise ValueError("Independent audit cases are incomplete")
    supplied = {item["case_id"]: item for item in audit["cases"]}
    if len(supplied) != len(cases) or set(supplied) != {case["case_id"] for case in cases}:
        raise ValueError("Independent audit cases are incomplete")
    disagreements = 0
    for case in cases:
        item = supplied[case["case_id"]]
        if (set(item) != {"case_id", "labels_sha256", "issue_checks", "unmentioned_finding_checks"}
                or item["labels_sha256"] != case["labels_sha256"]):
            raise ValueError("Independent audit labels are stale")
        for key, id_key, expected in (
                ("issue_checks", "issue_id", case["issue_ids"]),
                ("unmentioned_finding_checks", "finding_id", case["unmentioned_finding_ids"])):
            checks = item[key]
            if not isinstance(checks, list) or len(checks) != len(expected):
                raise ValueError("Independent audit checks are incomplete")
            seen = set()
            for entry in checks:
                if (not isinstance(entry, dict) or set(entry) != {id_key, "assessment", "rationale"}
                        or not isinstance(entry[id_key], str) or entry[id_key] in seen
                        or entry[id_key] not in expected or entry["assessment"] not in ("agree", "disagree")
                        or not isinstance(entry["rationale"], str) or not entry["rationale"].strip()):
                    raise ValueError("Independent audit check is invalid")
                seen.add(entry[id_key])
                disagreements += entry["assessment"] == "disagree"
            if seen != set(expected):
                raise ValueError("Independent audit checks are incomplete")
    return {"schema_version": 1, "status": "needs_adjudication" if disagreements else "recorded_agreement",
            "case_count": len(cases), "disagreements": disagreements,
            "manifest_sha256": digest(manifest_path), "audit_sha256": digest(private_path(audit_path)),
            "note": "Recorded agreement does not verify reviewer identity or scientific correctness."}


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
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
