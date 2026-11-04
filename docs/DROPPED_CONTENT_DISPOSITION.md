# Unrouted SSP content: adopt, reference, or decline

> **Status:** Proposed — needs a human disposition decision per class
> **Date:** 2026-10-29
> **Scope:** the blocks `docx_coverage_metrics.py` reports as dropped
> **Tooling:** [`compliance/scripts/classify_dropped_blocks.py`](../compliance/scripts/classify_dropped_blocks.py)
> **Related:** [`docs/CROSSWALK_COVERAGE.md`](CROSSWALK_COVERAGE.md) ·
> [`docs/INTEROPERABILITY.md`](INTEROPERABILITY.md) ·
> [ADR-018](ADR/ADR-018-instruction-anchored-fillback.md) ·
> [ADR-023](ADR/ADR-023-front-matter-authoring-guidance-extraction.md)

## Why this document exists

`docx_coverage_metrics.py` reports an **explicit loss budget** — 869 unrouted
blocks and 100,893 characters for the blank Moderate template. That number is
honest but undifferentiated: it counts blocks, not obligations. It cannot tell
the difference between a paragraph of the agency's own architecture narrative
and the NIST catalog's text for AC-2 reprinted into a Word file.

This document classifies every unrouted block by **what it is**, so each class
can be either adopted deliberately or declined on the record. An undifferentiated
loss budget invites two opposite errors: treating reprinted catalog text as
missing system content, and treating real narrative as acceptable loss.

Reproduce every number here with:

```bash
uv run python compliance/scripts/classify_dropped_blocks.py \
  --docx "templates/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
```

## The finding

Across all three committed blank baselines:

| class | Low | Moderate | High | what it is |
| --- | ---: | ---: | ---: | --- |
| `catalog-statement` | 558 / 65,565 | **605 / 67,159** | 852 / 104,844 | NIST SP 800-53 Rev. 5 statement text, reprinted |
| `control-title` | 158 / 6,697 | 0 / 0 | 410 / 21,927 | `AC-02(03): ... \| Disable Accounts` styled as body text |
| `structured-table` | 29 / 13,407 | 30 / 14,477 | 29 / 13,438 | tables the CIS anchor does not claim |
| `glossary-definition` | 70 / 19,344 | 70 / 19,330 | 70 / 19,327 | Appendix A term definitions |
| `authority-citation` | 31 / 2,661 | 31 / 2,666 | 31 / 2,662 | Appendix B statutes, FIPS, NIST, OMB, EO |
| `template-narrative` | 162 / 13,399 | **113 / 11,143** | 165 / 13,730 | the agency's own prose |
| `odp-placeholder` | 6 / 41 | 5 / 30 | 7 / 108 | `[Annually];` fragments |
| `document-furniture` | 14 / 340 | 14 / 340 | 14 / 338 | signature rules, "Approvals", revision scaffolding |
| `gsa-annotation` | 1 / 225 | 1 / 225 | 1 / 225 | an explicit `GSA Note:` aside |
| `diagram-image` | 1 / 0 | 1 / 0 | 1 / 0 | one inline image |
| **total** | **1,030 / 121,679** | **870 / 115,370** | **1,580 / 176,599** | |

Cells are *blocks / characters*. For `structured-table` the character figure is
cell text, since a table's content is in its cells.

**The headline: 58% of unrouted characters in the Moderate template are the NIST
catalog.** Another 19% are the glossary and the citation list. Only **11,143
characters — 9.7% — are the agency's own prose.**

### Three dispositions

| disposition | Low | Moderate | High | principle |
| --- | ---: | ---: | ---: | --- |
| **adopt** | 191 / 26,806 (22.0%) | 143 / 25,620 (22.2%) | 194 / 27,168 (15.4%) | system-specific; capture it |
| **reference** | 659 / 87,570 (72.0%) | 706 / 89,155 (77.3%) | 953 / 126,833 (71.8%) | authoritative elsewhere; cite, do not re-store |
| **decline** | 180 / 7,303 (6.0%) | 21 / 595 (0.5%) | 433 / 22,598 (12.8%) | structure or furniture; no content obligation |

## Class-by-class disposition

### Reference — do not re-store (72–77% of characters)

**`catalog-statement` — decline to re-store, on the record.** These blocks are
verbatim or parameter-substituted NIST control statement text, matched against
`compliance/data/nist_sp800-53_rev5_control_statements.json`. In the Moderate
template, **604 of 608 control-section prose blocks (99.3%)** match the catalog
exactly or above a 0.75 similarity threshold.

Capturing them would mean storing a second, drifting copy of text the project
already vendors from NIST and validates against. The `satisfies[]` reference
from a control to its requirement is the correct representation; the prose
belongs to the catalog.

> The four non-matching blocks in the Moderate template are worth a look: a
> `GSA Note:` on SC-28(01), an SI-10 example list, and two bracketed ODP
> fragments orphaned from their sentence. All four are handled by other classes.

**`glossary-definition` and `authority-citation` — decline to re-store.**
Appendix A is 70 term definitions (19,330 chars) and Appendix B is 31 authority
citations. Both are byte-identical across all three baselines, which is the
tell: they are boilerplate, not system content. A definition of "Audit Log" is
not a fact about the system.

The *system-specific* question — which authorities apply to *this* system — is
already a contract field (`applicable_laws_and_regulations`, sourced from the
"Applicable Laws and Regulations" table). The appendix prose adds nothing.

### Decline — no content obligation (0.5–12.8%)

**`control-title` — a structural defect, not content.** `AC-02(03): Account
Management | Disable Accounts` is a heading. The Low and High templates style
these as plain body paragraphs, so the walker counts them as prose:

| template | control titles heading-styled | plain paragraph |
| --- | ---: | ---: |
| Low | 0 | 158 |
| Moderate | **330** | 0 |
| High | 0 | 410 |

This single inconsistency is why High's "dropped" count (1,580) looks worse than
Moderate's (870) despite being the same document family. **It is a measurement
artifact, and it accounts for 21,927 of High's characters — 12.4% of its entire
loss budget.** Fixing it is a classifier change, not an extraction change.

**`document-furniture`, `odp-placeholder`, `diagram-image` — decline.**
Signature rules, "Table of Contents", bracketed fragments. The one image in each
blank template is the GSA logo. A *real* SSP's diagrams are a different matter —
see "What the blank templates cannot tell us".

**`gsa-annotation` — decline as prose, but see below.** One `GSA Note:` block.
Low volume, but these carry agency intent and may deserve typed capture rather
than being lumped with furniture.

### Adopt — capture as system content (15–22%)

**`structured-table` (25 named tables, ~371 cells) — adopt, and most of the
work is already done.** These are the system's factual inventory:

| table | contract entry | purpose |
| --- | :---: | --- |
| Information System Name | ✅ | system identity |
| Information Types | ✅ | FIPS-199 information types |
| Potential Impacts | ✅ | CIA impact levels |
| Digital Identity Acceptance | ✅ | DIAS assurance levels |
| System Owner / AO / Security Responsibility / Contacts | ✅ | parties |
| Operational Status | ✅ | status |
| Systems Providing / Receiving Controls | ✅ | inheritance both directions |
| Locations · Components and Boundaries · Web Site URLs | ✅ | boundary |
| Types of Users | ✅ | roles and privilege |
| Asset Inventory · Software Inventory | ✅ | inventory |
| External Services | ✅ | external dependencies |
| Ports, Protocols and Services | ✅ | topology source |
| System Interconnections | ✅ | interconnections |
| Hosted Subsystems | ✅ | subsystems |
| **List of AWS Services Used** | ❌ **missing** | cloud service inventory |

**21 of 22 named tables already have a `sourceHeading` entry in
`fedramp-ssp-v1.yaml`.** The gap is one table (`List of AWS Services Used`,
9 cells) plus the plumbing described in the next section.

**`template-narrative` (11,143 chars in Moderate) — adopt selectively.** This is
the material a decision is genuinely about. By heading:

| heading | blocks | chars | assessment |
| --- | ---: | ---: | --- |
| U.S. General Services Administration (front matter) | 30 | 3,316 | GSA process guidance — same in all three baselines; **decline** |
| Appendix B – References | 23 | 1,873 | citation list lead-ins; **decline** |
| Information Types | 2 | 872 | table lead-in; **adopt** (explains the table) |
| Information System Name | 3 | 592 | SSPP purpose statement; **adopt** |
| Types of Users | 5 | 433 | privilege-model narrative; **adopt** |
| Appendix A – Acronyms | 5 | 416 | glossary lead-ins; **decline** |
| Components and Boundaries · Providing/Receiving Controls · DIAS · Network Architecture · Asset Inventory · remainder | 45 | ~3,600 | table lead-ins and architecture prose; **adopt** |

So of 11,143 characters, roughly **5,600 are genuinely system-descriptive** and
~5,600 are template process guidance that is identical across baselines.

## Two measurement defects found during this analysis

Both inflate the reported loss. Neither is a content problem.

**1. The metric credits only the CIS anchor, so it undercounts routed tables by
seven.** The contract-driven parser consumes tables 2, 4, 6, 7, 8, 10 and 12
(system identity, impacts, and four party tables). `docx_coverage_metrics.py`
counts all seven as dropped because its `routed_table_ids` set comes only from
`find_control_tables()`.

**2. "Dropped" does not mean "absent from the vault."** `table_projection.py`
writes a review note for **all 361 tables** and **149 queryable row notes**, and
the package inventory classifies 2,660 of 2,743 nodes as `structured` with only
2 `opaque_preserved`. A table can be simultaneously "dropped" by the metric and
present in the vault as a preserved, queryable note.

> There is also a real extraction gap worth tracking separately: `PT-07(01)` in
> the Moderate template has its `Implementation Status:` marker on **row 2
> instead of row 1**, so `_looks_like_status_row()` misses it. That is **1 of
> 332 CIS-shaped tables (0.3%)**, Moderate only, and it is why the template
> reports 331 anchored rather than 332.

## Why the per-table adoption is not just a manifest edit

Ten of the named tables classify correctly but produce **no projectable rows**,
because `projectable_rows()` skips any row where every cell is empty — and in a
*blank* template every data row is empty:

```
revision_history · asset_inventory · system_components · user_roles
software_inventory · protocol · aws_services · system_inventory
interconnections · subsystems
```

In a filled SSP these populate. `user_roles` is the exception that will still
fail: it has **4 merged cells**, and `projectable_rows()` rejects any table with
`grid_span != 1` or a vertical merge. That is a genuine limitation needing either
merge-aware projection or an explicit decline.

## What the blank templates cannot tell us

The blank baselines are the floor, not the ceiling. Against the real
2026-09-30 FPDF SSPP (parent repository, `build/docx/fpdf-official-09302026-coverage.json`):

| | blank Moderate | real 2026-09-30 SSP |
| --- | ---: | ---: |
| dropped prose blocks | 839 | 517 |
| dropped prose chars | 100,893 | 135,037 |
| **average chars per block** | **120** | **261** |
| instruction paragraphs (routed) | 567 | 105 |

The inversion is the point. A real SSP has **fewer** dropped blocks but **more**
dropped characters, because the author replaced short template instructions with
long system-specific narrative. Where it lands:

| region | paras | chars |
| --- | ---: | ---: |
| runbook + architecture narrative (R19 headings) | 132 | **68,406** |
| named front-chapter sections | 65 | 24,528 |
| Appendix A glossary | 71 | 18,870 |
| control-section prose | 165 | 17,672 |
| Appendix B references | 53 | 4,522 |

**68,406 characters of DevSecOps, container and AWS runbook narrative** — the 31
H3 sections under DevOps/DevSecOps, Container-Based Workload and AWS Management.
Those headings *are* contract fields with `captureScope:
until_next_same_or_higher`, which should pull descendant sections in. The
contract captures 3,244 + 3,435 + 4,933 = 11,612 characters from the blank
template, so the mechanism works; whether it captures all 68,406 from a filled
one is **unverified and the single highest-value thing to measure next.**

This also means the blank-template `template-narrative` figure (11,143 chars)
*understates* the real decision by roughly 6×.

## Proposed dispositions

Ordered by value per unit of work. Each needs a human decision; none is
self-approving.

### Adopt now

1. **Add `List of AWS Services Used` to the contract.** The only named table
   with no entry. One `sourceHeading`, nine cells, cloud-service inventory.
2. **Credit contract-consumed tables in the metric.** Union `routed_table_ids`
   with the table indices the contract parser consumes. Moves 7 tables from
   dropped to routed and stops the metric contradicting the vault.
3. **Classify control titles as headings.** Match `^[A-Z]{2}-\d{1,2}(\(\d+\))?[:|]`
   in the walker regardless of paragraph style. Reclassifies 158 (Low) and 410
   (High) blocks out of the loss budget, and makes the three baselines
   comparable for the first time.
4. **Fix the `PT-07(01)` anchor.** Accept the `Implementation Status:` marker on
   row 1 **or** row 2. One line in `_looks_like_status_row()`; recovers the 332nd
   control table.

### Adopt after measurement

5. **Measure runbook-narrative capture against a filled SSP.** Confirm whether
   `until_next_same_or_higher` reaches all 31 H3 runbook sections. This governs
   68,406 characters — more than everything else here combined. Do not build
   anything until this is measured.
6. **Implement narrow merge-aware row projection.** The owner-authorized GSA
   Pages measurement found only four horizontal `grid_span=2` cells and no
   vertical merges in `user_roles`. Project that exact `user_roles` shape while
   retaining preserved-only behavior for every other merged table.

### Decline on the record

7. **Catalog statements, the glossary, and the citation list** — authoritative
   in the vendored NIST catalog and in the `applicable_laws_and_regulations`
   field respectively. Re-storing them creates a drifting second copy. This is
   ~75% of the loss budget and the largest single decision here.
8. **GSA front-matter process guidance** (~3,300 chars, identical across all
   three baselines) — template instructions to the author, not system facts.
9. **Document furniture, ODP fragments, the logo** — no content obligation.

### Change the metric's vocabulary

10. **Rename `explicit_loss_budget`.** It measures *unrouted blocks*, not lost
    content, and this analysis shows the two differ by about 8×. Report the
    disposition split alongside it so a reviewer sees "9.7% agency prose" rather
    than an undifferentiated 869.

## Open question for a reviewer

`gsa-annotation` is currently one block in a blank template, but in a filled SSP
these asides carry agency intent that is neither catalog text nor system fact
("GSA Note: For databases, encryption of the whole database, table, column, or
field levels..."). They are arguably typed guidance in the ADR-023 sense rather
than furniture. **One block is not enough evidence to decide**; revisit against a
filled SSP alongside item 5.

---

This analysis is AI-assisted. Every figure is reproducible with the command at
the top of this document, and the disposition column encodes a *proposal* — the
decision to adopt or decline each class is a human one.
