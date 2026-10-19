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


def lookup_one(doi, opener):
    url = "https://api.crossref.org/works/" + quote(doi, safe="/")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with opener(request, timeout=10) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            return {"status": "lookup_failed", "reason": "Crossref response exceeded 2 MB"}
        message = json.loads(raw)["message"]
        if not isinstance(message, dict) or not isinstance(message.get("DOI"), str):
            raise ValueError("Crossref response lacks a DOI")
        if message["DOI"].casefold() != doi.casefold():
            return {"status": "lookup_failed", "reason": "Crossref returned a different DOI"}
        return {
            "status": "registered_in_crossref",
            "registered_doi": message["DOI"],
            "title": first_text(message.get("title")),
            "container_title": first_text(message.get("container-title")),
            "publication_year": publication_year(message),
            "type": message.get("type") if isinstance(message.get("type"), str) else None,
        }
    except HTTPError as error:
        if error.code == 404:
            return {"status": "not_found_in_crossref"}
        return {"status": "lookup_failed", "reason": f"Crossref HTTP {error.code}"}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return {"status": "lookup_failed", "reason": "Crossref request or response failed"}


def check(run, allow_network=False, opener=urlopen, pause=time.sleep):
    if not allow_network:
        raise ValueError("Crossref lookup requires --allow-network; DOI values leave this computer")
    run = run.resolve()
    inventory_path = run / "doi-inventory.json"
    raw = inventory_path.read_bytes()
    saved = json.loads(raw)
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
        results.append({"doi": item["doi"], **lookup_one(item["doi"], opener)})
    output = {
        "schema_version": 1,
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
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
