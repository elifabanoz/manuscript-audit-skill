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


def doaj_result(home="https://example.org/journal", guide="https://www.example.org/authors",
                issn="1234-567X", record_id="doaj-record"):
    return {"total": 1, "results": [{"id": record_id, "bibjson": {
        "pissn": issn, "ref": {"journal": home, "author_instructions": guide}}}]}


class CandidateGuidelineTests(unittest.TestCase):
    def test_captures_same_host_page_but_leaves_profile_review_pending(self):
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request.full_url)
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result()).encode())
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
        self.assertEqual(locator["domain_basis"], "current_doaj_journal_homepage")
        self.assertEqual(locator["link_origin"], "doaj_metadata")
        self.assertEqual(locator["doaj_id"], "doaj-record")
        self.assertIsNone(locator["reason"])
        self.assertEqual(len(locator["doaj_response_sha256"]), 64)
        self.assertEqual(source["url"], "https://example.org/authors")
        self.assertIn("Use a data statement.", text)
        self.assertEqual(len(requests), 2)
        self.assertIn("doaj.org/api/search/journals/", requests[0])
        self.assertEqual(requests[1], "https://www.example.org/authors")

    def test_doaj_only_candidate_uses_current_journal_identity(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result()).encode())
            return Response("https://example.org/authors")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery()
            record["candidates"][0]["source_id"] = None
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["domain_basis"], "current_doaj_journal_homepage")
            self.assertIsNone(locator["openalex_source_response_sha256"])
            self.assertEqual(len(requests), 2)

    def test_operator_url_for_doaj_only_journal_requires_current_host(self):
        def opener(request, timeout):
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result()).encode())
            if request.full_url == "https://example.org/updated-authors":
                return Response(request.full_url)
            self.fail("An unverified host was fetched")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery()
            record["candidates"][0]["source_id"] = None
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            accepted = capture(candidate_file, "1234-567X", root / "sources", True, opener,
                               guideline_url="https://example.org/updated-authors")
            self.assertEqual(json.loads(accepted.read_text())["status"], "captured_pending_review")
            rejected = capture(candidate_file, "1234-567X", root / "sources", True, opener,
                               guideline_url="https://untrusted.example/authors")
            locator = json.loads(rejected.read_text())
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIsNone(locator["source_record"])

    def test_stale_or_forged_doaj_link_does_not_fetch_guidelines(self):
        for current in (doaj_result(), doaj_result(record_id="another-record"),
                        doaj_result(issn="9999-9999")):
            with self.subTest(current=current):
                requests = []

                def opener(request, timeout):
                    requests.append(request.full_url)
                    if "doaj.org/api/search/journals/" in request.full_url:
                        return Response(request.full_url, json.dumps(current).encode())
                    self.fail("Guideline URL was fetched before current DOAJ verification")

                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    candidate_file = root / "candidates.json"
                    candidate_file.write_text(json.dumps(discovery(
                        home="https://untrusted.example/journal",
                        guide="https://untrusted.example/authors")), encoding="utf-8")
                    path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
                    locator = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(locator["status"], "needs_manual_verification")
                    self.assertIsNone(locator["source_record"])
                    self.assertEqual(len(requests), 1)

    def test_publisher_host_is_verified_against_current_openalex_source(self):
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request.full_url)
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result(
                    guide="https://publisher.example/authors")).encode())
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
            self.assertEqual(len(requests), 4)

    def test_different_host_requires_manual_verification_when_publisher_differs(self):
        responses = [
            doaj_result(guide="https://publisher.example/authors"),
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
            def with_doaj(request, timeout):
                if "doaj.org/api/search/journals/" in request.full_url:
                    return Response(request.full_url, json.dumps(doaj_result(
                        guide="https://publisher.example/authors")).encode())
                return opener(request, timeout)

            path = capture(candidate_file, "1234-567X", root / "sources", True, with_doaj)
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

    def test_springer_adapter_locates_guidelines_without_doaj_link(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            if "/sources/S1" in request.full_url:
                source = {"id": "https://openalex.org/S1", "type": "journal",
                          "issn_l": "1234-567X", "homepage_url": "https://www.springer.com/journal/607"}
                return Response(request.full_url, json.dumps(source).encode())
            return Response("https://link.springer.com/journal/607/submission-guidelines")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery()
            del record["candidates"][0]["doaj_id"]
            del record["candidates"][0]["author_instructions_url"]
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["link_origin"], "springer_nature_adapter")
            self.assertEqual(locator["domain_basis"], "springer_nature_url_pattern")
            self.assertEqual(len(locator["openalex_source_response_sha256"]), 64)
            self.assertEqual(locator["author_instructions_url"], requests[1])
            self.assertEqual(len(requests), 2)

    def test_springer_adapter_rejects_wrong_current_journal_identity(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            source = {"id": "https://openalex.org/S1", "type": "journal",
                      "issn_l": "9999-9999", "homepage_url": "https://www.springer.com/journal/607"}
            return Response(request.full_url, json.dumps(source).encode())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery()
            del record["candidates"][0]["doaj_id"]
            del record["candidates"][0]["author_instructions_url"]
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "needs_manual_verification")
            self.assertIn("ISSN", locator["reason"])
            self.assertEqual(len(requests), 1)

    def test_ieee_access_adapter_uses_exact_current_journal_record(self):
        requests = []
        guide = "https://ieeeaccess.ieee.org/authors/submission-guidelines/"

        def opener(request, timeout):
            requests.append(request.full_url)
            if "/sources/S2485537415" in request.full_url:
                source = {"id": "https://openalex.org/S2485537415", "type": "journal",
                          "issn_l": "2169-3536", "host_organization": "https://openalex.org/P4310319808",
                          "homepage_url": "http://ieeexplore.ieee.org/servlet/opac?punumber=6287639"}
                return Response(request.full_url, json.dumps(source).encode())
            return Response(guide)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery(home="http://ieeexplore.ieee.org/servlet/opac?punumber=6287639")
            record["candidates"][0].update(
                source_id="https://openalex.org/S2485537415", issns=["2169-3536"])
            del record["candidates"][0]["doaj_id"]
            del record["candidates"][0]["author_instructions_url"]
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "2169-3536", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["link_origin"], "ieee_access_adapter")
            self.assertEqual(locator["domain_basis"], "ieee_access_url_pattern")
            self.assertEqual(locator["author_instructions_url"], guide)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[1], guide)

    def test_openalex_homepage_locates_one_same_host_guide(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            if "/sources/S1" in request.full_url:
                source = {"id": "https://openalex.org/S1", "type": "journal",
                          "issn_l": "1234-567X", "homepage_url": "http://current.example/journal"}
                return Response(request.full_url, json.dumps(source).encode())
            if request.full_url == "https://current.example/journal":
                return Response(request.full_url, b'<a href="/authors">Author guidelines</a>')
            self.assertEqual(request.full_url, "https://current.example/authors")
            return Response(request.full_url)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery(home="https://stale.example/journal", guide=None)
            record["candidates"][0].pop("doaj_id")
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["link_origin"], "official_homepage_link")
            self.assertEqual(locator["domain_basis"], "current_openalex_journal_homepage")
            self.assertEqual(locator["registry_homepage_url"], "http://current.example/journal")
            self.assertEqual(locator["fetched_homepage_url"], "https://current.example/journal")
            self.assertEqual(len(locator["homepage_response_sha256"]), 64)
            self.assertEqual(len(requests), 3)
            self.assertNotIn("https://stale.example/journal", requests)

    def test_doaj_only_homepage_locates_guide_from_current_record(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result(
                    home="https://current.example/journal", guide=None)).encode())
            if request.full_url == "https://current.example/journal":
                return Response(request.full_url, b'<a href="/authors">Instructions for Authors</a>')
            self.assertEqual(request.full_url, "https://current.example/authors")
            return Response(request.full_url)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            record = discovery(home="https://stale.example/journal", guide=None)
            record["candidates"][0]["source_id"] = None
            candidate_file.write_text(json.dumps(record), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["domain_basis"], "current_doaj_journal_homepage")
            self.assertEqual(len(locator["doaj_response_sha256"]), 64)
            self.assertEqual(len(requests), 3)

    def test_homepage_link_ambiguity_and_external_links_stay_pending(self):
        pages = (
            b'<a href="https://external.example/authors">Author guidelines</a>',
            b'<a href="/authors">Author guidelines</a><a href="/submit">Submission guidelines</a>',
        )
        for page in pages:
            with self.subTest(page=page):
                requests = []

                def opener(request, timeout):
                    requests.append(request.full_url)
                    if "/sources/S1" in request.full_url:
                        source = {"id": "https://openalex.org/S1", "type": "journal",
                                  "issn_l": "1234-567X", "homepage_url": "https://current.example/journal"}
                        return Response(request.full_url, json.dumps(source).encode())
                    self.assertEqual(request.full_url, "https://current.example/journal")
                    return Response(request.full_url, page)

                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    candidate_file = root / "candidates.json"
                    record = discovery(guide=None)
                    record["candidates"][0].pop("doaj_id")
                    candidate_file.write_text(json.dumps(record), encoding="utf-8")
                    path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
                    locator = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(locator["status"], "needs_manual_verification")
                    self.assertIn("no unique same-host", locator["reason"])
                    self.assertIsNone(locator["source_record"])
                    self.assertEqual(len(requests), 2)

    def test_current_doaj_springer_link_uses_verified_journal_pattern(self):
        guideline = "https://link.springer.com/journal/607/submission-guidelines"
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            if "doaj.org/api/search/journals/" in request.full_url:
                return Response(request.full_url, json.dumps(doaj_result(
                    home="https://www.springer.com/journal/607", guide=guideline)).encode())
            if "/sources/S1" in request.full_url:
                source = {"id": "https://openalex.org/S1", "type": "journal",
                          "issn_l": "1234-567X", "homepage_url": "https://www.springer.com/journal/607"}
                return Response(request.full_url, json.dumps(source).encode())
            return Response(guideline)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_file = root / "candidates.json"
            candidate_file.write_text(json.dumps(discovery(guide=guideline)), encoding="utf-8")
            path = capture(candidate_file, "1234-567X", root / "sources", True, opener)
            locator = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(locator["status"], "captured_pending_review")
            self.assertEqual(locator["domain_basis"], "springer_nature_url_pattern")
            self.assertEqual(len(requests), 3)

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
            def redirected_opener(request, timeout):
                if "doaj.org/api/search/journals/" in request.full_url:
                    return Response(request.full_url, json.dumps(doaj_result()).encode())
                return Response("https://other.example/authors")

            redirected = capture(candidate_file, "1234-567X", root / "sources", True, redirected_opener)
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
