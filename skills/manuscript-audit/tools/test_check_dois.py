"""Crossref lookup tests use invented DOI values and no network."""

import argparse
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from check_dois import check, publication_updates
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
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "published": {"date-parts": [[2024, 5, 1]]},
                "updated-by": [{"DOI": "10.1234/notice", "type": "retraction",
                                "source": "retraction-watch"}],
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
        self.assertEqual(output["results"][0]["first_author_family"], "Lovelace")
        self.assertEqual(output["results"][0]["update_metadata_status"], "reported")
        self.assertEqual(output["results"][0]["update_signals"], [
            {"type": "retraction", "notice_doi": "10.1234/notice", "source": "retraction-watch"}])
        self.assertEqual(len(output["results"][0]["response_sha256"]), 64)
        snapshot = self.run / output["results"][0]["response_path"]
        self.assertTrue(snapshot.is_file())
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                         output["results"][0]["response_sha256"])
        self.assertNotIn("# References", (self.run / "doi-metadata.json").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            check(self.run, allow_network=True, opener=fake_open, pause=lambda _: None)

    def test_malformed_post_publication_updates_are_not_treated_as_clear(self):
        self.assertEqual(publication_updates({}), ("not_reported", []))
        self.assertEqual(publication_updates({"updated-by": []}), ("not_reported", []))
        self.assertEqual(publication_updates({"updated-by": {"type": "retraction"}}), ("unusable", []))
        self.assertEqual(publication_updates({"updated-by": [{"type": "retraction"}]}), ("unusable", []))

    def test_contact_address_is_sent_only_in_the_request(self):
        requests = []

        def fake_open(request, timeout):
            requests.append(request.full_url)
            return BytesIO(json.dumps({"message": {"DOI": request.full_url.split("/works/")[1].split("?")[0]}}).encode())

        with patch.dict("os.environ", {"CROSSREF_CONTACT_EMAIL": "author@example.org"}):
            check(self.run, allow_network=True, opener=fake_open, pause=lambda _: None)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(parse_qs(urlsplit(url).query)["mailto"] == ["author@example.org"] for url in requests))
        self.assertNotIn("author@example.org", (self.run / "doi-metadata.json").read_text(encoding="utf-8"))

    def test_rejects_stale_inventory_before_network(self):
        path = self.run / "doi-inventory.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["dois"][0]["doi"] = "10.1234/other"
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            check(self.run, allow_network=True, opener=lambda *args, **kwargs: self.fail("Network was used"))


if __name__ == "__main__":
    unittest.main()
