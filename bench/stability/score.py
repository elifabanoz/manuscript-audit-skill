#!/usr/bin/env python3
"""Count structural repeatability across already validated private audit runs."""

import argparse
from collections import Counter
import hashlib
import json
from math import ceil
from pathlib import Path
import subprocess


VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"


def private_path(value, directory=False):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Stability paths must be absolute")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Stability inputs and results must stay outside Git")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing private stability input")
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("Oversized private stability record")
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def scope(manifest, profile, ledger):
    pinned = {key: manifest.get(key) for key in (
        "host", "models", "skill_sha256", "prompt_hashes", "config_hash", "data_freeze",
        "cross_host", "manuscript", "supplementary", "extraction", "sources")}
    units = sorted((item["kind"], item["section"], item["target_issn"],
                    item.get("rule_id"), item.get("review_class"), item["source_hash"])
                   for item in ledger)
    return fingerprint({"inputs": pinned, "profile": profile, "coverage_units": units})


def finding_key(finding):
    quotes = [item for item in finding["evidence"] if item["type"] == "manuscript_quote"]
    if len(quotes) != 1:
        return None
    anchor = quotes[0]
    return fingerprint({"category": finding["category"], "target_issn": finding["target_issn"],
                        "rule_id": finding.get("rule_id"), "review_class": finding.get("review_class"),
                        "path": anchor["path"], "line_id": anchor["line_id"]})


def score(suite_file):
    suite_file = private_path(suite_file)
    suite = read_json(suite_file)
    if (not isinstance(suite, dict) or set(suite) != {"schema_version", "run_directories"}
            or suite["schema_version"] != 1 or not isinstance(suite["run_directories"], list)
            or not 3 <= len(suite["run_directories"]) <= 10):
        raise ValueError("Stability suite needs 3 to 10 private runs")
    runs = [private_path(value, directory=True) for value in suite["run_directories"]]
    if len(set(runs)) != len(runs):
        raise ValueError("Stability runs must be distinct directories")
    rows, frequencies, expected_scope = [], Counter(), None
    for index, run in enumerate(runs, start=1):
        result = subprocess.run(["node", str(VALIDATOR), str(run)], capture_output=True,
                                text=True, check=False)
        if result.returncode:
            raise ValueError("Private audit run failed validation")
        manifest_file = private_path(run / "run-manifest.json")
        manifest = read_json(manifest_file)
        profile = read_json(private_path(run / "journal-profile.json"))
        ledger = read_json(private_path(run / "coverage-ledger.json"))
        findings = read_json(private_path(run / "findings.json"))
        current_scope = scope(manifest, profile, ledger)
        if expected_scope is None:
            expected_scope = current_scope
        elif current_scope != expected_scope:
            raise ValueError("Repeated runs differ in inputs, profile, model declarations, or coverage scope")
        if any(item["status"] != "covered" for item in ledger):
            raise ValueError("Every repeated coverage unit must be covered before comparison")
        keys, excluded = [], 0
        for finding in findings:
            if finding["verdict"] != "confirmed":
                continue
            key = finding_key(finding)
            if key is None:
                excluded += 1
            else:
                keys.append(key)
        duplicates = {key for key, count in Counter(keys).items() if count > 1}
        if duplicates:
            raise ValueError("A run has multiple confirmed findings at one class and line anchor")
        frequencies.update(keys)
        rows.append({"run_index": index, "run_manifest_sha256": digest(manifest_file),
                     "findings_sha256": digest(run / "findings.json"),
                     "confirmed_findings": len(keys) + excluded,
                     "single_quote_anchored_findings": len(keys),
                     "excluded_without_one_quote": excluded})
    threshold = ceil(len(runs) / 2)
    return {"schema_version": 1, "status": "local_structural_repeatability_only",
            "suite_sha256": digest(suite_file), "scope_sha256": expected_scope,
            "run_count": len(runs), "minimum_runs_for_retention": threshold,
            "runs": rows,
            "anchors": [{"anchor_sha256": key, "present_runs": count,
                         "retained_at_half_threshold": count >= threshold}
                        for key, count in sorted(frequencies.items())],
            "anchored_union": len(frequencies),
            "retained_anchor_count": sum(count >= threshold for count in frequencies.values()),
            "excluded_without_one_quote": sum(row["excluded_without_one_quote"] for row in rows),
            "note": "This is repeatability of one-line class anchors in validated local records, not correctness, confidence calibration, or general performance. Distinct directories and declarations do not prove independent hunts. Human review of anchor matches and run provenance is required."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite_file", type=Path)
    args = parser.parse_args()
    try:
        result = score(args.suite_file)
        output = private_path(args.suite_file).parent / "stability-results.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
