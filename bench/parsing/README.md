# Private parsing benchmark

This harness measures section-heading and reference-entry extraction from converted DOCX and standalone LaTeX manuscripts. Human reviewers label the original document, not the converted text. Source documents, gold labels, converter outputs, and results stay outside Git. The repository contains invented tests only.

Create a private gold JSON file with `schema_version: 1`, a fixed `data_freeze` date, and one or more cases. Each case needs a unique `case_id`, `format` (`docx` or `tex`), absolute private `source_file` and `extraction_record` paths, the original file's `source_sha256`, the converter's `extracted_sha256`, and human-labeled `sections` and `references` string arrays. `sections` lists visible heading titles, including a References heading when present. `references` lists complete reference entries as they appear in the original document. Keep each entry separate and repeat a label when it occurs more than once. The labels must be checked against the original document by a person before scoring.

Run the relevant local converter first, inspect its `extraction.json`, and compare the extracted Markdown with the rendered original. Then score the private gold file:

```sh
python bench/parsing/score.py /absolute/private/gold.json --output /absolute/private/parsing-results.json
```

The scorer checks original and extracted file hashes against the gold manifest and conversion record. It reports true positives, extra predictions, missed labels, precision, recall, and F1 by case, format, and overall. The result contains counts and hashes, not document or reference text. Undefined rates are `null`. DOCX paragraph boundaries and LaTeX Markdown paragraphs are interpreted differently when splitting reference entries.

This is an extraction benchmark, not evidence that any converted manuscript is safe to audit without visual comparison. PDF conversion currently emits plain text without reliable section or reference boundaries, so PDF structural accuracy is not scored here. No human-labeled parsing set or empirical parsing rate is included in the repository.
