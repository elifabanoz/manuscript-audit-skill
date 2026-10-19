# Compliance mutation benchmark

This scorer measures rule-level detection of controlled guideline violations. Manuscripts, guideline caches, agent records, raw candidates, and benchmark case labels stay outside Git. The repository contains benchmark tooling, source identifiers, and invented tests. No empirical M1 result is claimed here.

`public-papers.json` identifies two published JOSS articles ([09737](https://joss.theoj.org/papers/10.21105/joss.09737), [06825](https://joss.theoj.org/papers/10.21105/joss.06825)) and one [JOSE article](https://jose.theoj.org/papers/10.21105/jose.00059) by DOI and pins their public Markdown sources with SHA-256 hashes. The article pages state CC BY 4.0 for the published papers. Retrieve the sources privately with `python bench/compliance-mutations/fetch_public_papers.py /absolute/private/directory`; the downloader verifies each hash and refuses any destination inside a Git checkout. The paper text itself is not part of this repository. The second JOSS and JOSE sources have been downloaded and hash-checked, but they have not yet been labeled or scored in a compliance run.

A [limited heading suite](heading-suite-results.json) checked seven verified JOSS heading rules on that published source and seven controlled mutations, each removing one Markdown heading marker while retaining its text and section body. The raw and independently validated checks each detected 7/7 injected heading violations; neither flagged the one structural control. The measured validation-stage false-positive-rate reduction was zero in this suite. Sixty-three other applicable rules were excluded, and human control labels remain pending. These counts cover one paper and a single mutation type, so they are not an M1 recall or false-positive estimate for full manuscript compliance.

Prepare each control and mutated manuscript as a separate private run with the same `--data-freeze YYYY-MM-DD` value. Use the same journal profile and guideline snapshot for each pair. Build a coverage ledger for the selected article type, actually check every verified applicable compliance rule, and save a current check record for each such unit. Rules marked `needs_validation` remain pending and are listed as exclusions in every scored case; the reported rates apply only to verified rules. Save the phase 3 candidates as `raw-findings.json` directly in each run, with entries containing `category: "compliance"` and a verified applicable `rule_id`. Complete phases 4 and 5 in the run's `findings.json` and independent records. Every confirmed compliance rule must have appeared in the raw candidates. The scorer requires complete coverage of verified rules and runs the full local validator on every run before counting a confirmed finding.

Write a private case manifest outside Git:

```json
{
  "schema_version": 2,
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
      "mutation_class": "semantic",
      "run": "/absolute/private/mutation-run",
      "raw_findings": "/absolute/private/mutation-run/raw-findings.json"
    }
  ]
}
```

Run `python bench/compliance-mutations/score.py /absolute/private/cases.json`. It creates `compliance-mutation-scores.json` beside that manifest and refuses to overwrite it. Paths on Windows must be absolute Windows paths with JSON backslashes escaped. The output contains case IDs, rule IDs, input hashes, recall on injected violations, case-level false-positive rates on controls, and the difference between raw and validated false-positive rates. A mutation is refused when its target rule was already flagged in its paired control; investigate that baseline before scoring. The scorer also rejects changed journal profiles, source snapshots, and freeze dates.

Version 2 requires each mutation to name its class: `structural` for document structure, `semantic` for the meaning or presence of a required statement, or `numerical_format` for limits and prescribed formats. The result reports recall and paired-control false-positive rates separately for each class. A control used by several mutations counts once within each class that uses it; class rates can therefore share a control and must not be treated as independent samples. The scorer accepts existing version 1 manifests without class rates. It checks the declared class and run records, but cannot establish that a mutation truly changed only the intended rule.

The control label needs human review: publication in a journal does not prove the paper complies with every current instruction. A detected mutation does not establish that all other findings are correct. Keep the mutation recipe, license check, source identifiers, and independent adjudication notes with the private case manifest. Publish aggregate results only after those labels and the full case set have been checked; the output hashes allow the result to be tied to its exact private inputs without redistributing manuscripts.

Run `python bench/compliance-mutations/prepare_label_review.py /absolute/private/cases.json` to create an unfilled `human-label-review.md` beside the private manifest. The worksheet lists each scored control rule, each mutation target, short source excerpts, source URLs, private file paths, and SHA-256 hashes. A reviewer checks the manuscripts and source snapshots before reading agent findings, records a decision and evidence location for each rule, and checks whether each mutation changed only the intended property. A second reviewer records disagreements and adjudication. The generator validates the runs and refuses to write inside Git or overwrite an existing worksheet; it does not produce human labels or certify that review occurred.
