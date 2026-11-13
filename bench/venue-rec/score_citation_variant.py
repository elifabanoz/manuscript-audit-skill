#!/usr/bin/env python3
"""Compare private grouped and citation-augmented journal shortlists."""

import argparse
from datetime import date
import json
from pathlib import Path
import re

from score import ISSN, WORK_ID, candidate_pool, digest, metrics, private_file, rank_for, read_json


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def pinned_groups(base, combined):
    fields = ("query", "field_id", "published_after", "openalex_work_filter",
              "group_page_sha256", "source_response_sha256", "group_pages_seen",
              "group_pages_complete", "matching_works_total", "candidate_limit")
    for key in fields:
        if base.get(key) != combined.get(key):
            raise ValueError("Grouped and citation shortlists use different OpenAlex snapshots")
    for key in ("group_page_sha256", "source_response_sha256"):
        values = base.get(key)
        if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not SHA256.fullmatch(value) for value in values):
            raise ValueError("Grouped shortlist lacks pinned OpenAlex response hashes")
    if type(base.get("candidate_limit")) is not int or not 1 <= base["candidate_limit"] <= 100:
        raise ValueError("Grouped shortlist lacks a candidate cap")


def checked_pool(record, work_id, with_citation):
    keys, issn_sets = candidate_pool(record)
    allowed = {"openalex_grouped_works", "citation_graph"} if with_citation else {"openalex_grouped_works"}
    if (record.get("strategy") != "grouped" or record.get("excluded_work_id") != work_id
            or not isinstance(record.get("openalex_work_filter"), str)
            or f"ids.openalex:!{work_id}" not in record["openalex_work_filter"].split(",")
            or record.get("doaj_subject") is not None or record.get("web_input_sha256") is not None
            or any(not isinstance(item.get("evidence"), list) or not item["evidence"]
                   or len(set(item["evidence"])) != len(item["evidence"])
                   or set(item["evidence"]) - allowed for item in record["candidates"])):
        raise ValueError("Variant needs a target-excluded grouped shortlist with permitted evidence")
    if with_citation:
        if (not isinstance(record.get("citation_record_sha256"), str)
                or not SHA256.fullmatch(record["citation_record_sha256"])
                or record.get("citation_excluded_work_id") != work_id
                or not any("citation_graph" in item["evidence"] for item in record["candidates"])):
            raise ValueError("Citation variant lacks a matching target-excluded citation record")
    elif record.get("citation_record_sha256") is not None or any(
            item["evidence"] != ["openalex_grouped_works"] for item in record["candidates"]):
        raise ValueError("Grouped variant must not contain citation evidence")
    return keys, issn_sets


def score_manifest(manifest_path):
    manifest_path = private_file(manifest_path)
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "data_freeze", "cases"}
            or manifest["schema_version"] != 1
            or not isinstance(manifest["data_freeze"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", manifest["data_freeze"])):
        raise ValueError("Invalid citation variant manifest")
    try:
        date.fromisoformat(manifest["data_freeze"])
    except ValueError:
        raise ValueError("Invalid citation variant data freeze") from None
    if not isinstance(manifest["cases"], list) or not manifest["cases"]:
        raise ValueError("Citation variant manifest needs cases")
    rows, seen_ids, seen_works, seen_preprints = [], set(), set(), set()
    required = {"case_id", "field", "published_issn", "target_work_id", "preprint_file",
                "grouped_record", "citation_record", "combined_record"}
    for case in manifest["cases"]:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("Citation variant case has unexpected fields")
        case_id, field, target, work_id = (case[key] for key in
                                           ("case_id", "field", "published_issn", "target_work_id"))
        if (not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids
                or not isinstance(field, str) or not field.strip()
                or not isinstance(target, str) or not ISSN.fullmatch(target)
                or not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id)
                or work_id in seen_works):
            raise ValueError("Citation variant needs unique cases and valid work/venue IDs")
        seen_ids.add(case_id)
        seen_works.add(work_id)
        preprint = private_file(case["preprint_file"])
        preprint_hash = digest(preprint)
        if preprint_hash in seen_preprints:
            raise ValueError("Citation variant repeats a preprint")
        seen_preprints.add(preprint_hash)
        base_path = private_file(case["grouped_record"])
        citation_path = private_file(case["citation_record"])
        combined_path = private_file(case["combined_record"])
        base, citation, combined = (read_json(path) for path in
                                    (base_path, citation_path, combined_path))
        base_keys, base_issns = checked_pool(base, work_id, False)
        combined_keys, combined_issns = checked_pool(combined, work_id, True)
        pinned_groups(base, combined)
        if (combined["citation_record_sha256"] != digest(citation_path)
                or not isinstance(citation, dict) or citation.get("schema_version") != 1
                or citation.get("provider") != "OpenAlex"
                or citation.get("excluded_work_id") != work_id
                or not isinstance(citation.get("citing_filter"), str)
                or f"ids.openalex:!{work_id}" not in citation["citing_filter"].split(",")):
            raise ValueError("Citation source is stale or lacks the target exclusion")
        source_keys, source_issns = candidate_pool(
            {"schema_version": 4, "candidates": citation.get("candidates")})
        if not set(combined_keys) <= set(base_keys) | set(source_keys):
            raise ValueError("Citation variant contains an unpinned journal")
        base_identity = dict(zip(base_keys, base_issns))
        citation_identity = dict(zip(source_keys, source_issns))
        if any(base_identity[key] != issns for key, issns in zip(combined_keys, combined_issns)
               if key in base_identity):
            raise ValueError("Journal identity changed between variants")
        for candidate, key, issns in zip(combined["candidates"], combined_keys, combined_issns):
            if ("openalex_grouped_works" in candidate["evidence"] and key not in base_identity
                    or "citation_graph" in candidate["evidence"] and not any(
                        issns.intersection(source) for source in citation_identity.values())):
                raise ValueError("Citation variant assigns evidence absent from its source records")
        rows.append({"case_id": case_id, "field": field, "published_issn": target,
                     "target_work_id": work_id,
                     "grouped_rank": rank_for(target, base_keys, base_issns, base_keys),
                     "citation_augmented_rank": rank_for(target, combined_keys, combined_issns, combined_keys),
                     "preprint_sha256": preprint_hash, "grouped_record_sha256": digest(base_path),
                     "citation_record_sha256": digest(citation_path),
                     "combined_record_sha256": digest(combined_path)})
    def summarize(items):
        return {name: metrics([row[key] for row in items]) for name, key in
                (("grouped", "grouped_rank"), ("citation_augmented", "citation_augmented_rank"))}
    by_field = {field: summarize([row for row in rows if row["field"] == field])
                for field in sorted({row["field"] for row in rows})}
    return {"schema_version": 1, "status": "provisional_unreviewed",
            "note": "Both variants share hashed OpenAlex group/source responses and exclude the target work. Human checks of preprint leakage, duplicate works, reference selection, journal labels, and source freshness remain required.",
            "benchmark_manifest_sha256": digest(manifest_path), "cases": rows,
            "overall": summarize(rows), "by_field": by_field}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Absolute private citation variant manifest")
    args = parser.parse_args()
    try:
        result = score_manifest(args.cases)
        output = private_file(args.cases).parent / "citation-variant-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
