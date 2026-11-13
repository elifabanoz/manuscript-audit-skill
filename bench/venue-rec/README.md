# Venue recommendation benchmark (provisional)

`score.py` measures where the known publishing journal appears in a private discovery shortlist and in an independently supplied embedding-only ranking of the **same candidate pool**. It reports hit@1, hit@5, hit@10, and mean reciprocal rank overall and by field. The published journal is one known match, not the only suitable venue, so these metrics do not measure editorial fit or acceptance probability.

Keep the preprint, discovery record, baseline record, case manifest, and result outside Git. The scorer hashes those inputs, checks that baseline keys exactly match the discovery candidate pool, and refuses duplicate or ambiguous journal identities. It does not fetch data or send manuscript text to a provider.

Example private `cases.json`:

```json
{
  "schema_version": 1,
  "data_freeze": "2026-11-13",
  "cases": [
    {
      "case_id": "example-1",
      "field": "Computer Science",
      "published_issn": "2475-9066",
      "preprint_file": "/absolute/private/masked-preprint.md",
      "discovery_record": "/absolute/private/discovery-1/candidates.json",
      "embedding_baseline": "/absolute/private/embedding-1.json"
    }
  ]
}
```

The embedding baseline record has `schema_version: 1`, `method: "embedding_only"`, `preprint_sha256`, `discovery_record_sha256`, and `ranked_keys`. A key is a candidate's `source_id`, or its `issn_l`/first ISSN if no source ID exists. `ranked_keys` must contain every discovery candidate exactly once. The scorer checks the hashes and pool identity, but cannot verify that an embedding model actually produced the order.

Run `python bench/venue-rec/score.py /absolute/private/cases.json`. It writes `venue-rec-scores.json` beside the manifest and refuses to overwrite it. On Windows, use absolute Windows paths with escaped JSON backslashes.

Every output is labeled `provisional_unreviewed`. Before reporting empirical rates, independently inspect the preprint for journal/DOI/template leakage, remove the target paper from discovery source data, verify the published-venue label and embedding baseline, and use multiple fields. No real venue benchmark result is included here.
