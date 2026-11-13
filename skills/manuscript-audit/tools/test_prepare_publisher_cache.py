"""Private publisher export checks with invented source records."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_publisher_cache import find_publisher_cache_source, prepare_publisher_cache


class PublisherCacheExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="publisher-cache-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run-1"
        (self.run / "sources").mkdir(parents=True)
        self.url = "https://publisher.example/authors"
        self.contents = b"A data statement is required.\n"
        self.sha = hashlib.sha256(self.contents).hexdigest()
        self.source_id = "src_" + hashlib.sha256((self.url + "\0" + self.sha).encode()).hexdigest()[:16]
        (self.run / "sources" / f"{self.source_id}.txt").write_bytes(self.contents)
        self.rules = b'{"schema_version":1,"publisher":"Example Publisher","rules":[]}\n'
        (self.run / "publisher-rules.json").write_bytes(self.rules)
        self.approval = {"publisher": "Example Publisher", "ttl_days": 30,
                         "publisher_rules_path": "publisher-rules.json",
                         "publisher_rules_sha256": hashlib.sha256(self.rules).hexdigest(),
                         "profile_sha256": "a" * 64, "composition_sha256": "b" * 64,
                         "sources": {self.source_id: {"url": self.url,
                                      "retrieved_at": "2026-10-19T09:12:00Z", "sha256": self.sha,
                                      "path": f"sources/{self.source_id}.txt"}}}

    def test_exports_only_pinned_private_sources_and_rules(self):
        with patch("prepare_publisher_cache.validate_source_run", return_value=self.approval):
            package = prepare_publisher_cache(self.run, "Example Publisher", self.root / "export")
        self.assertEqual((package / self.source_id / "guideline.txt").read_bytes(), self.contents)
        self.assertEqual((package / "publisher-rules.json").read_bytes(), self.rules)
        record = json.loads((package / self.source_id / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(record["sha256"], self.sha)
        self.assertIsNone(json.loads((package / "reuse.json").read_text())["live_verification"])
        with patch("prepare_publisher_cache.validate_source_run", return_value=self.approval):
            with self.assertRaisesRegex(ValueError, "already exists"):
                prepare_publisher_cache(self.run, "Example Publisher", self.root / "export")

    def test_rejects_changed_source_and_output_inside_source_run(self):
        (self.run / "sources" / f"{self.source_id}.txt").write_bytes(b"Changed")
        with patch("prepare_publisher_cache.validate_source_run", return_value=self.approval):
            with self.assertRaisesRegex(ValueError, "changed after validation"):
                prepare_publisher_cache(self.run, "Example Publisher", self.root / "export")
            with self.assertRaisesRegex(ValueError, "inside the source run"):
                prepare_publisher_cache(self.run, "Example Publisher", self.run / "export")
        self.assertFalse((self.root / "export").exists())

    def test_private_cache_search_rejects_ambiguous_reviewed_sources(self):
        cache_root = self.root / "cache"
        first = cache_root / "paper-a" / "run-1"
        second = cache_root / "paper-b" / "run-2"
        ignored = cache_root / "paper-c" / "draft"
        for run in (first, second, ignored):
            run.mkdir(parents=True)
            (run / "publisher-composition.json").write_text("{}", encoding="utf-8")
        with patch("prepare_publisher_cache.validate_source_run", return_value=self.approval):
            self.assertEqual(find_publisher_cache_source(cache_root, "Example Publisher"), second)
        different = {**self.approval, "publisher_rules_sha256": "c" * 64}
        with patch("prepare_publisher_cache.validate_source_run",
                   side_effect=lambda run, *_: different if run == second else self.approval):
            with self.assertRaisesRegex(ValueError, "select --source-run explicitly"):
                find_publisher_cache_source(cache_root, "Example Publisher")
        with patch("prepare_publisher_cache.validate_source_run", side_effect=ValueError("stale")):
            with self.assertRaisesRegex(ValueError, "No fresh, reviewed"):
                find_publisher_cache_source(cache_root, "Example Publisher")


if __name__ == "__main__":
    unittest.main()
