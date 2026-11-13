#!/usr/bin/env python3
"""Prepare a private journal shortlist for independent scope and submission review."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from prepare_run import outside_git


ISSN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
SOURCE_ID = re.compile(r"^https://openalex\.org/S[1-9][0-9]*$")


def private_input(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("The discovery record must be an absolute, regular private file")
    path = path.resolve()
    outside_git(path)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("The discovery record is missing or oversized")
    return path


def prepare(discovery_path, limit=10):
    discovery_path = private_input(discovery_path)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("--limit must be between 1 and 50")
    raw = discovery_path.read_bytes()
    record = json.loads(raw.decode("utf-8"))
    candidates = record.get("candidates") if isinstance(record, dict) and record.get("schema_version") == 4 else None
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("The discovery record has no candidates")
    selected = []
    seen_issns = set()
    for rank, candidate in enumerate(candidates[:limit], start=1):
        if not isinstance(candidate, dict):
            raise ValueError("Invalid discovery candidate")
        issn_l, name, source_id = (candidate.get(key) for key in ("issn_l", "display_name", "source_id"))
        issns = candidate.get("issns")
        evidence = candidate.get("evidence")
        if (issn_l is not None and (not isinstance(issn_l, str) or not ISSN.fullmatch(issn_l))
                or not isinstance(name, str) or not name.strip()
                or (source_id is not None and (not isinstance(source_id, str)
                                                or not SOURCE_ID.fullmatch(source_id)))
                or not isinstance(issns, list) or not issns
                or (issn_l is not None and issn_l not in issns)
                or any(not isinstance(value, str) or not ISSN.fullmatch(value) for value in issns)
                or len(set(issns)) != len(issns)
                or not isinstance(evidence, list) or not evidence
                or any(not isinstance(value, str) or not value.strip() for value in evidence)
                or seen_issns.intersection(issns)):
            raise ValueError("Candidate identity or evidence is incomplete or ambiguous")
        seen_issns.update(issns)
        selected.append({"rank": rank, "display_name": name.strip(), "source_id": source_id,
                         "issn_l": issn_l, "issns": issns, "discovery_evidence": evidence,
                         "scope_check": "pending", "article_type_check": "pending",
                         "submission_status_check": "pending", "official_guidelines_check": "pending"})
    packet = {"schema_version": 1, "status": "pending_independent_review",
              "discovery_record_sha256": hashlib.sha256(raw).hexdigest(),
              "discovery_retrieved_at": record.get("retrieved_at"),
              "query": record.get("query"),
              "note": "Discovery evidence identifies possible journals; it does not verify scope, article type, current submissions, or acceptance probability. The author chooses the target after review.",
              "candidates": selected}
    output = discovery_path.parent / "candidate-review-packet.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discovery_record", type=Path, help="Absolute private candidates.json path")
    parser.add_argument("--limit", type=int, default=10, help="Number of candidates to review (1-50)")
    args = parser.parse_args()
    try:
        print(prepare(args.discovery_record, args.limit))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
