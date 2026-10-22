#!/usr/bin/env python3
"""Prepare a private run from a fresh, independently compared journal profile."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prepare_run import outside_git, prepare


VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "validate-cached-profile.cjs"
ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
RUN_DIRECTORY = re.compile(r"^run-\d+$")


def validate_source_run(source_run, ttl_days):
    check = subprocess.run(["node", str(VALIDATOR), str(source_run), str(ttl_days)],
                           capture_output=True, text=True, check=False)
    if check.returncode:
        raise ValueError(check.stderr.strip() or "Cached profile validation failed")
    return json.loads(check.stdout)


def find_cached_source(cache_root, issn, ttl_days):
    if not isinstance(issn, str) or not ISSN.fullmatch(issn):
        raise ValueError("--issn must be an ISSN-shaped identifier")
    if cache_root.is_symlink():
        raise ValueError("Cache root cannot be a symbolic link")
    root = cache_root.expanduser().resolve()
    outside_git(root)
    if not root.is_dir():
        raise ValueError("--cache-root must be an existing private directory")

    matches = []
    for paper in root.iterdir():
        if not paper.is_dir() or paper.is_symlink():
            continue
        for run in paper.iterdir():
            if not RUN_DIRECTORY.fullmatch(run.name) or not run.is_dir() or run.is_symlink():
                continue
            profile_path = run / "journal-profile.json"
            if not profile_path.is_file() or profile_path.is_symlink() or profile_path.stat().st_size > 2_000_000:
                continue
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                if not isinstance(profile, dict) or profile.get("issn") != issn:
                    continue
                approval = validate_source_run(run, ttl_days)
            except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if approval.get("issn") == issn:
                matches.append((run, approval))
    if not matches:
        raise ValueError("No fresh, independently agreed cached profile matches the selected ISSN")
    if len({approval["profile_sha256"] for _, approval in matches}) != 1:
        raise ValueError("Multiple different fresh profiles match the ISSN; select --source-run explicitly")
    return max(matches, key=lambda item: (item[1]["oldest_source_retrieved_at"], str(item[0])))[0]


def prepare_cached(args):
    cache_root = getattr(args, "cache_root", None)
    issn = getattr(args, "issn", None)
    if args.source_run is not None:
        if cache_root is not None or issn is not None:
            raise ValueError("Use --source-run or both --cache-root and --issn")
        source_run = args.source_run.expanduser().resolve()
    elif cache_root is not None and issn is not None:
        source_run = find_cached_source(cache_root, issn, args.ttl_days)
    else:
        raise ValueError("Use --source-run or both --cache-root and --issn")
    outside_git(source_run)
    output_root = args.output_root.expanduser()
    outside_git(output_root)
    approval = validate_source_run(source_run, args.ttl_days)
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
             "source_run": str(source_run), "selected_issn": profile["issn"],
             "source_manifest_sha256": approval["manifest_sha256"],
             "profile_sha256": approval["profile_sha256"],
             "oldest_source_retrieved_at": approval["oldest_source_retrieved_at"],
             "ttl_days": approval["ttl_days"]}
    (run / "profile-reuse.json").write_text(json.dumps(reuse, indent=2) + "\n", encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, help="Private run containing two agreed profile drafts")
    parser.add_argument("--cache-root", type=Path, help="Private run root to search for an agreed profile")
    parser.add_argument("--issn", help="Selected ISSN when searching --cache-root")
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
