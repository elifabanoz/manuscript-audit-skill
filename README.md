# Manuscript Audit Skill

An early-stage skill for checking a manuscript against a selected journal's guidelines. See the [architecture](docs/ARCHITECTURE.md) for the evidence flow and current limits.

The current implementation provides [skill instructions](skills/manuscript-audit/SKILL.md), [review role contracts](skills/manuscript-audit/roles/REVIEW-ROLES.md), JSON contracts, local validators, private report rendering, and a [run preparation tool](skills/manuscript-audit/tools/prepare_run.py) for UTF-8 text or Markdown. It can fetch public HTML or plain-text guideline pages and extract DOCX or text-based PDF content locally. Private run files are created outside any Git checkout.

For a DOCX manuscript, run `python skills/manuscript-audit/tools/convert_docx.py --input /path/to/paper.docx`. Inspect the private `extraction.json` warnings and `manuscript.md` before preparing the run. Pass both files to `prepare_run.py` with `--manuscript <manuscript.md> --extraction-record <extraction.json>` to retain the original DOCX hash in the private run. Tables are flattened; figures, notes, and some Word fields require separate inspection. Documents with tracked changes are rejected until those changes are resolved.

For a text-based PDF, install the optional dependency with `python -m pip install -r requirements-pdf.txt`, then run `python skills/manuscript-audit/tools/convert_pdf.py --input /path/to/paper.pdf`. Inspect every page of the original PDF against the private `manuscript.txt` before preparing a run, and pass `--extraction-record <extraction.json>` alongside `--manuscript <manuscript.txt>`. The extractor marks page boundaries, flags pages with no text, and rejects PDFs with no extractable text. It does not perform OCR or establish reliable heading, table, equation, or figure structure; Markdown-only measurements remain unresolved for PDF text.

If a journal has not been chosen, use an explicit, nonconfidential topic phrase to discover candidates from [OpenAlex](https://help.openalex.org/api/llm-quick-reference/) metadata:

```sh
python skills/manuscript-audit/tools/discover_journals.py --query "synthetic topic" --field-id 17 --published-after 2024-01-01
```

For a known field, add its OpenAlex field ID, such as `--field-id 17` for Computer Science. `--published-after` limits matching articles to a chosen publication window; it does not prove that a journal is still accepting submissions. By default, discovery groups matching articles by their primary journal, checks source type and ISSN-L against OpenAlex source records, and writes up to 50 candidates to a private `candidates.json` under `~/manuscript-audit/discovery/discovery-N/`. Change the cap with `--limit`. The output contains source metadata and aggregate counts, not article text. Its topic share divides matching articles by the source's all-time work count, so it is an approximate discovery signal, not a publication probability. Only the first page of OpenAlex source groups is considered; coverage is incomplete. Set `OPENALEX_API_KEY` in the environment if you have a key. The author selects the target journal after checking its scope and current submission status. A candidate's homepage URL is metadata, not a verified guideline source; fetch and verify the journal's official author instructions before building a profile. If official instructions cannot be verified, keep the profile partial. The earlier work-sample method remains available with `--strategy sample`.

Add `--doaj-subject "Computer Science"` when a separate, nonconfidential [DOAJ](https://doaj.org/docs/widgets/) subject phrase is available. This searches DOAJ's journal classifications and considers its first 50 results. Matching ISSNs add a second discovery source to an OpenAlex candidate; a limited number of DOAJ-only journals may also appear without OpenAlex work counts. The output records which source found each candidate and hashes both responses. DOAJ author-instruction links are pointers to review, not verified rule text: confirm the current official journal or publisher domain and page content before fetching guidelines or creating a profile. Neither index is exhaustive, and this shortlist is not an editorial suitability or acceptance ranking.

Citation venues are a separate, optional signal. Manually select actual reference DOIs into a private JSON file outside Git, such as `{"schema_version":1,"dois":["10.1038/nature14539"]}`, then run `python skills/manuscript-audit/tools/discover_citation_journals.py --references /private/references.json --allow-network`. This sends only those DOI values to OpenAlex, groups the journals of resolved references and the first page of journals publishing works that cite them, and writes a private `citation-candidates.json`. Pass that file to `discover_journals.py --citation-record /private/citation-candidates.json` to add citation-graph evidence to matching journals and include some citation-only candidates. A DOI inventory contains mentions, not a verified reference list, so do not pass it directly. The counts omit unindexed works and later group pages. Citation frequency does not establish topic fit or acceptance likelihood.

Optional web results enter through an ISSN gate. Save at most 20 manually selected, nonconfidential results outside Git as `{"schema_version":1,"candidates":[{"issn":"2475-9066","result_url":"https://joss.theoj.org/about"}]}`, then add `--web-candidates /private/web.json` to `discover_journals.py`. The tool sends only the ISSNs to OpenAlex, DOAJ, or Crossref and includes a candidate only after a unique journal record confirms its ISSN. The web URL is retained as an **unverified pointer** and is never fetched or used as the journal homepage; registry identity does not establish scope, legitimacy of the web page, or acceptance probability. Rejected ISSNs and response hashes remain in the private discovery record. Automatic web search and special-issue matching are not implemented.

For a selected DOAJ-linked candidate, run `python skills/manuscript-audit/tools/capture_candidate_guidelines.py --discovery-record /private/candidates.json --issn 2666-6510 --allow-network`. The tool checks the current DOAJ record for a unique matching journal ID, ISSN, and unchanged author-instruction link. It accepts the link on the current DOAJ journal homepage host; a different host requires a matching current OpenAlex journal or publisher homepage, or the exact Springer Nature journal URL pattern derived from the current OpenAlex homepage. DOAJ-only candidates can use their current journal host. Redirects outside the verified host are blocked; the Springer Nature adapter permits only its checked journal page and its publisher identity-cookie route. The private `source-locator.json` records the domain basis and registry response hashes. A `captured_pending_review` result includes a hashed `source.json` and extracted `guideline.txt`; inspect the current official page and extracted text before building any rules. A `needs_manual_verification` result requires an independently verified official journal or publisher page through `fetch_guidelines.py`. This locator does not create a journal profile or treat an indexed URL as proof that every instruction is current.

For a candidate without a usable DOAJ author-instruction link, the Springer Nature adapter can locate `submission-guidelines` when the current matching OpenAlex journal homepage has the exact `springer.com/journal/<id>` pattern. Otherwise, the tool checks the current ISSN-matched OpenAlex homepage, or the current DOAJ homepage for a DOAJ-only candidate, for one explicit same-host author-guide link. An HTTP homepage pointer is upgraded to HTTPS on the same host, without an HTTP request. It records the fetched homepage URL and response hash in the private locator; the raw homepage is not saved. Missing, ambiguous, or external links remain `needs_manual_verification`. An operator can supply an independently reviewed URL with `--guideline-url https://journal.example/authors`. The tool requires its host to match the current journal homepage, linked publisher homepage, or exact Springer Nature pattern; for a DOAJ-only candidate, it must match the current DOAJ journal homepage. A captured guideline still needs live-page review before profile building.

**Journal coverage decision:** Discovery is an evidence-backed candidate shortlist, not an exhaustive list or acceptance ranking. Grouped OpenAlex counts, optional DOAJ subject results, and an optional private citation record are separate signals; the first result pages and source metadata limit their coverage. The 20 public profiles are dated fixtures. For a selected journal, current official instructions must be captured privately, independently read, and quote-checked before any rule can support a finding. Prior-run profile reuse has an age and hash gate. Springer Nature has a narrow journal URL adapter and other journals can use an explicit same-host homepage link; other publisher adapters, automatic web discovery, profile inheritance, and automatic cache refresh remain planned. Missing instructions or disputed rules remain `needs_validation`.

To fetch a public guideline page and prepare a run, inspect the extracted text before using it:

```sh
python skills/manuscript-audit/tools/fetch_guidelines.py --url https://example.org/authors
python skills/manuscript-audit/tools/prepare_run.py --manuscript /path/to/paper.md --source-record /path/to/manuscript-audit/sources/source-1/source.json
```

Repeat `--source-record` for every relevant guideline page. Each page receives its own source ID, URL, retrieval time, and content hash in the private run; no cached page is committed to the repository.

For manually verified guideline text, supply the actual guideline URL and its known retrieval time:

```sh
python skills/manuscript-audit/tools/prepare_run.py --manuscript /path/to/paper.md --guideline-text /path/to/guideline.txt --guideline-url https://example.org/authors --retrieved-at 2026-10-19T09:00:00Z
node skills/manuscript-audit/validators/validate-run-manifest.cjs /path/to/manuscript-audit/paper/run-1
```

After adding `journal-profile.json`, `coverage-ledger.json`, `findings.json`, and the referenced agent records to that run, check the record and local evidence chain with `node skills/manuscript-audit/validators/validate-run.cjs <run-directory>`. A confirmed compliance finding requires a covered unit for its rule, and every compliance unit is fingerprinted against the full supplied corpus. Use `--supplementary <file.md>` during preparation for each supplied text supplement; an absence claim must search all of them. The validator checks records and hashes; it cannot prove that reviewers worked independently.

For a new journal profile, save two separately prepared drafts as `profile-a.json` and `profile-b.json` in the private run. `node skills/manuscript-audit/validators/compare-profiles.cjs <run-directory> profile-a.json profile-b.json --promote` checks both against the cached source, writes a private comparison record, and creates `journal-profile.json` only when all fields agree. If the journal identity and source set agree but individual rules differ, `--promote-partial` creates a private working profile that keeps disputed and one-sided rules marked `needs_validation`. It omits disputed scope or AI-policy text. Such a profile is incomplete: resolve each disagreement against the guideline before calling the journal profile verified. Matching text alone cannot prove the two readings were independent.

To reuse an agreed profile for another manuscript without downloading its guideline pages again, run `python skills/manuscript-audit/tools/prepare_cached_run.py --source-run /private/previous/run-1 --manuscript /private/new-paper.md --ttl-days 30`. Alternatively, use `--cache-root /private/previous --issn 2475-9066` to find an eligible run in that private root. The tool refuses to choose between different fresh profiles for the same ISSN. It creates a new private run with the exact source snapshots and profile, requires a current two-draft agreement record, checks all source and profile hashes, and rejects any source older than the chosen TTL. Compare the selected ISSN and current official instructions before relying on it: a recent snapshot can still be superseded, and matching drafts do not prove independent reading. Adjudicated and partial profiles require fresh review instead of this reuse path.

The [20 dated Computer Science profile fixtures](journal-profiles/README.md) show the resulting rule format; retrieve and hash-check each profile's cited official pages before applying its rules to a manuscript.

To resolve differences, save `decisions.json` privately with `{"schema_version":1,"resolutions":{"rules.example":{"choice":"first","reason":"The official wording supports this narrower rule."}}}`. Include exactly one reasoned decision for every field in the comparison record. Choices are `first`, `second`, or `omit`; set `"status":"needs_validation"` when the selected rule cannot yet be checked. Run `node skills/manuscript-audit/validators/adjudicate-profiles.cjs <run-directory> profile-a.json profile-b.json decisions.json`. The tool checks the selected profile and its source quotes, rejects journal identity differences and upgrades of unresolved rules, then creates the private `journal-profile.json`.

For a journal profile with Markdown abstract, main-text word, keyword-count, or required-heading rules, `python skills/manuscript-audit/tools/check_deterministic.py <run-directory> --article-type original_research --main-start Introduction --main-end References` records provisional measurements in the private run. Keywords can be under their own heading or on the final plain-text line of the abstract; that line is excluded from the abstract word count. A required heading uses `{"metric":"heading_count","heading":"Data Availability","op":">=","value":1}` in the journal profile. Unclear section boundaries or unsupported formatting stay unresolved; heading counts from PDF text are not inferred.

For explicitly labeled, same-line two-sided z statistics and p values, `python skills/manuscript-audit/tools/check_z_p.py <run-directory>` writes private `z-checks.json`. It compares the normal-tail probability while allowing for the printed precision of z and equality p values.

For explicit same-line two-sided `t(df)` and Pearson `r(df)` pairs, or `F(df1, df2)` and `χ²(df)` pairs, install the optional dependency with `python -m pip install -r requirements-stats.txt` and run `python skills/manuscript-audit/tools/check_stats.py <run-directory>`. The private `statistical-checks.json` compares the printed p value with SciPy distribution tails over the statistic's rounding interval. It skips one-sided and correction-labeled lines, unrecognized formats, and tests without the required degrees of freedom. `needs_review` and its proposed severity are screening leads only: inspect the original manuscript, test definition, tail, corrections, and assumptions before making a finding.

For a mean of one integer-valued item per observation, supply the sample size and scale bounds in a private `cases.json`, then run `python skills/manuscript-audit/tools/check_grim.py <run-directory> --cases /path/to/cases.json`. A case has `case_id`, `path`, `line_id`, `reported_mean` as a string, `sample_size`, `scale_min`, and `scale_max`; for example, `{"case_id":"M1","path":"manuscript.md","line_id":"L42","reported_mean":"2.34","sample_size":13,"scale_min":1,"scale_max":5}`. The tool checks that the mean appears on the cited line and whether any integer total could round to it. It cannot verify the sample size, scale, missing data, or how composite scores were calculated; inspect those assumptions before treating `needs_review` as a finding.

To inventory DOI mentions in the supplied manuscript and text supplements, run `python skills/manuscript-audit/tools/list_dois.py <run-directory>`. It writes a private `doi-inventory.json` with file and line references. DOI mentions do not establish that the reference list is complete or that a citation is correct.

For labeled arXiv identifiers, run `python skills/manuscript-audit/tools/list_arxiv_ids.py <run-directory>`. It writes a separate private `arxiv-inventory.json` with file and line references. This catches modern and legacy `arXiv:` IDs but does not look them up or infer which references lack an identifier.

To check those IDs against the [arXiv API](https://info.arxiv.org/help/api/user-manual.html), run `python skills/manuscript-audit/tools/check_arxiv_ids.py <run-directory> --allow-network`. This sends the IDs to arXiv and saves limited registry metadata in a private `arxiv-metadata.json`; it does not store abstracts or full text. The tool batches requests and follows [arXiv's three-second API interval](https://info.arxiv.org/help/api/tou.html). A missing record still needs manual review.

To check those DOI values against the [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/), run `python skills/manuscript-audit/tools/check_dois.py <run-directory> --allow-network`. This sends DOI values to Crossref, which may reveal the manuscript's reference topics. The private `doi-metadata.json` records registry status, response hashes, and limited title, year, and first-author metadata; a Crossref miss does not prove a DOI is invalid, and a registry match does not verify the citation details. No manuscript text or cached PDF is sent.

For an actual bibliography entry, select its physical line span in a private `reference-cases.json`, such as `{"schema_version":1,"references":[{"doi":"10.1234/alpha","path":"manuscript.md","start_line":42,"end_line":43,"stated_title":"Example title","stated_year":2024,"first_author_family":"Example"}]}`. Run `python skills/manuscript-audit/tools/check_reference_metadata.py <run-directory> --cases /private/reference-cases.json`. The tool requires the DOI, stated title, year, and optional first-author name to occur in that 1–8 line span, then compares them with the saved Crossref fields. It writes private `reference-metadata-checks.json` with `metadata_consistent`, `needs_review`, `metadata_incomplete`, or `registry_unavailable` statuses. A mismatch is a provisional lead: editions, online versus print dates, incomplete registry deposits, and citation formatting require manual inspection. An inventory of DOI mentions alone is not a bibliography.

A confirmed reference finding needs a manuscript quote from the selected reference span, a `reference_metadata` evidence item identifying the DOI and result index in `reference-metadata-checks.json`, and that file's SHA-256 as `checks_sha256`. The full run gate checks the private file, its underlying DOI inventory and Crossref metadata hashes, and the current manuscript span. A `needs_review` registry comparison is only a lead; independent defense and record verification still have to establish the actual criticism. Confirmed review findings similarly require a manuscript quote and a covered review unit for their review class.

Create a pending compliance ledger from the selected article type with `python skills/manuscript-audit/tools/build_coverage.py <run-directory> --article-type original_research`. The tool refuses to overwrite an existing ledger; each unit carries a fingerprint of the supplied manuscript files.

For a controlled compliance benchmark, set `--data-freeze YYYY-MM-DD` when preparing each private run. The [compliance mutation scorer](bench/compliance-mutations/README.md) checks paired controls and mutations, validates final records, and reports recall and false-positive rates before and after independent validation. The [limited heading suite](bench/compliance-mutations/heading-suite-results.json) records seven controlled mutations on one CC BY paper. It does not ship manuscript text or estimate full manuscript-compliance performance.

After actually checking a unit, run `node skills/manuscript-audit/validators/hash-coverage.cjs <run-directory> U-1` and save a private check record under `agents/` using `schemas/coverage-check.schema.json`. Record the unit ID, `decision: "checked"`, the actual invocation ID as `actor_id`, the printed `subject_sha256`, and a concrete `rationale`. Set that unit to `covered` and add the record path to `checked_by`. The full run gate rejects missing, mismatched, or stale check records; a file path alone does not establish coverage.

For explicit review coverage, add `--review-section Methods --review-class statistics` (repeat either flag for more sections or review lenses). This creates pending section/class pairs. The validator marks their fingerprints stale when supplied manuscript files change.

Once the profile, ledger, findings, and agent records pass `validate-run.cjs`, generate private summaries with `node skills/manuscript-audit/validators/render-report.cjs <run-directory>`. `REPORT.md` lists confirmed records, names every pending or stale coverage unit, and quotes a verified journal AI policy when one is recorded; unresolved records go to `NEEDS-VALIDATION.md`. `REVISION-PLAN.md` orders only confirmed findings by recorded severity and repeats their recorded suggested changes.

For a revised manuscript, prepare and validate a new run, then use `node skills/manuscript-audit/validators/compare-runs.cjs <previous-run> <current-run>`. The private comparison lists changed records and coverage states without treating a missing finding as resolved. Reviewer records from the prior run become stale when the manuscript files or guideline sources change.

## Public-paper example

The [ALICE Collaboration paper arXiv:2404.07542v2](https://arxiv.org/abs/2404.07542) is available under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). A private demonstration run compared its extracted PDF text with the [PLOS ONE submission guidelines](https://journals.plos.org/plosone/s/submission-guidelines) as a hypothetical target. The guideline page was retrieved on 2026-10-19 at 10:44:44 UTC; its locally extracted text has SHA-256 `9b001b7db59c1dce0434004ac044f220832cbda192eb900ed91f83c33817303f`. The original PDF and extracted text are not in this repository.

The validated local records produced this report summary:

```text
Journal: PLOS ONE
Coverage: 0 covered, 1 pending, 0 stale
Confirmed findings: none
Needs validation: F-1 — A short title is not apparent in the extracted manuscript text.
```

F-1 is an unresolved search result. The submission form, supporting files, and PDF reading order were not checked, so the example does not claim the paper violates a journal rule. The report does not predict an editorial decision.

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
