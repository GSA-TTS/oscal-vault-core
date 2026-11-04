# Review packet: referenced content versus typed extraction

**Decision requested:** whether the pipeline should duplicate NIST catalog
statements, Appendix A glossary definitions, and Appendix B citations as typed
vault data, or retain them as authoritative references while preserving the
source document/table evidence.

## Authorized measurement source

This packet is derived only from aggregate, read-only metrics against the
authorized GSA Pages generated Moderate SSP. No source prose, contacts,
endpoints, or table-cell values are reproduced here.

| Item | Value |
| --- | ---: |
| Source SHA-256 | `7987dd7293694693e1c769bd2e29bbca2053b8816f385ec2b11cf09f8b39a0a9` |
| Unrouted blocks | 846 |
| Unrouted characters | 101,969 |
| Catalog statements | 605 blocks / 67,159 chars (65.9%) |
| Glossary definitions | 70 blocks / 19,330 chars (19.0%) |
| Authority citations | 31 blocks / 2,666 chars (2.6%) |
| Total proposed for reference, not duplication | 706 blocks / 89,155 chars (87.4%) |
| System/template narrative proposed for typed adoption | 113 blocks / 11,789 chars (11.6%) |

## Side-by-side options

| Review criterion | Reference authoritative material (**proposed**) | Duplicate it as typed vault content |
| --- | --- | --- |
| NIST control language | Maintain one vendored catalog; controls link through `satisfies[]`. | Maintain a second Word-derived copy in addition to the catalog. |
| Glossary and citations | Preserve the document/table source evidence; extract only system-specific applicable laws. | Store boilerplate definitions and citations per vault. |
| Drift risk | Catalog updates have one authoritative location. | Word copy can diverge from the catalog and require reconciliation. |
| Offline vault reading | Reader follows the vendored catalog reference. | Reader sees a local duplicate of generic reference text. |
| Template tailoring | Keep non-catalog or system-specific prose in the adoption queue. | Requires rules to distinguish tailored text from boilerplate before copying. |
| Evidence fidelity | Source document remains preserved/reviewable; decision concerns typed promotion only. | Vault becomes a closer text reproduction, at the cost of redundant data. |

## Recommendation for disposition

Record `catalog-statement`, `glossary-definition`, and `authority-citation` as
**reference** classes: do not promote them into a second typed model, but retain
source projections and the catalog/applicable-law references that allow review.
Any non-catalog or system-specific exception remains eligible for extraction.

This is a review aid, not an implementation decision. A system owner may accept
the recommendation or require extraction of a specific class or exception.
