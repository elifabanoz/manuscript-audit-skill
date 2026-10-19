"""Crossref lookup tests use invented DOI values and no network."""

import argparse
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from check_dois import check
from list_dois import inventory
from prepare_run import prepare


class DoiMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-doi-metadata-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        manuscript = root / "paper.md"
        guideline = root / "guideline.txt"
        manuscript.write_text("# References\n10.1234/alpha 10.5678/beta\n", encoding="utf-8")
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

    def test_requires_explicit_network_opt_in(self):
        with self.assertRaisesRegex(ValueError, "requires --allow-network"):
            check(self.run, opener=lambda *args, **kwargs: self.fail("Network was used"))
        self.assertFalse((self.run / "doi-metadata.json").exists())

    def test_records_registry_status_without_manuscript_text(self):
        urls = []

        def fake_open(request, timeout):
            self.assertEqual(timeout, 10)
            urls.append(request.full_url)
            if request.full_url.endswith("10.5678/beta"):
                raise HTTPError(request.full_url, 404, "Not found", {}, None)
            return BytesIO(json.dumps({"message": {
                "DOI": "10.1234/alpha", "title": ["Invented title"],
                "container-title": ["Example Journal"], "type": "journal-article",
                "published": {"date-parts": [[2024, 5, 1]]},
            }}).encode("utf-8"))

        output = check(self.run, allow_network=True, opener=fake_open, pause=lambda _: None)
        self.assertEqual(urls, [
            "https://api.crossref.org/works/10.1234/alpha",
            "https://api.crossref.org/works/10.5678/beta",
        ])
        self.assertEqual([item["status"] for item in output["results"]],
                         ["registered_in_crossref", "not_found_in_crossref"])
        self.assertEqual(output["results"][0]["title"], "Invented title")
        self.assertEqual(output["results"][0]["publication_year"], 2024)
        self.assertNotIn("# References", (self.run / "doi-metadata.json").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            check(self.run, allow_network=True, opener=fake_open, pause=lambda _: None)

    def test_rejects_stale_inventory_before_network(self):
        path = self.run / "doi-inventory.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["dois"][0]["doi"] = "10.1234/other"
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            check(self.run, allow_network=True, opener=lambda *args, **kwargs: self.fail("Network was used"))


if __name__ == "__main__":
    unittest.main()
