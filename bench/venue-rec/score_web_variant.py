#!/usr/bin/env python3
"""Compare private grouped and manually selected web-augmented shortlists."""

import argparse
from datetime import date
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from score import ISSN, WORK_ID, candidate_pool, digest, metrics, private_file, rank_for, read_json
from score_citation_variant import SHA256, checked_pool, pinned_groups


def input_pairs(path):
    path = private_file(path)
    if path.stat().st_size > 32_768:
        raise ValueError("Web input exceeds 32 KB")
    record = read_json(path)
    entries = record.get("candidates") if isinstance(record, dict) and record.get("schema_version") == 1 else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError("Web input needs 1 to 20 ISSN and URL pairs")
    pairs = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"issn", "result_url"}:
            raise ValueError("Web input entries need only ISSN and result URL")
        issn, url = item["issn"], item["result_url"]
        if (not isinstance(issn, str) or not ISSN.fullmatch(issn) or issn in pairs
                or not isinstance(url, str) or len(url) > 2048 or any(char in url for char in "\r\n\x00")):
            raise ValueError("Web input has an invalid or repeated ISSN/URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Web input URL must use public HTTPS")
        if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost") or "." not in parsed.hostname:
            raise ValueError("Web input URL must use public HTTPS")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Web input URL must use public HTTPS")
        pairs[issn] = url
    return path, pairs


def score_manifest(manifest_path):
    manifest_path = private_file(manifest_path)
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "data_freeze", "cases"}
            or manifest["schema_version"] != 1 or not isinstance(manifest["data_freeze"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", manifest["data_freeze"])):
        raise ValueError("Invalid web variant manifest")
    try:
        date.fromisoformat(manifest["data_freeze"])
    except ValueError:
        raise ValueError("Invalid web variant data freeze") from None
    if not isinstance(manifest["cases"], list) or not manifest["cases"]:
        raise ValueError("Web variant manifest needs cases")
    rows, seen_ids, seen_works, seen_preprints = [], set(), set(), set()
    required = {"case_id", "field", "published_issn", "target_work_id", "preprint_file",
                "grouped_record", "web_input_file", "web_record"}
    for case in manifest["cases"]:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("Web variant case has unexpected fields")
        case_id, field, target, work_id = (case[key] for key in
                                           ("case_id", "field", "published_issn", "target_work_id"))
        if (not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids
                or not isinstance(field, str) or not field.strip()
                or not isinstance(target, str) or not ISSN.fullmatch(target)
                or not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id)
                or work_id in seen_works):
            raise ValueError("Web variant needs unique cases and valid work/venue IDs")
        seen_ids.add(case_id)
        seen_works.add(work_id)
        preprint = private_file(case["preprint_file"])
        preprint_hash = digest(preprint)
        if preprint_hash in seen_preprints:
            raise ValueError("Web variant repeats a preprint")
        seen_preprints.add(preprint_hash)
        base_path = private_file(case["grouped_record"])
        web_path = private_file(case["web_record"])
        input_path, pairs = input_pairs(case["web_input_file"])
        base, web = read_json(base_path), read_json(web_path)
        base_keys, base_issns = checked_pool(base, work_id, False)
        web_keys, web_issns = candidate_pool(web)
        pinned_groups(base, web)
        checks = web.get("web_registry_checks")
        if (web.get("strategy") != "grouped" or web.get("excluded_work_id") != work_id
                or not isinstance(web.get("openalex_work_filter"), str)
                or f"ids.openalex:!{work_id}" not in web["openalex_work_filter"].split(",")
                or web.get("doaj_subject") is not None or web.get("citation_record_sha256") is not None
                or web.get("web_input_sha256") != digest(input_path)
                or web.get("web_submitted_count") != len(pairs)
                or type(web.get("web_verified_count")) is not int
                or not 0 <= web["web_verified_count"] <= len(pairs)
                or not isinstance(checks, list) or len(checks) != len(pairs)
                or {item.get("issn") for item in checks if isinstance(item, dict)} != set(pairs)):
            raise ValueError("Web variant lacks a pinned input or target exclusion")
        checks_by_issn = {item["issn"]: item.get("registry_checks") for item in checks}
        base_identity = dict(zip(base_keys, base_issns))
        retained_web = set()
        for candidate, key, issns in zip(web["candidates"], web_keys, web_issns):
            evidence = candidate.get("evidence")
            if (not isinstance(evidence, list) or not evidence or len(set(evidence)) != len(evidence)
                    or set(evidence) - {"openalex_grouped_works", "web_registry_verified"}
                    or "openalex_grouped_works" in evidence and key not in base_identity
                    or key in base_identity and base_identity[key] != issns):
                raise ValueError("Web variant contains unsupported evidence or changed journal identity")
            matches = candidate.get("web_registry_matches")
            urls = candidate.get("web_results_unverified")
            if "web_registry_verified" not in evidence:
                if matches or urls:
                    raise ValueError("Web evidence appears without a web signal")
                continue
            if (not isinstance(matches, list) or not matches or not isinstance(urls, list)
                    or len(matches) != len(urls)):
                raise ValueError("Web variant has incomplete registry evidence")
            for match, url in zip(matches, urls):
                if not isinstance(match, dict) or set(match) != {"issn", "provider", "response_sha256"}:
                    raise ValueError("Web variant has malformed registry evidence")
                issn, provider, response_hash = (match[key] for key in ("issn", "provider", "response_sha256"))
                attempts = checks_by_issn.get(issn)
                if (issn not in pairs or issn not in issns or url != pairs[issn]
                        or issn in retained_web or provider not in {"OpenAlex", "DOAJ", "Crossref"}
                        or not isinstance(response_hash, str) or not SHA256.fullmatch(response_hash)
                        or not isinstance(attempts, list) or not any(
                            isinstance(item, dict) and item.get("provider") == provider
                            and item.get("status") == "matched"
                            and item.get("response_sha256") == response_hash for item in attempts)):
                    raise ValueError("Web variant registry match is not pinned to its private input")
                retained_web.add(issn)
        if not retained_web or len(retained_web) > web["web_verified_count"]:
            raise ValueError("Web variant has no retained, verified web evidence")
        rows.append({"case_id": case_id, "field": field, "published_issn": target,
                     "target_work_id": work_id,
                     "grouped_rank": rank_for(target, base_keys, base_issns, base_keys),
                     "web_augmented_rank": rank_for(target, web_keys, web_issns, web_keys),
                     "preprint_sha256": preprint_hash, "grouped_record_sha256": digest(base_path),
                     "web_input_sha256": digest(input_path), "web_record_sha256": digest(web_path)})
    def summarize(items):
        return {name: metrics([row[key] for row in items]) for name, key in
                (("grouped", "grouped_rank"), ("web_augmented", "web_augmented_rank"))}
    return {"schema_version": 1, "status": "provisional_unreviewed",
            "note": "The paired records share hashed OpenAlex responses and exclude the target work. Web leads were manually selected; registry response bodies, source selection, preprint leakage, duplicate works, journal fit, and labels require independent review.",
            "benchmark_manifest_sha256": digest(manifest_path), "cases": rows,
            "overall": summarize(rows),
            "by_field": {field: summarize([row for row in rows if row["field"] == field])
                         for field in sorted({row["field"] for row in rows})}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Absolute private web variant manifest")
    args = parser.parse_args()
    try:
        result = score_manifest(args.cases)
        output = private_file(args.cases).parent / "web-variant-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
