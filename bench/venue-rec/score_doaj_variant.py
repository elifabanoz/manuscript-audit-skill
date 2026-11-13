#!/usr/bin/env python3
"""Compare private grouped and DOAJ-augmented journal shortlists."""

import argparse
from datetime import date
import json
from pathlib import Path
import re

from score import ISSN, WORK_ID, candidate_pool, digest, metrics, private_file, rank_for, read_json
from score_citation_variant import SHA256, checked_pool, pinned_groups


def score_manifest(manifest_path):
    manifest_path = private_file(manifest_path)
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "data_freeze", "cases"}
            or manifest["schema_version"] != 1
            or not isinstance(manifest["data_freeze"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", manifest["data_freeze"])):
        raise ValueError("Invalid DOAJ variant manifest")
    try:
        date.fromisoformat(manifest["data_freeze"])
    except ValueError:
        raise ValueError("Invalid DOAJ variant data freeze") from None
    if not isinstance(manifest["cases"], list) or not manifest["cases"]:
        raise ValueError("DOAJ variant manifest needs cases")
    rows, seen_ids, seen_works, seen_preprints = [], set(), set(), set()
    required = {"case_id", "field", "published_issn", "target_work_id", "preprint_file",
                "grouped_record", "doaj_record"}
    for case in manifest["cases"]:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("DOAJ variant case has unexpected fields")
        case_id, field, target, work_id = (case[key] for key in
                                           ("case_id", "field", "published_issn", "target_work_id"))
        if (not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids
                or not isinstance(field, str) or not field.strip()
                or not isinstance(target, str) or not ISSN.fullmatch(target)
                or not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id)
                or work_id in seen_works):
            raise ValueError("DOAJ variant needs unique cases and valid work/venue IDs")
        seen_ids.add(case_id)
        seen_works.add(work_id)
        preprint = private_file(case["preprint_file"])
        preprint_hash = digest(preprint)
        if preprint_hash in seen_preprints:
            raise ValueError("DOAJ variant repeats a preprint")
        seen_preprints.add(preprint_hash)
        base_path = private_file(case["grouped_record"])
        doaj_path = private_file(case["doaj_record"])
        base, doaj = read_json(base_path), read_json(doaj_path)
        base_keys, base_issns = checked_pool(base, work_id, False)
        doaj_keys, doaj_issns = candidate_pool(doaj)
        pinned_groups(base, doaj)
        if (doaj.get("strategy") != "grouped" or doaj.get("excluded_work_id") != work_id
                or not isinstance(doaj.get("openalex_work_filter"), str)
                or f"ids.openalex:!{work_id}" not in doaj["openalex_work_filter"].split(",")
                or not isinstance(doaj.get("doaj_subject"), str) or not doaj["doaj_subject"].strip()
                or not isinstance(doaj.get("doaj_response_sha256"), str)
                or not SHA256.fullmatch(doaj["doaj_response_sha256"])
                or doaj.get("citation_record_sha256") is not None
                or doaj.get("web_input_sha256") is not None):
            raise ValueError("DOAJ variant lacks a pinned subject response and target exclusion")
        base_identity = dict(zip(base_keys, base_issns))
        seen_doaj = False
        seen_doaj_ids = set()
        for candidate, key, issns in zip(doaj["candidates"], doaj_keys, doaj_issns):
            evidence = candidate.get("evidence")
            if (not isinstance(evidence, list) or not evidence
                    or len(set(evidence)) != len(evidence)
                    or set(evidence) - {"openalex_grouped_works", "doaj_subject"}
                    or "openalex_grouped_works" in evidence and key not in base_identity
                    or key in base_identity and base_identity[key] != issns
                    or "doaj_subject" in evidence and not isinstance(candidate.get("doaj_id"), str)
                    or "doaj_subject" in evidence and not candidate.get("doaj_id", "").strip()):
                raise ValueError("DOAJ variant contains unsupported evidence or changed journal identity")
            if "doaj_subject" in evidence:
                if candidate["doaj_id"] in seen_doaj_ids:
                    raise ValueError("DOAJ variant repeats a journal identifier")
                seen_doaj_ids.add(candidate["doaj_id"])
            seen_doaj |= "doaj_subject" in evidence
        if not seen_doaj:
            raise ValueError("DOAJ variant has no retained DOAJ evidence")
        rows.append({"case_id": case_id, "field": field, "published_issn": target,
                     "target_work_id": work_id,
                     "grouped_rank": rank_for(target, base_keys, base_issns, base_keys),
                     "doaj_augmented_rank": rank_for(target, doaj_keys, doaj_issns, doaj_keys),
                     "preprint_sha256": preprint_hash, "grouped_record_sha256": digest(base_path),
                     "doaj_record_sha256": digest(doaj_path)})
    def summarize(items):
        return {name: metrics([row[key] for row in items]) for name, key in
                (("grouped", "grouped_rank"), ("doaj_augmented", "doaj_augmented_rank"))}
    return {"schema_version": 1, "status": "provisional_unreviewed",
            "note": "Both variants share hashed OpenAlex group/source responses and exclude the target work. DOAJ response content and subject choice, preprint leakage, duplicate works, and venue labels still need independent review.",
            "benchmark_manifest_sha256": digest(manifest_path), "cases": rows,
            "overall": summarize(rows),
            "by_field": {field: summarize([row for row in rows if row["field"] == field])
                         for field in sorted({row["field"] for row in rows})}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Absolute private DOAJ variant manifest")
    args = parser.parse_args()
    try:
        result = score_manifest(args.cases)
        output = private_file(args.cases).parent / "doaj-variant-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
