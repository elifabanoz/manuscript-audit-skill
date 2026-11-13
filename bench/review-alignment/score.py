#!/usr/bin/env python3
"""Score human-adjudicated peer issues against a validated private audit run."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess


VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"
def private_path(value, directory=False):
    if not isinstance(value, (str, Path)):
        raise ValueError("Benchmark paths must be absolute")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Benchmark paths must be absolute")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Benchmark inputs and results must stay outside Git")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing private benchmark input")
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Oversized private JSON record")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_run(run):
    result = subprocess.run(["node", str(VALIDATOR), str(run)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f"Private audit run failed validation: {result.stderr.strip()}")


def score_manifest(manifest_path):
    manifest_path = private_path(manifest_path)
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "cases"}
            or manifest["schema_version"] != 1 or not isinstance(manifest["cases"], list)
            or not manifest["cases"]):
        raise ValueError("Invalid review-alignment manifest")
    rows, seen_cases, seen_runs = [], set(), set()
    for case in manifest["cases"]:
        if not isinstance(case, dict) or set(case) != {"case_id", "run_directory", "peer_report_file", "labels_file"}:
            raise ValueError("Review-alignment case has unexpected fields")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_cases:
            raise ValueError("Review-alignment case IDs must be unique")
        seen_cases.add(case_id)
        run = private_path(case["run_directory"], directory=True)
        if run in seen_runs:
            raise ValueError("Review-alignment cases must use distinct runs")
        seen_runs.add(run)
        validate_run(run)
        report = private_path(case["peer_report_file"])
        labels_path = private_path(case["labels_file"])
        findings_path = private_path(run / "findings.json")
        findings = read_json(findings_path)
        confirmed = {item["id"]: item for item in findings
                     if item["category"] == "review" and item["verdict"] == "confirmed"}
        labels = read_json(labels_path)
        if (not isinstance(labels, dict) or set(labels) != {"schema_version", "peer_report_sha256",
                "findings_sha256", "reviewer", "reviewed_at", "manuscript_version", "issues"}
                or labels["schema_version"] != 1 or labels["peer_report_sha256"] != digest(report)
                or labels["findings_sha256"] != digest(findings_path)
                or not isinstance(labels["reviewer"], str) or not labels["reviewer"].strip()
                or labels["manuscript_version"] != "initial_submission"
                or not isinstance(labels["issues"], list) or not labels["issues"]):
            raise ValueError("Human labels are missing or stale")
        try:
            reviewed_at = datetime.fromisoformat(labels["reviewed_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise ValueError("Human review needs a timestamp") from None
        if reviewed_at.tzinfo is None:
            raise ValueError("Human review needs a timezone")
        issue_ids, matched_findings, in_scope, matched = set(), set(), 0, 0
        for issue in labels["issues"]:
            if (not isinstance(issue, dict) or set(issue) != {"issue_id", "in_scope", "matched_finding_ids", "rationale"}
                    or not isinstance(issue["issue_id"], str) or not issue["issue_id"].strip()
                    or issue["issue_id"] in issue_ids or type(issue["in_scope"]) is not bool
                    or not isinstance(issue["rationale"], str) or not issue["rationale"].strip()
                    or not isinstance(issue["matched_finding_ids"], list)
                    or any(not isinstance(finding_id, str) for finding_id in issue["matched_finding_ids"])
                    or len(set(issue["matched_finding_ids"])) != len(issue["matched_finding_ids"])):
                raise ValueError("Human issue adjudication is incomplete")
            issue_ids.add(issue["issue_id"])
            if not issue["in_scope"] and issue["matched_finding_ids"]:
                raise ValueError("Out-of-scope issue cannot have a matched finding")
            for finding_id in issue["matched_finding_ids"]:
                if finding_id not in confirmed:
                    raise ValueError("Issue match must name a confirmed review finding")
                matched_findings.add(finding_id)
            if issue["in_scope"]:
                in_scope += 1
                matched += bool(issue["matched_finding_ids"])
        if not in_scope:
            raise ValueError("At least one peer issue must be in scope")
        rows.append({"case_id": case_id, "peer_issues_in_scope": in_scope,
                     "peer_issues_matched": matched, "peer_issue_recall": matched / in_scope,
                     "confirmed_review_findings": len(confirmed),
                     "confirmed_findings_unmentioned": len(set(confirmed) - matched_findings),
                     "peer_report_sha256": digest(report), "findings_sha256": digest(findings_path),
                     "labels_sha256": digest(labels_path)})
    total = sum(row["peer_issues_in_scope"] for row in rows)
    found = sum(row["peer_issues_matched"] for row in rows)
    return {"schema_version": 1, "status": "local_human_label_records",
            "note": "Only human-adjudicated matches to confirmed review findings are counted. A peer report is incomplete ground truth; unmentioned findings are not false positives. Label identity, first-submission version, and scientific validity require independent audit.",
            "manifest_sha256": digest(manifest_path), "cases": rows,
            "overall": {"peer_issues_in_scope": total, "peer_issues_matched": found,
                        "peer_issue_recall": found / total,
                        "confirmed_findings_unmentioned": sum(row["confirmed_findings_unmentioned"] for row in rows)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Absolute private review-alignment manifest")
    args = parser.parse_args()
    try:
        result = score_manifest(args.manifest)
        output = private_path(args.manifest).parent / "review-alignment-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
