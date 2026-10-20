#!/usr/bin/env python3
"""Find journal signals around explicitly selected reference DOIs."""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from discover_journals import API_URL, ISSN, SOURCE_ID, SOURCE_URL, fetch_json, safe_metadata_url
from prepare_run import outside_git


DOI = re.compile(r"^10\.\d{4,9}/[^\s|,<>\[\]\"']{1,180}$", re.IGNORECASE)


def read_references(path):
    path = path.expanduser().resolve()
    outside_git(path)
    raw = path.read_bytes()
    if len(raw) > 32_768:
        raise ValueError("Reference DOI file exceeds 32 KB")
    try:
        record = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Reference DOI file is not valid JSON") from None
    values = record.get("dois") if isinstance(record, dict) and record.get("schema_version") == 1 else None
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise ValueError("Reference DOI file must contain 1 to 100 manually selected DOIs")
    if any(not isinstance(value, str) or not DOI.fullmatch(value) for value in values):
        raise ValueError("Reference DOI file contains an invalid DOI")
    if len({value.casefold() for value in values}) != len(values):
        raise ValueError("Reference DOI file contains duplicate DOIs")
    return values, hashlib.sha256(raw).hexdigest()


def grouped_citation_candidates(dois, limit, opener):
    params = urlencode({"filter": "doi:" + "|".join(dois), "select": "id,doi,primary_location",
                        "per_page": 100})
    works_payload, works_hash = fetch_json(f"{API_URL}?{params}", opener)
    works = works_payload.get("results") if isinstance(works_payload, dict) else None
    if not isinstance(works, list):
        raise ValueError("OpenAlex returned an unexpected reference-work response")
    resolved = {}
    reference_counts = Counter()
    for work in works:
        if not isinstance(work, dict) or not isinstance(work.get("id"), str):
            continue
        work_id = work["id"]
        if not re.fullmatch(r"https://openalex\.org/W\d+", work_id) or work_id in resolved:
            continue
        resolved[work_id] = work
        location = work.get("primary_location")
        source = location.get("source") if isinstance(location, dict) else None
        if isinstance(source, dict) and source.get("type") == "journal" and isinstance(source.get("id"), str):
            source_id = source["id"]
            if SOURCE_ID.fullmatch(source_id):
                reference_counts[source_id] += 1
    citing_counts = Counter()
    citing_total = 0
    citing_hash = None
    citing_groups_seen = 0
    if resolved:
        ids = [work_id.rsplit("/", 1)[1] for work_id in sorted(resolved)]
        params = urlencode({"filter": "cites:" + "|".join(ids) + ",type:article,primary_location.source.type:journal",
                            "group_by": "primary_location.source.id", "per_page": 100})
        citing_payload, citing_hash = fetch_json(f"{API_URL}?{params}", opener)
        groups = citing_payload.get("group_by") if isinstance(citing_payload, dict) else None
        if not isinstance(groups, list):
            raise ValueError("OpenAlex returned an unexpected citing-work response")
        citing_groups_seen = len(groups)
        metadata = citing_payload.get("meta")
        citing_total = metadata.get("count") if isinstance(metadata, dict) else None
        for group in groups:
            if (isinstance(group, dict) and isinstance(group.get("key"), str)
                    and SOURCE_ID.fullmatch(group["key"]) and type(group.get("count")) is int
                    and group["count"] > 0):
                citing_counts[group["key"]] = group["count"]
    source_ids = sorted(set(reference_counts) | set(citing_counts))
    sources = {}
    source_hashes = []
    for offset in range(0, len(source_ids), 100):
        batch = source_ids[offset:offset + 100]
        params = urlencode({"filter": "ids.openalex:" + "|".join(source_id.rsplit("/", 1)[1] for source_id in batch),
                            "select": "id,display_name,type,issn_l,issn,works_count,homepage_url", "per_page": 100})
        payload, source_hash = fetch_json(f"{SOURCE_URL}?{params}", opener)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("OpenAlex returned an unexpected source response")
        source_hashes.append(source_hash)
        for source in results:
            if isinstance(source, dict) and source.get("id") in batch:
                sources[source["id"]] = source
    candidates = []
    for source_id in source_ids:
        source = sources.get(source_id)
        if not isinstance(source, dict) or source.get("type") != "journal":
            continue
        name, issn_l, total = source.get("display_name"), source.get("issn_l"), source.get("works_count")
        if not (isinstance(name, str) and name.strip() and isinstance(issn_l, str)
                and ISSN.fullmatch(issn_l) and type(total) is int and total > 0):
            continue
        issns = {issn_l}
        if isinstance(source.get("issn"), list):
            issns.update(value for value in source["issn"] if isinstance(value, str) and ISSN.fullmatch(value))
        evidence = []
        if reference_counts[source_id]:
            evidence.append("reference_venues")
        if citing_counts[source_id]:
            evidence.append("citing_venues")
        candidates.append({"source_id": source_id, "display_name": name.strip(), "issn_l": issn_l,
                           "issns": sorted(issns), "homepage_url": safe_metadata_url(source.get("homepage_url")),
                           "reference_works": reference_counts[source_id],
                           "citing_works": citing_counts[source_id], "source_works_count": total,
                           "citing_share": round(citing_counts[source_id] / total, 8),
                           "evidence": evidence, "evidence_count": len(evidence)})
    candidates.sort(key=lambda item: (-item["evidence_count"], -item["reference_works"],
                                      -(item["citing_works"] / item["source_works_count"]),
                                      -item["citing_works"], item["display_name"].casefold()))
    return candidates[:limit], {"reference_works_resolved": len(resolved), "reference_response_sha256": works_hash,
                                "citing_works_total": citing_total, "citing_groups_seen": citing_groups_seen,
                                "citing_response_sha256": citing_hash, "source_response_sha256": source_hashes}


def discover(reference_file, output_root, limit=50, allow_network=False, opener=urlopen):
    if not allow_network:
        raise ValueError("Citation discovery requires --allow-network; selected DOI values leave this computer")
    if not 1 <= limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    root = output_root.expanduser()
    outside_git(root)
    dois, reference_hash = read_references(reference_file)
    candidates, details = grouped_citation_candidates(dois, limit, opener)
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"citation-discovery-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available citation discovery number")
    output = directory / "citation-candidates.json"
    record = {"schema_version": 1,
              "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "provider": "OpenAlex", "selected_references": len(dois),
              "reference_input_sha256": reference_hash, **details,
              "coverage_note": "Only manually selected DOI references and the first 100 citing-journal groups were considered; coverage is incomplete",
              "citing_share_denominator": "OpenAlex source works_count across all years; not restricted to citing article type",
              "note": "DOI aliases may resolve to one work. Citation-venue frequency is a discovery signal, not journal suitability or acceptance probability",
              "candidates": candidates}
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", required=True, type=Path, help="Private JSON with manually selected reference DOIs")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "discovery")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--allow-network", action="store_true", help="Send selected DOI values to OpenAlex")
    args = parser.parse_args()
    try:
        print(discover(args.references, args.output_root, args.limit, args.allow_network))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
