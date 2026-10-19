#!/usr/bin/env python3
"""Score private compliance mutation runs with and without record validation."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path


VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"


def outside_git(path):
    resolved = path.resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise ValueError("Benchmark inputs and results must stay outside a Git checkout")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly: {', '.join(keys)}")


def load_case(case, freeze):
    common = ("case_id", "kind", "article_type", "run", "raw_findings")
    if not isinstance(case, dict) or case.get("kind") not in ("control", "mutation"):
        raise ValueError("Each case needs kind control or mutation")
    keys = common if case["kind"] == "control" else (*common, "control_case_id", "expected_rule_id")
    exact_keys(case, keys, f"case {case.get('case_id', '<unknown>')}")
    if not isinstance(case["case_id"], str) or not case["case_id"].strip():
        raise ValueError("case_id must be nonempty")
    if not isinstance(case["run"], str) or not Path(case["run"]).is_absolute():
        raise ValueError(f"{case['case_id']}: run must be an absolute path")
    if not isinstance(case["raw_findings"], str) or not Path(case["raw_findings"]).is_absolute():
        raise ValueError(f"{case['case_id']}: raw_findings must be an absolute path")
    run = Path(case["run"]).resolve()
    raw_path = Path(case["raw_findings"]).resolve()
    outside_git(run)
    outside_git(raw_path)
    if raw_path.parent != run:
        raise ValueError(f"{case['case_id']}: raw_findings must be directly inside its private run")
    check = subprocess.run(["node", str(VALIDATOR), str(run)], capture_output=True, text=True)
    if check.returncode:
        raise ValueError(f"{case['case_id']}: full run validation failed: {check.stderr.strip()}")
    manifest_path = run / "run-manifest.json"
    profile_path = run / "journal-profile.json"
    ledger_path = run / "coverage-ledger.json"
    findings_path = run / "findings.json"
    manifest = read_json(manifest_path)
    profile = read_json(profile_path)
    ledger = read_json(ledger_path)
    if manifest["data_freeze"] != freeze:
        raise ValueError(f"{case['case_id']}: data_freeze differs from benchmark")
    if case["article_type"] not in profile["article_types"]:
        raise ValueError(f"{case['case_id']}: article_type is absent from the profile")
    applicable = {rule["rule_id"]: rule for rule in profile["rules"]
                  if case["article_type"] in rule.get("applies_to", [case["article_type"]])}
    scored = {rule_id for rule_id, rule in applicable.items() if rule.get("status") != "needs_validation"}
    excluded = set(applicable) - scored
    if not scored:
        raise ValueError(f"{case['case_id']}: no verified applicable rules can be scored")
    coverage = [unit for unit in ledger if unit["kind"] == "compliance"]
    if len(coverage) != len(applicable) or {unit["rule_id"] for unit in coverage} != set(applicable) \
            or any(unit["status"] != ("pending" if unit["rule_id"] in excluded else "covered") for unit in coverage):
        raise ValueError(f"{case['case_id']}: applicable compliance coverage is incomplete")
    raw = read_json(raw_path)
    final = read_json(findings_path)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{case['case_id']}: raw_findings must be an array of objects")
    known = scored
    raw_rules = set()
    for item in raw:
        if item.get("category") != "compliance" or item.get("rule_id") not in known:
            raise ValueError(f"{case['case_id']}: raw candidate must name a known compliance rule")
        raw_rules.add(item["rule_id"])
    final_rules = {item["rule_id"] for item in final if item["category"] == "compliance" and item["verdict"] == "confirmed"}
    if not final_rules.issubset(raw_rules):
        raise ValueError(f"{case['case_id']}: validated findings include rules absent from raw candidates")
    if case["kind"] == "mutation" and (not isinstance(case["expected_rule_id"], str)
                                       or case["expected_rule_id"] not in known):
        raise ValueError(f"{case['case_id']}: expected_rule_id is absent from the profile")
    return {
        **case,
        "issn": profile["issn"],
        "sources": {key: source["sha256"] for key, source in manifest["sources"].items()},
        "manuscript_sha256": manifest["manuscript"]["sha256"],
        "scored_rule_ids": sorted(scored),
        "excluded_rule_ids": sorted(excluded),
        "raw_rules": raw_rules,
        "final_rules": final_rules,
        "input_sha256": {name: sha256(file) for name, file in (
            ("manifest", manifest_path), ("profile", profile_path),
            ("coverage_ledger", ledger_path), ("raw_findings", raw_path), ("findings", findings_path))},
    }


def score(benchmark):
    exact_keys(benchmark, ("schema_version", "data_freeze", "cases"), "benchmark")
    if benchmark["schema_version"] != 1:
        raise ValueError("Unsupported benchmark schema_version")
    freeze = benchmark["data_freeze"]
    if not isinstance(freeze, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", freeze):
        raise ValueError("Benchmark data_freeze must be a YYYY-MM-DD date")
    try:
        date.fromisoformat(freeze)
    except ValueError as error:
        raise ValueError("Benchmark data_freeze is not a valid date") from error
    if not isinstance(benchmark["cases"], list):
        raise ValueError("Benchmark cases must be an array")
    cases = [load_case(item, freeze) for item in benchmark["cases"]]
    ids = [item["case_id"] for item in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Benchmark case IDs must be unique")
    by_id = {item["case_id"]: item for item in cases}
    controls = [item for item in cases if item["kind"] == "control"]
    mutations = [item for item in cases if item["kind"] == "mutation"]
    if not controls or not mutations:
        raise ValueError("Benchmark needs at least one control and one mutation")
    rows = []
    for item in cases:
        if item["kind"] == "mutation":
            control = by_id.get(item["control_case_id"])
            if not control or control["kind"] != "control":
                raise ValueError(f"{item['case_id']}: control_case_id is not a control case")
            if item["issn"] != control["issn"] or item["input_sha256"]["profile"] != control["input_sha256"]["profile"]:
                raise ValueError(f"{item['case_id']}: paired control has a different journal profile")
            if item["article_type"] != control["article_type"]:
                raise ValueError(f"{item['case_id']}: paired control has a different article type")
            if item["sources"] != control["sources"]:
                raise ValueError(f"{item['case_id']}: paired control has a different source snapshot")
            if item["manuscript_sha256"] == control["manuscript_sha256"]:
                raise ValueError(f"{item['case_id']}: mutation has the same manuscript hash as its control")
            target = item["expected_rule_id"]
            if target in control["raw_rules"] or target in control["final_rules"]:
                raise ValueError(f"{item['case_id']}: target rule was already flagged in its control")
            raw_flag = target in item["raw_rules"]
            final_flag = target in item["final_rules"]
        else:
            target = None
            raw_flag = bool(item["raw_rules"])
            final_flag = bool(item["final_rules"])
        rows.append({
            "case_id": item["case_id"], "kind": item["kind"], "target_rule_id": target,
            "raw_flagged": raw_flag, "validated_flagged": final_flag,
            "scored_rule_ids": item["scored_rule_ids"],
            "excluded_rule_ids": item["excluded_rule_ids"],
            "run_manifest_sha256": item["input_sha256"]["manifest"],
            "input_sha256": item["input_sha256"],
        })
    def rate(numerator, denominator):
        return numerator / denominator
    raw_hits = sum(row["raw_flagged"] for row in rows if row["kind"] == "mutation")
    validated_hits = sum(row["validated_flagged"] for row in rows if row["kind"] == "mutation")
    raw_false_alarms = sum(row["raw_flagged"] for row in rows if row["kind"] == "control")
    validated_false_alarms = sum(row["validated_flagged"] for row in rows if row["kind"] == "control")
    return {
        "schema_version": 1, "data_freeze": freeze,
        "mutations": {"count": len(mutations), "raw_detected": raw_hits,
                      "validated_detected": validated_hits,
                      "raw_recall": rate(raw_hits, len(mutations)),
                      "validated_recall": rate(validated_hits, len(mutations))},
        "controls": {"count": len(controls), "raw_flagged": raw_false_alarms,
                     "validated_flagged": validated_false_alarms,
                     "raw_false_positive_rate": rate(raw_false_alarms, len(controls)),
                     "validated_false_positive_rate": rate(validated_false_alarms, len(controls))},
        "validation_false_positive_rate_reduction": rate(raw_false_alarms - validated_false_alarms, len(controls)),
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Private benchmark case manifest outside Git")
    args = parser.parse_args()
    try:
        outside_git(args.cases)
        benchmark = read_json(args.cases)
        result = score(benchmark)
        result["benchmark_manifest_sha256"] = sha256(args.cases)
        output = args.cases.parent / "compliance-mutation-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
