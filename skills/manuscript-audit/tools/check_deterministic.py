#!/usr/bin/env python3
"""Measure selected Markdown limits and headings without audit verdicts."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from prepare_run import outside_git


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
WORD = re.compile(r"[^\W_]+(?:[-'\u2019][^\W_]+)*", re.UNICODE)
VALIDATORS = Path(__file__).resolve().parents[1] / "validators"


def checked_file(run, relative, expected_hash):
    root = run.resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or any(part in ("..", ".") for part in Path(relative).parts):
        raise ValueError("A cached file path is invalid")
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("A cached file is outside the run or missing")
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError(f"SHA-256 mismatch: {relative}")
    return data.decode("utf-8-sig")


def heading_spans(text):
    lines = text.splitlines()
    headings = []
    fence_char = None
    fence_size = 0
    for index, line in enumerate(lines):
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
        match = HEADING.fullmatch(line.strip())
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip().casefold()))
    return lines, headings


def measure_heading_count(text, heading):
    _, headings = heading_spans(text)
    return sum(label == heading.strip().casefold() for _, _, label in headings)


def section_words(lines, start, end):
    body_lines = [line for line in lines[start:end] if not HEADING.fullmatch(line.strip())]
    if any(re.search(r"```|`[^`]+`|!?\[[^]]+\]\([^)]+\)|<!--|^\s*\|", line) for line in body_lines):
        return None, "The section contains Markdown constructs that need a checked text extraction"
    return len(WORD.findall("\n".join(body_lines))), None


def measure_abstract(text):
    lines, headings = heading_spans(text)
    matches = [(position, level) for position, level, label in headings if label == "abstract"]
    if len(matches) != 1:
        return None, "Expected exactly one Markdown Abstract heading"
    start, level = matches[0]
    following = [position for position, depth, _ in headings if position > start and depth <= level]
    if not following:
        return None, "Abstract has no following heading at the same or higher level"
    return section_words(lines, start + 1, following[0])


def measure_main(text, start_label, end_label):
    if not start_label or not end_label:
        return None, "Specify both --main-start and --main-end heading names"
    lines, headings = heading_spans(text)
    starts = [position for position, _, label in headings if label == start_label.casefold()]
    ends = [position for position, _, label in headings if label == end_label.casefold()]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        return None, "Main text boundaries must be unique Markdown headings in order"
    return section_words(lines, starts[0] + 1, ends[0])


def measure_keywords(text):
    lines, headings = heading_spans(text)
    matches = [(position, level) for position, level, label in headings if label in ("keywords", "key words")]
    if len(matches) != 1:
        return None, "Expected exactly one Markdown Keywords heading"
    start, level = matches[0]
    following = [position for position, depth, _ in headings if position > start and depth <= level]
    end = following[0] if following else len(lines)
    body = [line.strip() for line in lines[start + 1:end] if line.strip()]
    if len(body) != 1 or any(mark in body[0] for mark in ("`", "[", "]", "|", "*", "<", ">")):
        return None, "Keywords must be one plain-text line"
    value = re.sub(r"^(?:keywords|key words)\s*:\s*", "", body[0], flags=re.IGNORECASE)
    if "," in value and ";" in value:
        return None, "Mixed keyword separators need manual review"
    separator = ";" if ";" in value else ","
    parts = [part.strip() for part in value.split(separator)]
    if any(not part for part in parts):
        return None, "Keywords contain an empty entry"
    return len(parts), None


def compare(actual, op, limit):
    return {
        "<": actual < limit,
        "<=": actual <= limit,
        "=": actual == limit,
        ">=": actual >= limit,
        ">": actual > limit,
    }[op]


def check(run, article_type, main_start=None, main_end=None):
    run = run.resolve()
    outside_git(run)
    manifest_file = run / "run-manifest.json"
    profile_file = run / "journal-profile.json"
    for validator, arguments in (("validate-run-manifest.cjs", [str(run)]), ("validate-journal-profile.cjs", [str(profile_file)])):
        result = subprocess.run(["node", str(VALIDATORS / validator), *arguments], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stderr.strip())
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    if article_type not in profile["article_types"]:
        raise ValueError("--article-type is absent from the journal profile")
    if profile["sources"] != manifest["sources"]:
        raise ValueError("Journal profile sources differ from the run manifest")
    text = checked_file(run, manifest["manuscript"]["path"], manifest["manuscript"]["sha256"])
    results = []
    for rule in profile["rules"]:
        if rule["kind"] != "deterministic":
            continue
        item = {"rule_id": rule["rule_id"], "metric": rule["check"]["metric"], "op": rule["check"]["op"], "limit": rule["check"]["value"], "source_ref": rule["source_ref"]}
        if "heading" in rule["check"]:
            item["heading"] = rule["check"]["heading"]
        source = profile["sources"][rule["source_ref"]]
        guideline = checked_file(run, source["path"], source["sha256"])
        if " ".join(rule["quote"].split()) not in " ".join(guideline.split()):
            raise ValueError(f"Rule quote is absent from its source: {rule['rule_id']}")
        if "applies_to" in rule and article_type not in rule["applies_to"]:
            item.update(status="not_applicable")
        elif not manifest["manuscript"]["path"].lower().endswith(".md"):
            item.update(status="unresolved", reason="Markdown headings are required for this metric")
        elif rule["check"]["metric"] == "abstract_words":
            value, reason = measure_abstract(text)
            if reason:
                item.update(status="unresolved", reason=reason)
            else:
                item.update(status="met" if compare(value, rule["check"]["op"], rule["check"]["value"]) else "triggered", measured_value=value)
        elif rule["check"]["metric"] == "main_text_words":
            value, reason = measure_main(text, main_start, main_end)
            if reason:
                item.update(status="unresolved", reason=reason)
            else:
                item.update(status="met" if compare(value, rule["check"]["op"], rule["check"]["value"]) else "triggered", measured_value=value)
        elif rule["check"]["metric"] == "keyword_count":
            value, reason = measure_keywords(text)
            if reason:
                item.update(status="unresolved", reason=reason)
            else:
                item.update(status="met" if compare(value, rule["check"]["op"], rule["check"]["value"]) else "triggered", measured_value=value)
        elif rule["check"]["metric"] == "heading_count":
            value = measure_heading_count(text, rule["check"]["heading"])
            item.update(status="met" if compare(value, rule["check"]["op"], rule["check"]["value"]) else "triggered", measured_value=value)
        else:
            item.update(status="unresolved", reason="Metric is not implemented")
        results.append(item)
    output = {
        "article_type": article_type,
        "manuscript_sha256": manifest["manuscript"]["sha256"],
        "method": "Markdown heading ranges; Unicode word tokens; headings excluded",
        "checks": results,
    }
    (run / "deterministic-checks.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--article-type", required=True)
    parser.add_argument("--main-start", help="First main-text Markdown heading")
    parser.add_argument("--main-end", help="Heading immediately after main text")
    args = parser.parse_args()
    try:
        output = check(args.run, args.article_type, args.main_start, args.main_end)
        print(json.dumps({"checks": len(output["checks"]), "output": str(args.run / "deterministic-checks.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
