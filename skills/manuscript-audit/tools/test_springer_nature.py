"""The Springer Nature adapter accepts only journal paths on known hosts."""

import unittest
from urllib.parse import quote
from urllib.request import Request

from publishers.springer_nature import SpringerRedirect, submission_guidelines


class SpringerNatureTests(unittest.TestCase):
    def test_maps_verified_homepage_path(self):
        expected = "https://link.springer.com/journal/607/submission-guidelines"
        for url in ("https://www.springer.com/journal/607",
                    "https://springer.com/journal/607/",
                    "https://link.springer.com/journal/607"):
            with self.subTest(url=url):
                self.assertEqual(submission_guidelines(url), expected)

    def test_rejects_other_hosts_paths_and_url_components(self):
        for url in ("http://www.springer.com/journal/607",
                    "https://evil-springer.com/journal/607",
                    "https://springer.com.evil.example/journal/607",
                    "https://www.springer.com/journal/607?next=evil",
                    "https://www.springer.com/journal/607#authors",
                    "https://www.springer.com:8443/journal/607",
                    "https://user@www.springer.com/journal/607",
                    "https://www.springer.com/journal/607/other",
                    "https://www.springer.com/journal/0"):
            with self.subTest(url=url):
                self.assertIsNone(submission_guidelines(url))

    def test_cookie_redirects_stay_within_one_official_journal_page(self):
        expected = "https://link.springer.com/journal/607/submission-guidelines"
        authorize = ("https://idp.springer.com/authorize?response_type=cookie"
                     f"&client_id=springerlink&redirect_uri={quote(expected, safe='')}")
        handler = SpringerRedirect(expected)
        request = handler.redirect_request(Request(expected), None, 303, "", {}, authorize)
        request = handler.redirect_request(request, None, 302, "", {},
                                           "https://idp.springer.com/transit?token=opaque")
        request = handler.redirect_request(request, None, 302, "", {}, expected)
        self.assertEqual(request.full_url, expected)
        for bad in ("https://evil.example/journal/607/submission-guidelines",
                    "https://link.springer.com/journal/608/submission-guidelines",
                    "https://idp.springer.com/authorize?redirect_uri=https://evil.example/",
                    "http://idp.springer.com/transit"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SpringerRedirect(expected).redirect_request(Request(expected), None, 302, "", {}, bad)


if __name__ == "__main__":
    unittest.main()
