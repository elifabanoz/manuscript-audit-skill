"""Resolve Springer Nature Link instructions from a journal homepage URL."""

import re
from http.cookiejar import CookieJar
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, build_opener


JOURNAL_PATH = re.compile(r"^/journal/([1-9]\d*)/?$")
HOMEPAGE_HOSTS = {"springer.com", "www.springer.com", "link.springer.com"}
GUIDELINE_URL = re.compile(r"^https://link\.springer\.com/journal/[1-9]\d*/submission-guidelines$")


def submission_guidelines(homepage_url):
    if not isinstance(homepage_url, str):
        return None
    try:
        parsed = urlsplit(homepage_url)
        if (parsed.scheme != "https" or parsed.hostname not in HOMEPAGE_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment):
            return None
    except ValueError:
        return None
    match = JOURNAL_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    return f"https://link.springer.com/journal/{match.group(1)}/submission-guidelines"


class SpringerRedirect(HTTPRedirectHandler):
    def __init__(self, guideline_url):
        self.guideline_url = guideline_url
        self.redirects = 0

    def redirect_request(self, request, fp, code, message, headers, new_url):
        self.redirects += 1
        parsed = urlsplit(new_url)
        try:
            safe_port = parsed.port in (None, 443)
        except ValueError:
            safe_port = False
        if (self.redirects > 6 or parsed.scheme != "https" or not safe_port
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("Springer guideline redirect is not allowed")
        if parsed.hostname == "link.springer.com":
            if new_url != self.guideline_url:
                raise ValueError("Springer guideline redirect changed the journal page")
        elif parsed.hostname == "idp.springer.com":
            if parsed.path == "/authorize":
                query = parse_qs(parsed.query)
                if (query.get("redirect_uri") != [self.guideline_url]
                        or query.get("client_id") != ["springerlink"]
                        or query.get("response_type") != ["cookie"]):
                    raise ValueError("Springer identity redirect has an unexpected return URL")
            elif parsed.path != "/transit":
                raise ValueError("Springer identity redirect has an unexpected path")
        else:
            raise ValueError("Springer guideline redirect left official hosts")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def open_guidelines(request, timeout):
    if not GUIDELINE_URL.fullmatch(request.full_url):
        raise ValueError("Springer guideline URL has an unsupported format")
    opener = build_opener(HTTPCookieProcessor(CookieJar()), SpringerRedirect(request.full_url))
    return opener.open(request, timeout=timeout)
