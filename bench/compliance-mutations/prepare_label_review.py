#!/usr/bin/env python3
"""Prepare a private, unfilled human-label worksheet for benchmark cases."""

import argparse
import json
from pathlib import Path

from score import outside_git, read_json, score, sha256


def make_packet(cases_path):
    benchmark = read_json(cases_path)
    result = score(benchmark)
    cases = {item["case_id"]: item for item in benchmark["cases"]}
    lines = [
        "# Human compliance label review",
        "",
        "This worksheet is private. Keep it, the manuscripts, source snapshots, and completed labels outside Git.",
        "Read each manuscript and the cited source snapshot before consulting any agent findings.",
        "For a control, label every scored rule. For a mutation, compare the paired control and",
        "mutation, then label the target rule and whether the edit introduced only the intended change.",
        "Record a reason and manuscript location for every decision. An unchecked box is not a label.",
        "",
        f"Data freeze: `{result['data_freeze']}`",
        f"Case manifest SHA-256: `{sha256(cases_path)}`",
        "Reviewer name or ID: ",
        "Review date: ",
        "",
    ]
    for row in result["cases"]:
        case = cases[row["case_id"]]
        run = Path(case["run"])
        profile = read_json(run / "journal-profile.json")
        manifest = read_json(run / "run-manifest.json")
        rules = {rule["rule_id"]: rule for rule in profile["rules"]}
        lines.extend([
            f"## {row['case_id']}",
            "",
            f"Kind: `{row['kind']}`",
            f"Journal: {profile['title']} (ISSN {profile['issn']})",
            f"Article type: {case['article_type']}",
            f"Manuscript: `{run / manifest['manuscript']['path']}`",
            f"Manuscript SHA-256: `{manifest['manuscript']['sha256']}`",
            f"Run manifest SHA-256: `{row['run_manifest_sha256']}`",
            f"Profile SHA-256: `{row['input_sha256']['profile']}`",
        ])
        if row["kind"] == "mutation":
            lines.append(f"Paired control: `{case['control_case_id']}`")
            if "mutation_class" in case:
                lines.append(f"Proposed mutation class (verify): `{case['mutation_class']}`")
            else:
                lines.append("Mutation class to assign: structural / semantic / numerical_format / unclear")
            lines.append(f"Proposed target rule (verify): `{case['expected_rule_id']}`")
        lines.extend(["", "Source snapshots:", ""])
        for source_ref, source in manifest["sources"].items():
            lines.append(
                f"- `{source_ref}`: {source['url']} (retrieved {source['retrieved_at']}; "
                f"SHA-256 `{source['sha256']}`; local `{run / source['path']}`)"
            )
        lines.extend(["", "### Independent labels", ""])
        rule_ids = row["scored_rule_ids"] if row["kind"] == "control" else [case["expected_rule_id"]]
        for rule_id in rule_ids:
            rule = rules[rule_id]
            requirement = rule.get("requirement") or json.dumps(rule["check"], ensure_ascii=False, sort_keys=True)
            lines.extend([
                f"#### `{rule_id}`",
                "",
                f"Rule: {requirement}",
                f"Source excerpt: {rule['quote']} (`{rule['source_ref']}`)",
                "- [ ] Compliant  - [ ] Noncompliant  - [ ] Not applicable or unclear",
                "- Manuscript location and reason: ",
                "",
            ])
        if row["kind"] == "mutation":
            lines.extend([
                "Paired control compliant with target rule: [ ] Yes  [ ] No  [ ] Unclear",
                "Only the intended change was introduced: [ ] Yes  [ ] No  [ ] Unclear",
                "Mutation class verified: [ ] Yes  [ ] No  [ ] Unclear",
                "- Difference and class rationale: ",
                "",
            ])
        if row["excluded_rule_ids"]:
            lines.extend([
                f"Excluded from scoring: {', '.join('`' + rule_id + '`' for rule_id in row['excluded_rule_ids'])}",
                "These rules require source/profile validation; this worksheet does not count them as reviewed.",
                "",
            ])
    lines.extend([
        "## Adjudication",
        "",
        "Second reviewer name or ID: ",
        "Disagreements and resolution with evidence: ",
        "Labels approved for scoring (yes/no): ",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Private benchmark case manifest outside Git")
    parser.add_argument("--output", type=Path, help="Private output path; defaults beside the manifest")
    args = parser.parse_args()
    output = args.output or args.cases.parent / "human-label-review.md"
    try:
        outside_git(args.cases)
        outside_git(output)
        packet = make_packet(args.cases)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(packet)
        print(output)
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
