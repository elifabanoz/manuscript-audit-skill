#!/usr/bin/env python3
"""Bind an in-text citation claim to private source text for human review."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from list_dois import collect, read_corpus
from prepare_run import outside_git


DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
LINE_ID = re.compile(r"^L([1-9]\d*)$")


def read_private_file(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Citation source and recipe paths must be absolute")
    path = path.resolve()
    outside_git(path)
    if not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError("Missing or oversized private citation input")
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_quote(value, text, label):
    if not isinstance(value, str) or not 8 <= len(value) <= 800 or value.strip() != value:
        raise ValueError(f"{label} needs an exact 8 to 800 character quote")
    if text.count(value) != 1:
        raise ValueError(f"{label} quote must occur exactly once")
    return value


def prepare(run_directory, recipe_file, output_file):
    run = Path(run_directory)
    if not run.is_absolute():
        raise ValueError("Run path must be absolute")
    run = run.resolve()
    outside_git(run)
    manifest, files = read_corpus(run)
    recipe_file = read_private_file(recipe_file)
    if recipe_file.stat().st_size > 65_536:
        raise ValueError("Citation recipe exceeds 64 KB")
    recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
    if (not isinstance(recipe, dict) or set(recipe) != {"schema_version", "cited_doi", "claim", "source"}
            or recipe["schema_version"] != 1 or not isinstance(recipe["cited_doi"], str)
            or not DOI.fullmatch(recipe["cited_doi"])
            or not isinstance(recipe["claim"], dict)
            or set(recipe["claim"]) != {"path", "line_id", "quote"}
            or not isinstance(recipe["source"], dict)
            or set(recipe["source"]) != {"file", "quote"}):
        raise ValueError("Invalid citation claim recipe")
    inventory = collect(run)
    if recipe["cited_doi"].casefold() not in {entry["doi"].casefold() for entry in inventory["dois"]}:
        raise ValueError("Cited DOI is absent from the supplied manuscript corpus")
    corpus = dict(files)
    claim = recipe["claim"]
    if not isinstance(claim["path"], str) or claim["path"] not in corpus:
        raise ValueError("Claim path is not a supplied manuscript file")
    line_match = LINE_ID.fullmatch(claim["line_id"]) if isinstance(claim["line_id"], str) else None
    if not line_match:
        raise ValueError("Claim needs a physical manuscript line ID")
    lines = corpus[claim["path"]].splitlines()
    line_number = int(line_match.group(1))
    if line_number > len(lines):
        raise ValueError("Claim line exceeds the supplied manuscript file")
    exact_quote(claim["quote"], lines[line_number - 1], "Manuscript claim")
    source_file = read_private_file(recipe["source"]["file"])
    if source_file in {(run / path).resolve() for path in corpus}:
        raise ValueError("Cited source cannot be the submitted manuscript corpus")
    if source_file.suffix.lower() not in (".md", ".txt"):
        raise ValueError("Citation source must be reviewed UTF-8 text or Markdown")
    source_text = source_file.read_text(encoding="utf-8-sig")
    exact_quote(recipe["source"]["quote"], source_text, "Cited source")
    packet = {
        "schema_version": 1,
        "status": "pending_human_citation_review",
        "cited_doi": recipe["cited_doi"],
        "run_manifest_sha256": sha256(run / "run-manifest.json"),
        "recipe_sha256": sha256(recipe_file),
        "claim": {"path": claim["path"], "line_id": claim["line_id"], "quote": claim["quote"],
                  "quote_sha256": hashlib.sha256(claim["quote"].encode("utf-8")).hexdigest()},
        "source": {"file": str(source_file), "file_sha256": sha256(source_file),
                   "quote": recipe["source"]["quote"],
                   "quote_sha256": hashlib.sha256(recipe["source"]["quote"].encode("utf-8")).hexdigest()},
        "review_questions": ["Does the in-text citation actually identify this DOI?",
                             "Does the private source file reproduce the cited work?",
                             "Does the complete cited work support the manuscript claim in context?"],
        "note": "Exact quote matching checks location only. DOI-to-source identity, citation linkage, and semantic support require human review. No finding is confirmed.",
    }
    output = Path(output_file)
    if not output.is_absolute():
        raise ValueError("Citation packet output must be absolute")
    output = output.resolve()
    outside_git(output)
    if not output.parent.is_dir():
        raise ValueError("Citation packet output parent is missing")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("recipe_file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        output = prepare(args.run_directory, args.recipe_file, args.output)
        print(json.dumps({"status": "pending_human_citation_review", "output": str(output)}))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
