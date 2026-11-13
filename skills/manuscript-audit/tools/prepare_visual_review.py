#!/usr/bin/env python3
"""Pin private Markdown figure and table leads for human visual review."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from build_manuscript_profile import markdown_structure
from list_dois import read_corpus
from prepare_run import outside_git


LINE_ID = re.compile(r"^L([1-9]\d*)$")
FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def private_file(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Visual review paths must be absolute")
    path = path.resolve()
    outside_git(path)
    if not path.is_file():
        raise ValueError("Missing private visual review input")
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def line_number(value):
    match = LINE_ID.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Visual item needs a physical line ID")
    return int(match.group(1))


def exact_quote(value, text):
    if (not isinstance(value, str) or not 8 <= len(value) <= 800
            or "\n" in value or "\r" in value or value.strip() != value or text.count(value) != 1):
        raise ValueError("Visual item quote must occur exactly once in its selected line or span")


def build_packet(run_directory, recipe_file):
    run = Path(run_directory)
    if not run.is_absolute():
        raise ValueError("Run path must be absolute")
    run = run.resolve()
    outside_git(run)
    manifest, files = read_corpus(run)
    recipe_file = private_file(recipe_file)
    if recipe_file.stat().st_size > 65_536:
        raise ValueError("Visual review recipe exceeds 64 KB")
    recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
    if (not isinstance(recipe, dict) or set(recipe) != {"schema_version", "items"}
            or recipe["schema_version"] != 1 or not isinstance(recipe["items"], list)
            or not 1 <= len(recipe["items"]) <= 100):
        raise ValueError("Invalid visual review recipe")
    corpus = dict(files)
    structured = {}
    pinned, seen_ids = [], set()
    for item in recipe["items"]:
        if (not isinstance(item, dict) or not isinstance(item.get("item_id"), str)
                or not item["item_id"].strip() or item["item_id"] in seen_ids
                or item.get("kind") not in ("figure", "table")
                or not isinstance(item.get("path"), str) or item["path"] not in corpus
                or Path(item["path"]).suffix.lower() != ".md"):
            raise ValueError("Visual item ID, kind, and supplied Markdown path must be valid")
        seen_ids.add(item["item_id"])
        text = corpus[item["path"]]
        lines = text.splitlines()
        structure = structured.setdefault(item["path"], markdown_structure(text))
        if item["kind"] == "figure":
            if set(item) != {"item_id", "kind", "path", "line_id", "quote", "asset_file"}:
                raise ValueError("Figure recipe has missing or unknown fields")
            number = line_number(item["line_id"])
            if number > len(lines) or item["line_id"] not in structure["figure_marker_lines"]:
                raise ValueError("Figure line is not an observed Markdown image marker")
            exact_quote(item["quote"], lines[number - 1])
            asset = private_file(item["asset_file"])
            if asset.suffix.lower() not in FIGURE_SUFFIXES or asset.stat().st_size > 50_000_000:
                raise ValueError("Figure asset must be a private PNG or JPEG under 50 MB")
            with asset.open("rb") as handle:
                header = handle.read(8)
            signature_matches = (header.startswith(b"\x89PNG\r\n\x1a\n") if asset.suffix.lower() == ".png"
                                 else header.startswith(b"\xff\xd8\xff"))
            if not signature_matches:
                raise ValueError("Figure asset does not have a supported image signature")
            pinned.append({"item_id": item["item_id"], "kind": "figure", "path": item["path"],
                           "line_id": item["line_id"], "quote": item["quote"],
                           "asset_file": str(asset), "asset_sha256": digest(asset)})
        else:
            if set(item) != {"item_id", "kind", "path", "start_line", "end_line", "quote"}:
                raise ValueError("Table recipe has missing or unknown fields")
            start, end = line_number(item["start_line"]), line_number(item["end_line"])
            if (end < start or end > min(len(lines), start + 39)
                    or not any(f"L{number}" in structure["table_marker_lines"]
                               for number in range(start, end + 1))):
                raise ValueError("Table span must contain an observed marker and at most 40 lines")
            span = "\n".join(lines[start - 1:end])
            exact_quote(item["quote"], span)
            pinned.append({"item_id": item["item_id"], "kind": "table", "path": item["path"],
                           "start_line": item["start_line"], "end_line": item["end_line"],
                           "quote": item["quote"],
                           "span_sha256": hashlib.sha256(span.encode("utf-8")).hexdigest()})
    return {"schema_version": 1, "status": "pending_human_visual_review",
            "run_directory": str(run), "run_manifest_sha256": digest(run / "run-manifest.json"),
            "recipe_file": str(recipe_file), "recipe_sha256": digest(recipe_file),
            "items": pinned,
            "note": "Markers, selected text, and asset hashes only. Inspect the rendered manuscript and images for axes, uncertainty displays, legends, captions, and numeric consistency; no visual defect is confirmed."}


def prepare(run_directory, recipe_file, output_file):
    packet = build_packet(run_directory, recipe_file)
    output = Path(output_file)
    if not output.is_absolute():
        raise ValueError("Visual packet output must be absolute")
    output = output.resolve()
    outside_git(output)
    if not output.parent.is_dir():
        raise ValueError("Visual packet output parent is missing")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def check(packet_file):
    packet_file = private_file(packet_file)
    if packet_file.stat().st_size > 2_000_000:
        raise ValueError("Visual review packet exceeds 2 MB")
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    if not isinstance(packet, dict) or set(packet) != {
            "schema_version", "status", "run_directory", "run_manifest_sha256", "recipe_file",
            "recipe_sha256", "items", "note"}:
        raise ValueError("Invalid visual review packet")
    if packet != build_packet(packet["run_directory"], packet["recipe_file"]):
        raise ValueError("Visual review packet or its private inputs changed")
    return {"status": "private_inputs_current", "packet_sha256": digest(packet_file),
            "items": len(packet["items"]), "note": "No visual judgment was made"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("run_directory", type=Path)
    prepare_parser.add_argument("recipe_file", type=Path)
    prepare_parser.add_argument("--output", required=True, type=Path)
    check_parser = subcommands.add_parser("check")
    check_parser.add_argument("packet_file", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            print(prepare(args.run_directory, args.recipe_file, args.output))
        else:
            print(json.dumps(check(args.packet_file), indent=2))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
