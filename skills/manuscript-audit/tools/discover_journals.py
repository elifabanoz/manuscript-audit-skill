#!/usr/bin/env python3
"""Shortlist journals from an explicit topic query using OpenAlex metadata."""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from prepare_run import outside_git


API_URL = "https://api.openalex.org/works"
SOURCE_URL = "https://api.openalex.org/sources"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
SOURCE_ID = re.compile(r"^https://openalex\.org/S\d+$")


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


def fetch_json(url, opener):
    request = Request(url, headers={"User-Agent": "manuscript-audit-skill/0.1", "Accept": "application/json"})
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


def work_filters(field_id, published_after):
    filters = ["type:article"]
    if field_id is not None:
        filters.append(f"primary_topic.field.id:{field_id}")
    if published_after is not None:
        filters.append(f"from_publication_date:{published_after}")
    return ",".join(filters)


def fetch_works(query, per_page, opener=urlopen, field_id=None, published_after=None):
    params = urlencode({"search.title_abstract_keywords": query, "filter": work_filters(field_id, published_after),
                        "per_page": per_page, "select": "id,primary_location"})
    return fetch_json(f"{API_URL}?{params}", opener)


def grouped_shortlist(query, limit, opener, field_id, published_after):
    params = urlencode({"search.title_abstract_keywords": query,
                        "filter": work_filters(field_id, published_after) + ",primary_location.source.type:journal",
                        "group_by": "primary_location.source.id", "per_page": 100})
    payload, response_hash = fetch_json(f"{API_URL}?{params}", opener)
    groups = payload.get("group_by") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        raise ValueError("OpenAlex returned an unexpected grouped response")
    ranked = []
    for group in groups:
        if (isinstance(group, dict) and isinstance(group.get("key"), str)
                and SOURCE_ID.fullmatch(group["key"]) and type(group.get("count")) is int
                and group["count"] > 0):
            ranked.append(group)
    ranked.sort(key=lambda item: (-item["count"], item["key"]))
    metadata = payload.get("meta")
    matching_works = metadata.get("count") if isinstance(metadata, dict) else None
    if not ranked:
        return [], response_hash, len(groups), matching_works
    source_ids = [group["key"].rsplit("/", 1)[1] for group in ranked]
    source_params = urlencode({"filter": "ids.openalex:" + "|".join(source_ids),
                               "select": "id,display_name,type,issn_l,works_count,homepage_url",
                               "per_page": 100})
    sources_payload, _ = fetch_json(f"{SOURCE_URL}?{source_params}", opener)
    sources = sources_payload.get("results") if isinstance(sources_payload, dict) else None
    if not isinstance(sources, list):
        raise ValueError("OpenAlex returned an unexpected source response")
    source_by_id = {source.get("id"): source for source in sources if isinstance(source, dict)}
    candidates = []
    for group in ranked:
        source_id = group["key"]
        source = source_by_id.get(source_id)
        if not isinstance(source, dict) or source.get("id") != source_id or source.get("type") != "journal":
            continue
        name = source.get("display_name")
        issn_l = source.get("issn_l")
        total = source.get("works_count")
        if not (isinstance(name, str) and name.strip() and isinstance(issn_l, str)
                and ISSN.fullmatch(issn_l) and type(total) is int and total > 0):
            continue
        candidates.append({"source_id": source_id, "display_name": name.strip(), "issn_l": issn_l,
                           "homepage_url": source.get("homepage_url") if isinstance(source.get("homepage_url"), str) else None,
                           "matching_works": group["count"], "source_works_count": total,
                           "topic_share": round(group["count"] / total, 8), "evidence": ["openalex_grouped_works"]})
    candidates.sort(key=lambda item: (-(item["matching_works"] / item["source_works_count"]),
                                      -item["matching_works"],
                                      item["display_name"].casefold(), item["source_id"]))
    return candidates[:limit], response_hash, len(groups), matching_works


def discover(query, output_root, per_page=50, limit=50, opener=urlopen, field_id=None,
             published_after=None, strategy="grouped"):
    query = query.strip()
    if not query or len(query) > 200 or any(character in query for character in "\r\n\x00"):
        raise ValueError("--query must be a single topic phrase of 1 to 200 characters")
    if not 1 <= per_page <= 100 or not 1 <= limit <= 100:
        raise ValueError("--works and --limit must be between 1 and 100")
    if strategy not in {"grouped", "sample"}:
        raise ValueError("--strategy must be grouped or sample")
    if field_id is not None and (isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0):
        raise ValueError("--field-id must be a positive OpenAlex field ID")
    if published_after is not None:
        if not isinstance(published_after, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_after):
            raise ValueError("--published-after must be a YYYY-MM-DD date")
        try:
            date.fromisoformat(published_after)
        except ValueError as error:
            raise ValueError("--published-after is not a valid date") from error
    root = output_root.expanduser()
    outside_git(root)
    if strategy == "grouped":
        candidates, response_hash, groups_seen, matching_works = grouped_shortlist(
            query, limit, opener, field_id, published_after)
    else:
        payload, response_hash = fetch_works(query, per_page, opener, field_id, published_after)
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
    work_filter = "article"
    if strategy == "grouped":
        work_filter += " with journal primary location"
    if field_id is not None:
        work_filter += " and matching primary field"
    record = {
        "schema_version": 2,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provider": "OpenAlex",
        "query": query,
        "field_id": field_id,
        "published_after": published_after,
        "work_filter": work_filter,
        "strategy": strategy,
        "works_requested": per_page if strategy == "sample" else None,
        "grouped_sources_seen": groups_seen if strategy == "grouped" else None,
        "matching_works_total": matching_works if strategy == "grouped" else None,
        "coverage_note": "First OpenAlex group page only; candidate coverage is incomplete" if strategy == "grouped" else "Sampled works only; candidate coverage is incomplete",
        "topic_share_denominator": "OpenAlex source works_count across all years; not restricted to the search date or article type" if strategy == "grouped" else None,
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
    parser.add_argument("--works", type=int, default=50, help="Works to inspect with --strategy sample (1-100)")
    parser.add_argument("--limit", type=int, default=50, help="Maximum journal candidates to retain (1-100)")
    parser.add_argument("--strategy", choices=("grouped", "sample"), default="grouped")
    parser.add_argument("--field-id", type=int, help="Optional OpenAlex primary field ID")
    parser.add_argument("--published-after", help="Keep only works published on or after this YYYY-MM-DD date")
    args = parser.parse_args()
    try:
        print(discover(args.query, args.output_root, args.works, args.limit,
                       field_id=args.field_id, published_after=args.published_after,
                       strategy=args.strategy))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
