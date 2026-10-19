#!/usr/bin/env python3
"""Extract text from a local PDF into a private, reviewable file."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from prepare_run import outside_git


MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PAGES = 500
MAX_TEXT_BYTES = 10 * 1024 * 1024


def extract(path, reader_factory=None):
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError("--input must be an existing .pdf file")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the size limit")
    if reader_factory is None:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF extraction requires pypdf; install requirements-pdf.txt") from None
        reader_factory = PdfReader
    try:
        reader = reader_factory(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs need a separate authorized conversion")
        page_count = len(reader.pages)
        if not 1 <= page_count <= MAX_PAGES:
            raise ValueError("PDF page count is outside the supported range")
        lines = []
        empty_pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
            if not text.strip():
                empty_pages.append(index)
            lines.append(f"[Page {index}]")
            lines.extend(line.rstrip() for line in text.splitlines() if line.strip())
        if len(empty_pages) == page_count:
            raise ValueError("No text could be extracted; scanned PDFs require OCR and visual review")
    except ValueError:
        raise
    except Exception:
        raise ValueError("PDF could not be read or extracted") from None
    output = ("\n".join(lines) + "\n").encode("utf-8")
    if len(output) > MAX_TEXT_BYTES:
        raise ValueError("Extracted PDF text exceeds the size limit")
    warnings = ["Verify reading order, tables, equations, and figures against the rendered PDF"]
    if empty_pages:
        warnings.append(f"No text extracted from page(s): {', '.join(map(str, empty_pages))}")
    return output, page_count, warnings


def convert(input_path, output_root, reader_factory=None):
    root = output_root.expanduser()
    outside_git(root)
    text, page_count, warnings = extract(input_path, reader_factory)
    original_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
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
    (directory / "manuscript.txt").write_bytes(text)
    record = {
        "schema_version": 1,
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_sha256": original_hash,
        "text_sha256": hashlib.sha256(text).hexdigest(),
        "page_count": page_count,
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
