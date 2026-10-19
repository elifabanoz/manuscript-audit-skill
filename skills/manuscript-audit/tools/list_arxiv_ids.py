#!/usr/bin/env python3
"""Inventory labeled arXiv identifiers in a prepared private manuscript run."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

from build_coverage import corpus_hash
from list_dois import read_corpus


ARXIV = re.compile(
    r"(?<![A-Za-z0-9])arxiv:\s*((?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v[1-9]\d*)?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def collect(run):
    manifest, files = read_corpus(run)
    found = {}
    for path, content in files:
        for line_number, line in enumerate(content.splitlines(), start=1):
            for match in ARXIV.finditer(line):
                value = match.group(1)
                key = value.casefold()
                if key not in found:
                    found[key] = {
                        "arxiv_id": value,
                        "url": "https://arxiv.org/abs/" + quote(value, safe="/"),
                        "occurrences": [],
                    }
                location = {"path": path, "line_id": f"L{line_number}"}
                if location not in found[key]["occurrences"]:
                    found[key]["occurrences"].append(location)
    return {
        "schema_version": 1,
        "corpus_sha256": corpus_hash(manifest),
        "note": "arXiv ID mentions only; record existence and reference completeness are not established",
        "arxiv_ids": sorted(found.values(), key=lambda item: item["arxiv_id"].casefold()),
    }


def inventory(run):
    output = collect(run)
    path = run.resolve() / "arxiv-inventory.json"
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
        print(json.dumps({"arxiv_ids": len(output["arxiv_ids"]), "output": str(args.run / "arxiv-inventory.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
