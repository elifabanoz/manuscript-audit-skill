#!/usr/bin/env python3
"""Export reviewed publisher source records for a different private journal run."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from prepare_cached_run import verify_live_sources
from prepare_run import outside_git


VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "validate-publisher-cache.cjs"
SOURCE_ID = re.compile(r"^src_[a-f0-9]{16}$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_source_run(source_run, publisher, ttl_days):
    result = subprocess.run(["node", str(VALIDATOR), str(source_run), publisher, str(ttl_days)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Publisher cache validation failed")
    return json.loads(result.stdout)


def prepare_publisher_cache(source_run, publisher, output_root, ttl_days=30, verify_live=False, opener=urlopen):
    source_run = source_run.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    outside_git(source_run)
    outside_git(output_root)
    if source_run == output_root or source_run in output_root.parents:
        raise ValueError("Publisher cache output cannot be inside the source run")
    approval = validate_source_run(source_run, publisher, ttl_days)
    if approval["publisher"] != publisher or approval["ttl_days"] != ttl_days:
        raise ValueError("Publisher cache approval differs from the request")
    rules_path = source_run / approval["publisher_rules_path"]
    if rules_path.is_symlink() or source_run not in rules_path.resolve().parents:
        raise ValueError("Publisher rules escaped the private source run")
    rules_bytes = rules_path.read_bytes()
    if digest(rules_bytes) != approval["publisher_rules_sha256"]:
        raise ValueError("Publisher rules changed after validation")
    source_records = []
    for source_id, source in approval["sources"].items():
        if not SOURCE_ID.fullmatch(source_id) or source["path"] != f"sources/{source_id}.txt":
            raise ValueError("Publisher source identity cannot be reproduced")
        source_path = source_run / source["path"]
        if source_path.is_symlink() or source_run not in source_path.resolve().parents:
            raise ValueError("Publisher source escaped the private source run")
        contents = source_path.read_bytes()
        if digest(contents) != source["sha256"]:
            raise ValueError("Publisher source changed after validation")
        expected_id = "src_" + digest((source["url"] + "\0" + source["sha256"]).encode("utf-8"))[:16]
        if expected_id != source_id:
            raise ValueError("Publisher source ID differs from its URL and content hash")
        source_records.append((source_id, source, contents))
    live = verify_live_sources(approval["sources"], opener) if verify_live else None
    output_root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", publisher.lower()).strip("-")[:48] or "publisher"
    destination = output_root / f"publisher-{slug}-{approval['publisher_rules_sha256'][:12]}"
    if destination.exists():
        raise ValueError("Publisher cache package already exists")
    with tempfile.TemporaryDirectory(prefix="publisher-export-", dir=output_root) as temporary:
        package = Path(temporary)
        (package / "publisher-rules.json").write_bytes(rules_bytes)
        for source_id, source, contents in source_records:
            directory = package / source_id
            directory.mkdir()
            (directory / "guideline.txt").write_bytes(contents)
            record = {"schema_version": 1, "text_file": "guideline.txt", "url": source["url"],
                      "retrieved_at": source["retrieved_at"], "sha256": source["sha256"]}
            (directory / "source.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        provenance = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                      "source_run": str(source_run), "publisher": publisher, "profile_sha256": approval["profile_sha256"],
                      "composition_sha256": approval["composition_sha256"],
                      "publisher_rules_sha256": approval["publisher_rules_sha256"],
                      "ttl_days": ttl_days, "live_verification": live}
        (package / "reuse.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        package.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--publisher", required=True, help="Exact publisher name in the reviewed source profile")
    parser.add_argument("--output-root", required=True, type=Path, help="Private directory outside Git")
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--verify-live", action="store_true", help="Compare the current publisher pages before export")
    args = parser.parse_args()
    try:
        print(prepare_publisher_cache(args.source_run, args.publisher, args.output_root,
                                      args.ttl_days, args.verify_live))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
