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
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def data_freeze(value):
    if value is None:
        return None
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError("--data-freeze must be a YYYY-MM-DD date")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("--data-freeze is not a valid date") from error
    return value


def read_text(path, flag, suffixes=(".md", ".txt")):
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"{flag} must be a {' or '.join(suffixes)} file")
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
    extraction_path = getattr(args, "extraction_record", None)
    extraction = None
    extraction_bytes = None
    if extraction_path:
        extraction_bytes = extraction_path.read_bytes()
        extraction = json.loads(extraction_bytes)
        if not isinstance(extraction, dict) or extraction.get("schema_version") != 1 or extraction.get("format") not in ("docx", "pdf", "tex"):
            raise ValueError("--extraction-record has an invalid format")
        expected_key = "markdown_sha256" if extraction["format"] in ("docx", "tex") else "text_sha256"
        expected_name = "manuscript.md" if extraction["format"] in ("docx", "tex") else "manuscript.txt"
        if args.manuscript.resolve() != (extraction_path.parent / expected_name).resolve():
            raise ValueError("--manuscript must be the extracted file beside --extraction-record")
        if extraction.get(expected_key) != digest(manuscript):
            raise ValueError("--extraction-record text hash does not match")
        if not isinstance(extraction.get("source_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", extraction["source_sha256"]):
            raise ValueError("--extraction-record has no valid original file hash")
    source_records = getattr(args, "source_record", None) or []
    if isinstance(source_records, Path):
        source_records = [source_records]
    sources = []
    if source_records:
        if args.guideline_text or args.guideline_url or args.retrieved_at:
            raise ValueError("--source-record cannot be combined with manual guideline fields")
        for source_record in source_records:
            source = json.loads(source_record.read_text(encoding="utf-8"))
            if not isinstance(source, dict) or source.get("schema_version") != 1 or source.get("text_file") != "guideline.txt":
                raise ValueError("--source-record has an invalid format")
            guideline_path = source_record.parent / "guideline.txt"
            if guideline_path.is_symlink():
                raise ValueError("--source-record cannot reference a symbolic link")
            guideline = read_text(guideline_path, "--source-record")
            if source.get("sha256") != digest(guideline):
                raise ValueError("--source-record text hash does not match")
            sources.append((source.get("url"), source.get("retrieved_at"), guideline))
    else:
        if not (args.guideline_text and args.guideline_url and args.retrieved_at):
            raise ValueError("Supply --source-record or all manual guideline fields")
        sources.append((args.guideline_url, args.retrieved_at,
                        read_text(args.guideline_text, "--guideline-text")))
    supplementary = [(path.suffix.lower(), read_text(path, "--supplementary", (".md", ".txt", ".bib")))
                     for path in args.supplementary]
    if any(not model.strip() for model in args.model):
        raise ValueError("--model must be nonempty when supplied")
    skill = SKILL_FILE.read_bytes()
    validated_sources = []
    seen_urls = set()
    for guideline_url, retrieved_at, guideline in sources:
        if not isinstance(guideline_url, str) or not isinstance(retrieved_at, str):
            raise ValueError("A guideline source is missing its URL or retrieval time")
        url = urlsplit(guideline_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("--guideline-url must be a valid HTTPS URL without credentials")
        if guideline_url in seen_urls:
            raise ValueError("Guideline source URLs must be unique within a run")
        seen_urls.add(guideline_url)
        source_hash = digest(guideline)
        source_id = f"src_{digest((guideline_url + chr(0) + source_hash).encode('utf-8'))[:16]}"
        validated_sources.append((source_id, guideline_url, timestamp(retrieved_at), source_hash, guideline))
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
    (run / "sources").mkdir()
    (run / f"manuscript{args.manuscript.suffix.lower()}").write_bytes(manuscript)
    if extraction_bytes:
        (run / "extraction.json").write_bytes(extraction_bytes)
    source_manifest = {}
    for source_id, guideline_url, retrieved_at, source_hash, guideline in validated_sources:
        source_path = f"sources/{source_id}.txt"
        (run / source_path).write_bytes(guideline)
        source_manifest[source_id] = {"url": guideline_url, "retrieved_at": retrieved_at,
                                      "sha256": source_hash, "path": source_path}
    if supplementary:
        (run / "supplementary").mkdir()
    supplement_records = []
    for index, (suffix, content) in enumerate(supplementary, start=1):
        relative = f"supplementary/supp-{index}{suffix}"
        (run / relative).write_bytes(content)
        supplement_records.append({"path": relative, "sha256": digest(content)})
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host": args.host,
        "models": args.model,
        "skill_sha256": digest(skill),
        "prompt_hashes": {"SKILL.md": digest(skill)},
        "config_hash": None,
        "data_freeze": data_freeze(getattr(args, "data_freeze", None)),
        "cross_host": False,
        "manuscript": {"path": f"manuscript{args.manuscript.suffix.lower()}", "sha256": digest(manuscript)},
        "supplementary": supplement_records,
        "sources": source_manifest,
    }
    if extraction:
        manifest["extraction"] = {"format": extraction["format"], "source_sha256": extraction["source_sha256"],
                                  "path": "extraction.json", "sha256": digest(extraction_bytes)}
    (run / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript", type=Path, required=True)
    parser.add_argument("--extraction-record", type=Path, help="Private extraction.json written by a converter")
    parser.add_argument("--guideline-text", type=Path)
    parser.add_argument("--supplementary", type=Path, action="append", default=[], help="Additional UTF-8 .md, .txt, or .bib file; repeat if needed")
    parser.add_argument("--guideline-url")
    parser.add_argument("--retrieved-at", help="ISO 8601 time with timezone")
    parser.add_argument("--source-record", type=Path, action="append", help="Private source.json written by fetch_guidelines.py; repeat for each guideline page")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit")
    parser.add_argument("--host", choices=("codex", "claude-code", "manual"), default="manual")
    parser.add_argument("--model", action="append", default=[], help="Model actually used; repeat if needed")
    parser.add_argument("--data-freeze", help="Fixed YYYY-MM-DD data date for a benchmark run")
    args = parser.parse_args()
    try:
        print(prepare(args))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
