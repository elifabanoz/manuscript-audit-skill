#!/usr/bin/env python3
"""Score private compliance mutation runs with and without record validation."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


VALIDATOR = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators" / "validate-run.cjs"
DETERMINISTIC_CHECKER = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "tools" / "check_deterministic.py"
MUTATION_CLASSES = {"structural", "semantic", "numerical_format"}
QUOTE_TYPES = {"guideline", "manuscript_quote"}


def outside_git(path):
    resolved = path.resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise ValueError("Benchmark inputs and results must stay outside a Git checkout")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scored_inputs_sha256(scored):
    encoded = json.dumps(scored["cases"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scope_review_sha256(scored):
    entries = [(row["case_id"], row["input_sha256"]["scope_record"])
               for row in scored["cases"]]
    encoded = json.dumps(sorted(entries), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly: {', '.join(keys)}")


def audit_quotes(run, manifest, raw, final, case_id):
    """Count unsupported quoted evidence without changing the raw candidates."""
    source_cache = {}
    allowed_files = {manifest["manuscript"]["path"]: manifest["manuscript"]["sha256"]}
    allowed_files.update({item["path"]: item["sha256"] for item in manifest.get("supplementary", [])})

    def pinned_text(relative, expected):
        path = run / relative
        resolved = path.resolve()
        if run not in resolved.parents or path.is_symlink():
            raise ValueError(f"{case_id}: quoted evidence path escaped the private run")
        content = resolved.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{case_id}: quoted evidence input changed after run validation")
        return content.decode("utf-8")

    def supported(evidence):
        quote = evidence["quote"]
        normalized_quote = re.sub(r"\s+", " ", quote).strip()
        if evidence["type"] == "guideline":
            source = manifest["sources"].get(evidence["source_ref"])
            if source is None:
                return False
            key = evidence["source_ref"]
            if key not in source_cache:
                source_cache[key] = pinned_text(source["path"], source["sha256"])
            text = source_cache[key]
        else:
            relative = evidence["path"]
            if relative not in allowed_files or not re.fullmatch(r"L[1-9][0-9]*", evidence["line_id"]):
                return False
            if relative not in source_cache:
                source_cache[relative] = pinned_text(relative, allowed_files[relative])
            lines = re.split(r"\r?\n", source_cache[relative])
            number = int(evidence["line_id"][1:])
            if number > len(lines):
                return False
            text = lines[number - 1]
        return normalized_quote in re.sub(r"\s+", " ", text).strip()

    raw_count = 0
    raw_unsupported = 0
    for index, candidate in enumerate(raw):
        exact_keys(candidate, ("category", "rule_id", "evidence"), f"{case_id}: raw candidate {index}")
        evidence_items = candidate["evidence"]
        if not isinstance(evidence_items, list) or not evidence_items:
            raise ValueError(f"{case_id}: raw candidate {index} needs quoted evidence")
        if not any(isinstance(item, dict) and item.get("type") == "guideline" for item in evidence_items):
            raise ValueError(f"{case_id}: raw candidate {index} needs a guideline quote")
        for evidence in evidence_items:
            if not isinstance(evidence, dict) or evidence.get("type") not in QUOTE_TYPES:
                raise ValueError(f"{case_id}: raw candidate {index} has unsupported evidence")
            keys = ("type", "source_ref", "quote") if evidence["type"] == "guideline" else (
                "type", "path", "line_id", "quote")
            exact_keys(evidence, keys, f"{case_id}: raw candidate {index} evidence")
            if any(not isinstance(evidence[key], str) or not evidence[key].strip() for key in keys):
                raise ValueError(f"{case_id}: raw candidate {index} evidence has an empty field")
            raw_count += 1
            raw_unsupported += not supported(evidence)

    validated_count = 0
    for finding in final:
        if finding.get("verdict") != "confirmed" or finding.get("category") != "compliance":
            continue
        for evidence in finding["evidence"]:
            if evidence["type"] in QUOTE_TYPES:
                validated_count += 1
                if not supported(evidence):
                    raise ValueError(f"{case_id}: confirmed finding contains an unsupported quote")
    return {"raw_quotes": raw_count, "raw_unsupported_quotes": raw_unsupported,
            "validated_confirmed_quotes": validated_count, "validated_unsupported_quotes": 0}


def check_control_deterministic(run, manifest, profile, article_type, scored, case_id):
    applicable = {rule["rule_id"] for rule in profile["rules"]
                  if rule["kind"] == "deterministic"
                  and article_type in rule.get("applies_to", [article_type])}
    verified = applicable & scored
    if not verified:
        return
    path = run / "deterministic-checks.json"
    if not path.is_file():
        raise ValueError(f"{case_id}: control needs deterministic checks for verified rules")
    report = read_json(path)
    checks = report.get("checks") if isinstance(report, dict) else None
    if not isinstance(report, dict) \
            or report.get("manuscript_sha256") != manifest["manuscript"]["sha256"] \
            or report.get("article_type") != article_type \
            or not isinstance(checks, list) \
            or any(not isinstance(item, dict) or not isinstance(item.get("rule_id"), str) for item in checks) \
            or len(checks) != len(applicable) \
            or {item["rule_id"] for item in checks} != applicable:
        raise ValueError(f"{case_id}: deterministic checks do not match the control inputs")
    command = [sys.executable, str(DETERMINISTIC_CHECKER), str(run), "--article-type", article_type, "--no-write"]
    for key, flag in (("main_start", "--main-start"), ("main_end", "--main-end")):
        if key in report:
            if not isinstance(report[key], str) or not report[key].strip():
                raise ValueError(f"{case_id}: deterministic {key} must be nonempty")
            command.extend((flag, report[key]))
    recalculated = subprocess.run(command, capture_output=True, text=True)
    if recalculated.returncode:
        raise ValueError(f"{case_id}: deterministic checks cannot be recomputed: {recalculated.stderr.strip()}")
    if json.loads(recalculated.stdout) != report:
        raise ValueError(f"{case_id}: deterministic checks differ from recomputed measurements")
    failed = sorted(item["rule_id"] for item in checks
                    if item["rule_id"] in verified and item.get("status") != "met")
    if failed:
        raise ValueError(f"{case_id}: control has unmet deterministic rules: {', '.join(failed)}")


def checked_scope_record(path, run, manifest_path, profile_path, article_type, applicable, case_id):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError(f"{case_id}: scope_record must be an absolute private path")
    scope_path = Path(path).resolve()
    outside_git(scope_path)
    if scope_path.parent != run or scope_path.name != "scope-review.json":
        raise ValueError(f"{case_id}: scope_record must be scope-review.json directly inside its run")
    scope_hash = sha256(scope_path)
    record = read_json(scope_path)
    exact_keys(record, ("schema_version", "manifest_sha256", "profile_sha256", "article_type", "rules"),
               f"{case_id}: scope record")
    if (record["schema_version"] != 1 or record["manifest_sha256"] != sha256(manifest_path)
            or record["profile_sha256"] != sha256(profile_path) or record["article_type"] != article_type):
        raise ValueError(f"{case_id}: scope record differs from the scored inputs")
    if not isinstance(record["rules"], list):
        raise ValueError(f"{case_id}: scope rules must be a list")
    decisions = {}
    for entry in record["rules"]:
        exact_keys(entry, ("rule_id", "decision", "reason"), f"{case_id}: scope rule")
        rule_id = entry["rule_id"]
        if not isinstance(rule_id, str) or rule_id not in applicable or rule_id in decisions:
            raise ValueError(f"{case_id}: scope rule is unknown or duplicated")
        if entry["decision"] not in ("score", "exclude"):
            raise ValueError(f"{case_id}: scope decision must be score or exclude")
        if not isinstance(entry["reason"], str) or len(entry["reason"].strip()) < 20:
            raise ValueError(f"{case_id}: each scope decision needs a concrete reason")
        if entry["decision"] == "score" and applicable[rule_id].get("status") == "needs_validation":
            raise ValueError(f"{case_id}: an unresolved source rule cannot be scored: {rule_id}")
        decisions[rule_id] = entry
    if set(decisions) != set(applicable):
        raise ValueError(f"{case_id}: scope record does not cover every applicable rule")
    if sha256(scope_path) != scope_hash:
        raise ValueError(f"{case_id}: scope record changed during scoring")
    scored = {rule_id for rule_id, entry in decisions.items() if entry["decision"] == "score"}
    excluded = set(applicable) - scored
    reasons = {rule_id: decisions[rule_id]["reason"] for rule_id in sorted(excluded)}
    return scored, excluded, scope_hash, reasons


def load_case(case, freeze, schema_version):
    common = ("case_id", "kind", "article_type", "run", "raw_findings")
    if not isinstance(case, dict) or case.get("kind") not in ("control", "mutation"):
        raise ValueError("Each case needs kind control or mutation")
    keys = common if case["kind"] == "control" else (*common, "control_case_id", "expected_rule_id")
    if case["kind"] == "mutation" and schema_version >= 2:
        keys = (*keys, "mutation_class")
    if case["kind"] == "mutation" and schema_version >= 3:
        keys = (*keys, "proposal_record", "proposal_file")
    if schema_version >= 5:
        keys = (*keys, "scope_record")
    exact_keys(case, keys, f"case {case.get('case_id', '<unknown>')}")
    if schema_version >= 2 and case["kind"] == "mutation" and (
            not isinstance(case["mutation_class"], str) or case["mutation_class"] not in MUTATION_CLASSES):
        raise ValueError(f"{case['case_id']}: mutation_class must be structural, semantic, or numerical_format")
    if schema_version >= 3 and case["kind"] == "mutation" and (
            not isinstance(case["proposal_record"], str) or not Path(case["proposal_record"]).is_absolute()
            or not isinstance(case["proposal_file"], str) or not case["proposal_file"]
            or Path(case["proposal_file"]).name != case["proposal_file"]):
        raise ValueError(f"{case['case_id']}: proposal_record must be absolute and proposal_file must be a filename")
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
    if schema_version >= 5:
        scored, excluded, scope_hash, scope_exclusions = checked_scope_record(
            case["scope_record"], run, manifest_path, profile_path, case["article_type"],
            applicable, case["case_id"])
    else:
        scored = {rule_id for rule_id, rule in applicable.items() if rule.get("status") != "needs_validation"}
        excluded = set(applicable) - scored
    if not scored:
        raise ValueError(f"{case['case_id']}: no verified applicable rules can be scored")
    coverage = [unit for unit in ledger if unit["kind"] == "compliance"]
    if len(coverage) != len(applicable) or {unit["rule_id"] for unit in coverage} != set(applicable) \
            or any((unit["rule_id"] in scored and unit["status"] != "covered")
                   or (unit["rule_id"] in excluded and unit["status"] not in ("pending", "covered"))
                   for unit in coverage):
        raise ValueError(f"{case['case_id']}: applicable compliance coverage is incomplete")
    raw = read_json(raw_path)
    final = read_json(findings_path)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{case['case_id']}: raw_findings must be an array of objects")
    known = set(applicable) if schema_version >= 5 else scored
    all_raw_rules = set()
    for item in raw:
        if item.get("category") != "compliance" or item.get("rule_id") not in known:
            raise ValueError(f"{case['case_id']}: raw candidate must name a known compliance rule")
        all_raw_rules.add(item["rule_id"])
    all_final_rules = {item["rule_id"] for item in final
                       if item["category"] == "compliance" and item["verdict"] == "confirmed"}
    if not all_final_rules.issubset(all_raw_rules):
        raise ValueError(f"{case['case_id']}: validated findings include rules absent from raw candidates")
    raw_rules = all_raw_rules & scored
    final_rules = all_final_rules & scored
    quote_audit = audit_quotes(run, manifest, raw, final, case["case_id"]) if schema_version >= 4 else None
    if case["kind"] == "mutation" and (not isinstance(case["expected_rule_id"], str)
                                       or case["expected_rule_id"] not in known):
        raise ValueError(f"{case['case_id']}: expected_rule_id is absent from the profile")
    if case["kind"] == "control":
        check_control_deterministic(run, manifest, profile, case["article_type"], scored, case["case_id"])
    input_files = {
        "manifest": manifest_path, "profile": profile_path,
        "coverage_ledger": ledger_path, "raw_findings": raw_path, "findings": findings_path,
    }
    if (run / "deterministic-checks.json").is_file():
        input_files["deterministic_checks"] = run / "deterministic-checks.json"
    if schema_version >= 5:
        input_files["scope_record"] = run / "scope-review.json"
    input_hashes = {name: sha256(file) for name, file in input_files.items()}
    if schema_version >= 5 and input_hashes["scope_record"] != scope_hash:
        raise ValueError(f"{case['case_id']}: scope record changed during scoring")
    return {
        **case,
        "issn": profile["issn"],
        "sources": {key: source["sha256"] for key, source in manifest["sources"].items()},
        "manuscript_sha256": manifest["manuscript"]["sha256"],
        "scored_rule_ids": sorted(scored),
        "excluded_rule_ids": sorted(excluded),
        **({"scope_exclusions": scope_exclusions} if schema_version >= 5 else {}),
        "raw_rules": raw_rules,
        "final_rules": final_rules,
        **({"out_of_scope_raw_rules": all_raw_rules - scored,
            "out_of_scope_final_rules": all_final_rules - scored} if schema_version >= 5 else {}),
        "input_sha256": input_hashes,
        **({"quote_audit": quote_audit} if quote_audit is not None else {}),
    }


def verify_proposal(case, control):
    record_path = Path(case["proposal_record"]).resolve()
    outside_git(record_path)
    if record_path.name != "proposals.json":
        raise ValueError(f"{case['case_id']}: proposal_record must name proposals.json")
    record = read_json(record_path)
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("status") not in (
            "unreviewed_proposal", "unreviewed_proposals"):
        raise ValueError(f"{case['case_id']}: unsupported proposal record")
    if (not isinstance(record.get("control_run"), str)
            or Path(record["control_run"]).resolve() != Path(control["run"]).resolve()
            or record.get("control_manifest_sha256") != control["input_sha256"]["manifest"]
            or record.get("control_manuscript_sha256") != control["manuscript_sha256"]
            or record.get("profile_sha256") != control["input_sha256"]["profile"]
            or record.get("article_type") != case["article_type"]):
        raise ValueError(f"{case['case_id']}: proposal does not match its control inputs")
    if record["status"] == "unreviewed_proposals":
        entries = record.get("mutations")
        if not isinstance(entries, list):
            raise ValueError(f"{case['case_id']}: proposal mutations must be a list")
        selected = [item for item in entries if isinstance(item, dict)
                    and item.get("file") == case["proposal_file"]]
        if len(selected) != 1:
            raise ValueError(f"{case['case_id']}: proposal_file must select one mutation")
        proposal = selected[0]
    else:
        proposal = record
    if (proposal.get("file") != case["proposal_file"]
            or proposal.get("rule_id") != case["expected_rule_id"]
            or proposal.get("mutation_class_proposed") != case["mutation_class"]
            or proposal.get("manuscript_sha256") != case["manuscript_sha256"]):
        raise ValueError(f"{case['case_id']}: proposal differs from the scored mutation")
    manuscript = record_path.parent / case["proposal_file"]
    if not manuscript.is_file() or sha256(manuscript) != case["manuscript_sha256"]:
        raise ValueError(f"{case['case_id']}: proposal manuscript differs from the mutation run")
    return sha256(record_path)


def score(benchmark):
    exact_keys(benchmark, ("schema_version", "data_freeze", "cases"), "benchmark")
    schema_version = benchmark["schema_version"]
    if schema_version not in (1, 2, 3, 4, 5):
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
    cases = [load_case(item, freeze, schema_version) for item in benchmark["cases"]]
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
            if schema_version >= 5 and item["scored_rule_ids"] != control["scored_rule_ids"]:
                raise ValueError(f"{item['case_id']}: paired control has a different scoring scope")
            if item["manuscript_sha256"] == control["manuscript_sha256"]:
                raise ValueError(f"{item['case_id']}: mutation has the same manuscript hash as its control")
            proposal_hash = verify_proposal(item, control) if schema_version >= 3 else None
            target = item["expected_rule_id"]
            if target in control["raw_rules"] or target in control["final_rules"]:
                raise ValueError(f"{item['case_id']}: target rule was already flagged in its control")
            raw_flag = target in item["raw_rules"]
            final_flag = target in item["final_rules"]
        else:
            target = None
            raw_flag = bool(item["raw_rules"])
            final_flag = bool(item["final_rules"])
        row = {
            "case_id": item["case_id"], "kind": item["kind"], "target_rule_id": target,
            "raw_flagged": raw_flag, "validated_flagged": final_flag,
            "scored_rule_ids": item["scored_rule_ids"],
            "excluded_rule_ids": item["excluded_rule_ids"],
            "run_manifest_sha256": item["input_sha256"]["manifest"],
            "input_sha256": item["input_sha256"],
        }
        if schema_version >= 2 and item["kind"] == "mutation":
            row["mutation_class"] = item["mutation_class"]
        if schema_version >= 3 and item["kind"] == "mutation":
            row["proposal_record_sha256"] = proposal_hash
        if schema_version >= 4:
            row["quote_audit"] = item["quote_audit"]
        if schema_version >= 5:
            row["scope_exclusions"] = item["scope_exclusions"]
            row["out_of_scope_raw_rule_ids"] = sorted(item["out_of_scope_raw_rules"])
            row["out_of_scope_validated_rule_ids"] = sorted(item["out_of_scope_final_rules"])
            if item["kind"] == "mutation":
                row["off_target_raw_rule_ids"] = sorted(item["raw_rules"] - {target})
                row["off_target_validated_rule_ids"] = sorted(item["final_rules"] - {target})
        rows.append(row)
    def rate(numerator, denominator):
        return numerator / denominator
    raw_hits = sum(row["raw_flagged"] for row in rows if row["kind"] == "mutation")
    validated_hits = sum(row["validated_flagged"] for row in rows if row["kind"] == "mutation")
    raw_false_alarms = sum(row["raw_flagged"] for row in rows if row["kind"] == "control")
    validated_false_alarms = sum(row["validated_flagged"] for row in rows if row["kind"] == "control")
    result = {
        "schema_version": schema_version, "data_freeze": freeze,
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
    if schema_version >= 5:
        mutation_rows = [row for row in rows if row["kind"] == "mutation"]
        result["mutations"].update({
            "off_target_raw_cases": sum(bool(row["off_target_raw_rule_ids"]) for row in mutation_rows),
            "off_target_validated_cases": sum(bool(row["off_target_validated_rule_ids"])
                                               for row in mutation_rows),
            "off_target_raw_rule_flags": sum(len(row["off_target_raw_rule_ids"])
                                              for row in mutation_rows),
            "off_target_validated_rule_flags": sum(len(row["off_target_validated_rule_ids"])
                                                    for row in mutation_rows),
        })
    if schema_version >= 2:
        by_class = {}
        for mutation_class in sorted({item["mutation_class"] for item in mutations}):
            class_mutations = [row for row in rows if row.get("mutation_class") == mutation_class]
            control_ids = {item["control_case_id"] for item in mutations
                           if item["mutation_class"] == mutation_class}
            class_controls = [row for row in rows if row["case_id"] in control_ids]
            class_raw_hits = sum(row["raw_flagged"] for row in class_mutations)
            class_validated_hits = sum(row["validated_flagged"] for row in class_mutations)
            class_raw_false_alarms = sum(row["raw_flagged"] for row in class_controls)
            class_validated_false_alarms = sum(row["validated_flagged"] for row in class_controls)
            by_class[mutation_class] = {
                "mutations": {"count": len(class_mutations), "raw_detected": class_raw_hits,
                              "validated_detected": class_validated_hits,
                              "raw_recall": rate(class_raw_hits, len(class_mutations)),
                              "validated_recall": rate(class_validated_hits, len(class_mutations))},
                "paired_controls": {"count": len(class_controls), "raw_flagged": class_raw_false_alarms,
                                    "validated_flagged": class_validated_false_alarms,
                                    "raw_false_positive_rate": rate(class_raw_false_alarms, len(class_controls)),
                                    "validated_false_positive_rate": rate(class_validated_false_alarms,
                                                                          len(class_controls))},
                "validation_false_positive_rate_reduction": rate(
                    class_raw_false_alarms - class_validated_false_alarms, len(class_controls)),
            }
            if schema_version >= 5:
                by_class[mutation_class]["mutations"].update({
                    "off_target_raw_cases": sum(bool(row["off_target_raw_rule_ids"])
                                                for row in class_mutations),
                    "off_target_validated_cases": sum(bool(row["off_target_validated_rule_ids"])
                                                      for row in class_mutations),
                    "off_target_raw_rule_flags": sum(len(row["off_target_raw_rule_ids"])
                                                   for row in class_mutations),
                    "off_target_validated_rule_flags": sum(len(row["off_target_validated_rule_ids"])
                                                         for row in class_mutations),
                })
        result["by_mutation_class"] = by_class
    if schema_version >= 4:
        raw_quotes = sum(row["quote_audit"]["raw_quotes"] for row in rows)
        raw_unsupported = sum(row["quote_audit"]["raw_unsupported_quotes"] for row in rows)
        validated_quotes = sum(row["quote_audit"]["validated_confirmed_quotes"] for row in rows)
        result["quote_audit"] = {
            "raw_quotes": raw_quotes,
            "raw_unsupported_quotes": raw_unsupported,
            "raw_unsupported_quote_rate": raw_unsupported / raw_quotes if raw_quotes else None,
            "validated_confirmed_quotes": validated_quotes,
            "validated_unsupported_quotes": 0,
        }
    return result


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
