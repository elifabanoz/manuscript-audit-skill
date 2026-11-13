# Review classes

These IDs name lenses for a pre-submission author-side review. They are not journal requirements, severity levels, or predictions of a peer review outcome. Assign only relevant `section × review_class` units with `build_coverage.py`; record an explicit no-finding check when a unit is covered without a concern. Read the full supplied manuscript and supplements when checking a claim that depends on material outside the assigned section.

| Review class ID | Question to check | Evidence and counterchecks |
| --- | --- | --- |
| `methods` | Can the design, controls, baselines, and data splits support the stated research question? | Cite the exact method and result passages. Check exclusions, leakage, and alternative designs before alleging a flaw. |
| `statistics` | Do the reported analysis choices and uncertainty support the numerical interpretation? | Inspect sample size, unit of analysis, tests, effect sizes, uncertainty, and multiplicity. Local arithmetic screens are leads, not verdicts. |
| `novelty_related_work` | Is the contribution stated in a way that can be distinguished from relevant prior work? | Cite the manuscript claim and inspect the actual prior work before alleging overlap. An abstract or metadata match alone cannot establish lack of novelty. |
| `reproducibility` | Could another researcher follow the described procedure with the stated materials and constraints? | Check methods, parameters, software/data access, seeds, and supplements. Separate a missing description from a journal-specific repository rule. |
| `claim_evidence` | Are conclusions and scope of generalization supported by the reported observations? | Pair each challenged claim with the closest results and limitations. Check whether a qualification elsewhere resolves the concern. |
| `presentation` | Can readers follow definitions, notation, figures, tables, and the argument? | Point to a specific ambiguous passage or display. Do not turn a personal style preference into a confirmed finding. |
| `ethics` | Are participant, privacy, data-rights, and broader-impact claims adequately explained where relevant? | Inspect the study context and supplied disclosures. Treat a journal's required declaration as compliance, not as a scientific review concern. |

For every candidate concern, save the smallest exact manuscript quote and physical line ID, name the affected class, and state what remains uncertain. A confirmed review finding requires a covered unit for the same class plus separate defense and record-verifier records. A valid quote can still be misinterpreted; reviewers must test the strongest plausible counterevidence. Do not infer an editor's decision from the count or severity of findings.
