# Compliance mutation benchmark

This scorer measures rule-level detection of controlled guideline violations. Manuscripts, guideline caches, agent records, raw candidates, and benchmark case labels stay outside Git. The repository contains only the scorer and invented tests. No empirical M1 result is claimed here.

Prepare each control and mutated manuscript as a separate private run with the same `--data-freeze YYYY-MM-DD` value. Use the same journal profile and guideline snapshot for each pair. Save the phase 3 candidates as `raw-findings.json` directly in each run, with entries containing `category: "compliance"` and a known `rule_id`. Complete phases 4 and 5 in the run's `findings.json` and independent records. Every confirmed compliance rule must have appeared in the raw candidates. The scorer runs the full local validator on every run before counting a confirmed finding.

Write a private case manifest outside Git:

```json
{
  "schema_version": 1,
  "data_freeze": "2026-10-19",
  "cases": [
    {
      "case_id": "control-1",
      "kind": "control",
      "run": "/absolute/private/control-run",
      "raw_findings": "/absolute/private/control-run/raw-findings.json"
    },
    {
      "case_id": "mutation-1",
      "kind": "mutation",
      "control_case_id": "control-1",
      "expected_rule_id": "data_availability",
      "run": "/absolute/private/mutation-run",
      "raw_findings": "/absolute/private/mutation-run/raw-findings.json"
    }
  ]
}
```

Run `python bench/compliance-mutations/score.py /absolute/private/cases.json`. It creates `compliance-mutation-scores.json` beside that manifest and refuses to overwrite it. Paths on Windows must be absolute Windows paths with JSON backslashes escaped. The output contains case IDs, rule IDs, input hashes, recall on injected violations, case-level false-positive rates on controls, and the difference between raw and validated false-positive rates. A mutation is refused when its target rule was already flagged in its paired control; investigate that baseline before scoring. The scorer also rejects changed journal profiles, source snapshots, and freeze dates.

The control label needs human review: publication in a journal does not prove the paper complies with every current instruction. A detected mutation does not establish that all other findings are correct. Keep the mutation recipe, license check, source identifiers, and independent adjudication notes with the private case manifest. Publish aggregate results only after those labels and the full case set have been checked; the output hashes allow the result to be tied to its exact private inputs without redistributing manuscripts.
