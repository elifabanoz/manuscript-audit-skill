"""Reuse tests use invented profiles and private temporary runs."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prepare_cached_run import prepare_cached
from prepare_run import prepare


class CachedRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-audit-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manuscript = self.root / "paper.md"
        self.manuscript.write_text("# An example\n\nMethods text.\n", encoding="utf-8")
        self.guideline = self.root / "guideline.txt"
        self.guideline.write_text("A data statement is required.\n", encoding="utf-8")
        self.retrieved_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.original = prepare(argparse.Namespace(
            manuscript=self.manuscript, extraction_record=None, source_record=[],
            guideline_text=self.guideline, guideline_url="https://journal.example/authors",
            retrieved_at=self.retrieved_at, supplementary=[], output_root=self.root / "old",
            host="manual", model=[], data_freeze=None))
        manifest = json.loads((self.original / "run-manifest.json").read_text(encoding="utf-8"))
        source_id = next(iter(manifest["sources"]))
        profile = {"issn": "1234-567X", "title": "Example Journal", "publisher": "Example",
                   "article_types": ["original_research"], "sources": manifest["sources"],
                   "rules": [{"rule_id": "data_statement", "kind": "semantic",
                              "quote": "A data statement is required.", "source_ref": source_id,
                              "requirement": "Include a data statement."}]}
        for name in ("profile-a.json", "profile-b.json"):
            (self.original / name).write_text(json.dumps(profile) + "\n", encoding="utf-8")
        self.args = argparse.Namespace(source_run=self.original, manuscript=self.manuscript,
                                       extraction_record=None, supplementary=[], output_root=self.root / "new",
                                       ttl_days=30, host="manual", model=[], data_freeze=None)

    def compare(self):
        script = Path(__file__).resolve().parents[1] / "validators" / "compare-profiles.cjs"
        subprocess.run(["node", str(script), str(self.original), "profile-a.json", "profile-b.json", "--promote"],
                       capture_output=True, text=True, check=True)

    def test_reuses_agreed_profile_and_exact_source_snapshot(self):
        self.compare()
        run = prepare_cached(self.args)
        original_profile = (self.original / "journal-profile.json").read_bytes()
        self.assertEqual((run / "journal-profile.json").read_bytes(), original_profile)
        original_manifest = json.loads((self.original / "run-manifest.json").read_text(encoding="utf-8"))
        new_manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(new_manifest["sources"], original_manifest["sources"])
        self.assertEqual(json.loads((run / "profile-reuse.json").read_text())["profile_sha256"],
                         hashlib.sha256(original_profile).hexdigest())

    def test_rejects_uncompared_stale_or_changed_profile(self):
        with self.assertRaisesRegex(ValueError, "journal-profile.json"):
            prepare_cached(self.args)
        final = self.original / "journal-profile.json"
        final.write_bytes((self.original / "profile-a.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "two-draft"):
            prepare_cached(self.args)
        final.unlink()
        self.retrieved_at = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        manifest_path = self.original / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_id = next(iter(manifest["sources"]))
        manifest["sources"][source_id]["retrieved_at"] = self.retrieved_at
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        for name in ("profile-a.json", "profile-b.json"):
            profile_path = self.original / name
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["sources"][source_id]["retrieved_at"] = self.retrieved_at
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
        self.compare()
        self.args.ttl_days = 0
        with self.assertRaisesRegex(ValueError, "TTL"):
            prepare_cached(self.args)
        self.args.ttl_days = 30
        with self.assertRaisesRegex(ValueError, "cache TTL"):
            prepare_cached(self.args)
        self.assertFalse(self.args.output_root.exists())

    def test_rejects_changed_draft_or_source(self):
        self.compare()
        (self.original / "profile-b.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "two-draft"):
            prepare_cached(self.args)
        profile = (self.original / "profile-a.json").read_bytes()
        (self.original / "profile-b.json").write_bytes(profile)
        source_file = next((self.original / "sources").iterdir())
        source_file.write_text("Changed page", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            prepare_cached(self.args)
