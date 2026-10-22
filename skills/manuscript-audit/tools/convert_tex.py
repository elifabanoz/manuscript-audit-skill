#!/usr/bin/env python3
"""Convert a standalone local LaTeX manuscript to private reviewable Markdown."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from prepare_run import outside_git


MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_MARKDOWN_BYTES = 20 * 1024 * 1024
EXTERNAL_COMMAND = re.compile(r"\\(?:input|include|includeonly|import|subimport|subfile|loadglsentries)\b")


def uncomment(source):
    lines = []
    for line in source.splitlines():
        for index, character in enumerate(line):
            if character == "%":
                preceding = len(line[:index]) - len(line[:index].rstrip("\\"))
                if preceding % 2 == 0:
                    line = line[:index]
                    break
        lines.append(line)
    return "\n".join(lines)


def extract(path, pandoc=None):
    if path.suffix.lower() != ".tex" or not path.is_file():
        raise ValueError("--input must be an existing .tex file")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("LaTeX source is empty or exceeds the size limit")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("LaTeX source must be UTF-8") from error
    active = uncomment(source)
    if EXTERNAL_COMMAND.search(active):
        raise ValueError("LaTeX source references another source file; consolidate and inspect it before conversion")
    executable = pandoc or shutil.which("pandoc")
    if not executable:
        raise ValueError("Pandoc is required for LaTeX conversion")
    try:
        result = subprocess.run(
            [str(executable), "-s", "-f", "latex", "-t", "markdown", "--wrap=none"],
            input=source, text=True, encoding="utf-8", errors="strict",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Pandoc conversion timed out") from error
    if result.returncode != 0:
        raise ValueError(f"Pandoc conversion failed: {result.stderr.strip()[:500]}")
    markdown = result.stdout.encode("utf-8")
    if not markdown.strip() or len(markdown) > MAX_MARKDOWN_BYTES:
        raise ValueError("Converted Markdown is empty or exceeds the size limit")
    warnings = ["Compare every section and citation with the rendered LaTeX manuscript before auditing"]
    if re.search(r"\\(?:bibliography|addbibresource)\b", active):
        warnings.append("External bibliography entries were not expanded; inspect references separately")
    if re.search(r"\\includegraphics\b", active):
        warnings.append("Figures were not extracted; inspect them separately")
    if re.search(r"\\(?:newcommand|renewcommand|def|DeclareMathOperator)\b", active):
        warnings.append("Custom macros need manual comparison with the rendered manuscript")
    return raw, markdown, warnings


def convert(input_path, output_root, pandoc=None):
    root = output_root.expanduser()
    outside_git(root)
    original, markdown, warnings = extract(input_path, pandoc=pandoc)
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"conversion-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available conversion number")
    (directory / "manuscript.md").write_bytes(markdown)
    record = {
        "schema_version": 1,
        "format": "tex",
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
        "warnings": warnings,
    }
    output = directory / "extraction.json"
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "extracted")
    args = parser.parse_args()
    try:
        print(convert(args.input, args.output_root))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
