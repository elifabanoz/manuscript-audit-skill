#!/usr/bin/env python3
"""Create pending compliance work units for a prepared manuscript run."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from prepare_run import outside_git


VALIDATORS = Path(__file__).resolve().parents[1] / "validators"


def corpus_hash(manifest):
    files = [manifest["manuscript"], *manifest.get("supplementary", [])]
    entries = sorted(([item["path"], item["sha256"]] for item in files), key=lambda item: item[0])
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build(run, article_type):
    run = run.resolve()
    outside_git(run)
    profile_file = run / "journal-profile.json"
    for script, argument in (("validate-run-manifest.cjs", run), ("validate-journal-profile.cjs", profile_file)):
        result = subprocess.run(["node", str(VALIDATORS / script), str(argument)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stderr.strip())
    manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    if profile["sources"] != manifest["sources"]:
        raise ValueError("Journal profile sources differ from the run manifest")
    if article_type not in profile["article_types"]:
        raise ValueError("--article-type is absent from the journal profile")
    rules = [rule for rule in profile["rules"] if article_type in rule.get("applies_to", [article_type])]
    if not rules:
        raise ValueError("The journal profile has no rules for this article type")
    digest = corpus_hash(manifest)
    ledger = [
        {
            "unit_id": f"U-{index}",
            "kind": "compliance",
            "rule_id": rule["rule_id"],
            "section": "all-supplied-files",
            "target_issn": profile["issn"],
            "status": "pending",
            "checked_by": [],
            "source_hash": digest,
        }
        for index, rule in enumerate(rules, start=1)
    ]
    output = run / "coverage-ledger.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=2)
        handle.write("\n")
    return ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--article-type", required=True)
    args = parser.parse_args()
    try:
        ledger = build(args.run, args.article_type)
        print(json.dumps({"pending_units": len(ledger), "output": str(args.run / "coverage-ledger.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
