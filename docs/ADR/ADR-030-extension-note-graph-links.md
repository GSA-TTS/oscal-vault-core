# ADR-030: Connect extension notes to their system and to the Index

**Status:** Proposed
**Date:** 2026-11-02
**Deciders:** Project architecture agent + user
**Phase:** R22 (vault graph, PR 2 of 5)

---

## Context

ADR-029 measured the generated vaults with path-aware link resolution. Across the 27 vaults in
`oscal-vault` there are **155 content orphans**: the Decisions, Diagrams, Inheritance and Runbooks
note of every system (26 each), 23 Boundaries and Relationships, 4 Privacy and 1 CRM note. They are
real content (median 0.9 to 17 KB) with no link to or from anything. Each carries `owner: null` and
only a bare `system_id` UUID in its frontmatter, and neither the System note nor `Index.md` links to
them (the Index dashboards are Dataview queries, which do not create links).

## Decision

1. Every extension note produced by `build_narrative_notes` gets
   `system: [[Systems/<slug>]]`, and `owner: [[Organizations/<slug>]]` when the system owner is known
   (otherwise `owner: null` as before). A shared helper, `extension_link_fields`, builds both so the
   rule has one definition. `system_id` is kept.
2. `Index.md` gains a static `## Navigate` block, between `<!-- navigate:begin -->` and
   `<!-- navigate:end -->`, listing the System, the Plan, and every extension note by title.
3. The graph links are **additive**. No field is removed or renamed, the `owner` key is always
   present, and the Dataview and Bases queries are untouched. The System note gets **no** mirror
   list of its extension notes: Obsidian renders that reverse view from the `system` links, and
   ADR-025 removed such mirror lists for that reason.

## Consequences

- On the synthetic SSP with narrative notes, content orphans go from 2 to 0 and edges from 22 to 28;
  with every extension type present, 0 orphans and every extension note links to the System.
  The real vaults need regenerating from their source documents to confirm the 155 reach 0; that
  step lives in `oscal-vault` and needs the protected `.docx` files (R22, PR 5).
- Extension notes now add one inbound edge each to the System hub. Hub concentration is unchanged in
  kind; the new edges carry information the bare UUID did not.
- Generated files change: each extension note (two frontmatter lines) and `Index.md` (one block).
  Nothing else. `validate_frontmatter` passes on all extension types; the one pre-existing
  `thin note` warning is unrelated.
- A reader that parses `system_id` is unaffected.

## Alternatives considered

- **Link from the System note to each extension note.** Rejected: a mirror list (ADR-025).
- **Put the links only in the body.** Rejected: frontmatter links are what the graph, Bases and
  ExcaliBrain read, and match how controls link to their system.
- **Make the Index a Dataview-only page.** Rejected: that is the current state, and creates no links.
