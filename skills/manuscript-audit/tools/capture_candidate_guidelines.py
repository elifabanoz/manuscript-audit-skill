#!/usr/bin/env python3
"""Capture a host-verified guideline page for one selected journal candidate."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from discover_journals import SOURCE_ID, fetch_json
from fetch_guidelines import https_url, normalized_host, save_source
from prepare_run import outside_git


ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
PUBLISHER_ID = re.compile(r"^https://openalex\.org/P\d+$")


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


def openalex_host(candidate, issn, instruction_host, opener):
    source_id = candidate.get("source_id")
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("A current OpenAlex journal record is required to verify the publisher host")
    source, source_hash = fetch_json(
        f"https://api.openalex.org/sources/{source_id.rsplit('/', 1)[1]}"
        "?select=id,type,issn,issn_l,homepage_url,host_organization", opener)
    if (not isinstance(source, dict) or source.get("id") != source_id
            or source.get("type") != "journal"
            or issn not in ([source.get("issn_l")] +
                            (source.get("issn") if isinstance(source.get("issn"), list) else []))):
        raise ValueError("The current OpenAlex journal identity does not match the selected ISSN")
    if homepage_host(source.get("homepage_url")) == instruction_host:
        return "openalex_journal_homepage", None, None, source_hash, None
    publisher_id = source.get("host_organization")
    if not isinstance(publisher_id, str) or not PUBLISHER_ID.fullmatch(publisher_id):
        raise ValueError("OpenAlex has no usable publisher identity for this journal")
    publisher, publisher_hash = fetch_json(
        f"https://api.openalex.org/publishers/{publisher_id.rsplit('/', 1)[1]}"
        "?select=id,homepage_url", opener)
    homepage = publisher.get("homepage_url") if isinstance(publisher, dict) else None
    if not isinstance(publisher, dict) or publisher.get("id") != publisher_id:
        raise ValueError("The OpenAlex publisher identity could not be confirmed")
    if homepage_host(homepage) != instruction_host:
        raise ValueError("The author-instruction host differs from the current publisher homepage")
    return "openalex_publisher_homepage", publisher_id, homepage, source_hash, publisher_hash


def capture(discovery_record, issn, output_root, allow_network=False, opener=urlopen,
            guideline_url=None):
    if not isinstance(issn, str) or not ISSN.fullmatch(issn):
        raise ValueError("--issn must be a valid ISSN-shaped identifier")
    root = output_root.expanduser()
    outside_git(root)
    candidate, discovery_hash = read_candidate(discovery_record, issn)
    home = candidate.get("homepage_url")
    instructions = guideline_url if guideline_url is not None else candidate.get("author_instructions_url")
    link_origin = "operator_supplied" if guideline_url is not None else "doaj_metadata"
    host = homepage_host(home)
    status = "needs_manual_verification"
    reason = None
    source_record = None
    domain_basis = None
    publisher_id = publisher_homepage = source_hash = publisher_hash = None
    if guideline_url is None and not candidate.get("doaj_id"):
        reason = "No DOAJ author-instruction link is attached to this candidate"
    elif not isinstance(instructions, str):
        reason = "No usable HTTPS author-instruction link is available for this candidate"
    else:
        try:
            https_url(instructions)
        except ValueError:
            reason = "The author-instruction link is not a public HTTPS URL"
        else:
            instruction_host = normalized_host(instructions)
            if not allow_network:
                reason = "Add --allow-network to capture the linked guideline page"
            elif guideline_url is None and instruction_host == host:
                domain_basis = "journal_homepage"
                status = "capture_pending"
            else:
                try:
                    domain_basis, publisher_id, publisher_homepage, source_hash, publisher_hash = openalex_host(
                        candidate, issn, instruction_host, opener)
                    status = "capture_pending"
                except (OSError, UnicodeError, ValueError) as error:
                    reason = str(error)
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
            source_path = save_source(instructions, directory, opener=opener,
                                      allowed_host=normalized_host(instructions))
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
              "link_origin": link_origin,
              "domain_basis": domain_basis, "openalex_publisher_id": publisher_id,
              "publisher_homepage_url": publisher_homepage,
              "openalex_source_response_sha256": source_hash,
              "openalex_publisher_response_sha256": publisher_hash,
              "status": status, "reason": reason, "source_record": source_record,
              "note": "A captured page still requires live-page review and independent source-backed profile comparison"}
    output = directory / "source-locator.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-record", required=True, type=Path, help="Private candidates.json")
    parser.add_argument("--issn", required=True, help="One ISSN from the selected candidate")
    parser.add_argument("--guideline-url", help="Operator-selected official HTTPS guideline URL; checked against current OpenAlex journal or publisher metadata")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "sources")
    parser.add_argument("--allow-network", action="store_true", help="Fetch a DOAJ author-instruction page after journal or publisher host checks")
    args = parser.parse_args()
    try:
        print(capture(args.discovery_record, args.issn, args.output_root, args.allow_network,
                      guideline_url=args.guideline_url))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
