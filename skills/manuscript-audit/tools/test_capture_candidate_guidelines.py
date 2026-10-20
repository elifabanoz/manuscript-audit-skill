"""Checks for private, host-locked journal guideline capture."""

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path

from capture_candidate_guidelines import capture


class Response:
    def __init__(self, url):
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, _size):
        return b"<h1>Guide</h1><p>Use a data statement.</p>"


def discovery(home="https://example.org/journal", guide="https://www.example.org/authors"):
    return {"schema_version": 4, "candidates": [
        {"display_name": "Example Journal", "source_id": "https://openalex.org/S1",
         "doaj_id": "doaj-record", "issns": ["1234-567X"],
         "homepage_url": home, "author_instructions_url": guide}]}


class CandidateGuidelineTests(unittest.TestCase):
    def test_captures_same_host_page_but_leaves_profile_review_pending(self):
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request.full_url)
            return Response("https://example.org/authors")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery()), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            source_path = path.parent / locator["source_record"]
            source = json.loads(source_path.read_text(encoding="utf-8"))
            text = (source_path.parent / "guideline.txt").read_text(encoding="utf-8")
        self.assertEqual(locator["status"], "captured_pending_review")
        self.assertEqual(locator["doaj_id"], "doaj-record")
        self.assertIsNone(locator["reason"])
        self.assertEqual(source["url"], "https://example.org/authors")
        self.assertIn("Use a data statement.", text)
        self.assertEqual(requests, ["https://www.example.org/authors"])

    def test_different_host_requires_manual_verification_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery(guide="https://publisher.example/authors")), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True,
                           lambda *_args, **_kwargs: self.fail("Unexpected network request"))
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("different host", locator["reason"])
            self.assertIsNone(locator["source_record"])

    def test_network_opt_in_and_redirect_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery()), encoding="utf-8")
            pending = capture(candidate_file, "1234-567X", root / "sources", False,
                              lambda *_args, **_kwargs: self.fail("Unexpected network request"))
            self.assertIn("allow-network", json.loads(pending.read_text())["reason"])
            redirected = capture(candidate_file, "1234-567X", root / "sources", True,
                                 lambda *_args, **_kwargs: Response("https://other.example/authors"))
            locator = json.loads(redirected.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("left the journal host", locator["reason"])
            self.assertIsNone(locator["source_record"])

    def test_requires_private_discovery_record_and_unique_issn(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one candidate"):
                capture(candidate_file, "2345-678X", root / "sources")
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                capture(Path(__file__).resolve(), "1234-567X", root / "sources")


if __name__ == "__main__":
    unittest.main()
