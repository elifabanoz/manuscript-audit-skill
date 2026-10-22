"""Bound public GET responses and retry temporary provider failures."""

import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ResponseBytes:
    body: bytes
    url: str
    headers: object


def retry_delay(error, attempt):
    value = error.headers.get("Retry-After") if error.headers is not None else None
    if isinstance(value, str) and value.isdecimal():
        return min(int(value), 30)
    return 0.5 * (2 ** attempt)


def same_public_host(url, expected):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname.removeprefix("www.") if parsed.hostname else None
        return (parsed.scheme == "https" and host == expected and not parsed.username
                and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        return False


class PublicRedirect(HTTPRedirectHandler):
    def __init__(self, host):
        self.host = host

    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not same_public_host(new_url, self.host):
            raise ValueError("Public request redirect left its HTTPS host")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def read_response(request, opener, *, timeout, max_bytes, pause=time.sleep, retries=2):
    host = urlsplit(request.full_url).hostname
    host = host.removeprefix("www.") if host else None
    if (request.get_method() != "GET" or not host or not same_public_host(request.full_url, host)
            or type(max_bytes) is not int or max_bytes < 1 or max_bytes > 10_000_000
            or type(retries) is not int or not 0 <= retries <= 3):
        raise ValueError("Public GET request or response limit is invalid")
    if opener is urlopen:
        opener = build_opener(PublicRedirect(host)).open
    for attempt in range(retries + 1):
        try:
            with opener(request, timeout=timeout) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
                if not same_public_host(final_url, host):
                    raise ValueError("Public request redirect left its HTTPS host")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError("Public response exceeded the size limit")
                return ResponseBytes(body, final_url, getattr(response, "headers", None))
        except HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt == retries:
                raise
            pause(retry_delay(error, attempt))
        except URLError:
            if attempt == retries:
                raise
            pause(0.5 * (2 ** attempt))
