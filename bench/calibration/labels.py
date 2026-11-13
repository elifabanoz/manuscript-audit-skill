#!/usr/bin/env python3
"""Prepare and validate private human correctness labels for finding calibration."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess


VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"
ASSESSMENTS = {"supported", "unsupported", "unclear"}


def private_path(value, directory=False):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Calibration paths must be absolute")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Calibration inputs must stay outside Git")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing private calibration input")
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Oversized private calibration record")
    return json.loads(path.read_text(encoding="utf-8"))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Human label time needs a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid human label timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("Human label time needs a timezone")
    return parsed


def run_inputs(run_directory):
    run = private_path(run_directory, directory=True)
    result = subprocess.run(["node", str(VALIDATOR), str(run)], capture_output=True,
                            text=True, check=False)
    if result.returncode:
        raise ValueError("Private audit run failed validation")
    manifest_file = private_path(run / "run-manifest.json")
    findings_file = private_path(run / "findings.json")
    findings = read_json(findings_file)
    if not isinstance(findings, list) or not findings:
        raise ValueError("Calibration labels need at least one recorded finding")
    ids = [item["id"] for item in findings]
    if len(set(ids)) != len(ids):
        raise ValueError("Calibration finding IDs must be unique")
    return run, manifest_file, findings_file, ids


def prepare(run_directory, output_file):
    run, manifest_file, findings_file, ids = run_inputs(run_directory)
    output = Path(output_file)
    if not output.is_absolute():
        raise ValueError("Calibration packet output must be absolute")
    output = output.resolve()
    if any((parent / ".git").exists() for parent in (output, *output.parents)) or not output.parent.is_dir():
        raise ValueError("Calibration packet output must stay in an existing private directory")
    packet = {"schema_version": 1, "status": "pending_human_labels", "run_directory": str(run),
              "run_manifest_sha256": digest(manifest_file), "findings_sha256": digest(findings_file),
              "finding_ids": ids,
              "label_template": {"schema_version": 1, "run_manifest_sha256": digest(manifest_file),
                                 "findings_sha256": digest(findings_file),
                                 "reviewers": [{"reviewer": "", "reviewed_at": "", "labels": []},
                                               {"reviewer": "", "reviewed_at": "", "labels": []}],
                                 "adjudication": {"adjudicator": "", "reviewed_at": "", "labels": []}},
              "note": "Review all findings independently against the original private evidence. This packet contains IDs and hashes, not manuscript or finding text."}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2)
        handle.write("\n")
    return output


def checked_labels(rows, expected):
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("Human labels must cover every finding")
    found = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"finding_id", "assessment", "rationale"}
                or not isinstance(row["finding_id"], str) or row["finding_id"] in found
                or row["finding_id"] not in expected or not isinstance(row["assessment"], str)
                or row["assessment"] not in ASSESSMENTS
                or not isinstance(row["rationale"], str) or not row["rationale"].strip()):
            raise ValueError("Human finding label is invalid")
        found[row["finding_id"]] = row["assessment"]
    if set(found) != set(expected):
        raise ValueError("Human labels must cover every finding")
    return found


def validate(run_directory, labels_file):
    _, manifest_file, findings_file, ids = run_inputs(run_directory)
    run_created_at = timestamp(read_json(manifest_file)["created_at"])
    labels_file = private_path(labels_file)
    labels = read_json(labels_file)
    if (not isinstance(labels, dict) or set(labels) !=
            {"schema_version", "run_manifest_sha256", "findings_sha256", "reviewers", "adjudication"}
            or labels["schema_version"] != 1 or labels["run_manifest_sha256"] != digest(manifest_file)
            or labels["findings_sha256"] != digest(findings_file)
            or not isinstance(labels["reviewers"], list) or len(labels["reviewers"]) != 2
            or not isinstance(labels["adjudication"], dict)):
        raise ValueError("Human calibration labels are incomplete or stale")
    reviewers, decisions, review_times = [], [], []
    for reviewer in labels["reviewers"]:
        if (not isinstance(reviewer, dict) or set(reviewer) != {"reviewer", "reviewed_at", "labels"}
                or not isinstance(reviewer["reviewer"], str) or not reviewer["reviewer"].strip()):
            raise ValueError("Independent reviewer record is incomplete")
        review_times.append(timestamp(reviewer["reviewed_at"]))
        reviewers.append(reviewer["reviewer"].strip().casefold())
        decisions.append(checked_labels(reviewer["labels"], ids))
    adjudication = labels["adjudication"]
    if (set(adjudication) != {"adjudicator", "reviewed_at", "labels"}
            or not isinstance(adjudication["adjudicator"], str)
            or not adjudication["adjudicator"].strip()):
        raise ValueError("Human adjudication record is incomplete")
    adjudicator = adjudication["adjudicator"].strip().casefold()
    if len(set([*reviewers, adjudicator])) != 3:
        raise ValueError("Two reviewers and adjudicator must have distinct names")
    adjudicated_at = timestamp(adjudication["reviewed_at"])
    if any(reviewed_at < run_created_at or adjudicated_at < reviewed_at for reviewed_at in review_times):
        raise ValueError("Human labels must follow the run and adjudication must follow both reviews")
    final = checked_labels(adjudication["labels"], ids)
    disagreements = sum(decisions[0][id] != decisions[1][id] for id in ids)
    return {"schema_version": 1, "status": "human_label_records_complete",
            "run_manifest_sha256": digest(manifest_file), "findings_sha256": digest(findings_file),
            "labels_sha256": digest(labels_file), "finding_count": len(ids),
            "reviewer_disagreements": disagreements,
            "adjudicated_counts": {value: sum(final[id] == value for id in ids)
                                   for value in ("supported", "unsupported", "unclear")},
            "note": "Record completeness does not prove reviewer identity or label correctness. No confidence model or reporting threshold is fitted."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "validate"))
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            print(prepare(args.run_directory, args.record))
        else:
            print(json.dumps(validate(args.run_directory, args.record), indent=2))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
