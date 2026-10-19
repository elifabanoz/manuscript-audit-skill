# Manuscript Audit Skill

An early-stage skill for checking a manuscript against a selected journal's guidelines.

The current implementation provides [skill instructions](skills/manuscript-audit/SKILL.md), JSON contracts, and local validators for journal profiles, coverage ledgers, findings, and source quotes. It does not yet fetch journal pages, parse manuscripts, or generate reports automatically.

## License

This version is offered under the [PolyForm Noncommercial License 1.0.0](LICENSE). It does not grant commercial-use rights. Earlier commits were released under MIT and retain their original terms.

Required Notice: Copyright (c) 2026 Elif Abanoz

Run the validator tests with Node.js:

```sh
node --test skills/manuscript-audit/validators/validators.test.cjs
```

Before committing, install gitleaks and enable the secret scan hook:

```sh
git config core.hooksPath .githooks
```
