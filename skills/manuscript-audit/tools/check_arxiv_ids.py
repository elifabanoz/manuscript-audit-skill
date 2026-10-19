#!/usr/bin/env python3
"""Look up inventoried arXiv IDs after explicit network opt-in."""

import argparse
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from list_arxiv_ids import collect


ATOM = "{http://www.w3.org/2005/Atom}"
VERSION = re.compile(r"v[1-9]\d*$", re.IGNORECASE)
USER_AGENT = "manuscript-audit-skill/0.1 (arXiv metadata lookup)"


def base_id(value):
    return VERSION.sub("", value).casefold()


def fetch_batch(ids, opener):
    query = urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    request = Request("https://export.arxiv.org/api/query?" + query,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"})
    try:
        with opener(request, timeout=20) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("arXiv response exceeded 5 MB")
        feed = ET.fromstring(raw)
        if feed.tag != ATOM + "feed":
            raise ValueError("arXiv response is not an Atom feed")
        entries = {}
        for entry in feed.findall(ATOM + "entry"):
            url = entry.findtext(ATOM + "id", default="")
            path = unquote(urlparse(url).path)
            if "/api/errors" in path:
                raise ValueError("arXiv API returned an error entry")
            if "/abs/" not in path:
                continue
            identifier = path.split("/abs/", 1)[1]
            title = " ".join(entry.findtext(ATOM + "title", default="").split())
            published = entry.findtext(ATOM + "published", default="")
            entries[identifier.casefold()] = {
                "registered_id": identifier,
                "title": title or None,
                "publication_year": int(published[:4]) if re.match(r"^\d{4}-", published) else None,
            }
        output = []
        for identifier in ids:
            match = entries.get(identifier.casefold())
            if not match and not VERSION.search(identifier):
                match = next((item for key, item in entries.items() if base_id(key) == base_id(identifier)), None)
            output.append({"arxiv_id": identifier, "status": "registered_in_arxiv", **match} if match
                          else {"arxiv_id": identifier, "status": "not_found_in_arxiv"})
        return output
    except HTTPError as error:
        reason = f"arXiv HTTP {error.code}"
    except (OSError, ET.ParseError, UnicodeError, ValueError):
        reason = "arXiv request or response failed"
    return [{"arxiv_id": identifier, "status": "lookup_failed", "reason": reason} for identifier in ids]


def check(run, allow_network=False, opener=urlopen, pause=time.sleep):
    if not allow_network:
        raise ValueError("arXiv lookup requires --allow-network; arXiv IDs leave this computer")
    run = run.resolve()
    inventory_path = run / "arxiv-inventory.json"
    raw = inventory_path.read_bytes()
    saved = json.loads(raw)
    current = collect(run)
    if saved != current:
        raise ValueError("arXiv inventory differs from the current run; prepare a new inventory")
    ids = [item["arxiv_id"] for item in current["arxiv_ids"]]
    if len(ids) > 100:
        raise ValueError("arXiv inventory exceeds the 100-record lookup limit")
    output_path = run / "arxiv-metadata.json"
    if output_path.exists():
        raise FileExistsError(output_path)
    results = []
    for index in range(0, len(ids), 20):
        if index:
            pause(3)
        results.extend(fetch_batch(ids[index:index + 20], opener))
    output = {
        "schema_version": 1,
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256": current["corpus_sha256"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "arXiv API",
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
    parser.add_argument("--allow-network", action="store_true", help="Send arXiv IDs to arXiv")
    args = parser.parse_args()
    try:
        result = check(args.run, args.allow_network)
        print(json.dumps({"checked": len(result["results"]), "output": str(args.run / "arxiv-metadata.json")}))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
