"""Locate one explicit author-guide link on a verified journal homepage."""

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import urlopen

from fetch_guidelines import fetch, https_url, normalized_host, same_host


LABELS = {
    "author guidelines", "guidelines for authors", "instructions for authors",
    "author instructions", "guide for authors", "submission guidelines",
    "manuscript submission guidelines",
}


class GuideLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.href = None
        self.words = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.words = []

    def handle_data(self, data):
        if self.href is not None:
            self.words.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            label = re.sub(r"\s+", " ", " ".join(self.words)).strip().lower()
            if label in LABELS:
                self.links.append(self.href)
            self.href = None
            self.words = []


def locate(homepage_url, opener=urlopen):
    """Return the unique same-host guide URL and the homepage response hash."""
    if not isinstance(homepage_url, str):
        raise ValueError("The current journal record has no usable homepage")
    parsed = urlsplit(homepage_url)
    if parsed.scheme == "http":
        if parsed.username or parsed.password or parsed.port not in (None, 80):
            raise ValueError("The current journal homepage cannot be upgraded to HTTPS")
        homepage_url = urlunsplit(("https", parsed.hostname or "", parsed.path,
                                   parsed.query, parsed.fragment))
    https_url(homepage_url)
    host = normalized_host(homepage_url)
    if not same_host(homepage_url, host):
        raise ValueError("The current journal homepage has a nonstandard HTTPS port")
    final_url, raw, _text = fetch(homepage_url, opener=opener, allowed_host=host)
    parser = GuideLinks()
    parser.feed(raw.decode("utf-8", errors="replace"))
    links = set()
    for href in parser.links:
        url, _fragment = urldefrag(urljoin(final_url, href))
        try:
            https_url(url)
        except ValueError:
            continue
        if same_host(url, host) and url != final_url:
            links.add(url)
    if len(links) != 1:
        raise ValueError("The current journal homepage has no unique same-host author-guide link")
    return links.pop(), final_url, hashlib.sha256(raw).hexdigest()
