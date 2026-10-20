#!/usr/bin/env python3
"""Prepare private, unreviewed single-heading mutation proposals."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "tools"
sys.path.insert(0, str(TOOLS))

from check_deterministic import check, compare, heading_spans, measure_heading_count  # noqa: E402
from score import outside_git, sha256  # noqa: E402


HEADING_LINE = re.compile(r"^([ \t]*)(#{1,6})([ \t]+)(.*?)(\r\n|\n|\r)?$")


def propose(run, output_root, article_type, rule_ids=None):
    run = run.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    outside_git(run)
    outside_git(output_root)
    report = check(run, article_type, write_output=False)
    manifest_path = run / "run-manifest.json"
    profile_path = run / "journal-profile.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    manuscript_path = run / manifest["manuscript"]["path"]
    original = manuscript_path.read_bytes()
    if hashlib.sha256(original).hexdigest() != manifest["manuscript"]["sha256"]:
        raise ValueError("Control manuscript differs from the run manifest")
    if original.startswith(b"\xef\xbb\xbf"):
        raise ValueError("A UTF-8 BOM would make the proposed edit ambiguous")
    text = original.decode("utf-8")
    lines, headings = heading_spans(text)
    original_lines = text.splitlines(keepends=True)
    if len(lines) != len(original_lines):
        raise ValueError("Control manuscript line boundaries are ambiguous")

    requested = set(rule_ids or [])
    if len(requested) != len(rule_ids or []):
        raise ValueError("Duplicate --rule-id")
    rules = {rule["rule_id"]: rule for rule in profile["rules"]}
    checks = {item["rule_id"]: item for item in report["checks"]}
    incomplete = [rule_id for rule_id, rule in rules.items()
                  if rule.get("kind") == "deterministic"
                  and rule.get("status") != "needs_validation"
                  and article_type in rule.get("applies_to", [article_type])
                  and checks.get(rule_id, {}).get("status") != "met"]
    if incomplete:
        raise ValueError("Control has unmet or unresolved verified deterministic rules: " +
                         ", ".join(sorted(incomplete)))
    candidates = []
    for rule_id, rule in rules.items():
        if requested and rule_id not in requested:
            continue
        specification = rule.get("check", {})
        if (rule.get("kind") != "deterministic" or specification.get("metric") != "heading_count"
                or rule.get("status") == "needs_validation"
                or article_type not in rule.get("applies_to", [article_type])):
            continue
        result = checks.get(rule_id)
        if (result is None or result.get("status") != "met" or result.get("measured_value") != 1
                or compare(0, specification["op"], specification["value"])):
            continue
        heading = specification["heading"]
        matches = [index for index, _, label in headings if label == heading.strip().casefold()]
        if len(matches) != 1:
            continue
        overlapping = [other["rule_id"] for other in profile["rules"]
                       if other["rule_id"] != rule_id and other.get("status") != "needs_validation"
                       and other.get("kind") == "deterministic"
                       and other.get("check", {}).get("metric") == "heading_count"
                       and other["check"]["heading"].strip().casefold() == heading.strip().casefold()]
        if overlapping:
            continue
        index = matches[0]
        match = HEADING_LINE.fullmatch(original_lines[index])
        if match is None:
            continue
        modified = original_lines.copy()
        modified[index] = match.group(1) + match.group(4) + (match.group(5) or "")
        mutation = "".join(modified)
        if measure_heading_count(mutation, heading) != 0:
            continue
        candidates.append((rule_id, heading, index + 1, mutation.encode("utf-8")))
    if requested - {item[0] for item in candidates}:
        missing = ", ".join(sorted(requested - {item[0] for item in candidates}))
        raise ValueError(f"Rules cannot support a single-heading proposal: {missing}")
    if not candidates:
        raise ValueError("No verified, unique, met heading rule supports a proposal")

    output_root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = output_root / f"heading-proposals-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available proposal directory")
    entries = []
    for index, (rule_id, heading, line, mutation) in enumerate(candidates, 1):
        name = f"mutation-{index}.md"
        (directory / name).write_bytes(mutation)
        entries.append({"rule_id": rule_id, "heading": heading, "line_id": f"L{line}",
                        "mutation_class_proposed": "structural", "file": name,
                        "manuscript_sha256": hashlib.sha256(mutation).hexdigest()})
    record = {"schema_version": 1, "status": "unreviewed_proposals",
              "control_run": str(run), "article_type": article_type,
              "control_manifest_sha256": sha256(manifest_path),
              "profile_sha256": sha256(profile_path),
              "control_manuscript_sha256": manifest["manuscript"]["sha256"],
              "edit": "Remove one Markdown heading marker and its separating whitespace; retain the title and body",
              "mutations": entries,
              "note": "A human must verify baseline compliance, single-change validity, and class before scoring"}
    output = directory / "proposals.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Private control run with a source-backed journal profile")
    parser.add_argument("--article-type", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--rule-id", action="append", help="Optional verified heading rule; repeat to select several")
    args = parser.parse_args()
    try:
        print(propose(args.run, args.output_root, args.article_type, args.rule_id))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
