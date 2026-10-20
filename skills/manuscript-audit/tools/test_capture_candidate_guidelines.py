"""Checks for private, host-locked journal guideline capture."""

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path

from capture_candidate_guidelines import capture


class Response:
    def __init__(self, url, body=None):
        self.url = url
        self.body = body or b"<h1>Guide</h1><p>Use a data statement.</p>"
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, _size):
        return self.body


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
        self.assertEqual(locator["domain_basis"], "journal_homepage")
        self.assertEqual(locator["link_origin"], "doaj_metadata")
        self.assertEqual(locator["doaj_id"], "doaj-record")
        self.assertIsNone(locator["reason"])
        self.assertEqual(source["url"], "https://example.org/authors")
        self.assertIn("Use a data statement.", text)
        self.assertEqual(requests, ["https://www.example.org/authors"])

    def test_publisher_host_is_verified_against_current_openalex_source(self):
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request.full_url)
            if "/sources/S1" in request.full_url:
                body = {"id": "https://openalex.org/S1", "type": "journal",
                        "issn_l": "1234-567X", "host_organization": "https://openalex.org/P2"}
                return Response(request.full_url, json.dumps(body).encode())
            if "/publishers/P2" in request.full_url:
                body = {"id": "https://openalex.org/P2", "homepage_url": "https://www.publisher.example"}
                return Response(request.full_url, json.dumps(body).encode())
            return Response("https://publisher.example/authors")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery(guide="https://publisher.example/authors")), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["domain_basis"], "openalex_publisher_homepage")
            self.assertEqual(locator["openalex_publisher_id"], "https://openalex.org/P2")
            self.assertEqual(len(locator["openalex_source_response_sha256"]), 64)
            self.assertEqual(len(locator["openalex_publisher_response_sha256"]), 64)
            self.assertEqual(len(requests), 3)

    def test_different_host_requires_manual_verification_when_publisher_differs(self):
        responses = [
            {"id": "https://openalex.org/S1", "type": "journal", "issn_l": "1234-567X",
             "host_organization": "https://openalex.org/P2"},
            {"id": "https://openalex.org/P2", "homepage_url": "https://other.example"}]

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            return Response(request.full_url, json.dumps(responses.pop(0)).encode())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery(guide="https://publisher.example/authors")), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("differs from the current publisher homepage", locator["reason"])
            self.assertIsNone(locator["source_record"])
            self.assertEqual(responses, [])

    def test_publisher_lookup_rejects_wrong_journal_issn(self):
        def opener(request, timeout):
            self.assertIn("/sources/S1", request.full_url)
            return Response(request.full_url, json.dumps({"id": "https://openalex.org/S1",
                "type": "journal", "issn_l": "9999-9999",
                "host_organization": "https://openalex.org/P2"}).encode())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery(guide="https://publisher.example/authors")), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("ISSN", locator["reason"])

    def test_operator_url_captures_openalex_only_journal_on_current_host(self):
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request.full_url)
            if "/sources/S1" in request.full_url:
                body = {"id": "https://openalex.org/S1", "type": "journal",
                        "issn": ["1234-567X"], "homepage_url": "https://current.example/journal"}
                return Response(request.full_url, json.dumps(body).encode())
            return Response("https://current.example/authors")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery()
            del record["candidates"][0]["doaj_id"]
            del record["candidates"][0]["author_instructions_url"]
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener,
                           guideline_url="https://current.example/authors")
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["domain_basis"], "openalex_journal_homepage")
            self.assertEqual(locator["link_origin"], "operator_supplied")
            self.assertIsNone(locator["openalex_publisher_id"])
            self.assertEqual(len(requests), 2)

    def test_operator_url_requires_network_and_current_official_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery()), encoding="utf-8")
            pending = capture(candidate_file, "1234-567X", root / "sources", False,
                              lambda *_args, **_kwargs: self.fail("Unexpected network request"),
                              guideline_url="https://publisher.example/authors")
            self.assertIn("allow-network", json.loads(pending.read_text())["reason"])

            responses = [
                {"id": "https://openalex.org/S1", "type": "journal",
                 "issn_l": "1234-567X", "homepage_url": "https://journal.example",
                 "host_organization": "https://openalex.org/P2"},
                {"id": "https://openalex.org/P2", "homepage_url": "https://different.example"}]

            def opener(request, timeout):
                self.assertEqual(timeout, 20)
                return Response(request.full_url, json.dumps(responses.pop(0)).encode())

            rejected = capture(candidate_file, "1234-567X", root / "sources", True, opener,
                               guideline_url="https://publisher.example/authors")
            locator = json.loads(rejected.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("differs", locator["reason"])
            self.assertIsNone(locator["source_record"])
            self.assertEqual(responses, [])

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
