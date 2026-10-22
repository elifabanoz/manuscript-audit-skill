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
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from http_client import read_response
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


def normalized_host(url):
    host = urlsplit(url).hostname
    return host.lower().removeprefix("www.") if host else None


def same_host(url, allowed_host):
    try:
        return normalized_host(url) == allowed_host and urlsplit(url).port in (None, 443)
    except ValueError:
        return False


class HostLockedRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_host):
        self.allowed_host = allowed_host

    def redirect_request(self, request, fp, code, message, headers, new_url):
        https_url(new_url)
        if not same_host(new_url, self.allowed_host):
            raise ValueError("Guideline redirect left the journal host")
        return super().redirect_request(request, fp, code, message, headers, new_url)


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


def fetch(url, opener=urlopen, allowed_host=None):
    https_url(url)
    if allowed_host is not None:
        if not same_host(url, allowed_host):
            raise ValueError("Guideline URL is outside the journal host")
        if opener is urlopen:
            opener = build_opener(HostLockedRedirect(allowed_host)).open
    request = Request(url, headers={"User-Agent": "manuscript-audit-skill/0.1", "Accept": "text/html,text/plain"})
    try:
        response = read_response(request, opener, timeout=20, max_bytes=MAX_BYTES)
        final_url = stable_source_url(url, https_url(response.url))
        if allowed_host is not None and not same_host(final_url, allowed_host):
            raise ValueError("Guideline redirect left the journal host")
        media_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.body
    except HTTPError as error:
        raise ValueError(f"Guideline request failed with HTTP {error.code}") from None
    except URLError:
        raise ValueError("Could not reach the guideline page") from None
    except ValueError as error:
        if allowed_host is not None and str(error) == "Public request redirect left its HTTPS host":
            raise ValueError("Guideline redirect left the journal host") from None
        raise
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


def save_source(url, output_root, opener=urlopen, allowed_host=None):
    root = output_root.expanduser()
    outside_git(root)
    final_url, raw, text = fetch(url, opener, allowed_host)
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
