"""Tests use invented text and temporary directories only."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prepare_run import prepare


class PrepareRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manuscript-audit-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manuscript = self.root / "Example paper.md"
        self.guideline = self.root / "guideline.txt"
        self.manuscript.write_text("Example title\nMethods\n", encoding="utf-8")
        self.guideline.write_text("A data statement is required.\n", encoding="utf-8")
        self.args = argparse.Namespace(
            manuscript=self.manuscript,
            guideline_text=self.guideline,
            supplementary=[],
            guideline_url="https://example.org/authors",
            retrieved_at="2026-10-19T12:30:00+03:00",
            output_root=self.root / "output",
            host="manual",
            model=[],
        )

    def test_private_run_records_exact_bytes_and_allocates_new_number(self):
        supplement = self.root / "appendix.md"
        supplement.write_text("Additional methods\n", encoding="utf-8")
        self.args.supplementary = [supplement]
        first = prepare(self.args)
        manifest = json.loads((first / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(first.name, "run-1")
        self.assertEqual(manifest["sources"][next(iter(manifest["sources"]))]["retrieved_at"], "2026-10-19T09:30:00Z")
        self.assertEqual((first / manifest["manuscript"]["path"]).read_bytes(), self.manuscript.read_bytes())
        self.assertEqual((first / next(iter(manifest["sources"].values()))["path"]).read_bytes(), self.guideline.read_bytes())
        self.assertEqual((first / manifest["supplementary"][0]["path"]).read_bytes(), supplement.read_bytes())
        self.assertEqual(prepare(self.args).name, "run-2")

    def test_bibtex_supplement_is_hashed_and_kept_private(self):
        bibliography = self.root / "paper.bib"
        bibliography.write_text("@article{example, doi={10.1234/example}}\n", encoding="utf-8")
        self.args.supplementary = [bibliography]
        run = prepare(self.args)
        manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        record = manifest["supplementary"][0]
        self.assertEqual(record["path"], "supplementary/supp-1.bib")
        self.assertEqual(record["sha256"], hashlib.sha256(bibliography.read_bytes()).hexdigest())
        self.assertEqual((run / record["path"]).read_bytes(), bibliography.read_bytes())

    def test_rejects_output_inside_git_checkout(self):
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            prepare(self.args)
        self.assertFalse(self.args.output_root.exists())

    def test_rejects_time_without_timezone(self):
        self.args.retrieved_at = "2026-10-19T12:30:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            prepare(self.args)

    def test_benchmark_freeze_is_recorded_and_invalid_dates_are_rejected(self):
        self.args.data_freeze = "2026-10-19"
        run = prepare(self.args)
        manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["data_freeze"], "2026-10-19")
        validator = Path(__file__).resolve().parents[1] / "validators" / "validate-run-manifest.cjs"
        self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)
        for invalid in ("2026-02-30", "2026-10-19T00:00:00Z", "yesterday"):
            self.args.data_freeze = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "data-freeze"):
                prepare(self.args)

    def test_rejects_pdf_and_invalid_utf8(self):
        self.args.manuscript = self.root / "paper.pdf"
        self.args.manuscript.write_bytes(b"%PDF")
        with self.assertRaisesRegex(ValueError, ".md or .txt"):
            prepare(self.args)
        self.args.manuscript = self.manuscript
        self.guideline.write_bytes(b"\xff")
        with self.assertRaises(UnicodeError):
            prepare(self.args)

    def test_cli_output_passes_manifest_validation_and_detects_changes(self):
        tool = Path(__file__).with_name("prepare_run.py")
        validator = tool.parents[1] / "validators" / "validate-run-manifest.cjs"
        command = [
            sys.executable, str(tool),
            "--manuscript", str(self.manuscript),
            "--guideline-text", str(self.guideline),
            "--guideline-url", self.args.guideline_url,
            "--retrieved-at", self.args.retrieved_at,
            "--output-root", str(self.args.output_root),
        ]
        run = Path(subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip())
        self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)
        (run / "manuscript.md").write_text("Changed", encoding="utf-8")
        failed = subprocess.run(["node", str(validator), str(run)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("SHA-256 mismatch", failed.stderr)

    def test_multiple_cached_guideline_pages_remain_distinct_and_hash_bound(self):
        records = []
        for index, text in enumerate(("Abstracts must be short.\n", "Abstracts must be short.\n"), start=1):
            directory = self.root / f"source-{index}"
            directory.mkdir()
            (directory / "guideline.txt").write_bytes(text.encode("utf-8"))
            record = {
                "schema_version": 1, "text_file": "guideline.txt",
                "url": f"https://example.org/guidelines/{index}",
                "retrieved_at": "2026-10-19T09:30:00Z",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            path = directory / "source.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            records.append(path)
        self.args.source_record = records
        self.args.guideline_text = None
        self.args.guideline_url = None
        self.args.retrieved_at = None
        run = prepare(self.args)
        manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["sources"]), 2)
        self.assertEqual(len({source["path"] for source in manifest["sources"].values()}), 2)
        self.assertEqual({source["url"] for source in manifest["sources"].values()},
                         {"https://example.org/guidelines/1", "https://example.org/guidelines/2"})
        validator = Path(__file__).resolve().parents[1] / "validators" / "validate-run-manifest.cjs"
        self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)
        copied = next(iter(manifest["sources"].values()))["path"]
        (run / copied).write_text("Changed source", encoding="utf-8")
        self.assertNotEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.args.source_record = [records[0], records[0]]
            prepare(self.args)


if __name__ == "__main__":
    unittest.main()
