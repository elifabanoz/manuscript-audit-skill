#!/usr/bin/env python3
"""Prepare a private, local manuscript audit run from UTF-8 text files."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SKILL_FILE = Path(__file__).resolve().parents[1] / "SKILL.md"
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    if not TIMESTAMP.fullmatch(value):
        raise ValueError("--retrieved-at must be an ISO 8601 time with a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("--retrieved-at is not a valid date-time") from error
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def read_text(path, flag):
    if path.suffix.lower() not in (".md", ".txt"):
        raise ValueError(f"{flag} must be a .md or .txt file")
    data = path.read_bytes()
    if not data or not data.decode("utf-8-sig").strip():
        raise ValueError(f"{flag} must contain UTF-8 text")
    return data


def outside_git(root):
    resolved = root.resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise ValueError("The output root must be outside a Git checkout")


def prepare(args):
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 or newer is required")
    manuscript = read_text(args.manuscript, "--manuscript")
    guideline = read_text(args.guideline_text, "--guideline-text")
    if any(not model.strip() for model in args.model):
        raise ValueError("--model must be nonempty when supplied")
    skill = SKILL_FILE.read_bytes()
    retrieved_at = timestamp(args.retrieved_at)
    url = urlsplit(args.guideline_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("--guideline-url must be a valid HTTPS URL without credentials")
    root = args.output_root.expanduser()
    outside_git(root)
    slug = re.sub(r"[^a-z0-9]+", "-", args.manuscript.stem.lower()).strip("-")[:64] or "manuscript"
    paper_root = root / slug
    outside_git(paper_root)
    paper_root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        run = paper_root / f"run-{number}"
        try:
            run.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available run number")
    source_hash = digest(guideline)
    source_id = f"src_{source_hash[:16]}"
    source_path = f"sources/{source_hash}.txt"
    (run / "sources").mkdir()
    (run / f"manuscript{args.manuscript.suffix.lower()}").write_bytes(manuscript)
    (run / source_path).write_bytes(guideline)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host": args.host,
        "models": args.model,
        "skill_sha256": digest(skill),
        "prompt_hashes": {"SKILL.md": digest(skill)},
        "config_hash": None,
        "data_freeze": None,
        "cross_host": False,
        "manuscript": {"path": f"manuscript{args.manuscript.suffix.lower()}", "sha256": digest(manuscript)},
        "sources": {source_id: {"url": args.guideline_url, "retrieved_at": retrieved_at, "sha256": source_hash, "path": source_path}},
    }
    (run / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript", type=Path, required=True)
    parser.add_argument("--guideline-text", type=Path, required=True)
    parser.add_argument("--guideline-url", required=True)
    parser.add_argument("--retrieved-at", required=True, help="ISO 8601 time with timezone")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit")
    parser.add_argument("--host", choices=("codex", "claude-code", "manual"), default="manual")
    parser.add_argument("--model", action="append", default=[], help="Model actually used; repeat if needed")
    args = parser.parse_args()
    try:
        print(prepare(args))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
