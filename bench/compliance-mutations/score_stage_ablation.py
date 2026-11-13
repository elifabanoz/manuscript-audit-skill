#!/usr/bin/env python3
"""Score private defense and record-verification stages after human review."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from finalize_labels import validate_labels, verify_profile_agreements
from score import outside_git, score, sha256


VERIFY_STAGE = Path(__file__).with_name("verify_defense_stage.cjs")


def stage_rates(rows):
    mutations = [row for row in rows if row["kind"] == "mutation"]
    controls = [row for row in rows if row["kind"] == "control"]
    if not mutations or not controls:
        raise ValueError("Stage rates need mutations and their controls")
    defense_hits = sum(row["defense_flagged"] for row in mutations)
    final_hits = sum(row["validated_flagged"] for row in mutations)
    defense_alarms = sum(row["defense_flagged"] for row in controls)
    final_alarms = sum(row["validated_flagged"] for row in controls)
    defense_recall = defense_hits / len(mutations)
    final_recall = final_hits / len(mutations)
    defense_fpr = defense_alarms / len(controls)
    final_fpr = final_alarms / len(controls)
    return {
        "mutations": {"count": len(mutations), "defense_detected": defense_hits,
                      "verified_detected": final_hits, "defense_recall": defense_recall,
                      "verified_recall": final_recall},
        "controls": {"count": len(controls), "defense_flagged": defense_alarms,
                     "verified_flagged": final_alarms, "defense_false_positive_rate": defense_fpr,
                     "verified_false_positive_rate": final_fpr},
        "record_verification_recall_difference": final_recall - defense_recall,
        "record_verification_false_positive_rate_difference": final_fpr - defense_fpr,
    }


def measure(scored, stages, control_ids):
    if scored["schema_version"] < 5:
        raise ValueError("Stage ablation requires benchmark schema version 5 and full rule labels")
    by_id = {row["case_id"]: row for row in scored["cases"]}
    if set(stages) != set(by_id):
        raise ValueError("A defense-stage snapshot is required for every case")
    rows = []
    for row in scored["cases"]:
        case_id = row["case_id"]
        confirmed = stages[case_id]["confirmed_rule_ids"]
        if not isinstance(confirmed, list) or len(confirmed) != len(set(confirmed)) \
                or any(not isinstance(rule, str) for rule in confirmed):
            raise ValueError(f"{case_id}: invalid defense-stage rule IDs")
        scoped = set(confirmed) & set(row["scored_rule_ids"])
        if row["kind"] == "mutation":
            target = row["target_rule_id"]
            defense_flag = target in scoped
            control_id = control_ids.get(case_id)
            if control_id not in by_id or by_id[control_id]["kind"] != "control":
                raise ValueError(f"{case_id}: paired control is missing")
            control = stages[control_id]["confirmed_rule_ids"]
            if target in control:
                raise ValueError(f"{case_id}: paired control was flagged at the defense stage")
        else:
            defense_flag = bool(scoped)
        rows.append({"case_id": case_id, "kind": row["kind"],
                     "defense_flagged": defense_flag,
                     "validated_flagged": row["validated_flagged"],
                     "defense_snapshot_sha256": stages[case_id]["snapshot_sha256"],
                     **({"mutation_class": row["mutation_class"], "control_case_id": control_id}
                        if row["kind"] == "mutation" else {})})
    result = {**stage_rates(rows), "cases": rows}
    by_class = {}
    for mutation_class in sorted({row["mutation_class"] for row in rows if row["kind"] == "mutation"}):
        class_mutations = [row for row in rows if row.get("mutation_class") == mutation_class]
        control_ids = {row["control_case_id"] for row in class_mutations}
        class_controls = [row for row in rows if row["case_id"] in control_ids]
        by_class[mutation_class] = stage_rates(class_mutations + class_controls)
    result["by_mutation_class"] = by_class
    return result


def finalize(cases_path, labels_path, output_path=None):
    outside_git(cases_path)
    outside_git(labels_path)
    cases_bytes = cases_path.read_bytes()
    labels_bytes = labels_path.read_bytes()
    benchmark = json.loads(cases_bytes)
    scored = score(benchmark)
    if scored["schema_version"] < 5:
        raise ValueError("Stage ablation requires benchmark schema version 5")
    benchmark_hash = hashlib.sha256(cases_bytes).hexdigest()
    agreements = verify_profile_agreements(benchmark, scored)
    review = validate_labels(scored, json.loads(labels_bytes), benchmark_hash)
    runs = {case["case_id"]: Path(case["run"]).resolve() for case in benchmark["cases"]}
    for case in benchmark["cases"]:
        run = runs[case["case_id"]]
        if Path(case["raw_findings"]).resolve() != run / "raw-findings.json":
            raise ValueError(f"{case['case_id']}: stage capture requires raw-findings.json in its run")
    stages = {}
    for case_id, run in runs.items():
        check = subprocess.run(["node", str(VERIFY_STAGE), str(run)],
                               capture_output=True, text=True)
        if check.returncode:
            raise ValueError(f"{case_id}: defense-stage verification failed: {check.stderr.strip()}")
        stages[case_id] = json.loads(check.stdout)
    controls = {case["case_id"]: case["control_case_id"] for case in benchmark["cases"]
                if case["kind"] == "mutation"}
    result = measure(scored, stages, controls)
    if sha256(cases_path) != benchmark_hash or sha256(labels_path) != hashlib.sha256(labels_bytes).hexdigest():
        raise ValueError("Benchmark manifest or human labels changed during stage scoring")
    for case_id, run in runs.items():
        if sha256(run / "defense-stage.json") != stages[case_id]["snapshot_sha256"]:
            raise ValueError(f"{case_id}: defense-stage snapshot changed during scoring")
    output = output_path or cases_path.parent / "stage-ablation-scores.json"
    outside_git(output)
    result.update({"schema_version": 1, "data_freeze": scored["data_freeze"],
                   "human_review_status": "approved", "human_review": review,
                   "profile_agreements": agreements,
                   "benchmark_manifest_sha256": benchmark_hash,
                   "human_labels_sha256": hashlib.sha256(labels_bytes).hexdigest()})
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Private benchmark case manifest")
    parser.add_argument("labels", type=Path, help="Private approved human labels")
    parser.add_argument("--output", type=Path, help="Private output path")
    args = parser.parse_args()
    try:
        print(finalize(args.cases, args.labels, args.output))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
