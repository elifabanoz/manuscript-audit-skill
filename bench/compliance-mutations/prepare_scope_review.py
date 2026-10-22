#!/usr/bin/env python3
"""Create an unfilled, private scoring-scope worksheet for one benchmark run."""

import argparse
import html
import json
import re
import subprocess
from pathlib import Path

from score import exact_keys, outside_git, read_json, sha256


VALIDATORS = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators"


def scoped_inputs(run, article_type):
    run = run.resolve()
    outside_git(run)
    manifest_path = run / "run-manifest.json"
    profile_path = run / "journal-profile.json"
    for script, argument in (("validate-run-manifest.cjs", run),
                             ("validate-journal-profile.cjs", profile_path)):
        result = subprocess.run(["node", str(VALIDATORS / script), str(argument)],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stderr.strip())
    manifest = read_json(manifest_path)
    profile = read_json(profile_path)
    if profile["sources"] != manifest["sources"]:
        raise ValueError("Profile sources differ from the pinned run sources")
    if article_type not in profile["article_types"]:
        raise ValueError("The article type is absent from the journal profile")
    rules = [rule for rule in profile["rules"]
             if article_type in rule.get("applies_to", [article_type])]
    if not rules:
        raise ValueError("The profile has no applicable rules")
    return run, manifest_path, profile_path, manifest, profile, rules


def prepare(run, article_type):
    run, manifest_path, profile_path, _, _, rules = scoped_inputs(run, article_type)
    record = {
        "schema_version": 1,
        "manifest_sha256": sha256(manifest_path),
        "profile_sha256": sha256(profile_path),
        "article_type": article_type,
        "rules": [{"rule_id": rule["rule_id"], "decision": None, "reason": ""}
                  for rule in rules],
    }
    output = run / "scope-review.template.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return output


def markdown_data(value):
    encoded = html.escape(json.dumps(value, ensure_ascii=False), quote=False)
    return re.sub(r"([`*_\[\]()])", r"\\\1", encoded)


def proposed_decisions(path, run, manifest_path, profile_path, article_type, rules):
    if path is None:
        return {}, None
    path = path.resolve()
    outside_git(path)
    if path.parent != run or path.name != "scope-review.proposed.json":
        raise ValueError("The proposal must be scope-review.proposed.json inside the private run")
    proposal = read_json(path)
    exact_keys(proposal, ("schema_version", "manifest_sha256", "profile_sha256",
                          "article_type", "rules", "status", "warning"), "scope proposal")
    if (proposal["schema_version"] != 1 or proposal["status"] != "unreviewed_proposal"
            or proposal["manifest_sha256"] != sha256(manifest_path)
            or proposal["profile_sha256"] != sha256(profile_path)
            or proposal["article_type"] != article_type):
        raise ValueError("The proposal differs from the private run inputs")
    if not isinstance(proposal["warning"], str) or not proposal["warning"].strip():
        raise ValueError("The proposal needs an unreviewed warning")
    if not isinstance(proposal["rules"], list):
        raise ValueError("The proposal rules must be a list")
    applicable = {rule["rule_id"]: rule for rule in rules}
    decisions = {}
    for entry in proposal["rules"]:
        exact_keys(entry, ("rule_id", "decision", "reason"), "proposed scope rule")
        rule_id = entry["rule_id"]
        if not isinstance(rule_id, str) or rule_id not in applicable or rule_id in decisions:
            raise ValueError("The proposal has an unknown or duplicated rule")
        if entry["decision"] not in ("score", "exclude"):
            raise ValueError("The proposal decision must be score or exclude")
        if not isinstance(entry["reason"], str) or len(entry["reason"].strip()) < 20:
            raise ValueError("The proposal needs a reason for every rule")
        if entry["decision"] == "score" and applicable[rule_id].get("status") == "needs_validation":
            raise ValueError("The proposal cannot score an unresolved source rule")
        decisions[rule_id] = entry
    if set(decisions) != set(applicable):
        raise ValueError("The proposal does not cover every applicable rule")
    digest = sha256(path)
    return decisions, digest


def make_review_packet(run, article_type, proposal_path=None):
    run, manifest_path, profile_path, manifest, profile, rules = scoped_inputs(run, article_type)
    decisions, proposal_hash = proposed_decisions(
        proposal_path, run, manifest_path, profile_path, article_type, rules)
    lines = [
        "# Scoring scope review",
        "",
        "Private worksheet. The proposal is unreviewed. Keep this file and all inputs outside Git.",
        "Each reviewer must inspect the pinned source and supplied files before deciding whether",
        "a rule can be scored from this case. Exclusion is not a compliance verdict.",
        "Enter the adjudicated decisions and reasons in scope-review.json only after review.",
        "",
        f"Run: `{run}`",
        f"Journal: {markdown_data(profile['title'])} (ISSN {profile['issn']})",
        f"Article type: {markdown_data(article_type)}",
        f"Manifest SHA-256: `{sha256(manifest_path)}`",
        f"Profile SHA-256: `{sha256(profile_path)}`",
        f"Manuscript: `{run / manifest['manuscript']['path']}`",
        f"Submitted filename: {markdown_data(manifest['manuscript'].get('submitted_filename', 'not recorded'))}",
        f"Manuscript SHA-256: `{manifest['manuscript']['sha256']}`",
        *([f"Unreviewed proposal SHA-256: `{proposal_hash}`"] if proposal_hash else []),
        "",
        "Supplied supporting files:",
    ]
    for item in manifest.get("supplementary", []):
        lines.append(f"- `{run / item['path']}`; submitted filename "
                     f"{markdown_data(item.get('submitted_filename', 'not recorded'))}; "
                     f"SHA-256 `{item['sha256']}`")
    if not manifest.get("supplementary"):
        lines.append("- None recorded.")
    lines.extend(["", "Pinned sources:"])
    for source_ref, source in manifest["sources"].items():
        lines.append(f"- `{source_ref}`: {markdown_data(source['url'])}; "
                     f"retrieved {markdown_data(source['retrieved_at'])}; "
                     f"SHA-256 `{source['sha256']}`; file `{run / source['path']}`")
    for rule in rules:
        rule_id = rule["rule_id"]
        proposal = decisions.get(rule_id)
        lines.extend([
            "", f"## `{rule_id}`", "",
            f"Source status: `{rule.get('status', 'verified')}`",
            f"Requirement: {markdown_data(rule.get('requirement', rule.get('check')))}",
            f"Source quote: {markdown_data(rule['quote'])} (`{rule['source_ref']}`)",
            f"Proposal: `{proposal['decision']}` — {markdown_data(proposal['reason'])}"
            if proposal else "Proposal: none",
            "- Reviewer 1 ID, decision (score/exclude), evidence, reason:",
            "- Reviewer 2 ID, decision (score/exclude), evidence, reason:",
            "- Adjudicated decision, evidence, reason:",
        ])
    lines.extend(["", "## Final review", "",
                  "- Reviewer 1 name and date:", "- Reviewer 2 name and date:",
                  "- Adjudicator name and date:", "- Disagreements and resolution:",
                  "- Every applicable rule reviewed: [ ] Yes  [ ] No", ""])
    if proposal_hash and sha256(proposal_path.resolve()) != proposal_hash:
        raise ValueError("The proposal changed during worksheet generation")
    return "\n".join(lines)


def prepare_review_packet(run, article_type, proposal_path=None):
    run = run.resolve()
    outside_git(run)
    output = run / "scope-review-worksheet.md"
    packet = make_review_packet(run, article_type, proposal_path)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(packet)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--article-type", required=True)
    parser.add_argument("--review-packet", action="store_true",
                        help="Create an unfilled Markdown worksheet instead of the JSON template")
    parser.add_argument("--proposal", type=Path,
                        help="Private unreviewed scope proposal; requires --review-packet")
    args = parser.parse_args()
    try:
        if args.proposal and not args.review_packet:
            raise ValueError("--proposal requires --review-packet")
        print(prepare_review_packet(args.run, args.article_type, args.proposal)
              if args.review_packet else prepare(args.run, args.article_type))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
