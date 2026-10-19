#!/usr/bin/env python3
"""Inventory DOI mentions in a prepared private manuscript run."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from build_coverage import corpus_hash
from check_deterministic import checked_file
from prepare_run import outside_git


DOI = re.compile(r"(?<![A-Za-z0-9])10\.\d{4,9}/[^\s<>\[\]\"']+", re.IGNORECASE)
VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "validate-run-manifest.cjs"


def clean_doi(value):
    value = value.rstrip(".,;:}")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def inventory(run):
    run = run.resolve()
    outside_git(run)
    result = subprocess.run(["node", str(VALIDATOR), str(run)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
    files = [manifest["manuscript"], *manifest.get("supplementary", [])]
    found = {}
    for file in files:
        text = checked_file(run, file["path"], file["sha256"])
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in DOI.finditer(line):
                value = clean_doi(match.group())
                key = value.casefold()
                if key not in found:
                    found[key] = {"doi": value, "doi_url": f"https://doi.org/{value}", "occurrences": []}
                location = {"path": file["path"], "line_id": f"L{line_number}"}
                if location not in found[key]["occurrences"]:
                    found[key]["occurrences"].append(location)
    output = {
        "schema_version": 1,
        "corpus_sha256": corpus_hash(manifest),
        "note": "DOI mentions only; reference completeness and bibliographic accuracy are not established",
        "dois": sorted(found.values(), key=lambda item: item["doi"].casefold()),
    }
    path = run / "doi-inventory.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        output = inventory(args.run)
        print(json.dumps({"dois": len(output["dois"]), "output": str(args.run / "doi-inventory.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
