"""Exercise private publisher-source reuse across two invented journals."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from prepare_publisher_cache import prepare_publisher_cache
from prepare_run import prepare


VALIDATORS = Path(__file__).resolve().parents[1] / "validators"


def source_record(root, name, url, text):
    directory = root / name
    directory.mkdir()
    contents = (text + "\n").encode("utf-8")
    (directory / "guideline.txt").write_bytes(contents)
    record = {"schema_version": 1, "text_file": "guideline.txt", "url": url,
              "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "sha256": hashlib.sha256(contents).hexdigest()}
    (directory / "source.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return directory / "source.json"


def run_command(script, *arguments):
    result = subprocess.run(["node", str(VALIDATORS / script), *map(str, arguments)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"{script}: {result.stderr.strip()}")


def make_run(root, name, manuscript, records):
    return prepare(argparse.Namespace(manuscript=manuscript, extraction_record=None,
                                      source_record=records, guideline_text=None, guideline_url=None,
                                      retrieved_at=None, supplementary=[], output_root=root / name,
                                      host="manual", model=[], data_freeze=None))


class CrossJournalPublisherTests(unittest.TestCase):
    def test_reuses_only_shared_publisher_sources(self):
        temporary = tempfile.TemporaryDirectory(prefix="publisher-cross-journal-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        first_manuscript = root / "first.md"
        second_manuscript = root / "second.md"
        first_manuscript.write_text("# Invented first manuscript\n", encoding="utf-8")
        second_manuscript.write_text("# Invented second manuscript\n", encoding="utf-8")
        publisher_record = source_record(root, "publisher", "https://publisher.example/authors",
                                         "A data statement is required.")
        first_record = source_record(root, "first-journal", "https://first.example/authors",
                                     "Use an unstructured abstract.")
        first = make_run(root, "first-runs", first_manuscript, [publisher_record, first_record])
        first_manifest = json.loads((first / "run-manifest.json").read_text(encoding="utf-8"))
        publisher_id = next(key for key, source in first_manifest["sources"].items()
                            if source["url"] == "https://publisher.example/authors")
        first_id = next(key for key, source in first_manifest["sources"].items()
                        if source["url"] == "https://first.example/authors")
        publisher_rules = {"schema_version": 1, "publisher": "Example Publisher",
                           "sources": {publisher_id: first_manifest["sources"][publisher_id]},
                           "rules": [{"rule_id": "data_statement", "kind": "semantic",
                                      "requirement": "Include a data statement.",
                                      "quote": "A data statement is required.", "source_ref": publisher_id}]}
        (first / "publisher-rules.json").write_text(json.dumps(publisher_rules), encoding="utf-8")
        first_profile = {"issn": "1234-567X", "title": "First Journal", "publisher": "Example Publisher",
                         "article_types": ["original_research"], "sources": first_manifest["sources"],
                         "rules": [{"rule_id": "abstract", "kind": "semantic",
                                    "requirement": "Use an unstructured abstract.",
                                    "quote": "Use an unstructured abstract.", "source_ref": first_id}]}
        (first / "journal-draft.json").write_text(json.dumps(first_profile), encoding="utf-8")
        (first / "overrides.json").write_text('{"schema_version":1,"overrides":[]}', encoding="utf-8")
        run_command("compose-publisher-rules.cjs", first, "publisher-rules.json", "journal-draft.json", "overrides.json")
        (first / "independent-draft.json").write_bytes((first / "composed-profile.json").read_bytes())
        run_command("compare-profiles.cjs", first, "composed-profile.json", "independent-draft.json", "--promote")

        package = prepare_publisher_cache(first, "Example Publisher", root / "private-export")
        second_record = source_record(root, "second-journal", "https://second.example/authors",
                                      "Use a structured abstract.")
        second = make_run(root, "second-runs", second_manuscript,
                          [package / publisher_id / "source.json", second_record])
        second_manifest = json.loads((second / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertIn(publisher_id, second_manifest["sources"])
        self.assertNotIn(first_id, second_manifest["sources"])
        second_id = next(key for key, source in second_manifest["sources"].items()
                         if source["url"] == "https://second.example/authors")
        second_profile = {"issn": "2345-678X", "title": "Second Journal", "publisher": "Example Publisher",
                          "article_types": ["original_research"], "sources": second_manifest["sources"],
                          "rules": [{"rule_id": "abstract", "kind": "semantic",
                                     "requirement": "Use a structured abstract.",
                                     "quote": "Use a structured abstract.", "source_ref": second_id}]}
        (second / "journal-draft.json").write_text(json.dumps(second_profile), encoding="utf-8")
        (second / "publisher-rules.json").write_bytes((package / "publisher-rules.json").read_bytes())
        (second / "overrides.json").write_text('{"schema_version":1,"overrides":[]}', encoding="utf-8")
        run_command("compose-publisher-rules.cjs", second, "publisher-rules.json", "journal-draft.json", "overrides.json")
        composed = json.loads((second / "composed-profile.json").read_text(encoding="utf-8"))
        self.assertEqual([rule["rule_id"] for rule in composed["rules"]], ["data_statement", "abstract"])
        self.assertEqual(composed["rules"][1]["source_ref"], second_id)


if __name__ == "__main__":
    unittest.main()
