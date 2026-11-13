# Contributing

This project checks a manuscript against a selected journal's instructions and records evidence for review. Contributions should keep that boundary clear: a valid record is reproducible local evidence, not proof that a journal will accept a manuscript or that a scientific criticism is correct.

## Before changing code

Use Python 3.11 or newer, Node.js, and Pandoc. Install the dependencies in `requirements-pdf.txt` and `requirements-stats.txt` for the full local check. Enable the repository's gitleaks hook with `git config core.hooksPath .githooks` and keep `gitleaks` on `PATH`. Run `python scripts/check_local.py` before committing. The project uses local checks; the GitHub workflow is manual only.

Keep changes focused. Add a test when it checks a behavior or failure mode that could otherwise regress. If you change a role definition in `skills/manuscript-audit/roles/roles.yaml`, regenerate the checked-in host files with `python skills/manuscript-audit/roles/gen_agents.py --write --output-root .` and run the local check. The generator refuses to replace modified files without `--force`; inspect differences before using that flag.

## Journal profiles

Add a fixture to `journal-profiles/<ISSN>.json` only when the journal identity, article type, and current official instruction page have been checked. Use a short exact quote for each rule, with its source URL, retrieval time, and SHA-256. Use IDs from `COMPLIANCE-CLASSES.md` when the requirement matches; keep ambiguous or conditional rules as `needs_validation`. Check article-type applicability explicitly. Keep the full page snapshot in a private run outside Git and verify that the quoted text occurs in that snapshot before submitting the fixture. A URL and hash alone do not prove that a page is still current or authoritative.

For a targeted run, independent profile drafts are compared with `compare-profiles.cjs`; disagreements require source-backed adjudication. A public fixture is a dated starting point and does not replace that check. Do not copy entire publisher pages or long passages into a fixture.

## Review classes and prompts

`REVIEW-CLASSES.md` describes the general review lenses. A new class needs a distinct question, examples of evidence and counterchecks, and a clear boundary from journal compliance. A field-specific class should say where it applies; it should not make every section of every manuscript an automatic review unit. Keep the role contracts in `roles/REVIEW-ROLES.md` and `roles/roles.yaml` consistent when their responsibilities change.

Prompt or validator changes can change findings even when unit tests still pass. Run the local gate and any private, permissioned benchmark cases available to you. Describe the exact cases checked and the remaining uncertainty. Do not report recall, false-positive rates, review alignment, or venue ranking as empirical performance without the required human labels and leakage checks.

## Repository boundaries

Keep unpublished manuscripts, other people's PDFs, peer reports, guideline page copies, `run-*/` directories, raw benchmark data, model weights, and secrets outside Git. Public benchmark files may contain identifiers, hashes, URLs, download scripts, and aggregate results only when their provenance and release rights are checked. Do not send private manuscript text to external discovery APIs or a new model provider without the author's approval.

Write code, comments, documentation, commit messages, and issue text in English. Check `git status` and the staged diff before committing. The repository is licensed under the [PolyForm Noncommercial License](LICENSE); contributions need to be compatible with that license and preserve its required notice.
