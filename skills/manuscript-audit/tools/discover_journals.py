#!/usr/bin/env python3
"""Shortlist journals from an explicit topic query using OpenAlex work metadata."""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from prepare_run import outside_git


API_URL = "https://api.openalex.org/works"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")


def shortlist(payload, limit):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("OpenAlex returned an unexpected response")
    counts = defaultdict(set)
    names = {}
    issns = {}
    for work in payload["results"]:
        if not isinstance(work, dict) or not isinstance(work.get("id"), str):
            continue
        location = work.get("primary_location")
        source = location.get("source") if isinstance(location, dict) else None
        if not isinstance(source, dict) or source.get("type") != "journal":
            continue
        source_id = source.get("id")
        name = source.get("display_name")
        issn_l = source.get("issn_l")
        if not (isinstance(source_id, str) and source_id.startswith("https://openalex.org/S")
                and isinstance(name, str) and name.strip()
                and isinstance(issn_l, str) and ISSN.fullmatch(issn_l)):
            continue
        counts[source_id].add(work["id"])
        names[source_id] = name.strip()
        issns[source_id] = issn_l
    candidates = [
        {"source_id": source_id, "display_name": names[source_id], "issn_l": issns[source_id],
         "matching_works": len(work_ids), "work_ids": sorted(work_ids)}
        for source_id, work_ids in counts.items()
    ]
    candidates.sort(key=lambda item: (-item["matching_works"], item["display_name"].casefold(), item["source_id"]))
    return candidates[:limit]


def fetch_works(query, per_page, opener=urlopen, field_id=None):
    filters = ["type:article"]
    if field_id is not None:
        filters.append(f"primary_topic.field.id:{field_id}")
    params = urlencode({"search.title_abstract_keywords": query, "filter": ",".join(filters),
                        "per_page": per_page, "select": "id,primary_location"})
    request = Request(f"{API_URL}?{params}", headers={"User-Agent": "manuscript-audit-skill/0.1", "Accept": "application/json"})
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with opener(request, timeout=20) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise ValueError(f"OpenAlex request failed with HTTP {error.code}") from None
    except URLError:
        raise ValueError("Could not reach OpenAlex") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("OpenAlex response exceeded the size limit")
    try:
        payload = json.loads(data)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("OpenAlex returned invalid JSON") from None
    return payload, hashlib.sha256(data).hexdigest()


def discover(query, output_root, per_page=50, limit=10, opener=urlopen, field_id=None):
    query = query.strip()
    if not query or len(query) > 200 or any(character in query for character in "\r\n\x00"):
        raise ValueError("--query must be a single topic phrase of 1 to 200 characters")
    if not 1 <= per_page <= 100 or not 1 <= limit <= 100:
        raise ValueError("--works and --limit must be between 1 and 100")
    if field_id is not None and (isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0):
        raise ValueError("--field-id must be a positive OpenAlex field ID")
    root = output_root.expanduser()
    outside_git(root)
    payload, response_hash = fetch_works(query, per_page, opener, field_id)
    candidates = shortlist(payload, limit)
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"discovery-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available discovery number")
    record = {
        "schema_version": 1,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provider": "OpenAlex",
        "query": query,
        "field_id": field_id,
        "work_filter": "article with matching primary field" if field_id is not None else "article",
        "works_requested": per_page,
        "response_sha256": response_hash,
        "candidates": candidates,
    }
    output = directory / "candidates.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Explicit topic phrase; do not use confidential manuscript text")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "discovery")
    parser.add_argument("--works", type=int, default=50, help="Number of OpenAlex works to inspect (1-100)")
    parser.add_argument("--limit", type=int, default=10, help="Maximum journal candidates to retain (1-100)")
    parser.add_argument("--field-id", type=int, help="Optional OpenAlex primary field ID")
    args = parser.parse_args()
    try:
        print(discover(args.query, args.output_root, args.works, args.limit, field_id=args.field_id))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
