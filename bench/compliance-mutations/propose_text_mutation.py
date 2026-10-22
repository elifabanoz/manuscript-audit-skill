#!/usr/bin/env python3
"""Prepare one private, unreviewed semantic or format mutation proposal."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "skills" / "manuscript-audit" / "tools"
sys.path.insert(0, str(TOOLS))

from check_deterministic import FENCE, check, heading_spans  # noqa: E402
from score import outside_git, sha256  # noqa: E402


def content_line(lines, target, date_metadata=False):
    if lines and lines[0].strip() == "---":
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is None or target <= closing:
            if not date_metadata or not re.fullmatch(r"\s*date:\s*[^\r\n]+", lines[target].rstrip("\r\n")):
                raise ValueError("The edit cannot target manuscript metadata")
    fence_char = None
    fence_size = 0
    for index, line in enumerate(lines[:target + 1]):
        if fence_char:
            candidate = line.strip()
            if index == target:
                raise ValueError("The edit cannot target a fenced code block")
            if candidate and set(candidate) == {fence_char} and len(candidate) >= fence_size:
                fence_char = None
            continue
        match = FENCE.match(line)
        if match:
            if index == target:
                raise ValueError("The edit cannot target a fenced code block")
            fence_char = match.group(1)[0]
            fence_size = len(match.group(1))


def propose(run, recipe_path, output_root):
    run = run.expanduser().resolve()
    recipe_path = recipe_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    for path in (run, recipe_path, output_root):
        outside_git(path)
    recipe_bytes = recipe_path.read_bytes()
    if len(recipe_bytes) > 16_384:
        raise ValueError("The private recipe exceeds 16 KB")
    recipe = json.loads(recipe_bytes)
    expected_keys = {"schema_version", "article_type", "rule_id", "mutation_class",
                     "original", "replacement"}
    if not isinstance(recipe, dict) or set(recipe) != expected_keys or recipe["schema_version"] != 1:
        raise ValueError("The private mutation recipe has an unexpected format")
    original, replacement = recipe["original"], recipe["replacement"]
    if (not isinstance(original, str) or not 8 <= len(original) <= 2_000
            or not isinstance(replacement, str) or len(replacement) > 2_000
            or original == replacement or any(char in original + replacement for char in "\r\n")):
        raise ValueError("The recipe needs one changed, single-line manuscript span")
    if (not isinstance(recipe["mutation_class"], str)
            or recipe["mutation_class"] not in {"semantic", "numerical_format"}):
        raise ValueError("Only semantic and numerical_format proposals are supported")
    if not isinstance(recipe["article_type"], str) or not isinstance(recipe["rule_id"], str):
        raise ValueError("The recipe needs an article type and rule ID")

    report = check(run, recipe["article_type"], write_output=False)
    manifest_path = run / "run-manifest.json"
    profile_path = run / "journal-profile.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if recipe["article_type"] not in profile["article_types"]:
        raise ValueError("The article type is absent from the journal profile")
    rules = [rule for rule in profile["rules"] if rule["rule_id"] == recipe["rule_id"]]
    if (len(rules) != 1 or rules[0].get("status", "verified") != "verified"
            or recipe["article_type"] not in rules[0].get("applies_to", [recipe["article_type"]])):
        raise ValueError("The target rule is not verified and applicable")
    if recipe["mutation_class"] == "semantic" and rules[0]["kind"] != "semantic":
        raise ValueError("A semantic proposal needs a semantic target rule")
    failed = [item["rule_id"] for item in report["checks"] if item["status"] != "met"
              and next((rule for rule in profile["rules"] if rule["rule_id"] == item["rule_id"]), {}).get(
                  "status", "verified") == "verified"]
    if failed:
        raise ValueError("Control has unmet or unresolved deterministic rules: " + ", ".join(sorted(failed)))

    manuscript = run / manifest["manuscript"]["path"]
    source = manuscript.read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["manuscript"]["sha256"]:
        raise ValueError("Control manuscript differs from the run manifest")
    if source.startswith(b"\xef\xbb\xbf"):
        raise ValueError("A UTF-8 BOM would make the proposed edit ambiguous")
    text = source.decode("utf-8")
    if text.count(original) != 1:
        raise ValueError("The original span must occur exactly once in the control manuscript")
    target = text[:text.index(original)].count("\n")
    lines = text.splitlines(keepends=True)
    if target >= len(lines) or target in {index for index, _, _ in heading_spans(text)[1]}:
        raise ValueError("The edit cannot target a Markdown heading")
    date_metadata = (recipe["mutation_class"] == "numerical_format"
                     and recipe["rule_id"] == "metadata_date_format")
    if date_metadata:
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        line = lines[target].rstrip("\r\n")
        match = re.fullmatch(r"\s*date:\s*(?P<value>[^\r\n]+)", line)
        if (not lines or lines[0].strip() != "---" or closing is None or target >= closing
                or match is None or match.group("value").strip() != original or not replacement):
            raise ValueError("A date-format proposal must replace only the metadata date value")
    content_line(lines, target, date_metadata)
    changed = text.replace(original, replacement, 1)
    if (len(changed.splitlines(keepends=True)) != len(lines)
            or heading_spans(changed)[1] != heading_spans(text)[1]):
        raise ValueError("The edit must preserve line and heading structure")
    mutation = changed.encode("utf-8")

    output_root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = output_root / f"text-proposal-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available proposal directory")
    name = "mutation.md"
    (directory / name).write_bytes(mutation)
    record = {"schema_version": 1, "status": "unreviewed_proposal",
              "control_run": str(run), "article_type": recipe["article_type"],
              "control_manifest_sha256": sha256(manifest_path),
              "profile_sha256": sha256(profile_path),
              "control_manuscript_sha256": manifest["manuscript"]["sha256"],
              "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
              "rule_id": recipe["rule_id"], "rule_source_ref": rules[0]["source_ref"],
              "mutation_class_proposed": recipe["mutation_class"],
              "edit_line_id": f"L{target + 1}",
              "original_span_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
              "replacement_span_sha256": hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
              "file": name, "manuscript_sha256": hashlib.sha256(mutation).hexdigest(),
              "note": "Human reviewers must verify baseline compliance, the intended violation, the class, and any collateral effects before scoring"}
    path = directory / "proposals.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Private control run with source-backed profile")
    parser.add_argument("--recipe", type=Path, required=True, help="Private single-span mutation recipe JSON")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(propose(args.run, args.recipe, args.output_root))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
