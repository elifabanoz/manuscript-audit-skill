#!/usr/bin/env python3
"""Prepare a private, hash-bound worksheet for human peer-issue labels."""

import argparse
import json
from pathlib import Path

from score import digest, private_path, read_json, validate_run


def prepare(run_directory, peer_report_file, output_file):
    run = private_path(run_directory, directory=True)
    report = private_path(peer_report_file)
    output = Path(output_file)
    if not output.is_absolute():
        raise ValueError("Label packet output must be absolute")
    parent = private_path(output.parent, directory=True)
    output = parent / output.name
    validate_run(run)
    findings_path = private_path(run / "findings.json")
    manifest_path = private_path(run / "run-manifest.json")
    findings = read_json(findings_path)
    manifest = read_json(manifest_path)
    confirmed = [{"finding_id": item["id"], "review_class": item["review_class"]}
                 for item in findings if item["category"] == "review" and item["verdict"] == "confirmed"]
    packet = {
        "schema_version": 1,
        "status": "pending_human_labels",
        "note": "Read the first submitted manuscript and the original peer report. Split the report into distinct issues, decide scope, and adjudicate matches against confirmed review findings. The packet does not copy either text or verify reviewer identity.",
        "run_directory": str(run),
        "run_manifest_sha256": digest(manifest_path),
        "manuscript_sha256": manifest["manuscript"]["sha256"],
        "peer_report_file": str(report),
        "peer_report_sha256": digest(report),
        "findings_sha256": digest(findings_path),
        "confirmed_review_findings": confirmed,
        "pending_review_findings": [item["id"] for item in findings
                                    if item["category"] == "review" and item["verdict"] == "needs_validation"],
        "label_template": {
            "schema_version": 1,
            "peer_report_sha256": digest(report),
            "findings_sha256": digest(findings_path),
            "reviewer": "",
            "reviewed_at": "",
            "manuscript_version": "initial_submission",
            "issues": []
        }
    }
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("peer_report_file", type=Path)
    parser.add_argument("output_file", type=Path)
    args = parser.parse_args()
    try:
        print(prepare(args.run_directory, args.peer_report_file, args.output_file))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
