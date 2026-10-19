#!/usr/bin/env python3
"""Fetch one public guideline page into a private, reviewable source record."""

import argparse
import hashlib
import html.parser
import ipaddress
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from prepare_run import outside_git


MAX_BYTES = 2 * 1024 * 1024
BLOCKS = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4",
          "h5", "h6", "header", "hr", "li", "main", "ol", "p", "section", "table", "td", "th", "tr", "ul"}


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A public HTTPS URL without credentials is required")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        raise ValueError("A public HTTPS URL without credentials is required")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("A public HTTPS URL without credentials is required")
    return value


def stable_source_url(requested, received):
    """Discard Springer's transient cookie-error token from an otherwise identical URL."""
    expected = urlsplit(requested)
    actual = urlsplit(received)
    query = parse_qs(actual.query, keep_blank_values=True)
    if (expected.scheme == actual.scheme and expected.netloc == actual.netloc
            and expected.path == actual.path and not expected.query
            and expected.fragment == actual.fragment
            and set(query) == {"error", "code"}
            and query["error"] == ["cookies_not_supported"]
            and len(query["code"]) == 1 and query["code"][0]):
        return requested
    return received


class VisibleText(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "svg"}:
            self.hidden += 1
        elif not self.hidden and tag in BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template", "svg"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line) + "\n"


def fetch(url, opener=urlopen):
    https_url(url)
    request = Request(url, headers={"User-Agent": "manuscript-audit-skill/0.1", "Accept": "text/html,text/plain"})
    try:
        with opener(request, timeout=20) as response:
            final_url = stable_source_url(url, https_url(response.geturl()))
            media_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        raise ValueError(f"Guideline request failed with HTTP {error.code}") from None
    except URLError:
        raise ValueError("Could not reach the guideline page") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("Guideline response exceeded the size limit")
    if media_type not in {"text/html", "text/plain"}:
        raise ValueError("Only HTML and plain-text guideline pages are supported")
    try:
        decoded = raw.decode(charset)
    except (LookupError, UnicodeError):
        raise ValueError("Guideline response has an unsupported text encoding") from None
    if media_type == "text/html":
        parser = VisibleText()
        parser.feed(decoded)
        extracted = parser.text()
    else:
        extracted = "\n".join(line.strip() for line in decoded.splitlines() if line.strip()) + "\n"
    if not extracted.strip():
        raise ValueError("Guideline page contains no extractable text")
    return final_url, raw, extracted.encode("utf-8")


def save_source(url, output_root, opener=urlopen):
    root = output_root.expanduser()
    outside_git(root)
    final_url, raw, text = fetch(url, opener)
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"source-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available source number")
    text_path = directory / "guideline.txt"
    text_path.write_bytes(text)
    record = {
        "schema_version": 1,
        "requested_url": url,
        "url": final_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "sha256": hashlib.sha256(text).hexdigest(),
        "text_file": "guideline.txt",
    }
    path = directory / "source.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public HTTPS author-guideline page")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "sources")
    args = parser.parse_args()
    try:
        print(save_source(args.url, args.output_root))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
