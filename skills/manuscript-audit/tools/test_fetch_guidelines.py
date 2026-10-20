"""Tests use invented guideline pages and private temporary directories."""

import argparse
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib.request import Request

from fetch_guidelines import HostLockedRedirect, save_source
from prepare_run import prepare


class Response:
    def __init__(self, body, url="https://example.org/authors", media_type="text/html"):
        self.body = body
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = media_type

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, _size):
        return self.body


class GuidelineFetchTests(unittest.TestCase):
    def test_fetch_and_prepare_bind_the_same_private_text(self):
        body = b"<h1>Author guide</h1><script>secret code</script><p>Use a data statement.</p>"

        def opener(_request, timeout):
            self.assertEqual(timeout, 20)
            return Response(body)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = save_source("https://example.org/authors", root / "sources", opener)
            record = json.loads(source_path.read_text(encoding="utf-8"))
            text = (source_path.parent / "guideline.txt").read_text(encoding="utf-8")
            self.assertIn("Use a data statement.", text)
            self.assertNotIn("secret code", text)
            self.assertEqual(record["text_file"], "guideline.txt")
            manuscript = root / "paper.md"
            manuscript.write_text("# Invented paper\n", encoding="utf-8")
            args = argparse.Namespace(manuscript=manuscript, guideline_text=None, guideline_url=None,
                                      retrieved_at=None, source_record=source_path, supplementary=[],
                                      output_root=root / "runs", host="manual", model=[])
            run = prepare(args)
            manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
            source = next(iter(manifest["sources"].values()))
            self.assertEqual(source["url"], record["url"])
            self.assertEqual((run / source["path"]).read_text(encoding="utf-8"), text)
            (source_path.parent / "guideline.txt").write_text("Changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                prepare(args)

    def test_rejects_unsafe_redirect_and_unsupported_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sources"
            for response in (Response(b"text", url="http://example.org/authors"),
                             Response(b"%PDF", media_type="application/pdf")):
                with self.subTest(response=response.url), self.assertRaises(ValueError):
                    save_source("https://example.org/authors", root, lambda *_args, **_kwargs: response)
            self.assertFalse(root.exists())

    def test_cookie_redirect_uses_stable_source_url(self):
        requested = "https://link.springer.com/journal/10515/submission-guidelines"
        redirected = requested + "?error=cookies_not_supported&code=temporary-token"
        body = b"<p>Submit a synthetic manuscript.</p>"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = save_source(requested, root / "sources", lambda *_args, **_kwargs: Response(body, redirected))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["requested_url"], requested)
            self.assertEqual(record["url"], requested)
            manuscript = root / "paper.md"
            manuscript.write_text("# Synthetic manuscript\n", encoding="utf-8")
            args = argparse.Namespace(manuscript=manuscript, guideline_text=None, guideline_url=None,
                                      retrieved_at=None, source_record=path, supplementary=[],
                                      output_root=root / "runs", host="manual", model=[])
            run = prepare(args)
            source = next(iter(json.loads((run / "run-manifest.json").read_text())["sources"].values()))
            self.assertEqual(source["url"], requested)

    def test_other_redirect_query_is_preserved(self):
        requested = "https://example.org/authors"
        redirected = requested + "?version=2"
        with tempfile.TemporaryDirectory() as temporary:
            path = save_source(requested, Path(temporary) / "sources",
                               lambda *_args, **_kwargs: Response(b"<p>Guide</p>", redirected))
            self.assertEqual(json.loads(path.read_text())["url"], redirected)

    def test_rejects_repository_output_before_network(self):
        root = Path(__file__).resolve().parents[3]

        def no_network(*_args, **_kwargs):
            self.fail("Network request should not run")

        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            save_source("https://example.org/authors", root / "sources", no_network)

    def test_host_lock_rejects_cross_host_redirect(self):
        handler = HostLockedRedirect("example.org")
        with self.assertRaisesRegex(ValueError, "left the journal host"):
            handler.redirect_request(Request("https://example.org/authors"), None, 302,
                                     "Found", {}, "https://other.example/authors")
        with self.assertRaisesRegex(ValueError, "left the journal host"):
            handler.redirect_request(Request("https://example.org/authors"), None, 302,
                                     "Found", {}, "https://example.org:8443/authors")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sources"
            with self.assertRaisesRegex(ValueError, "left the journal host"):
                save_source("https://example.org/authors", root,
                            lambda *_args, **_kwargs: Response(b"<p>Guide</p>", "https://other.example/authors"),
                            allowed_host="example.org")
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
