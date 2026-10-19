"""Benchmark tests use invented manuscripts and guidelines only."""

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score import score


ROOT = Path(__file__).resolve().parents[2]
SCORER = Path(__file__).with_name("score.py")
PREPARE = ROOT / "skills" / "manuscript-audit" / "tools" / "prepare_run.py"
HASH_FINDING = ROOT / "skills" / "manuscript-audit" / "validators" / "hash-finding.cjs"


class ComplianceMutationScoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mutation-score-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.guideline = self.root / "guideline.txt"
        self.guideline.write_text(
            "A data availability statement is required. An ethics statement is required.", encoding="utf-8"
        )

    def make_run(self, name, manuscript, raw_rules=(), confirmed=False, freeze="2026-10-19"):
        paper = self.root / f"{name}.md"
        paper.write_text(manuscript, encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(PREPARE), "--manuscript", str(paper),
            "--guideline-text", str(self.guideline),
            "--guideline-url", "https://example.org/guidelines",
            "--retrieved-at", "2026-10-19T09:00:00Z",
            "--data-freeze", freeze,
            "--output-root", str(self.root / "runs")
        ], check=True, capture_output=True, text=True)
        run = Path(result.stdout.strip())
        manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        source_ref = next(iter(manifest["sources"]))
        profile = {
            "issn": "1234-567X", "title": "Synthetic Journal", "publisher": "Synthetic Publisher",
            "article_types": ["original_research"], "sources": manifest["sources"],
            "rules": [
                {"rule_id": "data_availability", "kind": "semantic",
                 "requirement": "Include a data availability statement",
                 "quote": "A data availability statement is required.", "source_ref": source_ref},
                {"rule_id": "ethics_statement", "kind": "semantic",
                 "requirement": "Include an ethics statement",
                 "quote": "An ethics statement is required.", "source_ref": source_ref},
            ],
        }
        (run / "journal-profile.json").write_text(json.dumps(profile), encoding="utf-8")
        (run / "coverage-ledger.json").write_text("[]", encoding="utf-8")
        (run / "raw-findings.json").write_text(json.dumps([
            {"category": "compliance", "rule_id": rule} for rule in raw_rules
        ]), encoding="utf-8")
        findings = []
        if confirmed:
            findings.append({
                "id": "F-1", "verdict": "confirmed", "category": "compliance", "severity": "major",
                "target_issn": profile["issn"], "rule_id": "data_availability",
                "claim": "The synthetic manuscript lacks a data availability statement.",
                "evidence": [
                    {"type": "guideline", "source_ref": source_ref,
                     "quote": "A data availability statement is required."},
                    {"type": "manuscript_absence", "searched": ["manuscript.md"],
                     "patterns": ["data availability"]},
                ],
                "suggested_fix": "Add the statement.",
                "provenance": {
                    "hunter": "agents/hunter.json", "validator": "agents/defense.json",
                    "record_verifier": "agents/verifier.json",
                },
            })
        (run / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        if confirmed:
            (run / "agents").mkdir()
            subject = subprocess.run(["node", str(HASH_FINDING), str(run), "F-1"],
                                     check=True, capture_output=True, text=True).stdout.strip()
            for filename, role, decision in (("hunter", "hunter", "candidate"),
                                             ("defense", "defense", "confirmed"),
                                             ("verifier", "record_verifier", "confirmed")):
                record = {"role": role, "finding_id": "F-1", "decision": decision,
                          "actor_id": f"synthetic-{role}", "subject_sha256": subject,
                          "rationale": "Invented benchmark fixture."}
                (run / "agents" / f"{filename}.json").write_text(json.dumps(record), encoding="utf-8")
        return run

    def test_scores_paired_mutations_and_validation_ablation(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",), confirmed=True)
        other_control = self.make_run("other-control", "# Title\nData availability: on request.\n",
                                      raw_rules=("ethics_statement",))
        cases = [
            {"case_id": "control", "kind": "control", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "control_case_id": "control",
             "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
            {"case_id": "other-control", "kind": "control", "run": str(other_control),
             "raw_findings": str(other_control / "raw-findings.json")},
        ]
        result = score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})
        self.assertEqual(result["mutations"]["raw_recall"], 1)
        self.assertEqual(result["mutations"]["validated_recall"], 1)
        self.assertEqual(result["controls"]["raw_false_positive_rate"], 0.5)
        self.assertEqual(result["controls"]["validated_false_positive_rate"], 0)
        self.assertEqual(result["validation_false_positive_rate_reduction"], 0.5)
        self.assertEqual(len(result["cases"]), 3)
        self.assertNotIn("No statement here", json.dumps(result))
        case_file = self.root / "cases.json"
        case_file.write_text(json.dumps({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases}), encoding="utf-8")
        scored = subprocess.run([sys.executable, str(SCORER), str(case_file)], check=True,
                                capture_output=True, text=True)
        output = Path(scored.stdout.strip())
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["benchmark_manifest_sha256"], hashlib.sha256(case_file.read_bytes()).hexdigest())
        self.assertEqual(saved["mutations"], result["mutations"])
        self.assertNotEqual(subprocess.run([sys.executable, str(SCORER), str(case_file)],
                                           capture_output=True).returncode, 0)

    def test_rejects_a_mutation_already_flagged_in_its_control(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n",
                                raw_rules=("data_availability",))
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",))
        cases = [
            {"case_id": "control", "kind": "control", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "control_case_id": "control",
             "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
        ]
        with self.assertRaisesRegex(ValueError, "already flagged"):
            score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})
        with self.assertRaisesRegex(ValueError, "data_freeze differs"):
            score({"schema_version": 1, "data_freeze": "2026-10-20", "cases": cases})

    def test_validated_rule_must_have_been_a_raw_candidate(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n", confirmed=True)
        cases = [
            {"case_id": "control", "kind": "control", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "control_case_id": "control",
             "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
        ]
        with self.assertRaisesRegex(ValueError, "absent from raw candidates"):
            score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})


if __name__ == "__main__":
    unittest.main()
