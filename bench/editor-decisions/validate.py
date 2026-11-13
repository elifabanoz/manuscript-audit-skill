#!/usr/bin/env python3
"""Validate private, pre-decision prediction and first-decision label records."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
RUN_VALIDATOR = ROOT / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"
CATEGORIES = {"desk_reject", "reject_after_review", "major_revision", "minor_revision", "accept"}
STAGE = "first_editorial_decision"


def private_file(value, directory=False):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Benchmark paths must be absolute")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Benchmark inputs must stay outside Git")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing private benchmark input")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_record(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Oversized private JSON record")
    return json.loads(path.read_text(encoding="utf-8"))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid decision benchmark timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError("Invalid decision benchmark timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("Decision benchmark timestamps need a timezone")
    return parsed


def validate(manifest_file):
    manifest_file = private_file(manifest_file)
    manifest = read_record(manifest_file)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "cases"}
            or manifest["schema_version"] != 1 or not isinstance(manifest["cases"], list)
            or not manifest["cases"]):
        raise ValueError("Invalid decision benchmark manifest")
    rows, case_ids, runs = [], set(), set()
    for case in manifest["cases"]:
        if (not isinstance(case, dict) or set(case) != {"case_id", "run_directory", "prediction_file",
                                                   "outcome_file", "decision_letter_file"}
                or not isinstance(case["case_id"], str) or not case["case_id"].strip()
                or case["case_id"] in case_ids):
            raise ValueError("Decision case IDs and fields must be valid and unique")
        case_ids.add(case["case_id"])
        run = private_file(case["run_directory"], directory=True)
        if run in runs:
            raise ValueError("Decision cases must use distinct runs")
        runs.add(run)
        result = subprocess.run(["node", str(RUN_VALIDATOR), str(run)], capture_output=True,
                                text=True, check=False)
        if result.returncode:
            raise ValueError("Private audit run failed validation")
        run_manifest_file = private_file(run / "run-manifest.json")
        run_manifest = read_record(run_manifest_file)
        profile = read_record(private_file(run / "journal-profile.json"))
        prediction_file = private_file(case["prediction_file"])
        outcome_file = private_file(case["outcome_file"])
        letter_file = private_file(case["decision_letter_file"])
        prediction = read_record(prediction_file)
        outcome = read_record(outcome_file)
        if (not isinstance(prediction, dict)
                or set(prediction) != {"schema_version", "run_manifest_sha256", "target_issn",
                                       "stage", "category", "recorded_at", "predictor_id"}
                or prediction["schema_version"] != 1
                or prediction["run_manifest_sha256"] != digest(run_manifest_file)
                or prediction["target_issn"] != profile["issn"]
                or prediction["stage"] != STAGE or not isinstance(prediction["category"], str)
                or prediction["category"] not in CATEGORIES
                or not isinstance(prediction["predictor_id"], str)
                or not prediction["predictor_id"].strip()):
            raise ValueError("Prediction record is incomplete or stale")
        if (not isinstance(outcome, dict)
                or set(outcome) != {"schema_version", "target_issn", "stage", "category",
                                        "decision_at", "letter_sha256", "labeler", "labeled_at"}
                or outcome["schema_version"] != 1 or outcome["target_issn"] != profile["issn"]
                or outcome["stage"] != STAGE or not isinstance(outcome["category"], str)
                or outcome["category"] not in CATEGORIES
                or outcome["letter_sha256"] != digest(letter_file)
                or not isinstance(outcome["labeler"], str) or not outcome["labeler"].strip()):
            raise ValueError("Outcome record is incomplete or stale")
        created_at = timestamp(run_manifest["created_at"])
        recorded_at = timestamp(prediction["recorded_at"])
        decision_at = timestamp(outcome["decision_at"])
        labeled_at = timestamp(outcome["labeled_at"])
        if not created_at <= recorded_at < decision_at <= labeled_at:
            raise ValueError("Prediction must be recorded before the first decision")
        rows.append({"case_id": case["case_id"], "run_manifest_sha256": digest(run_manifest_file),
                     "prediction_sha256": digest(prediction_file), "outcome_sha256": digest(outcome_file),
                     "decision_letter_sha256": digest(letter_file)})
    return {"schema_version": 1, "status": "records_structurally_valid",
            "manifest_sha256": digest(manifest_file), "cases": rows,
            "note": "Timestamp and model provenance are self-declared. A human must verify the original decision stage, label, and prediction lock before any accuracy estimate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.manifest), indent=2))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
