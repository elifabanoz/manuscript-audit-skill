"""Behavior checks for private journal discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from discover_journals import discover, merge_web, shortlist


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

    def test_citation_record_merges_with_openalex_and_doaj_by_source_or_issn(self):
        groups = {"meta": {"count": 20}, "group_by": [
            {"key": "https://openalex.org/S10", "count": 10}]}
        sources = {"results": [{"id": "https://openalex.org/S10", "display_name": "Matching Journal",
                                "type": "journal", "issn_l": "1234-567X",
                                "issn": ["1234-567X", "2345-678X"], "works_count": 100}]}
        doaj = {"total": 1, "results": [
            {"id": "doaj-match", "bibjson": {"title": "Matching Journal", "eissn": "2345-678X"}}]}
        citation = {"schema_version": 1, "provider": "OpenAlex", "reference_input_sha256": "a" * 64,
                    "candidates": [
                        {"source_id": "https://openalex.org/S10", "display_name": "Matching Journal",
                         "issn_l": "1234-567X", "issns": ["1234-567X"], "reference_works": 1,
                         "citing_works": 8, "source_works_count": 100,
                         "evidence": ["reference_venues", "citing_venues"]},
                        {"source_id": "https://openalex.org/S20", "display_name": "Citation Journal",
                         "issn_l": "3456-789X", "issns": ["3456-789X"], "reference_works": 0,
                         "citing_works": 5, "source_works_count": 50,
                         "evidence": ["citing_venues"]}]}
        responses = [groups, sources, doaj]

        def opener(_request, timeout):
            self.assertEqual(timeout, 20)
            return Response(json.dumps(responses.pop(0)).encode())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            citation_path = root / "citation-candidates.json"
            citation_path.write_text(json.dumps(citation), encoding="utf-8")
            output = discover("synthetic topic", root / "results", limit=3, opener=opener,
                              doaj_subject="Computer Science", citation_record=citation_path)
            record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["citation_reference_input_sha256"], "a" * 64)
        self.assertEqual(len(record["candidates"]), 2)
        primary, supplemental = record["candidates"]
        self.assertEqual(primary["evidence"], ["openalex_grouped_works", "doaj_subject", "citation_graph"])
        self.assertEqual(primary["evidence_count"], 3)
        self.assertEqual(primary["citation_signal_details"], ["reference_venues", "citing_venues"])
        self.assertEqual(supplemental["source_id"], "https://openalex.org/S20")
        self.assertEqual(supplemental["evidence"], ["citation_graph"])

    def test_citation_record_must_be_private_and_grouped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "requires --strategy grouped"):
                discover("synthetic topic", root, strategy="sample", citation_record=root / "missing.json",
                         opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                discover("synthetic topic", root, citation_record=Path(__file__).resolve(),
                         opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))

    def test_web_candidates_require_registry_issn_and_keep_web_url_unverified(self):
        groups = {"meta": {"count": 10}, "group_by": [
            {"key": "https://openalex.org/S10", "count": 5}]}
        sources = {"results": [{"id": "https://openalex.org/S10", "display_name": "Topic Journal",
                                "type": "journal", "issn_l": "1234-567X", "issn": ["1234-567X"],
                                "works_count": 100}]}
        matched = {"results": [sources["results"][0]]}
        empty = {"results": []}
        no_doaj = {"total": 0, "results": []}
        crossref = {"message": {"title": "Registry Journal", "ISSN": ["3456-789X"]}}
        bad_crossref = {"message": {"title": "Unrelated Journal", "ISSN": ["9999-9999"]}}
        responses = [groups, sources, matched, empty, no_doaj, crossref,
                     empty, no_doaj, bad_crossref]
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request)
            return Response(json.dumps(responses.pop(0)).encode())

        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENALEX_API_KEY": "test-key"}):
            root = Path(temporary)
            input_file = root / "web.json"
            input_file.write_text(json.dumps({"schema_version": 1, "candidates": [
                {"issn": "1234-567X", "result_url": "https://search.example/topic"},
                {"issn": "3456-789X", "result_url": "https://search.example/special-issue"},
                {"issn": "4567-890X", "result_url": "https://search.example/unverified"}]}), encoding="utf-8")
            output = discover("synthetic topic", root / "results", limit=3,
                              web_candidates=input_file, opener=opener)
            record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["web_submitted_count"], 3)
        self.assertEqual(record["web_verified_count"], 2)
        self.assertEqual(len(record["web_rejected"]), 1)
        self.assertEqual(record["web_rejected"][0]["issn"], "4567-890X")
        self.assertEqual(len(record["candidates"]), 2)
        topic = next(item for item in record["candidates"] if item["source_id"] == "https://openalex.org/S10")
        registry = next(item for item in record["candidates"] if item["display_name"] == "Registry Journal")
        self.assertEqual(topic["evidence"], ["openalex_grouped_works", "web_registry_verified"])
        self.assertEqual(topic["web_results_unverified"], ["https://search.example/topic"])
        self.assertEqual(registry["evidence"], ["web_registry_verified"])
        self.assertIsNone(registry["homepage_url"])
        self.assertEqual(registry["web_registry_matches"][0]["provider"], "Crossref")
        self.assertNotIn("Unrelated Journal", json.dumps(record))
        self.assertFalse(requests[-2].get_header("Authorization"))
        self.assertFalse(requests[-1].get_header("Authorization"))
        self.assertEqual(responses, [])

    def test_web_candidate_can_be_verified_by_doaj_without_openalex(self):
        responses = [{"meta": {"count": 0}, "group_by": []}, {"results": []},
                     {"total": 1, "results": [{"id": "doaj-1", "bibjson": {
                         "title": "Open Journal", "eissn": "2345-678X",
                         "ref": {"journal": "https://open.example/journal",
                                 "author_instructions": "https://open.example/authors"}}}]}]

        def opener(_request, timeout):
            self.assertEqual(timeout, 20)
            return Response(json.dumps(responses.pop(0)).encode())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_file = root / "web.json"
            input_file.write_text(json.dumps({"schema_version": 1, "candidates": [
                {"issn": "2345-678X", "result_url": "https://search.example/open"}]}), encoding="utf-8")
            output = discover("synthetic topic", root / "results", web_candidates=input_file, opener=opener)
            candidate = json.loads(output.read_text(encoding="utf-8"))["candidates"][0]
        self.assertEqual(candidate["doaj_id"], "doaj-1")
        self.assertEqual(candidate["web_registry_matches"][0]["provider"], "DOAJ")
        self.assertEqual(candidate["author_instructions_url"], "https://open.example/authors")

    def test_web_registry_matches_merge_into_one_journal_and_enrich_doaj_only(self):
        candidates = [{"source_id": None, "display_name": "DOAJ Journal", "issn_l": None,
                       "issns": ["1234-567X"], "homepage_url": None,
                       "source_works_count": None, "evidence": ["doaj_subject"]}]
        registry = [{"source_id": "https://openalex.org/S10", "display_name": "Registry Journal",
                     "issn_l": "1234-567X", "issns": ["1234-567X", "2345-678X"],
                     "homepage_url": "https://journal.example", "source_works_count": 100,
                     "registry_provider": "OpenAlex", "registry_response_sha256": "hash-one",
                     "web_input_issn": "1234-567X", "web_result_url_unverified": "https://search.example/one"},
                    {"source_id": "https://openalex.org/S10", "display_name": "Registry Journal",
                     "issn_l": "1234-567X", "issns": ["1234-567X", "2345-678X"],
                     "homepage_url": "https://journal.example", "source_works_count": 100,
                     "registry_provider": "OpenAlex", "registry_response_sha256": "hash-two",
                     "web_input_issn": "2345-678X", "web_result_url_unverified": "https://search.example/two"}]
        merged, rejected = merge_web(candidates, registry, 5)
        self.assertEqual(rejected, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_id"], "https://openalex.org/S10")
        self.assertEqual(merged[0]["evidence"], ["doaj_subject", "web_registry_verified"])
        self.assertEqual(len(merged[0]["web_registry_matches"]), 2)
        self.assertEqual(len(merged[0]["web_results_unverified"]), 2)

    def test_web_candidate_input_must_be_private_and_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "web.json"
            for value in ("http://example.org", "https://127.0.0.1/journal",
                          "https://example.org:8443/journal"):
                file.write_text(json.dumps({"schema_version": 1, "candidates": [
                    {"issn": "1234-567X", "result_url": value}]}), encoding="utf-8")
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "public HTTPS"):
                    discover("synthetic topic", root / "results", web_candidates=file,
                             opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                discover("synthetic topic", root, web_candidates=Path(__file__),
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
