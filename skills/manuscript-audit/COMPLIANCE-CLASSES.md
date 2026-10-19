# Compliance rule IDs

Use the same `rule_id` for the same kind of requirement across journals. These names identify a check, not evidence that any journal requires it. A rule enters a profile only with a short exact quote from that journal's current instructions. If a journal has different limits for article types, add an article-type suffix to the ID and set `applies_to`.

| Rule ID | Meaning |
| --- | --- |
| `abstract_position` | Where the abstract must appear. |
| `abstract_max_words` | Maximum abstract word count. |
| `structured_abstract` | Required abstract headings or structure. |
| `keywords_min` | Minimum keyword count. |
| `keywords_max` | Maximum keyword count. |
| `main_text_word_limit` | Main text or article word limit, including stated exclusions. |
| `word_count_statement` | A word count that the author must report. |
| `required_section` | A named manuscript section that must appear. |
| `reference_style` | Required bibliography or citation style. |
| `data_availability` | Required data access or availability statement. |
| `ethics_approval` | Required ethics approval or exemption statement. |
| `competing_interests` | Required conflict or competing-interest declaration. |
| `funding_statement` | Required funding disclosure. |
| `author_contributions` | Required author contribution statement. |
| `spelling_consistency` | Required consistency of spelling convention. |
| `figure_table_presentation` | Numbering, captions, or in-text citations for figures and tables. |
| `ai_authorship` | Restrictions on listing AI tools as authors. |
| `ai_use_disclosure` | Required disclosure of AI assistance. |
| `orcid_requirement` | Required ORCID identifier. |
| `repository_access` | Required access to the submitted software or materials. |
| `software_license` | Required license for submitted research software. |

Keep a rule semantic when a qualifier cannot be measured from the extracted manuscript. In particular, a word limit that excludes tables, figures, or references is not a plain `main_text_words` check unless the extractor can reliably identify those exclusions. Do not turn an encouragement into a requirement. Preserve uncertainty as `needs_validation` and compare both independent drafts before using a rule for a confirmed finding.
