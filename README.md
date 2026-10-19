# Manuscript Audit Skill

An early-stage skill for checking a manuscript against a selected journal's guidelines.

The current implementation provides [skill instructions](skills/manuscript-audit/SKILL.md), JSON contracts, local validators, and a [run preparation tool](skills/manuscript-audit/tools/prepare_run.py) for UTF-8 text or Markdown. It does not yet fetch journal pages, parse PDF or DOCX manuscripts, or generate reports automatically. Private run files are created outside any Git checkout.

To prepare a run, supply a manuscript, a guideline text file, the actual guideline URL, and its known retrieval time:

```sh
python skills/manuscript-audit/tools/prepare_run.py --manuscript /path/to/paper.md --guideline-text /path/to/guideline.txt --guideline-url https://example.org/authors --retrieved-at 2026-10-19T09:00:00Z
node skills/manuscript-audit/validators/validate-run-manifest.cjs /path/to/manuscript-audit/paper/run-1
```

## License

This version is offered under the [PolyForm Noncommercial License 1.0.0](LICENSE). It does not grant commercial-use rights. Earlier commits were released under MIT and retain their original terms.

Required Notice: Copyright (c) 2026 Elif Abanoz

Run the validator tests with Node.js:

```sh
node --test skills/manuscript-audit/validators/validators.test.cjs
python -m unittest discover -s skills/manuscript-audit/tools -p 'test_*.py'
```

Before committing, install gitleaks and enable the secret scan hook:

```sh
git config core.hooksPath .githooks
```
