#!/usr/bin/env python3
"""Capture a DOAJ-linked guideline page for one selected journal candidate."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from fetch_guidelines import https_url, normalized_host, save_source
from prepare_run import outside_git


ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")


def read_candidate(discovery_record, issn):
    path = discovery_record.expanduser().resolve()
    outside_git(path)
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError("Discovery record exceeds 2 MB")
    try:
        record = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Discovery record is not valid JSON") from None
    if (not isinstance(record, dict) or record.get("schema_version") != 4
            or not isinstance(record.get("candidates"), list)):
        raise ValueError("Discovery record has an unexpected format")
    matches = [item for item in record["candidates"] if isinstance(item, dict)
               and isinstance(item.get("issns"), list) and issn in item["issns"]]
    if len(matches) != 1:
        raise ValueError("Selected ISSN must identify exactly one candidate")
    candidate = matches[0]
    if not isinstance(candidate.get("display_name"), str) or not candidate["display_name"].strip():
        raise ValueError("Selected candidate has no journal name")
    return candidate, hashlib.sha256(raw).hexdigest()


def homepage_host(homepage_url):
    if not isinstance(homepage_url, str):
        return None
    try:
        parsed = urlsplit(homepage_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)):
            return None
    except ValueError:
        return None
    try:
        https_url(f"https://{parsed.hostname}")
    except ValueError:
        return None
    return normalized_host(homepage_url)


def capture(discovery_record, issn, output_root, allow_network=False, opener=urlopen):
    if not isinstance(issn, str) or not ISSN.fullmatch(issn):
        raise ValueError("--issn must be a valid ISSN-shaped identifier")
    root = output_root.expanduser()
    outside_git(root)
    candidate, discovery_hash = read_candidate(discovery_record, issn)
    home = candidate.get("homepage_url")
    instructions = candidate.get("author_instructions_url")
    host = homepage_host(home)
    status = "needs_manual_verification"
    reason = None
    source_record = None
    if not candidate.get("doaj_id"):
        reason = "No DOAJ author-instruction link is attached to this candidate"
    elif not isinstance(instructions, str):
        reason = "DOAJ has no usable HTTPS author-instruction link for this candidate"
    elif host is None:
        reason = "The journal homepage has no usable public host"
    else:
        try:
            https_url(instructions)
        except ValueError:
            reason = "The author-instruction link is not a public HTTPS URL"
        else:
            if normalized_host(instructions) != host:
                reason = "The author-instruction link uses a different host from the journal homepage"
            elif not allow_network:
                reason = "Add --allow-network to capture the linked guideline page"
            else:
                status = "capture_pending"
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"candidate-guidelines-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available candidate-guideline number")
    if status == "capture_pending":
        try:
            source_path = save_source(instructions, directory, opener=opener, allowed_host=host)
            source_record = str(source_path.relative_to(directory)).replace("\\", "/")
            status = "captured_pending_review"
        except (OSError, UnicodeError, ValueError) as error:
            status = "needs_manual_verification"
            reason = str(error)
    record = {"schema_version": 1,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "discovery_record_sha256": discovery_hash,
              "journal": candidate["display_name"].strip(), "selected_issn": issn,
              "openalex_source_id": candidate.get("source_id"), "doaj_id": candidate.get("doaj_id"),
              "homepage_url": home, "author_instructions_url": instructions,
              "status": status, "reason": reason, "source_record": source_record,
              "note": "A captured page still requires live-page review and independent source-backed profile comparison"}
    output = directory / "source-locator.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-record", required=True, type=Path, help="Private candidates.json")
    parser.add_argument("--issn", required=True, help="One ISSN from the selected candidate")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "sources")
    parser.add_argument("--allow-network", action="store_true", help="Fetch a same-host DOAJ author-instruction page")
    args = parser.parse_args()
    try:
        print(capture(args.discovery_record, args.issn, args.output_root, args.allow_network))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
