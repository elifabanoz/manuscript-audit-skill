#!/usr/bin/env python3
"""Prepare a private run from a fresh, independently compared journal profile."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prepare_run import outside_git, prepare


VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "validate-cached-profile.cjs"


def prepare_cached(args):
    source_run = args.source_run.expanduser().resolve()
    outside_git(source_run)
    output_root = args.output_root.expanduser()
    outside_git(output_root)
    check = subprocess.run(["node", str(VALIDATOR), str(source_run), str(args.ttl_days)],
                           capture_output=True, text=True, check=False)
    if check.returncode:
        raise ValueError(check.stderr.strip() or "Cached profile validation failed")
    approval = json.loads(check.stdout)
    manifest_bytes = (source_run / "run-manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != approval["manifest_sha256"]:
        raise ValueError("Cached manifest changed after validation")
    manifest = json.loads(manifest_bytes)
    profile_bytes = (source_run / "journal-profile.json").read_bytes()
    if hashlib.sha256(profile_bytes).hexdigest() != approval["profile_sha256"]:
        raise ValueError("Cached profile changed after validation")

    with tempfile.TemporaryDirectory(prefix="manuscript-audit-cache-") as temporary:
        source_records = []
        for index, (source_id, source) in enumerate(manifest["sources"].items(), start=1):
            expected_id = "src_" + hashlib.sha256(
                (source["url"] + "\0" + source["sha256"]).encode("utf-8")).hexdigest()[:16]
            if source_id != expected_id or source["path"] != f"sources/{source_id}.txt":
                raise ValueError("Cached source identity cannot be reproduced")
            directory = Path(temporary) / f"source-{index}"
            directory.mkdir()
            source_path = source_run / source["path"]
            cached = source_path.resolve()
            if source_run not in cached.parents or source_path.is_symlink():
                raise ValueError("Cached source path escaped the private run")
            text = cached.read_bytes()
            if hashlib.sha256(text).hexdigest() != source["sha256"]:
                raise ValueError("Cached source changed after validation")
            (directory / "guideline.txt").write_bytes(text)
            record = {"schema_version": 1, "text_file": "guideline.txt", "url": source["url"],
                      "retrieved_at": source["retrieved_at"], "sha256": source["sha256"]}
            record_path = directory / "source.json"
            record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            source_records.append(record_path)
        prepared = argparse.Namespace(
            manuscript=args.manuscript, extraction_record=args.extraction_record,
            source_record=source_records, guideline_text=None, guideline_url=None,
            retrieved_at=None, supplementary=args.supplementary,
            output_root=output_root, host=args.host, model=args.model,
            data_freeze=args.data_freeze)
        run = prepare(prepared)

    new_manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
    profile = json.loads(profile_bytes)
    if new_manifest["sources"] != profile["sources"]:
        raise ValueError("Prepared source records differ from the cached profile")
    (run / "journal-profile.json").write_bytes(profile_bytes)
    reuse = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
             "source_manifest_sha256": approval["manifest_sha256"],
             "profile_sha256": approval["profile_sha256"],
             "oldest_source_retrieved_at": approval["oldest_source_retrieved_at"],
             "ttl_days": approval["ttl_days"]}
    (run / "profile-reuse.json").write_text(json.dumps(reuse, indent=2) + "\n", encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path, help="Private run containing two agreed profile drafts")
    parser.add_argument("--manuscript", required=True, type=Path)
    parser.add_argument("--extraction-record", type=Path)
    parser.add_argument("--supplementary", action="append", type=Path, default=[])
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit")
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--host", choices=("codex", "claude-code", "manual"), default="manual")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--data-freeze")
    args = parser.parse_args()
    try:
        print(prepare_cached(args))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
