"""Reuse tests use invented profiles and private temporary runs."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

from prepare_cached_run import find_cached_source, prepare_cached
from prepare_run import prepare


class Response:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = "text/plain; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, amount):
        return self.body[:amount]


class CachedRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-audit-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manuscript = self.root / "paper.md"
        self.manuscript.write_text("# An example\n\nMethods text.\n", encoding="utf-8")
        self.guideline = self.root / "guideline.txt"
        self.guideline.write_bytes(b"A data statement is required.\n")
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
        reuse = json.loads((run / "profile-reuse.json").read_text(encoding="utf-8"))
        self.assertEqual(reuse["source_run"], str(self.original))
        self.assertEqual(reuse["selected_issn"], "1234-567X")
        self.assertRegex(reuse["comparison_record"], r"^profile-comparison-v2-[a-f0-9-]+\.json$")
        self.assertRegex(reuse["comparison_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("adjudication_record", reuse)
        self.assertIsNone(reuse["live_verification"])

    def test_reuses_adjudicated_profile_with_pinned_decision_provenance(self):
        second_path = self.original / "profile-b.json"
        second = json.loads(second_path.read_text(encoding="utf-8"))
        second["rules"][0]["requirement"] = "Provide a data statement."
        second_path.write_text(json.dumps(second) + "\n", encoding="utf-8")
        decisions = {"schema_version": 1, "resolutions": {
            "rules.data_statement": {"choice": "first", "reason": "The pinned source requires a data statement."}
        }}
        (self.original / "decisions.json").write_text(json.dumps(decisions) + "\n", encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "validators" / "adjudicate-profiles.cjs"
        subprocess.run(["node", str(script), str(self.original), "profile-a.json", "profile-b.json",
                        "decisions.json"], capture_output=True, text=True, check=True)
        run = prepare_cached(self.args)
        reuse = json.loads((run / "profile-reuse.json").read_text(encoding="utf-8"))
        self.assertRegex(reuse["comparison_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(reuse["adjudication_record"], r"^profile-adjudication-v1-[a-f0-9-]+\.json$")
        self.assertEqual(reuse["adjudication_sha256"], hashlib.sha256(
            (self.original / reuse["adjudication_record"]).read_bytes()).hexdigest())

    def test_live_verification_accepts_identical_current_guidelines(self):
        self.compare()
        self.args.verify_live = True
        requested = []

        def opener(request, timeout):
            requested.append(request.full_url)
            self.assertEqual(timeout, 20)
            return Response(request.full_url, b"A data statement is required.\n")

        run = prepare_cached(self.args, opener=opener)
        self.assertEqual(requested, ["https://journal.example/authors"])
        reuse = json.loads((run / "profile-reuse.json").read_text(encoding="utf-8"))
        source_id = next(iter(reuse["live_verification"]["sources"]))
        self.assertEqual(reuse["live_verification"]["sources"][source_id]["sha256"],
                         hashlib.sha256(b"A data statement is required.\n").hexdigest())

    def test_live_verification_rejects_changed_guidelines_before_creating_run(self):
        self.compare()
        self.args.verify_live = True
        with self.assertRaisesRegex(ValueError, "changed; rebuild"):
            prepare_cached(self.args, opener=lambda request, timeout: Response(
                request.full_url, b"A revised statement is required.\n"))
        self.assertFalse(self.args.output_root.exists())
        with self.assertRaisesRegex(ValueError, "changed; rebuild"):
            prepare_cached(self.args, opener=lambda request, timeout: Response(
                "https://journal.example/new-authors", b"A data statement is required.\n"))
        self.assertFalse(self.args.output_root.exists())

    def test_finds_only_agreed_profile_for_selected_issn(self):
        self.compare()
        self.args.source_run = None
        self.args.cache_root = self.root / "old"
        self.args.issn = "1234-567X"
        self.assertEqual(find_cached_source(self.args.cache_root, self.args.issn, 30), self.original)
        run = prepare_cached(self.args)
        self.assertEqual((run / "journal-profile.json").read_bytes(),
                         (self.original / "journal-profile.json").read_bytes())
        self.args.issn = "9999-9999"
        with self.assertRaisesRegex(ValueError, "No fresh"):
            prepare_cached(self.args)
        self.args.issn = "invalid"
        with self.assertRaisesRegex(ValueError, "ISSN-shaped"):
            prepare_cached(self.args)

    def test_does_not_choose_between_different_agreed_profiles(self):
        self.compare()
        second_paper = self.root / "other.md"
        second_paper.write_text("# Another example\n", encoding="utf-8")
        second = prepare(argparse.Namespace(
            manuscript=second_paper, extraction_record=None, source_record=[],
            guideline_text=self.guideline, guideline_url="https://journal.example/authors",
            retrieved_at=self.retrieved_at, supplementary=[], output_root=self.root / "old",
            host="manual", model=[], data_freeze=None))
        profile = json.loads((self.original / "journal-profile.json").read_text(encoding="utf-8"))
        profile["rules"][0]["requirement"] = "Include the required data statement."
        for name in ("profile-a.json", "profile-b.json"):
            (second / name).write_text(json.dumps(profile) + "\n", encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "validators" / "compare-profiles.cjs"
        subprocess.run(["node", str(script), str(second), "profile-a.json", "profile-b.json", "--promote"],
                       capture_output=True, text=True, check=True)
        self.args.source_run = None
        self.args.cache_root = self.root / "old"
        self.args.issn = "1234-567X"
        with self.assertRaisesRegex(ValueError, "Multiple different fresh profiles"):
            prepare_cached(self.args)
        self.assertFalse(self.args.output_root.exists())

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
