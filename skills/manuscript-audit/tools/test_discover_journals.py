"""Behavior checks for private journal discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

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
                              published_after="2024-01-01")
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
