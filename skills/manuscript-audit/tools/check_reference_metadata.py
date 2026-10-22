#!/usr/bin/env python3
"""Compare selected private bibliography entries with saved Crossref metadata."""

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from list_dois import collect, read_corpus
from prepare_run import outside_git


def normalized(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def contains_phrase(haystack, phrase):
    needle = normalized(phrase)
    return bool(needle and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized(haystack)))


def selected_cases(path):
    path = path.expanduser().resolve()
    outside_git(path)
    raw = path.read_bytes()
    if len(raw) > 65_536:
        raise ValueError("Reference cases exceed 64 KB")
    record = json.loads(raw)
    cases = record.get("references") if isinstance(record, dict) and record.get("schema_version") == 1 else None
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("Reference cases need 1 to 100 selected bibliography entries")
    seen = set()
    required = {"doi", "path", "start_line", "end_line", "stated_title", "stated_year"}
    for item in cases:
        if not isinstance(item, dict) or not required <= set(item) \
                or set(item) - required - {"first_author_family"}:
            raise ValueError("Reference case has missing or unknown fields")
        if not all(isinstance(item[key], str) and item[key].strip()
                   for key in ("doi", "path", "stated_title")):
            raise ValueError("Reference case DOI, path, and title must be nonempty")
        if "first_author_family" in item and (not isinstance(item["first_author_family"], str)
                                               or not item["first_author_family"].strip()):
            raise ValueError("First author family name must be nonempty when supplied")
        if type(item["start_line"]) is not int or type(item["end_line"]) is not int \
                or item["start_line"] < 1 or not item["start_line"] <= item["end_line"] <= item["start_line"] + 7:
            raise ValueError("Reference span must contain 1 to 8 physical lines")
        if type(item["stated_year"]) is not int or not 1600 <= item["stated_year"] <= 2200:
            raise ValueError("Reference year must be an integer from 1600 to 2200")
        key = (item["doi"].casefold(), item["path"], item["start_line"], item["end_line"])
        if key in seen:
            raise ValueError("Reference case is duplicated")
        seen.add(key)
    return cases, hashlib.sha256(raw).hexdigest()


def compare(run, cases_path):
    run = run.resolve()
    outside_git(run)
    output_path = run / "reference-metadata-checks.json"
    if output_path.exists():
        raise FileExistsError(output_path)
    cases, cases_hash = selected_cases(cases_path)
    _, files = read_corpus(run)
    corpus_files = {path: text.splitlines() for path, text in files}
    inventory_path = run / "doi-inventory.json"
    inventory_bytes = inventory_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    current = collect(run)
    if inventory != current:
        raise ValueError("DOI inventory differs from the current run")
    metadata_bytes = (run / "doi-metadata.json").read_bytes()
    metadata = json.loads(metadata_bytes)
    if (not isinstance(metadata, dict) or metadata.get("schema_version") != 1
            or metadata.get("inventory_sha256") != hashlib.sha256(inventory_bytes).hexdigest()
            or metadata.get("corpus_sha256") != current["corpus_sha256"]
            or not isinstance(metadata.get("results"), list)):
        raise ValueError("Crossref metadata does not match the current DOI inventory")
    indexed = {item["doi"].casefold(): item for item in metadata["results"]
               if isinstance(item, dict) and isinstance(item.get("doi"), str)}
    inventoried = {item["doi"].casefold() for item in current["dois"]}
    if set(indexed) != inventoried or len(indexed) != len(metadata["results"]):
        raise ValueError("Crossref result set differs from the DOI inventory")
    for doi, item in indexed.items():
        if item.get("status") == "registered_in_crossref" and (
                not isinstance(item.get("registered_doi"), str)
                or item["registered_doi"].casefold() != doi
                or not isinstance(item.get("response_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["response_sha256"])):
            raise ValueError("Registered Crossref result lacks matching DOI provenance")

    results = []
    for item in cases:
        key = item["doi"].casefold()
        if key not in inventoried:
            raise ValueError("Selected reference DOI is not in the current inventory")
        if item["path"] not in corpus_files:
            raise ValueError("Selected reference path is not a supplied manuscript file")
        lines = corpus_files[item["path"]]
        if item["end_line"] > len(lines):
            raise ValueError("Reference span exceeds the supplied file")
        span = "\n".join(lines[item["start_line"] - 1:item["end_line"]])
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(item['doi'])}(?![A-Za-z0-9/._-])",
                         span, flags=re.IGNORECASE):
            raise ValueError("Reference DOI is absent from its selected span")
        citation_text = re.sub(re.escape(item["doi"]), "", span, flags=re.IGNORECASE)
        if not contains_phrase(citation_text, item["stated_title"]):
            raise ValueError("Stated title is absent from its selected span")
        if not re.search(rf"(?<!\d){item['stated_year']}(?!\d)", citation_text):
            raise ValueError("Stated year is absent from its selected span")
        if "first_author_family" in item and not contains_phrase(citation_text, item["first_author_family"]):
            raise ValueError("Stated first author is absent from its selected span")
        registry = indexed[key]
        checks = {}
        if registry.get("status") == "registered_in_crossref":
            title = registry.get("title")
            if isinstance(title, str) and title.strip():
                stated, registered = normalized(item["stated_title"]), normalized(title)
                checks["title"] = "same" if stated == registered else "review"
                title_similarity = round(SequenceMatcher(None, stated, registered).ratio(), 3)
            else:
                checks["title"] = "metadata_missing"
                title_similarity = None
            year = registry.get("publication_year")
            checks["year"] = ("same" if year == item["stated_year"] else "review") \
                if type(year) is int else "metadata_missing"
            if "first_author_family" in item:
                family = registry.get("first_author_family")
                checks["first_author_family"] = ("same" if normalized(family) == normalized(item["first_author_family"])
                                                  else "review") if isinstance(family, str) and family.strip() else "metadata_missing"
            update_status = registry.get("update_metadata_status")
            signals = registry.get("update_signals")
            if update_status == "reported" and isinstance(signals, list) and signals:
                checks["post_publication_update"] = "review"
            elif update_status == "not_reported" and signals == []:
                checks["post_publication_update"] = "none_reported"
            else:
                checks["post_publication_update"] = "metadata_missing"
            status = "needs_review" if "review" in checks.values() else (
                "metadata_incomplete" if "metadata_missing" in checks.values() else "metadata_consistent")
        else:
            status = "registry_unavailable"
            title_similarity = None
        results.append({"doi": item["doi"], "path": item["path"],
                        "start_line": item["start_line"], "end_line": item["end_line"],
                        "reference_span_sha256": hashlib.sha256(span.encode("utf-8")).hexdigest(),
                        "registry_status": registry.get("status"), "status": status,
                        "checks": checks, "title_similarity": title_similarity})
    output = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "corpus_sha256": current["corpus_sha256"],
              "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
              "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
              "cases_sha256": cases_hash,
              "note": "Metadata comparison is a provisional lead; verify the original reference and publication record before making a finding",
              "results": results}
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", required=True, type=Path, help="Private selected bibliography entries")
    args = parser.parse_args()
    try:
        output = compare(args.run, args.cases)
        print(json.dumps({"checked": len(output["results"]), "output": str(args.run / "reference-metadata-checks.json")}))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
