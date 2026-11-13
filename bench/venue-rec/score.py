#!/usr/bin/env python3
"""Score private journal shortlists against published venues, provisionally."""

import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re


ISSN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
WORK_ID = re.compile(r"^W[1-9][0-9]*$")


def private_file(value):
    if not isinstance(value, (str, Path)):
        raise ValueError("Benchmark paths must be absolute")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Benchmark paths must be absolute")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Benchmark inputs and results must stay outside Git")
    if not path.is_file():
        raise ValueError(f"Missing private file: {path}")
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError(f"Oversized JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_pool(record):
    candidates = record.get("candidates") if isinstance(record, dict) and record.get("schema_version") == 4 else None
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
        raise ValueError("Discovery record needs 1 to 100 candidates")
    keys, issn_sets, seen_issns = [], [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Invalid discovery candidate")
        issns = candidate.get("issns")
        if not isinstance(issns, list) or not issns or any(
                not isinstance(value, str) or not ISSN.fullmatch(value) for value in issns):
            raise ValueError("Discovery candidate needs ISSNs")
        if len(set(issns)) != len(issns) or seen_issns.intersection(issns):
            raise ValueError("Discovery candidate ISSNs overlap")
        seen_issns.update(issns)
        key = candidate.get("source_id") or candidate.get("issn_l") or issns[0]
        if not isinstance(key, str) or not key.strip() or key in keys:
            raise ValueError("Discovery candidate keys must be unique")
        keys.append(key)
        issn_sets.append(set(issns))
    return keys, issn_sets


def rank_for(target, keys, issn_sets, order):
    return next((position for position, key in enumerate(order, start=1)
                 if target in issn_sets[keys.index(key)]), None)


def embedding_order(record, keys, preprint_hash, discovery_hash):
    if (not isinstance(record, dict) or set(record) != {
            "schema_version", "model_id", "preprint_sha256", "discovery_record_sha256",
            "preprint_vector", "candidate_vectors"}
            or record["schema_version"] != 1
            or not isinstance(record["model_id"], str) or not record["model_id"].strip()
            or record["preprint_sha256"] != preprint_hash
            or record["discovery_record_sha256"] != discovery_hash
            or not isinstance(record["candidate_vectors"], dict)
            or set(record["candidate_vectors"]) != set(keys)):
        raise ValueError("Embedding vectors do not match the pinned preprint and candidate pool")

    def normalized(values, dimension=None):
        if (not isinstance(values, list) or not 2 <= len(values) <= 2048
                or dimension is not None and len(values) != dimension
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       or abs(value) > 1_000_000 for value in values)):
            raise ValueError("Embedding vectors need equal, finite dimensions")
        norm = math.sqrt(math.fsum(value * value for value in values))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("Embedding vectors must be nonzero")
        return [value / norm for value in values]

    preprint_vector = normalized(record["preprint_vector"])
    scores = {}
    for key in keys:
        vector = normalized(record["candidate_vectors"][key], len(preprint_vector))
        scores[key] = math.fsum(left * right for left, right in zip(preprint_vector, vector))
    return sorted(keys, key=lambda key: (-scores[key], key))


def metrics(ranks):
    count = len(ranks)
    return {"count": count,
            "hit_at_1": sum(rank is not None and rank <= 1 for rank in ranks) / count,
            "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count,
            "hit_at_10": sum(rank is not None and rank <= 10 for rank in ranks) / count,
            "mrr": sum(1 / rank for rank in ranks if rank is not None) / count}


def score_manifest(manifest_path):
    manifest_path = private_file(manifest_path)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "data_freeze", "cases"} \
            or manifest["schema_version"] != 2:
        raise ValueError("Unsupported venue benchmark manifest")
    if not isinstance(manifest["data_freeze"], str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", manifest["data_freeze"]):
        raise ValueError("Invalid data_freeze date")
    try:
        date.fromisoformat(manifest["data_freeze"])
    except (TypeError, ValueError):
        raise ValueError("Invalid data_freeze date") from None
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("Venue benchmark needs cases")
    rows, seen_ids, seen_preprints, seen_works = [], set(), set(), set()
    for case in cases:
        required = {"case_id", "field", "published_issn", "target_work_id", "preprint_file",
                    "discovery_record", "embedding_baseline"}
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("Venue case has unexpected fields")
        case_id, field, target = case["case_id"], case["field"], case["published_issn"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
            raise ValueError("Venue case IDs must be unique")
        seen_ids.add(case_id)
        if not isinstance(field, str) or not field.strip() or not isinstance(target, str) \
                or not ISSN.fullmatch(target):
            raise ValueError(f"{case_id}: invalid field or published ISSN")
        work_id = case["target_work_id"]
        if not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id):
            raise ValueError(f"{case_id}: invalid target OpenAlex work ID")
        preprint = private_file(case["preprint_file"])
        discovery = private_file(case["discovery_record"])
        baseline_path = private_file(case["embedding_baseline"])
        preprint_hash = digest(preprint)
        if preprint_hash in seen_preprints:
            raise ValueError(f"{case_id}: duplicate preprint in benchmark")
        seen_preprints.add(preprint_hash)
        if work_id in seen_works:
            raise ValueError(f"{case_id}: duplicate target work in benchmark")
        seen_works.add(work_id)
        discovery_hash = digest(discovery)
        discovery_record = read_json(discovery)
        keys, issn_sets = candidate_pool(discovery_record)
        if (discovery_record.get("strategy") != "grouped"
                or discovery_record.get("excluded_work_id") != work_id
                or not isinstance(discovery_record.get("openalex_work_filter"), str)
                or f"ids.openalex:!{work_id}" not in
                discovery_record.get("openalex_work_filter", "").split(",")
                or any(discovery_record.get(key) is not None for key in
                       ("citation_record_sha256", "doaj_subject", "web_input_sha256"))
                or any(candidate.get("evidence") != ["openalex_grouped_works"]
                       for candidate in discovery_record["candidates"])):
            raise ValueError(f"{case_id}: discovery must exclude its target work and use OpenAlex grouped evidence only")
        baseline = read_json(baseline_path)
        if not isinstance(baseline, dict) or set(baseline) != {
                "schema_version", "method", "model_id", "preprint_sha256", "discovery_record_sha256",
                "vector_file", "vectors_sha256", "ranked_keys"} \
                or baseline["schema_version"] != 2 or baseline["method"] != "embedding_only" \
                or baseline["preprint_sha256"] != preprint_hash \
                or baseline["discovery_record_sha256"] != discovery_hash \
                or not isinstance(baseline["ranked_keys"], list):
            raise ValueError(f"{case_id}: baseline does not rank the pinned candidate pool")
        vector_path = private_file(baseline["vector_file"])
        vectors = read_json(vector_path)
        if (digest(vector_path) != baseline["vectors_sha256"]
                or not isinstance(vectors, dict)
                or baseline["model_id"] != vectors.get("model_id")
                or baseline["ranked_keys"] != embedding_order(
                    vectors, keys, preprint_hash, discovery_hash)):
            raise ValueError(f"{case_id}: baseline does not rank the pinned candidate pool")
        rows.append({"case_id": case_id, "field": field, "published_issn": target,
                     "target_work_id": work_id,
                     "discovery_rank": rank_for(target, keys, issn_sets, keys),
                     "embedding_baseline_rank": rank_for(target, keys, issn_sets, baseline["ranked_keys"]),
                     "preprint_sha256": preprint_hash,
                     "discovery_record_sha256": discovery_hash,
                     "embedding_baseline_sha256": digest(baseline_path)})
    by_field = {}
    for field in sorted({row["field"] for row in rows}):
        selected = [row for row in rows if row["field"] == field]
        by_field[field] = {name: metrics([row[key] for row in selected]) for name, key in (
            ("discovery", "discovery_rank"), ("embedding_baseline", "embedding_baseline_rank"))}
    return {"schema_version": 2, "status": "provisional_unreviewed",
            "note": "The target OpenAlex work was excluded from grouped discovery. Duplicate records, preprint clues, labels and the baseline still need independent review. Published venue is one known match, not the only suitable journal.",
            "benchmark_manifest_sha256": digest(manifest_path), "cases": rows,
            "overall": {name: metrics([row[key] for row in rows]) for name, key in (
                ("discovery", "discovery_rank"), ("embedding_baseline", "embedding_baseline_rank"))},
            "by_field": by_field}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Absolute private venue benchmark manifest")
    args = parser.parse_args()
    try:
        result = score_manifest(args.cases)
        output = private_file(args.cases).parent / "venue-rec-scores.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
