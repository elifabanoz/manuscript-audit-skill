"""Benchmark tests use invented manuscripts and guidelines only."""

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from score import checked_scope_record, check_control_deterministic, score, sha256
from prepare_label_review import make_packet
from prepare_scope_review import prepare as prepare_scope_review


ROOT = Path(__file__).resolve().parents[2]
SCORER = Path(__file__).with_name("score.py")
PREPARE = ROOT / "skills" / "manuscript-audit" / "tools" / "prepare_run.py"
HASH_FINDING = ROOT / "skills" / "manuscript-audit" / "validators" / "hash-finding.cjs"
HASH_COVERAGE = ROOT / "skills" / "manuscript-audit" / "validators" / "hash-coverage.cjs"
BUILD_COVERAGE = ROOT / "skills" / "manuscript-audit" / "tools" / "build_coverage.py"
CHECK_DETERMINISTIC = ROOT / "skills" / "manuscript-audit" / "tools" / "check_deterministic.py"


class ComplianceMutationScoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mutation-score-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.guideline = self.root / "guideline.txt"
        self.guideline.write_text(
            "A data availability statement is required. An ethics statement is required.", encoding="utf-8"
        )

    def make_run(self, name, manuscript, raw_rules=(), confirmed=False, freeze="2026-10-19", unresolved_ethics=False):
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
                 "quote": "An ethics statement is required.", "source_ref": source_ref,
                 **({"status": "needs_validation"} if unresolved_ethics else {})},
            ],
        }
        (run / "journal-profile.json").write_text(json.dumps(profile), encoding="utf-8")
        subprocess.run([sys.executable, str(BUILD_COVERAGE), str(run),
                        "--article-type", "original_research"], check=True, capture_output=True)
        ledger_path = run / "coverage-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        (run / "agents").mkdir()
        for unit in ledger:
            if unresolved_ethics and unit["rule_id"] == "ethics_statement":
                continue
            subject = subprocess.run(["node", str(HASH_COVERAGE), str(run), unit["unit_id"]],
                                     check=True, capture_output=True, text=True).stdout.strip()
            record_path = f"agents/coverage-{unit['unit_id']}.json"
            (run / record_path).write_text(json.dumps({
                "unit_id": unit["unit_id"], "decision": "checked",
                "actor_id": f"synthetic-coverage-{unit['unit_id']}",
                "subject_sha256": subject, "rationale": "Invented fixture checked against both rules."
            }), encoding="utf-8")
            unit["status"] = "covered"
            unit["checked_by"] = [record_path]
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
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

    def test_scope_record_requires_every_rule_and_pinned_inputs(self):
        run = self.make_run("scope-control", "# Title\nData availability: on request.\n",
                            unresolved_ethics=True)
        manifest_path = run / "run-manifest.json"
        profile_path = run / "journal-profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        applicable = {rule["rule_id"]: rule for rule in profile["rules"]}
        path = run / "scope-review.json"
        record = {
            "schema_version": 1, "manifest_sha256": sha256(manifest_path),
            "profile_sha256": sha256(profile_path), "article_type": "original_research",
            "rules": [
                {"rule_id": "data_availability", "decision": "score",
                 "reason": "The submitted manuscript can show the required statement."},
                {"rule_id": "ethics_statement", "decision": "exclude",
                 "reason": "The source interpretation still needs independent validation."},
            ],
        }
        path.write_text(json.dumps(record), encoding="utf-8")
        scored, excluded, digest, reasons = checked_scope_record(
            str(path), run, manifest_path, profile_path, "original_research", applicable, "control")
        self.assertEqual(scored, {"data_availability"})
        self.assertEqual(excluded, {"ethics_statement"})
        self.assertEqual(digest, sha256(path))
        self.assertIn("source interpretation", reasons["ethics_statement"])
        changed = json.loads(path.read_text(encoding="utf-8"))
        changed["rules"][1]["decision"] = "score"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unresolved source rule"):
            checked_scope_record(str(path), run, manifest_path, profile_path,
                                 "original_research", applicable, "control")
        changed["rules"] = changed["rules"][:1]
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "every applicable rule"):
            checked_scope_record(str(path), run, manifest_path, profile_path,
                                 "original_research", applicable, "control")
        changed["profile_sha256"] = "0" * 64
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from the scored inputs"):
            checked_scope_record(str(path), run, manifest_path, profile_path,
                                 "original_research", applicable, "control")

    def test_scope_template_is_unfilled_and_bound_to_private_run(self):
        run = self.make_run("scope-template", "# Title\nData availability: on request.\n")
        output = prepare_scope_review(run, "original_research")
        template = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(template["manifest_sha256"], sha256(run / "run-manifest.json"))
        self.assertEqual(template["profile_sha256"], sha256(run / "journal-profile.json"))
        self.assertEqual({item["rule_id"] for item in template["rules"]},
                         {"data_availability", "ethics_statement"})
        self.assertTrue(all(item["decision"] is None and item["reason"] == ""
                            for item in template["rules"]))
        with self.assertRaises(FileExistsError):
            prepare_scope_review(run, "original_research")

    def test_scores_paired_mutations_and_validation_ablation(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",), confirmed=True)
        other_control = self.make_run("other-control", "# Title\nData availability: on request.\n",
                                      raw_rules=("ethics_statement",))
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research", "control_case_id": "control",
             "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
            {"case_id": "other-control", "kind": "control", "article_type": "original_research", "run": str(other_control),
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

    def test_version_two_reports_each_class_against_its_paired_controls(self):
        structural_control = self.make_run("structural-control", "# Title\nData availability: on request.\n")
        structural_mutation = self.make_run("structural-mutation", "# Title\nNo statement here.\n",
                                            raw_rules=("data_availability",), confirmed=True)
        semantic_control = self.make_run("semantic-control", "# Title\nData availability: on request.\n",
                                         raw_rules=("ethics_statement",))
        semantic_mutation = self.make_run("semantic-mutation", "# Title\nStatement removed.\n",
                                          raw_rules=("data_availability",))
        cases = [
            {"case_id": "structural-control", "kind": "control", "article_type": "original_research",
             "run": str(structural_control), "raw_findings": str(structural_control / "raw-findings.json")},
            {"case_id": "structural-mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "structural-control", "expected_rule_id": "data_availability",
             "mutation_class": "structural", "run": str(structural_mutation),
             "raw_findings": str(structural_mutation / "raw-findings.json")},
            {"case_id": "semantic-control", "kind": "control", "article_type": "original_research",
             "run": str(semantic_control), "raw_findings": str(semantic_control / "raw-findings.json")},
            {"case_id": "semantic-mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "semantic-control", "expected_rule_id": "data_availability",
             "mutation_class": "semantic", "run": str(semantic_mutation),
             "raw_findings": str(semantic_mutation / "raw-findings.json")},
        ]
        benchmark = {"schema_version": 2, "data_freeze": "2026-10-19", "cases": cases}
        result = score(benchmark)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["mutations"]["raw_recall"], 1)
        self.assertEqual(result["mutations"]["validated_recall"], 0.5)
        self.assertEqual(result["by_mutation_class"]["structural"]["mutations"]["validated_recall"], 1)
        self.assertEqual(result["by_mutation_class"]["semantic"]["mutations"]["validated_recall"], 0)
        self.assertEqual(result["by_mutation_class"]["structural"]["paired_controls"]["raw_false_positive_rate"], 0)
        self.assertEqual(result["by_mutation_class"]["semantic"]["paired_controls"]["raw_false_positive_rate"], 1)
        self.assertEqual(result["by_mutation_class"]["semantic"]["paired_controls"]["validated_false_positive_rate"], 0)
        self.assertEqual(result["cases"][1]["mutation_class"], "structural")
        self.assertNotIn("Statement removed", json.dumps(result))

        missing_class = dict(cases[1])
        del missing_class["mutation_class"]
        with self.assertRaisesRegex(ValueError, "mutation_class"):
            score({**benchmark, "cases": [cases[0], missing_class, *cases[2:]]})
        unknown_class = {**cases[1], "mutation_class": "other"}
        with self.assertRaisesRegex(ValueError, "mutation_class must be"):
            score({**benchmark, "cases": [cases[0], unknown_class, *cases[2:]]})
        non_string_class = {**cases[1], "mutation_class": ["semantic"]}
        with self.assertRaisesRegex(ValueError, "mutation_class must be"):
            score({**benchmark, "cases": [cases[0], non_string_class, *cases[2:]]})

    def test_version_three_binds_proposal_and_mutation_run(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",), confirmed=True)
        control_manifest = control / "run-manifest.json"
        mutant_manifest = json.loads((mutation / "run-manifest.json").read_text(encoding="utf-8"))
        proposal_dir = self.root / "proposal"
        proposal_dir.mkdir()
        proposal_file = proposal_dir / "mutation.md"
        proposal_file.write_bytes((mutation / "manuscript.md").read_bytes())
        record = {
            "schema_version": 1, "status": "unreviewed_proposal",
            "control_run": str(control), "article_type": "original_research",
            "control_manifest_sha256": hashlib.sha256(control_manifest.read_bytes()).hexdigest(),
            "control_manuscript_sha256": json.loads(control_manifest.read_text(encoding="utf-8"))["manuscript"]["sha256"],
            "profile_sha256": hashlib.sha256((control / "journal-profile.json").read_bytes()).hexdigest(),
            "rule_id": "data_availability", "mutation_class_proposed": "semantic",
            "file": "mutation.md", "manuscript_sha256": mutant_manifest["manuscript"]["sha256"],
        }
        record_path = proposal_dir / "proposals.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research",
             "run": str(control), "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "control", "expected_rule_id": "data_availability",
             "mutation_class": "semantic", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json"),
             "proposal_record": str(record_path), "proposal_file": "mutation.md"},
        ]
        benchmark = {"schema_version": 3, "data_freeze": "2026-10-19", "cases": cases}
        result = score(benchmark)
        self.assertEqual(result["cases"][1]["proposal_record_sha256"],
                         hashlib.sha256(record_path.read_bytes()).hexdigest())
        self.assertEqual(result["by_mutation_class"]["semantic"]["mutations"]["validated_recall"], 1)
        plural = {key: value for key, value in record.items()
                  if key not in {"rule_id", "mutation_class_proposed", "file", "manuscript_sha256"}}
        plural["status"] = "unreviewed_proposals"
        plural["mutations"] = [{key: record[key] for key in
                                ("rule_id", "mutation_class_proposed", "file", "manuscript_sha256")}]
        record_path.write_text(json.dumps(plural), encoding="utf-8")
        self.assertEqual(score(benchmark)["cases"][1]["proposal_record_sha256"],
                         hashlib.sha256(record_path.read_bytes()).hexdigest())
        record_path.write_text(json.dumps(record), encoding="utf-8")
        proposal_file.write_text("Different text", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "proposal manuscript differs"):
            score(benchmark)
        proposal_file.write_bytes((mutation / "manuscript.md").read_bytes())
        record["control_manifest_sha256"] = "0" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "proposal does not match"):
            score(benchmark)

    def test_version_four_audits_raw_quotes_without_publishing_them(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",), confirmed=True)
        source_ref = next(iter(json.loads((mutation / "run-manifest.json").read_text(encoding="utf-8"))["sources"]))
        candidates = [{"category": "compliance", "rule_id": "data_availability", "evidence": [
            {"type": "guideline", "source_ref": source_ref,
             "quote": "A data availability statement is required."},
            {"type": "guideline", "source_ref": source_ref,
             "quote": "Invented requirement absent from the guideline."},
            {"type": "manuscript_quote", "path": "manuscript.md", "line_id": "L2",
             "quote": "Invented manuscript sentence."},
        ]}]
        raw_path = mutation / "raw-findings.json"
        raw_path.write_text(json.dumps(candidates), encoding="utf-8")
        proposal_dir = self.root / "proposal"
        proposal_dir.mkdir()
        (proposal_dir / "mutation.md").write_bytes((mutation / "manuscript.md").read_bytes())
        control_manifest = json.loads((control / "run-manifest.json").read_text(encoding="utf-8"))
        mutation_manifest = json.loads((mutation / "run-manifest.json").read_text(encoding="utf-8"))
        proposal = {
            "schema_version": 1, "status": "unreviewed_proposal",
            "control_run": str(control), "article_type": "original_research",
            "control_manifest_sha256": hashlib.sha256((control / "run-manifest.json").read_bytes()).hexdigest(),
            "control_manuscript_sha256": control_manifest["manuscript"]["sha256"],
            "profile_sha256": hashlib.sha256((control / "journal-profile.json").read_bytes()).hexdigest(),
            "rule_id": "data_availability", "mutation_class_proposed": "semantic",
            "file": "mutation.md", "manuscript_sha256": mutation_manifest["manuscript"]["sha256"],
        }
        record_path = proposal_dir / "proposals.json"
        record_path.write_text(json.dumps(proposal), encoding="utf-8")
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research",
             "run": str(control), "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "control", "expected_rule_id": "data_availability",
             "mutation_class": "semantic", "run": str(mutation), "raw_findings": str(raw_path),
             "proposal_record": str(record_path), "proposal_file": "mutation.md"},
        ]
        benchmark = {"schema_version": 4, "data_freeze": "2026-10-19", "cases": cases}
        result = score(benchmark)
        self.assertEqual(result["quote_audit"], {
            "raw_quotes": 3, "raw_unsupported_quotes": 2,
            "raw_unsupported_quote_rate": 2 / 3,
            "validated_confirmed_quotes": 1, "validated_unsupported_quotes": 0,
        })
        self.assertEqual(result["cases"][1]["quote_audit"]["raw_unsupported_quotes"], 2)
        self.assertNotIn("Invented requirement", json.dumps(result))
        candidates[0]["evidence"] = []
        raw_path.write_text(json.dumps(candidates), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "needs quoted evidence"):
            score(benchmark)

    def test_version_five_excludes_verified_rule_needing_external_evidence(self):
        control = self.make_run("control-v5", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation-v5", "# Title\nNo statement here.\n")
        for run in (control, mutation):
            ledger_path = run / "coverage-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            for unit in ledger:
                if unit["rule_id"] == "ethics_statement":
                    unit["status"] = "pending"
                    unit["checked_by"] = []
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            scope = {
                "schema_version": 1,
                "manifest_sha256": sha256(run / "run-manifest.json"),
                "profile_sha256": sha256(run / "journal-profile.json"),
                "article_type": "original_research",
                "rules": [
                    {"rule_id": "data_availability", "decision": "score",
                     "reason": "The manuscript can show the data availability statement."},
                    {"rule_id": "ethics_statement", "decision": "exclude",
                     "reason": "This test supplied no independent ethics approval evidence."},
                ],
            }
            (run / "scope-review.json").write_text(json.dumps(scope), encoding="utf-8")
        proposal_dir = self.root / "proposal-v5"
        proposal_dir.mkdir()
        (proposal_dir / "mutation.md").write_bytes((mutation / "manuscript.md").read_bytes())
        control_manifest = json.loads((control / "run-manifest.json").read_text(encoding="utf-8"))
        mutation_manifest = json.loads((mutation / "run-manifest.json").read_text(encoding="utf-8"))
        proposal_path = proposal_dir / "proposals.json"
        proposal_path.write_text(json.dumps({
            "schema_version": 1, "status": "unreviewed_proposal",
            "control_run": str(control), "article_type": "original_research",
            "control_manifest_sha256": sha256(control / "run-manifest.json"),
            "control_manuscript_sha256": control_manifest["manuscript"]["sha256"],
            "profile_sha256": sha256(control / "journal-profile.json"),
            "rule_id": "data_availability", "mutation_class_proposed": "semantic",
            "file": "mutation.md", "manuscript_sha256": mutation_manifest["manuscript"]["sha256"],
        }), encoding="utf-8")
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research",
             "run": str(control), "raw_findings": str(control / "raw-findings.json"),
             "scope_record": str(control / "scope-review.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "control", "expected_rule_id": "data_availability",
             "mutation_class": "semantic", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json"),
             "proposal_record": str(proposal_path), "proposal_file": "mutation.md",
             "scope_record": str(mutation / "scope-review.json")},
        ]
        result = score({"schema_version": 5, "data_freeze": "2026-10-19", "cases": cases})
        self.assertEqual(result["cases"][0]["scored_rule_ids"], ["data_availability"])
        self.assertEqual(result["cases"][0]["excluded_rule_ids"], ["ethics_statement"])
        self.assertEqual(result["cases"][1]["scored_rule_ids"], ["data_availability"])
        self.assertEqual(result["quote_audit"]["raw_quotes"], 0)
        self.assertEqual(result["mutations"]["validated_recall"], 0)
        for run in (control, mutation):
            ledger_path = run / "coverage-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            excluded_unit = next(unit for unit in ledger if unit["rule_id"] == "ethics_statement")
            excluded_unit["status"] = "covered"
            excluded_unit["checked_by"] = [f"agents/coverage-{excluded_unit['unit_id']}.json"]
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        reviewed_exclusion = score({"schema_version": 5, "data_freeze": "2026-10-19", "cases": cases})
        self.assertEqual(reviewed_exclusion["cases"][0]["excluded_rule_ids"], ["ethics_statement"])
        self.assertEqual(reviewed_exclusion["mutations"]["validated_recall"], 0)
        changed = json.loads((control / "scope-review.json").read_text(encoding="utf-8"))
        changed["manifest_sha256"] = "0" * 64
        (control / "scope-review.json").write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "scope record differs"):
            score({"schema_version": 5, "data_freeze": "2026-10-19", "cases": cases})

    def test_rejects_a_mutation_already_flagged_in_its_control(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n",
                                raw_rules=("data_availability",))
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",))
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research", "control_case_id": "control",
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
            {"case_id": "control", "kind": "control", "article_type": "original_research", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research", "control_case_id": "control",
             "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
        ]
        with self.assertRaisesRegex(ValueError, "absent from raw candidates"):
            score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})

    def test_pending_compliance_rule_cannot_lower_false_positive_rate(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",))
        ledger_path = control / "coverage-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger[0]["status"] = "pending"
        ledger[0]["checked_by"] = []
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research",
             "run": str(control), "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "control", "expected_rule_id": "data_availability",
             "run": str(mutation), "raw_findings": str(mutation / "raw-findings.json")},
        ]
        with self.assertRaisesRegex(ValueError, "coverage is incomplete"):
            score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})

    def test_unresolved_rules_are_excluded_and_disclosed_without_weakening_verified_coverage(self):
        control = self.make_run("control", "# Title\nData availability: on request.\n", unresolved_ethics=True)
        mutation = self.make_run("mutation", "# Title\nNo statement here.\n",
                                 raw_rules=("data_availability",), confirmed=True, unresolved_ethics=True)
        cases = [
            {"case_id": "control", "kind": "control", "article_type": "original_research", "run": str(control),
             "raw_findings": str(control / "raw-findings.json")},
            {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
             "control_case_id": "control", "expected_rule_id": "data_availability", "run": str(mutation),
             "raw_findings": str(mutation / "raw-findings.json")},
        ]
        result = score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})
        self.assertEqual(result["mutations"]["validated_recall"], 1)
        self.assertEqual(result["cases"][0]["scored_rule_ids"], ["data_availability"])
        self.assertEqual(result["cases"][0]["excluded_rule_ids"], ["ethics_statement"])
        (control / "raw-findings.json").write_text(json.dumps([
            {"category": "compliance", "rule_id": "ethics_statement"}
        ]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "known compliance rule"):
            score({"schema_version": 1, "data_freeze": "2026-10-19", "cases": cases})

    def test_private_review_packet_leaves_labels_blank_and_binds_source_hashes(self):
        control = self.make_run("control", "# Secret control text\nData availability: on request.\n")
        mutation = self.make_run("mutation", "# Secret mutation text\nNo statement here.\n")
        cases_path = self.root / "cases.json"
        cases_path.write_text(json.dumps({
            "schema_version": 2, "data_freeze": "2026-10-19", "cases": [
                {"case_id": "control", "kind": "control", "article_type": "original_research",
                 "run": str(control), "raw_findings": str(control / "raw-findings.json")},
                {"case_id": "mutation", "kind": "mutation", "article_type": "original_research",
                 "control_case_id": "control", "expected_rule_id": "data_availability",
                 "mutation_class": "semantic", "run": str(mutation),
                 "raw_findings": str(mutation / "raw-findings.json")},
            ],
        }), encoding="utf-8")
        packet = make_packet(cases_path)
        control_manifest = json.loads((control / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertIn(control_manifest["manuscript"]["sha256"], packet)
        self.assertIn('Submitted filename: "control.md"', packet)
        self.assertIn('Submitted filename: "mutation.md"', packet)
        self.assertIn(next(iter(control_manifest["sources"].values()))["sha256"], packet)
        self.assertIn("A data availability statement is required.", packet)
        self.assertIn("- [ ] Compliant", packet)
        self.assertIn("Proposed mutation class (verify): `semantic`", packet)
        self.assertNotIn("Secret control text", packet)
        self.assertNotIn("Secret mutation text", packet)

    def test_control_rejects_a_triggered_verified_deterministic_rule(self):
        def checked_run(name, manuscript):
            run = self.make_run(name, manuscript)
            profile_path = run / "journal-profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["rules"].append({
                "rule_id": "required_heading", "kind": "deterministic",
                "check": {"metric": "heading_count", "heading": "Data Availability", "op": ">=", "value": 1},
                "quote": "A data availability statement is required.",
                "source_ref": next(iter(profile["sources"])),
            })
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            subprocess.run([sys.executable, str(CHECK_DETERMINISTIC), str(run),
                            "--article-type", "original_research"], check=True, capture_output=True)
            manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
            return run, manifest, profile

        run, manifest, profile = checked_run("control", "# Title\nNo matching heading.\n")
        path = run / "deterministic-checks.json"
        with self.assertRaisesRegex(ValueError, "unmet deterministic rules: required_heading"):
            check_control_deterministic(run, manifest, profile, "original_research", {"required_heading"}, "control")
        report = json.loads(path.read_text(encoding="utf-8"))
        report["checks"][0]["status"] = "met"
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differ from recomputed measurements"):
            check_control_deterministic(run, manifest, profile, "original_research", {"required_heading"}, "control")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
        valid_run, valid_manifest, valid_profile = checked_run("valid-control", "# Title\n# Data Availability\n")
        check_control_deterministic(valid_run, valid_manifest, valid_profile,
                                    "original_research", {"required_heading"}, "valid-control")
        report["manuscript_sha256"] = "b" * 64
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "do not match the control inputs"):
            check_control_deterministic(run, manifest, profile, "original_research", {"required_heading"}, "control")


if __name__ == "__main__":
    unittest.main()
