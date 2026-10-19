#!/usr/bin/env python3
"""Check user-specified means of single integer items for GRIM feasibility."""

import argparse
import hashlib
import json
import re
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from build_coverage import corpus_hash
from list_dois import read_corpus
from prepare_run import outside_git


DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")
LINE_ID = re.compile(r"^L([1-9]\d*)$")
FIELDS = {"case_id", "path", "line_id", "reported_mean", "sample_size", "scale_min", "scale_max"}


def checked_cases(path, files):
    path = path.resolve()
    outside_git(path)
    raw = path.read_bytes()
    cases = json.loads(raw.decode("utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Cases must be a nonempty JSON array")
    available = {name: content.splitlines() for name, content in files}
    ids = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != FIELDS:
            raise ValueError(f"Case {index + 1} has missing or unknown fields")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in ids:
            raise ValueError("Case IDs must be nonempty and unique")
        ids.add(case_id)
        if not isinstance(case["path"], str) or case["path"] not in available:
            raise ValueError(f"Case {case_id} references a file outside the supplied corpus")
        line_match = LINE_ID.fullmatch(case["line_id"]) if isinstance(case["line_id"], str) else None
        if not line_match or int(line_match.group(1)) > len(available[case["path"]]):
            raise ValueError(f"Case {case_id} has an invalid line ID")
        mean = case["reported_mean"]
        if not isinstance(mean, str) or not DECIMAL.fullmatch(mean):
            raise ValueError(f"Case {case_id} needs a plain decimal mean string")
        line = available[case["path"]][int(line_match.group(1)) - 1]
        if not re.search(r"(?<![\w.+-])" + re.escape(mean) + r"(?!\w|\.\d)", line):
            raise ValueError(f"Case {case_id} mean is absent from its cited line")
        for field in ("sample_size", "scale_min", "scale_max"):
            value = case[field]
            if type(value) is not int or abs(value) > 10_000_000:
                raise ValueError(f"Case {case_id} {field} must be a bounded integer")
        if case["sample_size"] < 1 or case["scale_min"] > case["scale_max"]:
            raise ValueError(f"Case {case_id} has an invalid sample size or scale")
    return cases, raw


def evaluate(case):
    mean = Decimal(case["reported_mean"])
    sample_size = case["sample_size"]
    digits = len(case["reported_mean"].split(".", 1)[1]) if "." in case["reported_mean"] else 0
    half_unit = Decimal(1).scaleb(-digits) / 2
    lower = (mean - half_unit) * sample_size
    upper = (mean + half_unit) * sample_size
    first = max(case["scale_min"] * sample_size, int(lower.to_integral_value(rounding=ROUND_CEILING)))
    last = min(case["scale_max"] * sample_size, int(upper.to_integral_value(rounding=ROUND_FLOOR)))
    result = {
        "case_id": case["case_id"],
        "path": case["path"],
        "line_id": case["line_id"],
        "reported_mean": case["reported_mean"],
        "sample_size": sample_size,
        "scale_min": case["scale_min"],
        "scale_max": case["scale_max"],
        "status": "compatible" if first <= last else "needs_review",
    }
    if first <= last:
        result["possible_integer_sum_range"] = [first, last]
    return result


def check(run, cases_path):
    manifest, files = read_corpus(run)
    cases_path = cases_path.resolve()
    cases, raw = checked_cases(cases_path, files)
    output = {
        "schema_version": 1,
        "corpus_sha256": corpus_hash(manifest),
        "cases_sha256": hashlib.sha256(raw).hexdigest(),
        "method": "Single integer item per observation; arithmetic mean; inclusive rounding interval",
        "note": "The sample size and integer scale are supplied assumptions, not verified by this tool; check them before treating a flag as a finding",
        "checks": [evaluate(case) for case in cases],
    }
    output_path = run.resolve() / "grim-checks.json"
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", type=Path, required=True, help="Private JSON array of explicitly checked cases")
    args = parser.parse_args()
    try:
        output = check(args.run, args.cases)
        print(json.dumps({"checks": len(output["checks"]), "output": str(args.run / "grim-checks.json")}))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
