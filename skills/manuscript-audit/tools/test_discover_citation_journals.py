"""Checks for private citation-venue discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from discover_citation_journals import discover, read_references


class Response:
    def __init__(self, value):
        self.data = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _size):
        return self.data


class CitationDiscoveryTests(unittest.TestCase):
    def test_selected_references_and_citing_journals_keep_only_metadata(self):
        works = {"results": [
            {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1111/alpha",
             "primary_location": {"source": {"id": "https://openalex.org/S1", "type": "journal"}},
             "title": "Unpublished text must never be copied"},
            {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.2222/beta",
             "primary_location": {"source": {"id": "https://openalex.org/S2", "type": "journal"}}}]}
        citing = {"meta": {"count": 42}, "group_by": [
            {"key": "https://openalex.org/S1", "count": 8},
            {"key": "https://openalex.org/S3", "count": 10}]}
        sources = {"results": [
            {"id": f"https://openalex.org/S{number}", "display_name": name, "type": "journal",
             "issn_l": issn, "works_count": total}
            for number, name, issn, total in ((1, "Both", "1234-567X", 100),
                                               (2, "Reference", "2345-678X", 200),
                                               (3, "Citing", "3456-789X", 500))]}
        responses = [works, citing, sources]
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            requests.append(request)
            return Response(responses.pop(0))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_file = root / "references.json"
            reference_file.write_text(json.dumps({"schema_version": 1, "dois": ["10.1111/alpha", "10.2222/beta"]}), encoding="utf-8")
            output = discover(reference_file, root / "results", allow_network=True, opener=opener)
            record_text = output.read_text(encoding="utf-8")
            record = json.loads(record_text)
        self.assertEqual(record["selected_references"], 2)
        self.assertEqual(record["reference_works_resolved"], 2)
        self.assertEqual(record["citing_works_total"], 42)
        self.assertEqual([item["display_name"] for item in record["candidates"]],
                         ["Both", "Reference", "Citing"])
        self.assertEqual(record["candidates"][0]["evidence_count"], 2)
        self.assertEqual(record["candidates"][0]["citing_share"], 0.08)
        self.assertNotIn("10.1111/alpha", record_text)
        self.assertNotIn("Unpublished text must never be copied", record_text)
        self.assertEqual(parse_qs(urlsplit(requests[0].full_url).query)["filter"],
                         ["doi:10.1111/alpha|10.2222/beta"])
        self.assertEqual(parse_qs(urlsplit(requests[1].full_url).query)["filter"],
                         ["cites:W1|W2,type:article,primary_location.source.type:journal"])

    def test_network_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "allow-network"):
                discover(Path(temporary) / "references.json", Path(temporary),
                         opener=lambda *_args, **_kwargs: self.fail("Unexpected network request"))

    def test_references_must_be_selected_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "references.json"
            for values in ([], ["10.1111/alpha", "10.1111/ALPHA"], ["bad"], ["10.1111/a|b"]):
                path.write_text(json.dumps({"schema_version": 1, "dois": values}), encoding="utf-8")
                with self.subTest(values=values), self.assertRaisesRegex(ValueError, "Reference DOI file"):
                    read_references(path)
        repo_file = Path(__file__).resolve()
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            read_references(repo_file)


if __name__ == "__main__":
    unittest.main()
