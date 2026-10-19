# Manuscript Audit Skill

An early-stage skill for checking a manuscript against a selected journal's guidelines.

The current implementation provides [skill instructions](skills/manuscript-audit/SKILL.md), JSON contracts, local validators, private report rendering, and a [run preparation tool](skills/manuscript-audit/tools/prepare_run.py) for UTF-8 text or Markdown. It does not yet fetch journal guideline pages or parse PDF or DOCX manuscripts. Private run files are created outside any Git checkout.

If a journal has not been chosen, use an explicit, nonconfidential topic phrase to discover candidates from [OpenAlex](https://help.openalex.org/api/llm-quick-reference/) metadata:

```sh
python skills/manuscript-audit/tools/discover_journals.py --query "synthetic topic"
```

This writes a private `candidates.json` under `~/manuscript-audit/discovery/discovery-N/`. It contains source IDs, ISSN-L values, and matching work IDs; it does not copy article text. Set `OPENALEX_API_KEY` in the environment if you have a key. The author selects the target journal; the matching-work count is only a discovery signal.

To prepare a run, supply a manuscript, a guideline text file, the actual guideline URL, and its known retrieval time:

```sh
python skills/manuscript-audit/tools/prepare_run.py --manuscript /path/to/paper.md --guideline-text /path/to/guideline.txt --guideline-url https://example.org/authors --retrieved-at 2026-10-19T09:00:00Z
node skills/manuscript-audit/validators/validate-run-manifest.cjs /path/to/manuscript-audit/paper/run-1
```

After adding `journal-profile.json`, `coverage-ledger.json`, `findings.json`, and the referenced agent records to that run, check the record and local evidence chain with `node skills/manuscript-audit/validators/validate-run.cjs <run-directory>`. Use `--supplementary <file.md>` during preparation for each supplied text supplement; an absence claim must search all of them. The validator checks records and hashes; it cannot prove that reviewers worked independently.

For a journal profile with Markdown abstract or main-text word limits, `python skills/manuscript-audit/tools/check_deterministic.py <run-directory> --article-type original_research --main-start Introduction --main-end References` records provisional measurements in the private run. Unclear section boundaries or unsupported formatting stay unresolved.

Create a pending compliance ledger from the selected article type with `python skills/manuscript-audit/tools/build_coverage.py <run-directory> --article-type original_research`. The tool refuses to overwrite an existing ledger; each unit carries a fingerprint of the supplied manuscript files.

Once the profile, ledger, findings, and agent records pass `validate-run.cjs`, generate private summaries with `node skills/manuscript-audit/validators/render-report.cjs <run-directory>`. The report lists confirmed records and pending coverage; unresolved records go to `NEEDS-VALIDATION.md`.

## License

This version is offered under the [PolyForm Noncommercial License 1.0.0](LICENSE). It does not grant commercial-use rights. Earlier commits were released under MIT and retain their original terms.

Required Notice: Copyright (c) 2026 Elif Abanoz

Run the local tests with Node.js and Python 3.11 or newer:

```sh
node --test skills/manuscript-audit/validators/validators.test.cjs
python -m unittest discover -s skills/manuscript-audit/tools -p 'test_*.py'
```

Before committing, install gitleaks and enable the secret scan hook:

```sh
git config core.hooksPath .githooks
```
