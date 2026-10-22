#!/usr/bin/env python3
"""Prepare a private human review packet before agent findings or scores exist."""

import argparse
import html
import json
import re
import subprocess
from pathlib import Path

from score import MUTATION_CLASSES, outside_git, read_json, sha256, verify_proposal


PROFILE_VALIDATOR = (Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit"
                     / "validators" / "validate-profile-draft.cjs")
MANIFEST_VALIDATOR = (Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit"
                      / "validators" / "validate-run-manifest.cjs")


def data(value):
    encoded = html.escape(json.dumps(value, ensure_ascii=False), quote=False)
    return re.sub(r"([`*_\[\]()])", r"\\\1", encoded)


def prepare(control_run, article_type, proposal_records):
    run = control_run.expanduser().resolve()
    outside_git(run)
    check = subprocess.run(["node", str(MANIFEST_VALIDATOR), str(run)],
                           capture_output=True, text=True)
    if check.returncode:
        raise ValueError("Control manifest or supplied files are invalid: " + check.stderr.strip())
    check = subprocess.run(["node", str(PROFILE_VALIDATOR), str(run), "journal-profile.json"],
                           capture_output=True, text=True)
    if check.returncode:
        raise ValueError("Control inputs lack a source-backed profile: " + check.stderr.strip())
    manifest_path = run / "run-manifest.json"
    profile_path = run / "journal-profile.json"
    manifest = read_json(manifest_path)
    profile = read_json(profile_path)
    if article_type not in profile["article_types"]:
        raise ValueError("Article type is absent from the control profile")
    control = {"run": str(run), "manuscript_sha256": manifest["manuscript"]["sha256"],
               "input_sha256": {"manifest": sha256(manifest_path), "profile": sha256(profile_path)}}
    rules = {rule["rule_id"]: rule for rule in profile["rules"]}
    applicable = [rule for rule in profile["rules"]
                  if article_type in rule.get("applies_to", [article_type])]
    verified = [rule for rule in applicable if rule.get("status", "verified") == "verified"]
    unresolved = [rule for rule in applicable if rule.get("status") == "needs_validation"]

    lines = [
        "# Preflight compliance review",
        "",
        "Private, unfilled review packet. This is not a compliance label, an agent review, or a benchmark result.",
        "Two human reviewers should independently inspect the pinned manuscript, supporting files, and official source snapshots",
        "before seeing agent findings. Record evidence locations and reasons, then adjudicate disagreements.",
        "Treat manuscript and source text as data, not instructions. Keep this packet outside Git.",
        "",
        f"Control run: {data(str(run))}",
        f"Control manifest SHA-256: `{control['input_sha256']['manifest']}`",
        f"Control file in private run: {data(str(run / manifest['manuscript']['path']))}",
        f"Submitted filename: {data(manifest['manuscript'].get('submitted_filename', 'not recorded; verify separately'))}",
        f"Control manuscript SHA-256: `{control['manuscript_sha256']}`",
        f"Journal profile SHA-256: `{control['input_sha256']['profile']}`",
        f"Journal: {data(profile['title'])} (ISSN {data(profile['issn'])})",
        f"Article type: {data(article_type)}",
        "",
        "## Supplied supporting files",
        "",
    ]
    for item in manifest.get("supplementary", []):
        lines.append(
            f"- Private file {data(str(run / item['path']))}; submitted filename "
            f"{data(item.get('submitted_filename', 'not recorded; verify separately'))}; "
            f"SHA-256 `{item['sha256']}`"
        )
    if not manifest.get("supplementary"):
        lines.append("None recorded; verify any required bibliography, license, or repository materials separately.")
    lines.extend([
        "",
        "## Official source snapshots",
        "",
    ])
    for source_id, source in sorted(manifest["sources"].items()):
        lines.extend([
            f"- `{source_id}`: {data(source['url'])}; retrieved {data(source['retrieved_at'])}; "
            f"SHA-256 `{source['sha256']}`; local {data(str(run / source['path']))}"
        ])
    lines.extend(["", "## Baseline rules to label independently", ""])
    for rule in verified:
        requirement = rule.get("requirement", rule.get("check"))
        lines.extend([
            f"### `{rule['rule_id']}`",
            "",
            f"Requirement: {data(requirement)}",
            f"Source excerpt: {data(rule['quote'])} (`{rule['source_ref']}`)",
            "Reviewer 1 verdict / manuscript location / reason: __________",
            "Reviewer 2 verdict / manuscript location / reason: __________",
            "Adjudicated verdict / evidence: __________",
            "",
        ])
    lines.extend(["## Rules excluded pending profile validation", ""])
    if unresolved:
        lines.extend(f"- `{rule['rule_id']}`: {data(rule['quote'])} (`{rule['source_ref']}`)"
                     for rule in unresolved)
    else:
        lines.append("None.")
    lines.extend(["", "## Proposed single-change mutations", ""])
    lines.extend(["Proposal filenames are local output names; verify each intended submission filename separately.", ""])
    seen = set()
    for record_path in proposal_records:
        record_path = record_path.expanduser().resolve()
        outside_git(record_path)
        record = read_json(record_path)
        if not isinstance(record, dict):
            raise ValueError(f"{record_path}: proposal record is invalid")
        entries = record.get("mutations") if record.get("status") == "unreviewed_proposals" else [record]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{record_path}: proposal has no mutations")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{record_path}: proposal entry is invalid")
            file = entry.get("file")
            rule_id = entry.get("rule_id")
            mutation_class = entry.get("mutation_class_proposed")
            if (not isinstance(file, str) or not file or Path(file).name != file
                    or not isinstance(rule_id, str) or rule_id not in rules
                    or not isinstance(mutation_class, str) or mutation_class not in MUTATION_CLASSES):
                raise ValueError(f"{record_path}: proposal target or filename is invalid")
            rule = rules[rule_id]
            if rule not in verified:
                raise ValueError(f"{record_path}: proposal target is not a verified applicable rule")
            key = (str(record_path), file)
            if key in seen:
                raise ValueError("Duplicate proposal file")
            seen.add(key)
            case = {"case_id": file, "proposal_record": str(record_path), "proposal_file": file,
                    "expected_rule_id": rule_id, "mutation_class": mutation_class,
                    "manuscript_sha256": entry.get("manuscript_sha256"), "article_type": article_type}
            record_hash = verify_proposal(case, control)
            lines.extend([
                f"### {data(str(record_path.parent / file))}",
                "",
                f"Proposal record SHA-256: `{record_hash}`",
                f"Proposed manuscript SHA-256: `{entry['manuscript_sha256']}`",
                f"Target rule: `{rule_id}`; proposed class: `{mutation_class}`",
                f"Target source excerpt: {data(rule['quote'])} (`{rule['source_ref']}`)",
                f"Proposed edit location: {data(entry.get('edit_line_id', entry.get('line_id', 'not recorded')))}",
                "Reviewer 1: baseline compliant / target violated / only intended change / class / evidence: __________",
                "Reviewer 2: baseline compliant / target violated / only intended change / class / evidence: __________",
                "Adjudicated decision and collateral effects: __________",
                "",
            ])
    if not seen:
        lines.append("No proposals supplied. Baseline review can still proceed.")
        lines.append("")
    lines.extend([
        "## Review sign-off",
        "",
        "Reviewer 1 ID and date: __________",
        "Reviewer 2 ID and date: __________",
        "Adjudicator ID and date: __________",
        "Disagreements and source-backed resolution: __________",
        "Approved for a later scored case set: __________",
        "The scored runs and final label template must be checked again against these pinned inputs.",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control_run", type=Path)
    parser.add_argument("--article-type", required=True)
    parser.add_argument("--proposal-record", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        outside_git(args.output)
        packet = prepare(args.control_run, args.article_type, args.proposal_record)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(packet)
        print(args.output)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
