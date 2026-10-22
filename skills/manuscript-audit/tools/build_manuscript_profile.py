#!/usr/bin/env python3
"""Record reviewable manuscript structure from a prepared private run."""

import argparse
import json
import re
from pathlib import Path

from check_deterministic import FENCE, HEADING, WORD, heading_spans
from list_dois import collect, read_corpus


REFERENCE_TITLES = {"references", "bibliography"}
PDF_PAGE = re.compile(r"^\[Page ([1-9][0-9]*)\]$")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
TABLE_CAPTION = re.compile(r"^\s*Table\s+[1-9][0-9]*\s*[:.\-]", re.IGNORECASE)
TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")


def body_start(lines):
    if not lines or lines[0].strip() != "---":
        return 0, []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            return index + 1, []
    return len(lines), ["Opening YAML metadata has no closing delimiter; Markdown structure was not inferred"]


def markdown_structure(text):
    lines, parsed = heading_spans(text)
    start, warnings = body_start(lines)
    headings = []
    for position, level, _ in parsed:
        if position < start:
            continue
        match = HEADING.fullmatch(lines[position].strip())
        headings.append((position, level, match.group(2).strip()))
    sections = []
    for position, level, title in headings:
        end = next((later for later, depth, _ in headings
                    if later > position and depth <= level), len(lines))
        sections.append({"title": title, "level": level, "heading_line_id": f"L{position + 1}",
                         "end_line_id": f"L{end}"})
    refs = [item for item in sections if item["title"].casefold() in REFERENCE_TITLES]
    if len(refs) == 1:
        reference_section = {"status": "located", "heading_line_id": refs[0]["heading_line_id"],
                             "end_line_id": refs[0]["end_line_id"]}
    elif refs:
        reference_section = {"status": "ambiguous", "heading_line_ids": [item["heading_line_id"] for item in refs]}
    else:
        reference_section = {"status": "not_located"}
    figure_lines = []
    table_lines = []
    fence_char = None
    fence_size = 0
    for index, line in enumerate(lines[start:], start=start + 1):
        if fence_char:
            candidate = line.strip()
            if candidate and set(candidate) == {fence_char} and len(candidate) >= fence_size:
                fence_char = None
            continue
        fence = FENCE.match(line)
        if fence:
            fence_char = fence.group(1)[0]
            fence_size = len(fence.group(1))
            continue
        if IMAGE.search(line):
            figure_lines.append(f"L{index}")
        if TABLE_CAPTION.match(line) or TABLE_DIVIDER.match(line):
            table_lines.append(f"L{index}")
    if not sections:
        warnings.append("No ATX Markdown headings were located outside metadata and fenced code")
    return {
        "status": "atx_markdown_observed" if sections else "structure_unresolved",
        "sections": sections,
        "reference_section": reference_section,
        "figure_marker_lines": figure_lines,
        "table_marker_lines": table_lines,
        "rough_word_count": len(WORD.findall("\n".join(lines[start:]))),
        "warnings": warnings + [
            "Only ATX Markdown headings outside fenced code were mapped; rendered headings and bibliography entries need visual review",
            "Figure and table lines are literal markers, not verified object counts",
            "Rough words include Markdown syntax and citation keys; use source-backed rule-specific checks for limits",
        ],
    }


def plain_text_structure(text, extraction):
    pages = []
    if extraction and extraction.get("format") == "pdf":
        for index, line in enumerate(text.splitlines(), start=1):
            match = PDF_PAGE.fullmatch(line)
            if match:
                pages.append({"page": int(match.group(1)), "line_id": f"L{index}"})
    return {
        "status": "structure_unresolved",
        "sections": [],
        "reference_section": {"status": "unresolved"},
        "page_markers": pages,
        "rough_word_count": len(WORD.findall(text)),
        "warnings": [
            "Plain-text extraction does not establish section or bibliography boundaries",
            "Rough words include page markers and extraction artifacts; no journal word-limit verdict is supported",
        ],
    }


def build(run):
    run = run.expanduser().resolve()
    manifest, files = read_corpus(run)
    manuscript_path = manifest["manuscript"]["path"]
    text = next(content for path, content in files if path == manuscript_path)
    extraction_info = manifest.get("extraction")
    extraction = (json.loads((run / extraction_info["path"]).read_text(encoding="utf-8"))
                  if extraction_info else None)
    if Path(manuscript_path).suffix.lower() == ".md":
        structure = markdown_structure(text)
    else:
        structure = plain_text_structure(text, extraction)
    identifiers = collect(run)
    return {
        "schema_version": 1,
        "manuscript_path": manuscript_path,
        "submitted_filename": manifest["manuscript"].get("submitted_filename"),
        "manuscript_sha256": manifest["manuscript"]["sha256"],
        "corpus_sha256": identifiers["corpus_sha256"],
        "extraction_format": extraction_info["format"] if extraction_info else "supplied_text",
        "extraction_record_sha256": extraction_info["sha256"] if extraction_info else None,
        "structure": structure,
        "doi_mentions": identifiers["dois"],
        "doi_note": "DOI mentions are not a verified reference list; registry resolution requires a separate authorized check",
        "supplementary_files": [{"path": item["path"],
                                 "submitted_filename": item.get("submitted_filename"),
                                 "sha256": item["sha256"]}
                                for item in manifest.get("supplementary", [])],
        "extraction_warnings": extraction.get("warnings", []) if extraction else [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        profile = build(args.run)
        target = args.run.expanduser().resolve() / "manuscript-profile.json"
        with target.open("x", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(target)
    except (OSError, UnicodeError, ValueError, StopIteration) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
