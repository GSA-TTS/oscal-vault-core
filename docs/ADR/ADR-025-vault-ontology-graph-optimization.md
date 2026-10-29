# ADR-025: Vault Ontology + Graph Optimization (R21)

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** Project architecture agent + user
**Phase:** R21 (ontology/graph de-duplication, Increment 1)

---

## Context

An ontology + graph review of the generated `<system>` vault
(Index dashboards + Obsidian graph) surfaced systematic redundancy that the
`build_control_note` generator introduces on every control:

- **Content duplication:** `parameters:` frontmatter is repeated verbatim as a
  `# Parameters` body table (159/159); `statement_parts:` is repeated as
  `## Part` headings; and a third field `statements:` duplicates
  `statement_parts` with a `label` that is `null` in 100% of notes.
- **Boilerplate bodies:** an identical 4-bullet `# CRM Guidance` stub (159/159),
  and `_(No evidence…)_` / `_(Not recorded…)_` placeholder prose (157/159) that
  is worse than absence for the thin-note / content-integrity signals.
- **Graph redundancy:** every control emits three wikilinks to the *same* Plan
  node (`source_profile`, `source_ssp`, `up_control`) = 477 edges where 159
  suffice; the Plan mirrors this with a 159-item `child_controls` list and the
  Component with a 159-item `implements_controls` list — pure back-reference
  duplication Obsidian already renders in reverse.
- **Broken hierarchy:** the `up_control`/`sub_controls` fields promise an
  ExcaliBrain parent/child control hierarchy, but base controls set
  `up_control`→Plan (not a parent control) and `sub_controls: []` is *never*
  populated (the "filled retrospectively by export_obsidian_vault.py" comment
  describes code that does not exist). The real AC-02 → AC-02(01) hierarchy is
  invisible; instead the graph is a 159-way star into the Plan node.

## Decision

Ship **Increment 1** — the changes with no downstream-contract risk, verified
against this repo's OSCAL/canonical-JSON export and the SSPP
`vault_reader`/`vault_to_precogly` transformer:

1. **Remove the dead `statements:` frontmatter field.** `label` is always
   `null`; no consumer reads it (this repo's `export_oscal` rebuilds statements
   from body `## Part` sections; SSPP loads it into a dataclass field that is
   never read). `statement_parts` (the consumed index) stays.
2. **Fix the enhancement hierarchy.** Base controls: Parent = Plan via a
   dedicated `up_plan` field; Children = actual enhancement controls via a
   now-*populated* `sub_controls`. Enhancement controls keep
   `up_control`→base control. This makes the real control hierarchy navigable
   and removes the false 159→Plan star from `up_control`.
3. **Collapse the triple control→plan edge** to a single `source_ssp` link;
   `source_profile` and `source_ssp` are vault provenance metadata excluded from
   generic OSCAL export; no exporter fallback is required.
4. **Drop the mirror back-reference lists** `plan.child_controls` and
   `component.implements_controls` (Obsidian renders the reverse of
   `control.up_plan` / `control.component` automatically; neither list is read
   downstream). The small `system.child_components` / `plan.child_systems`
   fan-outs stay.
5. **Omit empty/boilerplate body sections** — emit `# CRM Guidance` only when a
   CRM entry exists; omit `# Evidence` / `## Related Controls` when empty.
6. **Fix + modernize the Index dashboards** — correct the inherited-source
   query, compute counts via Dataview instead of hardcoding, and ship a
   committed Obsidian **Bases** view (`_meta/views/controls.base`).

## Consequences

- Control→plan/control graph edges drop from ~795 to ~318; the graph becomes a
  legible System→Component→Control / Plan→enhancement tree instead of a star.
- Every control note shrinks by removing dead frontmatter and boilerplate
  stubs; content-integrity / thin-note signals stop firing on placeholder prose.
  The human-readable Parameters table remains intentionally duplicated from
  frontmatter in Increment 1 to preserve lossless, local note readability.
- Vocabulary/mapping changes no longer churn 318 back-reference list entries.
- **No downstream contract change** — `normalized_status`,
  `normalized_origination`, `statement_parts`, and `parameters` (the fields the
  SSPP transformer reads) are all preserved.

## Deferred to Increment 2 (tracked, separate ADR)

- De-duplicating the `# Parameters` body table (render via Bases instead of a
  literal table) — needs a Bases render replacement shipped first.
- Making `normalized_status`/`normalized_origination` derived rather than
  stored — the SSPP `vault_reader` reads them directly, so removal requires a
  coordinated lockstep downstream change.

## Alternatives considered

- *Keep `statements:` for forward compatibility* — rejected: an all-null,
  unread field is not forward compatibility, it is dead weight; it can be
  re-added from `statement_parts` if a labeled variant is ever needed.
- *Populate `child_controls`/`implements_controls` correctly instead of
  removing them* — rejected: they are exact reverses of existing forward links
  that Obsidian's backlink/graph already computes; maintaining both doubles
  churn for zero navigational gain.
