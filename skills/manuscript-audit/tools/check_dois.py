#!/usr/bin/env python3
"""Look up inventoried DOIs in Crossref after explicit network opt-in."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from http_client import crossref_url, read_response
from list_dois import collect


USER_AGENT = "manuscript-audit-skill/0.1 (DOI metadata lookup)"


def first_text(value):
    return value[0] if isinstance(value, list) and value and isinstance(value[0], str) else None


def publication_year(message):
    published = message.get("published")
    parts = published.get("date-parts") if isinstance(published, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        year = parts[0][0]
        return year if isinstance(year, int) and not isinstance(year, bool) else None
    return None


def first_author_family(message):
    authors = message.get("author")
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return None
    family = authors[0].get("family")
    return family.strip() if isinstance(family, str) and family.strip() else None


def publication_updates(message):
    updates = message.get("updated-by")
    if updates is None or updates == []:
        return "not_reported", []
    if not isinstance(updates, list) or len(updates) > 100:
        return "unusable", []
    signals = []
    for update in updates:
        if (not isinstance(update, dict) or not isinstance(update.get("type"), str)
                or not 1 <= len(update["type"].strip()) <= 64
                or not isinstance(update.get("DOI"), str)
                or not 1 <= len(update["DOI"].strip()) <= 255):
            return "unusable", []
        source = update.get("source")
        signals.append({"type": update["type"].strip().casefold(), "notice_doi": update["DOI"].strip(),
                        "source": source if isinstance(source, str) and source in {"publisher", "retraction-watch"} else None})
    return "reported", signals


def parse_response(raw, doi):
    message = json.loads(raw)["message"]
    if not isinstance(message, dict) or not isinstance(message.get("DOI"), str):
        raise ValueError("Crossref response lacks a DOI")
    if message["DOI"].casefold() != doi.casefold():
        raise ValueError("Crossref returned a different DOI")
    update_status, update_signals = publication_updates(message)
    return {
        "status": "registered_in_crossref",
        "registered_doi": message["DOI"],
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "title": first_text(message.get("title")),
        "container_title": first_text(message.get("container-title")),
        "publication_year": publication_year(message),
        "first_author_family": first_author_family(message),
        "type": message.get("type") if isinstance(message.get("type"), str) else None,
        "update_metadata_status": update_status,
        "update_signals": update_signals,
    }


def lookup_one(doi, opener):
    url = crossref_url("https://api.crossref.org/works/" + quote(doi, safe="/"))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        raw = read_response(request, opener, timeout=10, max_bytes=2_000_000).body
        try:
            return parse_response(raw, doi), raw
        except ValueError as error:
            return {"status": "lookup_failed", "reason": str(error)}, None
    except HTTPError as error:
        if error.code == 404:
            return {"status": "not_found_in_crossref"}, None
        return {"status": "lookup_failed", "reason": f"Crossref HTTP {error.code}"}, None
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return {"status": "lookup_failed", "reason": "Crossref request or response failed"}, None


def cache_response(run, raw):
    directory = run / "sources" / "crossref"
    if (run / "sources").is_symlink() or directory.is_symlink():
        raise ValueError("Crossref cache cannot use a symbolic link")
    directory.mkdir(exist_ok=True)
    if not directory.resolve().is_relative_to(run):
        raise ValueError("Crossref cache must remain inside the private run")
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"sources/crossref/{digest}.json"
    target = run / relative
    try:
        with target.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != raw:
            raise ValueError("Existing private Crossref snapshot differs from the response") from None
    return relative


def check(run, allow_network=False, opener=urlopen, pause=time.sleep):
    if not allow_network:
        raise ValueError("Crossref lookup requires --allow-network; DOI values leave this computer")
    run = run.resolve()
    inventory_path = run / "doi-inventory.json"
    inventory_bytes = inventory_path.read_bytes()
    saved = json.loads(inventory_bytes)
    current = collect(run)
    if saved != current:
        raise ValueError("DOI inventory differs from the current run; prepare a new inventory")
    if len(current["dois"]) > 100:
        raise ValueError("DOI inventory exceeds the 100-record lookup limit")
    output_path = run / "doi-metadata.json"
    if output_path.exists():
        raise FileExistsError(output_path)
    results = []
    for index, item in enumerate(current["dois"]):
        if index:
            pause(0.2)
        registry, response_raw = lookup_one(item["doi"], opener)
        if response_raw is not None:
            registry["response_path"] = cache_response(run, response_raw)
        results.append({"doi": item["doi"], **registry})
    output = {
        "schema_version": 1,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "corpus_sha256": current["corpus_sha256"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "Crossref REST API",
        "note": "Registry metadata only; citation accuracy, reference completeness, and scholarly relevance require review",
        "results": results,
    }
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--allow-network", action="store_true", help="Send DOI values to Crossref")
    args = parser.parse_args()
    try:
        result = check(args.run, args.allow_network)
        print(json.dumps({"checked": len(result["results"]), "output": str(args.run / "doi-metadata.json")}))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
