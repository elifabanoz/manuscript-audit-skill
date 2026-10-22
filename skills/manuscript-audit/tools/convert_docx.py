#!/usr/bin/env python3
"""Extract reviewable Markdown from a local DOCX without publishing its contents."""

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from prepare_run import outside_git


WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_STYLES_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
UNSUPPORTED = {"ins", "del", "moveFrom", "moveTo", "altChunk", "txbxContent"}


def paragraph_text(paragraph):
    pieces = []
    for item in paragraph.iter():
        if item.tag == WORD + "t" and item.text:
            pieces.append(item.text)
        elif item.tag == WORD + "tab":
            pieces.append("\t")
        elif item.tag in (WORD + "br", WORD + "cr"):
            pieces.append(" ")
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def heading_level(paragraph):
    properties = paragraph.find(WORD + "pPr")
    style = properties.find(WORD + "pStyle") if properties is not None else None
    style_id = style.get(WORD + "val", "") if style is not None else ""
    match = re.fullmatch(r"Heading([1-6])", style_id, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def visibility_markers(properties):
    if properties is None:
        return set()
    markers = set()
    for name in ("vanish", "webHidden"):
        element = properties.find(WORD + name)
        if element is not None and element.get(WORD + "val", "true").lower() not in ("0", "false", "off"):
            markers.add(name)
    for name in ("sz", "szCs"):
        element = properties.find(WORD + name)
        if element is not None and element.get(WORD + "val") == "0":
            markers.add("zero font size")
    color = properties.find(WORD + "color")
    if color is not None and color.get(WORD + "val", "").lstrip("#").lower() == "ffffff":
        markers.add("white font color")
    return markers


def check_text_visibility(body, styles_root):
    styles = {} if styles_root is None else {
        style.get(WORD + "styleId"): style
        for style in styles_root.iter(WORD + "style") if style.get(WORD + "styleId")
    }
    defaults = None if styles_root is None else styles_root.find(
        WORD + "docDefaults/" + WORD + "rPrDefault/" + WORD + "rPr")

    def style_markers(style_id):
        markers, visited = set(), set()
        while style_id and style_id not in visited:
            visited.add(style_id)
            style = styles.get(style_id)
            if style is None:
                break
            markers.update(visibility_markers(style.find(WORD + "rPr")))
            based_on = style.find(WORD + "basedOn")
            style_id = based_on.get(WORD + "val") if based_on is not None else None
        return markers

    for paragraph in body.iter(WORD + "p"):
        paragraph_properties = paragraph.find(WORD + "pPr")
        paragraph_style = paragraph_properties.find(WORD + "pStyle") if paragraph_properties is not None else None
        paragraph_style_id = paragraph_style.get(WORD + "val") if paragraph_style is not None else None
        paragraph_run_properties = paragraph_properties.find(WORD + "rPr") if paragraph_properties is not None else None
        for run in paragraph.iter(WORD + "r"):
            if not any(item.text and item.text.strip() for item in run.iter(WORD + "t")):
                continue
            properties = run.find(WORD + "rPr")
            character_style = properties.find(WORD + "rStyle") if properties is not None else None
            character_style_id = character_style.get(WORD + "val") if character_style is not None else None
            markers = visibility_markers(defaults)
            markers.update(style_markers(paragraph_style_id))
            markers.update(visibility_markers(paragraph_run_properties))
            markers.update(style_markers(character_style_id))
            markers.update(visibility_markers(properties))
            if markers:
                raise ValueError("DOCX contains text with hidden or low-visibility formatting: " + ", ".join(sorted(markers)))


def render_body(body):
    lines = []
    table_count = 0
    for block in body:
        if block.tag == WORD + "p":
            value = paragraph_text(block)
            if value:
                level = heading_level(block)
                lines.append(("#" * level + " " if level else "") + value)
        elif block.tag == WORD + "tbl":
            table_count += 1
            for row in block.findall(WORD + "tr"):
                cells = []
                for cell in row.findall(WORD + "tc"):
                    parts = [paragraph_text(paragraph) for paragraph in cell.findall(WORD + "p")]
                    cells.append(" ".join(part for part in parts if part))
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines) + "\n", table_count


def extract(path):
    if path.suffix.lower() != ".docx" or not path.is_file():
        raise ValueError("--input must be an existing .docx file")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("DOCX archive exceeds the size limit")
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 5000 or sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("DOCX archive exceeds the uncompressed size limit")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                raise ValueError("DOCX document XML exceeds the size limit")
            xml = archive.read(info)
            names = set(archive.namelist())
            styles_xml = None
            if "word/styles.xml" in names:
                styles_info = archive.getinfo("word/styles.xml")
                if styles_info.file_size > MAX_STYLES_BYTES:
                    raise ValueError("DOCX styles XML exceeds the size limit")
                styles_xml = archive.read(styles_info)
    except (zipfile.BadZipFile, KeyError, RuntimeError):
        raise ValueError("DOCX is unreadable or missing word/document.xml") from None
    if b"<!DOCTYPE" in xml.upper() or (styles_xml and b"<!DOCTYPE" in styles_xml.upper()):
        raise ValueError("DOCX XML contains a document type declaration")
    try:
        root = ElementTree.fromstring(xml)
        styles_root = ElementTree.fromstring(styles_xml) if styles_xml else None
    except ElementTree.ParseError:
        raise ValueError("DOCX document XML is invalid") from None
    present = {item.tag.removeprefix(WORD) for item in root.iter() if item.tag.startswith(WORD)}
    unsafe = sorted(UNSUPPORTED & present)
    if unsafe:
        raise ValueError(f"DOCX contains unsupported revision or embedded-text elements: {', '.join(unsafe)}")
    body = root.find(WORD + "body")
    if body is None:
        raise ValueError("DOCX has no document body")
    check_text_visibility(body, styles_root)
    markdown, tables = render_body(body)
    if not markdown.strip():
        raise ValueError("DOCX has no extractable body text")
    warnings = []
    if tables:
        warnings.append(f"Flattened {tables} table(s); verify row and cell order")
    if "drawing" in present or "pict" in present:
        warnings.append("Figures were not extracted; inspect them separately")
    if any(name.startswith("word/header") or name.startswith("word/footer") for name in names):
        warnings.append("Headers or footers were not extracted")
    if "word/footnotes.xml" in names or "word/endnotes.xml" in names:
        warnings.append("Footnotes or endnotes were not extracted")
    if "fldChar" in present or "instrText" in present:
        warnings.append("Word fields need manual inspection")
    return markdown.encode("utf-8"), warnings


def convert(input_path, output_root):
    root = output_root.expanduser()
    outside_git(root)
    text, warnings = extract(input_path)
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
    (directory / "manuscript.md").write_bytes(text)
    record = {
        "schema_version": 1,
        "format": "docx",
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_sha256": original_hash,
        "markdown_sha256": hashlib.sha256(text).hexdigest(),
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
