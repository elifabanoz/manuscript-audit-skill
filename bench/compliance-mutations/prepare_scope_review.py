#!/usr/bin/env python3
"""Create an unfilled, private scoring-scope worksheet for one benchmark run."""

import argparse
import json
import subprocess
from pathlib import Path

from score import outside_git, read_json, sha256


VALIDATORS = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "validators"


def prepare(run, article_type):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--article-type", required=True)
    args = parser.parse_args()
    try:
        print(prepare(args.run, args.article_type))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
