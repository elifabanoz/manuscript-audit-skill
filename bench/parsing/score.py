#!/usr/bin/env python3
"""Score private, human-labeled Markdown extraction results."""

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata


HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
REFERENCE_TITLES = {"references", "bibliography"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_file(path):
    if not path.is_absolute():
        raise ValueError("Input and output paths must be absolute")
    resolved = path.resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("Parsing benchmark inputs and outputs must stay outside Git")
    return resolved


def read_json(path):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError(f"Missing or oversized JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value):
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def clean_heading(value):
    value = re.sub(r"\s*\{#[^}]+\}\s*$", "", value)
    return value.strip(" `*_\t")


def parse_markdown(text, format_name="tex"):
    lines = text.splitlines()
    headings = []
    fence = None
    frontmatter = bool(lines and lines[0].strip() == "---")
    visible = []
    for index, line in enumerate(lines):
        if frontmatter:
            if index and line.strip() == "---":
                frontmatter = False
            visible.append("")
            continue
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            visible.append("")
            continue
        visible.append("" if fence else line)

    for index, line in enumerate(visible):
        match = HEADING.match(line)
        if match:
            headings.append((index, len(match.group(1)), clean_heading(match.group(2))))
        elif index and SETEXT.match(line) and visible[index - 1].strip():
            previous = visible[index - 1]
            if not HEADING.match(previous):
                headings.append((index - 1, 1 if line.lstrip().startswith("=") else 2,
                                 clean_heading(previous)))

    references = []
    for position, (start, level, title) in enumerate(headings):
        if normalize(title) not in REFERENCE_TITLES:
            continue
        end = len(visible)
        for next_start, next_level, _ in headings[position + 1:]:
            if next_level <= level:
                end = next_start
                break
        entry = []

        def flush():
            if entry:
                references.append(" ".join(entry))
                entry.clear()

        first_entry_line = start + 1
        if first_entry_line < len(visible) and SETEXT.match(visible[first_entry_line]):
            first_entry_line += 1
        for line in visible[first_entry_line:end]:
            stripped = line.strip()
            if not stripped:
                flush()
                continue
            if format_name == "docx":
                flush()
                entry.append(stripped)
                continue
            bullet = LIST_ITEM.match(line)
            if bullet:
                flush()
                entry.append(bullet.group(1).strip())
            else:
                entry.append(stripped)
        flush()
        break
    return [title for _, _, title in headings], references


def labels(value, field):
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError(f"{field} must be a bounded list")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 5000
           for item in value):
        raise ValueError(f"{field} contains an invalid label")
    return [normalize(item) for item in value]


def measure(expected, predicted):
    gold = Counter(expected)
    actual = Counter(normalize(item) for item in predicted)
    tp = sum((gold & actual).values())
    fp = sum((actual - gold).values())
    fn = sum((gold - actual).values())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = None
    if precision is not None and recall is not None:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


def score_case(case):
    if not isinstance(case, dict) or set(case) != {
            "case_id", "format", "source_file", "source_sha256", "extraction_record",
            "extracted_sha256", "sections", "references"}:
        raise ValueError("Each case needs exact pinned source, extraction, and label fields")
    if not isinstance(case["case_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case["case_id"]):
        raise ValueError("Invalid case_id")
    if case["format"] not in ("docx", "tex"):
        raise ValueError("Only structurally converted DOCX and LaTeX are supported")
    if any(not isinstance(case[key], str) or not SHA256.fullmatch(case[key])
           for key in ("source_sha256", "extracted_sha256")):
        raise ValueError("Invalid case hash")
    if any(not isinstance(case[key], str) for key in ("source_file", "extraction_record")):
        raise ValueError("Case file paths must be strings")
    source = private_file(Path(case["source_file"]))
    record_path = private_file(Path(case["extraction_record"]))
    if not source.is_file() or digest(source) != case["source_sha256"]:
        raise ValueError(f"{case['case_id']}: source file differs from its pin")
    record = read_json(record_path)
    if not isinstance(record, dict):
        raise ValueError(f"{case['case_id']}: extraction record must be an object")
    if record.get("format") != case["format"] or record.get("source_sha256") != case["source_sha256"]:
        raise ValueError(f"{case['case_id']}: extraction record differs from the source")
    if record.get("markdown_sha256") != case["extracted_sha256"]:
        raise ValueError(f"{case['case_id']}: extraction record differs from the gold pin")
    manuscript = private_file(record_path.parent / "manuscript.md")
    if not manuscript.is_file() or digest(manuscript) != case["extracted_sha256"]:
        raise ValueError(f"{case['case_id']}: extracted Markdown differs from its pin")
    expected_sections = labels(case["sections"], "sections")
    expected_references = labels(case["references"], "references")
    found_sections, found_references = parse_markdown(
        manuscript.read_text(encoding="utf-8"), case["format"])
    return {"case_id": case["case_id"], "format": case["format"],
            "source_sha256": case["source_sha256"],
            "extracted_sha256": case["extracted_sha256"],
            "extraction_record_sha256": digest(record_path),
            "sections": measure(expected_sections, found_sections),
            "references": measure(expected_references, found_references)}


def totals(cases, field):
    counts = {key: sum(case[field][key] for case in cases) for key in ("tp", "fp", "fn")}
    return measure(["match"] * counts["tp"] + ["missing"] * counts["fn"],
                   ["match"] * counts["tp"] + ["extra"] * counts["fp"])


def score(gold_path, output_path):
    gold_path = private_file(gold_path)
    output_path = private_file(output_path)
    if output_path.exists():
        raise ValueError("Output already exists")
    gold = read_json(gold_path)
    if not isinstance(gold, dict) or set(gold) != {"schema_version", "data_freeze", "cases"}:
        raise ValueError("Gold manifest has invalid fields")
    if gold["schema_version"] != 1 or not isinstance(gold["data_freeze"], str):
        raise ValueError("Unsupported gold manifest version or freeze")
    try:
        if date.fromisoformat(gold["data_freeze"]).isoformat() != gold["data_freeze"]:
            raise ValueError()
    except ValueError:
        raise ValueError("data_freeze must be a real YYYY-MM-DD date") from None
    if not isinstance(gold["cases"], list) or not gold["cases"] or len(gold["cases"]) > 100:
        raise ValueError("Gold manifest needs 1-100 cases")
    cases = [score_case(case) for case in gold["cases"]]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case_id")
    results = {"schema_version": 1, "data_freeze": gold["data_freeze"],
               "gold_sha256": digest(gold_path), "cases": cases,
               "aggregate": {field: totals(cases, field) for field in ("sections", "references")},
               "by_format": {format_name: {
                   field: totals([case for case in cases if case["format"] == format_name], field)
                   for field in ("sections", "references")}
                   for format_name in sorted({case["format"] for case in cases})}}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold", type=Path, help="Private gold manifest outside Git")
    parser.add_argument("--output", required=True, type=Path, help="New private result path")
    args = parser.parse_args()
    try:
        print(score(args.gold, args.output))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
