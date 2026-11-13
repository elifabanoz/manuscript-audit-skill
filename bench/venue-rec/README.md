# Venue recommendation benchmark (provisional)

`score.py` measures where the known publishing journal appears in a private discovery shortlist and in an embedding-only ranking of the **same candidate pool**. It reports hit@1, hit@5, hit@10, and mean reciprocal rank overall and by field. The published journal is one known match, not the only suitable venue, so these metrics do not measure editorial fit or acceptance probability.

Keep the preprint, discovery record, embedding vectors, baseline record, case manifest, and result outside Git. The scorer hashes those inputs, recomputes cosine ranking from the supplied vectors, checks that every candidate has exactly one vector, and refuses duplicate or ambiguous journal identities. Each case needs a grouped OpenAlex discovery record created with `--exclude-work-id W...` for its target work. The scorer rejects records that add citation, DOAJ, or manually selected web candidates, since their contribution is not covered by this exclusion. It does not fetch data or send manuscript text to a provider.

Example private `cases.json`:

```json
{
  "schema_version": 2,
  "data_freeze": "2026-11-13",
  "cases": [
    {
      "case_id": "example-1",
      "field": "Computer Science",
      "published_issn": "2475-9066",
      "target_work_id": "W123456789",
      "preprint_file": "/absolute/private/masked-preprint.md",
      "discovery_record": "/absolute/private/discovery-1/candidates.json",
      "embedding_baseline": "/absolute/private/embedding-1.json"
    }
  ]
}
```

Generate each discovery record with `python skills/manuscript-audit/tools/discover_journals.py --query "nonconfidential topic" --exclude-work-id W123456789`, using that case's verified OpenAlex work ID. The saved `excluded_work_id` and `openalex_work_filter` show which work was omitted from the grouped query. Supply a private `vectors.json` with `schema_version: 1`, a nonempty `model_id`, the preprint and discovery SHA-256 values, one `preprint_vector`, and `candidate_vectors` keyed by every candidate's `source_id` (or `issn_l`/first ISSN if no source ID exists). Vectors must have the same finite dimension and nonzero norm. Run `python bench/venue-rec/rank_embeddings.py /absolute/private/masked-preprint.md /absolute/private/discovery-1/candidates.json /absolute/private/vectors.json /absolute/private/embedding-1.json` to create the baseline. It stores the vector-file hash and cosine order without copying vector values. The scorer reproduces the order, but cannot prove that the stated model produced the supplied vectors or that journal representations were prepared without target leakage.

Run `python bench/venue-rec/score.py /absolute/private/cases.json`. It writes `venue-rec-scores.json` beside the manifest and refuses to overwrite it. On Windows, use absolute Windows paths with escaped JSON backslashes.

Every output is labeled `provisional_unreviewed`. Excluding one OpenAlex work ID does not establish that duplicate records, source-level counts, or manuscript clues are leakage-free. Before reporting empirical rates, independently inspect the preprint for journal/DOI/template leakage, check for duplicate target records in discovery data, verify the published-venue label and embedding baseline, and use multiple fields. No real venue benchmark result is included here.
