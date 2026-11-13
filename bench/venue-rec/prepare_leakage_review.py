#!/usr/bin/env python3
"""Flag exact target-venue clues in private masked preprint text for review."""

import argparse
import json
from pathlib import Path
import re

from score import ISSN, WORK_ID, digest, private_file


DOI = re.compile(r"^10\.[0-9]{4,9}/[^\s<>\"']{1,180}$", re.IGNORECASE)
MAX_TEXT_BYTES = 10_000_000


def prepare(preprint_file, target_work_id, published_issn, venue_names, output,
            target_doi=None):
    preprint = private_file(preprint_file)
    if preprint.suffix.lower() not in {".md", ".txt"} or preprint.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError("The masked preprint must be a private UTF-8 .md or .txt file under 10 MB")
    if not isinstance(target_work_id, str) or not WORK_ID.fullmatch(target_work_id):
        raise ValueError("Invalid target OpenAlex work ID")
    if not isinstance(published_issn, str) or not ISSN.fullmatch(published_issn):
        raise ValueError("Invalid target journal ISSN")
    if target_doi is not None and (not isinstance(target_doi, str) or not DOI.fullmatch(target_doi)):
        raise ValueError("Invalid target DOI")
    if (not isinstance(venue_names, list) or not 1 <= len(venue_names) <= 5
            or any(not isinstance(name, str) or not 6 <= len(name.strip()) <= 120
                   or any(character in name for character in "\r\n\x00") for name in venue_names)
            or len({name.strip().casefold() for name in venue_names}) != len(venue_names)):
        raise ValueError("Supply 1 to 5 distinct target journal names")
    output = Path(output)
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("The review output must be an absolute private path")
    output = output.resolve()
    if any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise ValueError("The review output must stay outside Git")
    if output.parent != preprint.parent:
        raise ValueError("The review output must be beside the private preprint")
    raw = preprint.read_bytes()
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise ValueError("The masked preprint must be UTF-8 text") from None
    clues = {"target_work_id": target_work_id, "published_issn": published_issn}
    if target_doi is not None:
        clues["target_doi"] = target_doi
    clues.update({f"venue_name_{index}": name.strip() for index, name in enumerate(venue_names, start=1)})
    signals = {}
    for label, value in clues.items():
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        found = [number for number, line in enumerate(lines, start=1) if pattern.search(line)]
        signals[label] = {"line_count": len(found), "line_numbers": found[:50],
                          "line_numbers_truncated": len(found) > 50}
    packet = {"schema_version": 1, "status": "pending_human_review",
              "preprint_sha256": digest(preprint), "target_work_id": target_work_id,
              "published_issn": published_issn, "target_doi": target_doi,
              "venue_names": [name.strip() for name in venue_names], "signals": signals,
              "note": "Exact string hits are review leads, including in references. Zero hits do not establish that venue, DOI, template, or duplicate-record leakage is absent. Inspect the original preprint independently."}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preprint_file", type=Path, help="Absolute private masked .md or .txt file")
    parser.add_argument("output", type=Path, help="Absolute private review-packet output beside preprint")
    parser.add_argument("--target-work-id", required=True)
    parser.add_argument("--published-issn", required=True)
    parser.add_argument("--target-doi")
    parser.add_argument("--venue-name", action="append", required=True)
    args = parser.parse_args()
    try:
        print(prepare(args.preprint_file, args.target_work_id, args.published_issn,
                      args.venue_name, args.output, args.target_doi))
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
