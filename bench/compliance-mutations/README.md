# Compliance mutation benchmark

This scorer measures rule-level detection of controlled guideline violations. Manuscripts, guideline caches, agent records, raw candidates, and benchmark case labels stay outside Git. The repository contains benchmark tooling, source identifiers, and invented tests. No empirical M1 result is claimed here.

`public-papers.json` identifies one published JOSS article by DOI and pins its public Markdown source with a SHA-256 hash. The [article page](https://joss.theoj.org/papers/10.21105/joss.09737) states CC BY 4.0 for the published paper. Retrieve the source privately with `python bench/compliance-mutations/fetch_public_papers.py /absolute/private/directory`; the downloader verifies the hash and refuses any destination inside a Git checkout. The paper text itself is not part of this repository.

A [limited heading pilot](heading-pilot-results.json) checked seven verified JOSS heading rules on that published source and one controlled mutation that removed a Markdown heading marker. The raw and independently validated checks each detected the one injected heading violation; neither flagged the one control. Sixty-three other applicable rules were excluded, and full human control labels remain pending. These 1/1 and 0/1 counts are a tooling pilot, not an M1 recall or false-positive estimate for manuscript compliance.

Prepare each control and mutated manuscript as a separate private run with the same `--data-freeze YYYY-MM-DD` value. Use the same journal profile and guideline snapshot for each pair. Build a coverage ledger for the selected article type, actually check every verified applicable compliance rule, and save a current check record for each such unit. Rules marked `needs_validation` remain pending and are listed as exclusions in every scored case; the reported rates apply only to verified rules. Save the phase 3 candidates as `raw-findings.json` directly in each run, with entries containing `category: "compliance"` and a verified applicable `rule_id`. Complete phases 4 and 5 in the run's `findings.json` and independent records. Every confirmed compliance rule must have appeared in the raw candidates. The scorer requires complete coverage of verified rules and runs the full local validator on every run before counting a confirmed finding.

Write a private case manifest outside Git:

```json
{
  "schema_version": 1,
  "data_freeze": "2026-10-19",
  "cases": [
    {
      "case_id": "control-1",
      "kind": "control",
      "article_type": "original_research",
      "run": "/absolute/private/control-run",
      "raw_findings": "/absolute/private/control-run/raw-findings.json"
    },
    {
      "case_id": "mutation-1",
      "kind": "mutation",
      "article_type": "original_research",
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
