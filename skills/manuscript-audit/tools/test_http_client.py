"""Public metadata fetches share bounded reads and retry rules."""

import unittest
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

from http_client import PublicRedirect, read_response


class Response:
    def __init__(self, url, body=b"ok"):
        self.url = url
        self.body = body
        self.headers = Message()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, amount):
        return self.body[:amount]


class HttpClientTests(unittest.TestCase):
    def test_retries_temporary_status_with_bounded_retry_after(self):
        request = Request("https://api.example.org/records")
        calls = []
        pauses = []
        headers = Message()
        headers["Retry-After"] = "90"

        def opener(received, timeout):
            calls.append((received.full_url, timeout))
            if len(calls) == 1:
                raise HTTPError(received.full_url, 429, "Rate limited", headers, None)
            return Response(received.full_url)

        result = read_response(request, opener, timeout=12, max_bytes=100, pause=pauses.append)
        self.assertEqual(result.body, b"ok")
        self.assertEqual(calls, [(request.full_url, 12)] * 2)
        self.assertEqual(pauses, [30])

    def test_retry_exhaustion_and_permanent_error(self):
        request = Request("https://api.example.org/records")
        pauses = []
        attempts = []

        def unavailable(received, timeout):
            attempts.append(received.full_url)
            raise URLError("temporary")

        with self.assertRaises(URLError):
            read_response(request, unavailable, timeout=10, max_bytes=100, pause=pauses.append)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(pauses, [0.5, 1.0])

        def missing(received, timeout):
            raise HTTPError(received.full_url, 404, "Missing", {}, None)

        with self.assertRaises(HTTPError):
            read_response(request, missing, timeout=10, max_bytes=100, pause=pauses.append)
        self.assertEqual(pauses, [0.5, 1.0])

    def test_rejects_oversized_or_downgraded_response(self):
        request = Request("https://api.example.org/records")
        with self.assertRaisesRegex(ValueError, "size limit"):
            read_response(request, lambda _request, timeout: Response(request.full_url, b"12345"),
                          timeout=10, max_bytes=4)
        with self.assertRaisesRegex(ValueError, "left its HTTPS host"):
            read_response(request, lambda _request, timeout: Response("http://api.example.org/records"),
                          timeout=10, max_bytes=100)
        with self.assertRaisesRegex(ValueError, "left its HTTPS host"):
            read_response(request, lambda _request, timeout: Response("https://other.example/records"),
                          timeout=10, max_bytes=100)
        with self.assertRaisesRegex(ValueError, "left its HTTPS host"):
            PublicRedirect("api.example.org").redirect_request(
                request, None, 302, "Found", {}, "https://other.example/records")
        with self.assertRaisesRegex(ValueError, "Public GET"):
            read_response(Request("http://api.example.org/records"), lambda *_: None,
                          timeout=10, max_bytes=100)


if __name__ == "__main__":
    unittest.main()
