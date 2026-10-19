"""arXiv metadata tests use invented identifiers and no network."""

import argparse
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from check_arxiv_ids import check, fetch_batch
from list_arxiv_ids import inventory
from prepare_run import prepare


ATOM = 'xmlns="http://www.w3.org/2005/Atom"'


class ArxivMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-arxiv-metadata-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        guideline = root / "guideline.txt"
        manuscript.write_text("# References\narXiv:2404.01234v2 arXiv:hep-ph/9912345\n", encoding="utf-8")
        guideline.write_text("Citations are required.\n", encoding="utf-8")
        self.run = prepare(argparse.Namespace(
            manuscript=manuscript,
            guideline_text=guideline,
            supplementary=[],
            guideline_url="https://example.org/authors",
            retrieved_at="2026-10-19T09:00:00Z",
            output_root=root / "output",
            host="manual",
            model=[],
        ))
        inventory(self.run)

    def test_requires_opt_in_and_rejects_stale_inventory(self):
        with self.assertRaisesRegex(ValueError, "requires --allow-network"):
            check(self.run, opener=lambda *args, **kwargs: self.fail("Network was used"))
        path = self.run / "arxiv-inventory.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["arxiv_ids"][0]["arxiv_id"] = "2404.09999"
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            check(self.run, allow_network=True, opener=lambda *args, **kwargs: self.fail("Network was used"))

    def test_extracts_limited_metadata_and_reports_missing_ids(self):
        requested = []

        def fake_open(request, timeout):
            self.assertEqual(timeout, 20)
            requested.extend(parse_qs(urlparse(request.full_url).query)["id_list"][0].split(","))
            return BytesIO((f'<feed {ATOM}>'
                            '<entry><id>https://arxiv.org/abs/2404.01234v2</id>'
                            '<title>  Invented\n title  </title><published>2024-04-03T00:00:00Z</published>'
                            '<summary>Private-looking abstract should not be saved.</summary></entry>'
                            '</feed>').encode("utf-8"))

        output = check(self.run, allow_network=True, opener=fake_open, pause=lambda _: self.fail("Extra request"))
        self.assertEqual(requested, ["2404.01234v2", "hep-ph/9912345"])
        self.assertEqual([item["status"] for item in output["results"]],
                         ["registered_in_arxiv", "not_found_in_arxiv"])
        self.assertEqual(output["results"][0]["publication_year"], 2024)
        self.assertEqual(output["results"][0]["title"], "Invented title")
        self.assertNotIn("Private-looking abstract", (self.run / "arxiv-metadata.json").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            check(self.run, allow_network=True, opener=fake_open)

    def test_unversioned_id_matches_returned_version(self):
        def fake_open(request, timeout):
            return BytesIO((f'<feed {ATOM}><entry><id>http://arxiv.org/abs/2404.01234v3</id>'
                            '<title>Invented title</title></entry></feed>').encode("utf-8"))

        result = fetch_batch(["2404.01234"], fake_open)
        self.assertEqual(result[0]["status"], "registered_in_arxiv")
        self.assertEqual(result[0]["registered_id"], "2404.01234v3")

    def test_multiple_batches_pause_three_seconds(self):
        path = self.run / "arxiv-inventory.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["arxiv_ids"] = [{"arxiv_id": f"2404.{index:05d}"} for index in range(1, 22)]
        path.write_text(json.dumps(saved), encoding="utf-8")
        requests = []
        pauses = []

        def fake_open(request, timeout):
            requests.append(request.full_url)
            return BytesIO(f"<feed {ATOM}></feed>".encode("utf-8"))

        with patch("check_arxiv_ids.collect", return_value=saved):
            output = check(self.run, allow_network=True, opener=fake_open, pause=pauses.append)
        self.assertEqual(len(requests), 2)
        self.assertEqual(pauses, [3])
        self.assertEqual(len(output["results"]), 21)


if __name__ == "__main__":
    unittest.main()
