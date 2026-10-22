#!/usr/bin/env python3
"""Focused safety checks for the private public-paper downloader."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("fetch_public_papers.py")
SPEC = importlib.util.spec_from_file_location("fetch_public_papers", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PAPER = json.loads(Path(__file__).with_name("public-papers.json").read_text(encoding="utf-8"))["papers"][0]
JOSE_PAPER = next(paper for paper in json.loads(Path(__file__).with_name("public-papers.json").read_text(encoding="utf-8"))["papers"]
                  if paper["doi"] == "10.21105/jose.00307")


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.data


class FetchPublicPapersTests(unittest.TestCase):
    def test_matching_source_is_written_outside_git(self):
        data = b"# Published paper\n"
        paper = {**PAPER, "source_sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "private"
            with patch.object(MODULE.urllib.request, "urlopen", return_value=Response(data)):
                target, status = MODULE.download(paper, output)
            self.assertEqual(status, "downloaded")
            self.assertEqual(target.read_bytes(), data)
            with patch.object(MODULE.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request")):
                _, status = MODULE.download(paper, output)
            self.assertEqual(status, "verified existing")

    def test_hash_mismatch_does_not_write_paper(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "private"
            with patch.object(MODULE.urllib.request, "urlopen", return_value=Response(b"changed")):
                with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                    MODULE.download(PAPER, output)
            self.assertFalse(output.exists())

    def test_git_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".git").mkdir()
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                MODULE.download(PAPER, root / "raw")

    def test_path_traversal_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid output name"):
            MODULE.validate_paper({**PAPER, "output_name": "../paper.md"})

    def test_supplementary_file_keeps_submitted_basename(self):
        MODULE.validate_paper(JOSE_PAPER)
        data = b"@article{example, title={Example}}\n"
        source = {**JOSE_PAPER["supplementary"][0], "source_sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / JOSE_PAPER["supplementary_dir"] / source["output_name"]
            with patch.object(MODULE.urllib.request, "urlopen", return_value=Response(data)):
                saved, status = MODULE.download_source(source, target, JOSE_PAPER["doi"])
            self.assertEqual(status, "downloaded")
            self.assertEqual(saved.name, "paper.bib")
            self.assertEqual(saved.read_bytes(), data)

    def test_supplementary_ref_must_match_paper(self):
        source = {**JOSE_PAPER["supplementary"][0],
                  "source_url": JOSE_PAPER["supplementary"][0]["source_url"].replace(
                      "cb42f234b3cfcadee5c99f2fa22b69f47f3008da", "main")}
        with self.assertRaisesRegex(ValueError, "pinned repository and ref"):
            MODULE.validate_paper({**JOSE_PAPER, "supplementary": [source]})

    def test_supplementary_hash_mismatch_does_not_write(self):
        source = JOSE_PAPER["supplementary"][0]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "jose-00307" / "paper.bib"
            with patch.object(MODULE.urllib.request, "urlopen", return_value=Response(b"changed")):
                with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                    MODULE.download_source(source, target, JOSE_PAPER["doi"])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
