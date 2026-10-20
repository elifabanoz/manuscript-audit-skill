"""Behavior checks for private journal discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from discover_journals import discover, shortlist


def work(number, source_id, name, kind="journal", issn_l="1234-567X"):
    return {"id": f"https://openalex.org/W{number}", "title": "Unused title",
            "primary_location": {"source": {"id": f"https://openalex.org/S{source_id}",
                                            "display_name": name, "type": kind, "issn_l": issn_l}}}


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _size):
        return self.data


class DiscoveryTests(unittest.TestCase):
    def test_ranks_journals_and_deduplicates_work_ids(self):
        payload = {"results": [work(1, 10, "Beta"), work(1, 10, "Beta"),
                               work(2, 10, "Beta"), work(3, 20, "Alpha"),
                               work(4, 30, "Repository", "repository"),
                               work(5, 40, "No ISSN", issn_l="invalid")]}
        candidates = shortlist(payload, 10)
        self.assertEqual([item["display_name"] for item in candidates], ["Beta", "Alpha"])
        self.assertEqual(candidates[0]["matching_works"], 2)
        self.assertEqual(candidates[0]["work_ids"],
                         ["https://openalex.org/W1", "https://openalex.org/W2"])

    def test_explicit_query_writes_metadata_only_outside_git(self):
        payload = {"results": [work(1, 10, "Example Journal")]}
        data = json.dumps(payload).encode()
        requests = []

        def opener(request, timeout):
            requests.append(request)
            self.assertEqual(timeout, 20)
            return Response(data)

        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENALEX_API_KEY": "test-key"}):
            output = discover("synthetic topic", Path(temporary), opener=opener, field_id=17,
                              published_after="2024-01-01", strategy="sample")
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["query"], "synthetic topic")
            self.assertEqual(record["field_id"], 17)
            self.assertEqual(record["published_after"], "2024-01-01")
            self.assertEqual(record["candidates"][0]["display_name"], "Example Journal")
            self.assertNotIn("Unused title", output.read_text(encoding="utf-8"))
            params = parse_qs(urlsplit(requests[0].full_url).query)
            self.assertEqual(params["search.title_abstract_keywords"], ["synthetic topic"])
            self.assertEqual(params["filter"],
                             ["type:article,primary_topic.field.id:17,from_publication_date:2024-01-01"])
            self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-key")
            self.assertNotIn("test-key", requests[0].full_url)

    def test_grouped_discovery_enriches_sources_and_normalizes_volume(self):
        groups = {"meta": {"count": 246, "groups_count": 3}, "group_by": [
            {"key": "https://openalex.org/S10", "count": 20},
            {"key": "https://openalex.org/S20", "count": 8},
            {"key": "https://openalex.org/S30", "count": 30}]}
        sources = {"results": [
            {"id": "https://openalex.org/S10", "display_name": "Large Journal", "type": "journal",
             "issn_l": "1234-567X", "works_count": 10000, "homepage_url": "https://journal.example"},
            {"id": "https://openalex.org/S20", "display_name": "Focused Journal", "type": "journal",
             "issn_l": "2345-678X", "works_count": 100, "homepage_url": "https://focused.example"},
            {"id": "https://openalex.org/S30", "display_name": "Repository", "type": "repository",
             "issn_l": "3456-789X", "works_count": 200}]}
        requests = []

        def opener(request, timeout):
            requests.append(request)
            self.assertEqual(timeout, 20)
            return Response(json.dumps(groups if len(requests) == 1 else sources).encode())

        with tempfile.TemporaryDirectory() as temporary:
            output = discover("synthetic topic", Path(temporary), limit=2, opener=opener,
                              field_id=17, published_after="2024-01-01")
            record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["strategy"], "grouped")
        self.assertEqual(record["matching_works_total"], 246)
        self.assertEqual(record["grouped_sources_seen"], 3)
        self.assertEqual([item["display_name"] for item in record["candidates"]],
                         ["Focused Journal", "Large Journal"])
        self.assertEqual(record["candidates"][0]["topic_share"], 0.08)
        self.assertEqual(record["candidates"][0]["evidence"], ["openalex_grouped_works"])
        first = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(first["group_by"], ["primary_location.source.id"])
        self.assertEqual(first["filter"], ["type:article,primary_topic.field.id:17,from_publication_date:2024-01-01,primary_location.source.type:journal"])
        second = parse_qs(urlsplit(requests[1].full_url).query)
        self.assertEqual(second["filter"], ["ids.openalex:S30|S10|S20"])
        self.assertNotIn("Unused title", json.dumps(record))

    def test_doaj_subject_adds_issn_matched_and_supplemental_journals(self):
        groups = {"meta": {"count": 20}, "group_by": [
            {"key": "https://openalex.org/S10", "count": 10}]}
        sources = {"results": [{"id": "https://openalex.org/S10", "display_name": "Matching Journal",
                                "type": "journal", "issn_l": "1234-567X",
                                "issn": ["1234-567X", "2345-678X"], "works_count": 100}]}
        doaj = {"total": 2, "results": [
            {"id": "doaj-match", "bibjson": {"title": "Matching Journal", "eissn": "2345-678X",
              "ref": {"author_instructions": "https://publisher.example/authors"}}},
            {"id": "doaj-only", "bibjson": {"title": "DOAJ Journal", "pissn": "3456-789X",
              "ref": {"journal": "https://doaj-journal.example", "author_instructions": "http://insecure.example/authors"}}}]}
        responses = [groups, sources, doaj]
        requests = []

        def opener(request, timeout):
            requests.append(request)
            self.assertEqual(timeout, 20)
            return Response(json.dumps(responses.pop(0)).encode())

        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENALEX_API_KEY": "test-key"}):
            output = discover("synthetic topic", Path(temporary), limit=3, opener=opener,
                              doaj_subject="Computer Science")
            record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["providers"], ["OpenAlex", "DOAJ"])
        self.assertEqual(record["doaj_results_total"], 2)
        self.assertEqual(len(record["candidates"]), 2)
        combined, supplemental = record["candidates"]
        self.assertEqual(combined["evidence"], ["openalex_grouped_works", "doaj_subject"])
        self.assertEqual(combined["evidence_count"], 2)
        self.assertEqual(combined["author_instructions_url"], "https://publisher.example/authors")
        self.assertEqual(supplemental["evidence"], ["doaj_subject"])
        self.assertIsNone(supplemental["source_id"])
        self.assertIsNone(supplemental["issn_l"])
        self.assertIsNone(supplemental["author_instructions_url"])
        self.assertEqual(unquote(urlsplit(requests[2].full_url).path),
                         "/api/search/journals/index.classification:Computer Science")
        self.assertIsNone(requests[2].get_header("Authorization"))

    def test_rejects_unsafe_doaj_subject_before_network_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            for subject in ("", "science:other", "a/b", "word\nsecret"):
                with self.subTest(subject=subject), self.assertRaisesRegex(ValueError, "doaj-subject"):
                    discover("synthetic topic", Path(temporary), doaj_subject=subject,
                             opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))

    def test_rejects_repository_output_before_network_request(self):
        root = Path(__file__).resolve().parents[3]

        def no_network(*_args, **_kwargs):
            self.fail("Network request should not run")

        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            discover("synthetic topic", root / "discovery", opener=no_network)

    def test_requires_short_explicit_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            for query in ("", "word\nsecret", "x" * 201):
                with self.subTest(query=query[:10]), self.assertRaisesRegex(ValueError, "topic phrase"):
                    discover(query, Path(temporary), opener=lambda *_args, **_kwargs: None)

    def test_rejects_invalid_field_before_network_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            for field_id in (0, -1, True):
                with self.subTest(field_id=field_id), self.assertRaisesRegex(ValueError, "field-id"):
                    discover("synthetic topic", Path(temporary), field_id=field_id,
                             opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))

    def test_rejects_invalid_publication_date_before_network_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            for value in ("2024-02-30", "2024", "yesterday"):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "published-after"):
                    discover("synthetic topic", Path(temporary), published_after=value,
                             opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))


if __name__ == "__main__":
    unittest.main()
