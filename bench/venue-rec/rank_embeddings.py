#!/usr/bin/env python3
"""Rank a private journal pool from supplied embeddings without a network call."""

import argparse
import json
from pathlib import Path

from score import candidate_pool, digest, embedding_order, private_file, read_json


def create_baseline(preprint_file, discovery_record, vector_file, output):
    preprint = private_file(preprint_file)
    discovery = private_file(discovery_record)
    vectors = private_file(vector_file)
    output = Path(output)
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("The baseline output path must be absolute and private")
    output = output.resolve()
    if any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise ValueError("The baseline output must stay outside Git")
    if output.parent != vectors.parent:
        raise ValueError("The baseline must be written beside its private vectors")
    preprint_hash, discovery_hash = digest(preprint), digest(discovery)
    keys, _ = candidate_pool(read_json(discovery))
    vector_record = read_json(vectors)
    order = embedding_order(vector_record, keys, preprint_hash, discovery_hash)
    result = {"schema_version": 2, "method": "embedding_only",
              "model_id": vector_record["model_id"],
              "preprint_sha256": preprint_hash, "discovery_record_sha256": discovery_hash,
              "vector_file": str(vectors), "vectors_sha256": digest(vectors),
              "ranked_keys": order}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preprint_file", type=Path, help="Absolute private masked preprint path")
    parser.add_argument("discovery_record", type=Path, help="Absolute private candidates.json path")
    parser.add_argument("vector_file", type=Path, help="Absolute private embedding vectors JSON path")
    parser.add_argument("output", type=Path, help="Absolute private baseline JSON path beside vectors")
    args = parser.parse_args()
    try:
        print(create_baseline(args.preprint_file, args.discovery_record, args.vector_file, args.output))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
