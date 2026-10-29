# Implementation Plan: Vault Ontology + Graph Optimization (R21, ADR-025)

**Status:** Proposed
**Date:** 2026-09-28
**Scope:** `oscal-vault` parser (`compliance/lib/vault.py`) + regenerated example vaults
**Trigger:** Ontology/graph review of the `<system>` vault (Index dashboards + node/edge graph)

---

## 1. Problem statement

The generated vault carries substantial redundancy in both the **content**
(frontmatter ↔ body duplication) and the **graph** (redundant / mirror /
semantically-wrong edges). This inflates every note, makes the Obsidian graph
unreadable (a 159-way star to the Plan node), pollutes advisory health signals
(thin-note / integrity), and forces 159-file regeneration churn on trivial
vocabulary changes.

This plan is **Increment 1: the safe subset** — changes with **no downstream
contract risk**, verified against both this repo's OSCAL/canonical-JSON export
and the SSPP `vault_reader`/`vault_to_precogly` transformer.

---

## 2. Evidence (<system>, 159 controls)

### Content redundancy
| Finding | Count | Source of truth kept |
| --- | --- | --- |
| `parameters:` frontmatter **and** `# Parameters` body table (identical) | 159/159 | frontmatter |
| `statement_parts:` frontmatter **and** `## Part` body headings | 159/159 | both (body carries narrative; list is the index) |
| `statements:` frontmatter — 3rd copy of `statement_parts`, `label` always `null` | 159/159 | **remove** |
| identical boilerplate `# CRM Guidance` 4-bullet stub | 159/159 | remove when no CRM entry |
| empty `# Evidence` placeholder prose | 157/159 | omit section when empty |
| empty `## Related Controls` placeholder prose | 157/159 | omit section when empty |

### Graph redundancy
| Finding | Edges today | Non-redundant | Removable |
| --- | --- | --- | --- |
| control→plan via `source_profile`+`source_ssp`+`up_control` (same target) | 477 | 159 | 318 |
| `plan.child_controls` mirror of `control.up_control` | 159 | 0 (Obsidian renders reverse) | 159 |
| `component.implements_controls` mirror of `control.component` | 159 | 0 | 159 |
| **broken hierarchy:** 14 base controls with enhancements have `sub_controls: []`; base `up_control`→Plan (not a control) | — | — | fix semantics |

### Downstream contract (verified)
the downstream vault reader (now `compliance/lib/vault_notes.py`) reads:
`normalized_status`, `normalized_origination`, `statement_parts`, `parameters`
(all **kept**). It also loads `statements` into a dataclass field **but never
consumes it** (grep: assigned at `vault_reader.py:183`, zero readers) — so
removing `statements` is safe. This repo's `export_oscal.py` derives statements
from **body `## Part` sections**, not the `statements:` frontmatter — also safe.

---

## 3. Increment 1 changes (this PR — no contract risk)

All changes are in `compliance/lib/build_control_note` unless noted.

### C1. Drop the dead `statements:` frontmatter field
- Remove the `fm["statements"] = [{"id": sid, "label": None} ...]` emission
  (`vault.py:743`). Keep `statement_parts` (the consumed index).
- `label` is `null` in 100% of notes and no consumer reads it (verified both
  repos + OSCAL export, which rebuilds statements from body Part sections).

### C2. Fix the enhancement hierarchy (graph correctness)
- For an **enhancement** control (`enhancement` set), `up_control` already
  correctly points to the base control (verified: `AT-02(02)`→`[[Controls/AT-02]]`).
  Keep.
- For a **base** control, `up_control`→Plan is semantically wrong for an
  ExcaliBrain "Parent" slot. Change base-control `up_control` to point to the
  Plan **only via a dedicated `up_plan` field** (systems already use `up_plan`),
  and populate `sub_controls` on base controls with their actual enhancement
  children. This makes the real AC-02 → AC-02(01) hierarchy visible and removes
  the false "159 controls → Plan" star.
- Net: base control Parent = Plan (via `up_plan`), Children = enhancements
  (via `sub_controls`); enhancement Parent = base control (via `up_control`).

### C3. Collapse the triple control→plan edge
- Keep `source_ssp` (provenance, read by `export_oscal`) as the single
  plan-provenance link. Drop `source_profile` as a **frontmatter wikilink**
  when it is identical to `source_ssp` — retain the value only if a distinct
  profile is ever set (it never is in current data). Both fields are vault
  provenance metadata excluded from generic OSCAL export, so no exporter
  fallback is necessary.
- Result: control→plan edges drop from 3×159 to 1×159.

### C4. Drop mirror back-reference edges
- Remove `plan.child_controls` (159-item list) and
  `component.implements_controls` (159-item list). Obsidian's graph and
  backlinks pane already render the reverse of `control.up_plan` /
  `control.component`, so these lists are pure duplication that also churn on
  every control change.
- **Downstream check:** `implements_controls` is **not** read by the SSPP
  transformer (grep clean). `child_controls` is not read either. Safe.
- Keep `system.child_components` and `plan.child_systems` (small, and the
  primary navigation for the 1→few fan-out; not a 159-way mirror).

### C5. Omit empty/boilerplate body sections
- `# CRM Guidance` stub: emit **only** when `crm_entry is not None`. The 4-bullet
  generic stub (159/159) is removed otherwise.
- `# Evidence`: omit the section entirely when there are no evidence refs
  (instead of writing `_(No evidence references recorded…)_`).
- `## Related Controls`: omit when empty (instead of `_(Not recorded…)_`).
- Rationale: absence is cleaner than placeholder prose for the thin-note /
  content-integrity signals, and shrinks every note.

### C6. Fix + modernize the Index dashboards
- Fix the "Inherited controls with source" query: it queries `inherited_from`
  which is only set for pure-inherited; broaden to surface the component link
  for hybrid too.
- Update counts block (Controls 159 etc.) to be a Dataview-computed count, not
  a hardcoded number that silently drifts.
- Ship a committed Obsidian **Bases** view (`_meta/views/controls.base`) as the
  modern successor to the Dataview code blocks (the `.base` format the user
  opened), while keeping the Dataview blocks for backward compat.

---

## 4. Explicitly deferred to Increment 2 (separate ADR-backed PR)

These carry downstream-contract or larger-blast-radius risk and are **filed as
tracked follow-ups**, not done here:

- **De-duplicate the `# Parameters` body table** (keep only `parameters:`
  frontmatter, render via a Bases/Dataview view). Deferred: needs confirmation
  no human-facing workflow relies on the inline table, and a Bases render
  replacement shipped first.
- **Make `normalized_status`/`normalized_origination` derived, not stored.**
  Deferred: the SSPP `vault_reader` reads these directly; removing them
  requires a coordinated downstream change (compute from the `Mappings/`
  tables). Tracked for lockstep update.

---

## 5. Verification plan (run-and-verify loop)

1. `uv run pytest -q -m "not slow"` — full suite green (existing roundtrip,
   OSCAL export, vault-validation, content-integrity tests).
2. `uv run ruff check compliance/ && uv run ruff format --check compliance/`.
3. Regenerate every example vault; `uv run python scripts/validate_vault.py`
   green (template-guidance invariant + structural checks).
4. `uv run python compliance/scripts/export_oscal.py --validate` — Layer-1 NIST
   schema still passes (proves statement/param export unaffected by C1/C3).
5. `uv run python compliance/scripts/roundtrip_demo.py` — fill-back parity holds.
6. Graph regression assertions (new test): base controls with enhancements have
   non-empty `sub_controls`; no control emits `statements`; control→plan edge
   count == control count.
7. Regenerate the SSPP submodule pin locally and run its `make vault-validate`
   to confirm the transformer still reads what it needs (`normalized_*`,
   `statement_parts`, `parameters`).

## 6. Rollback

Single squashed commit; revert restores prior generator + regenerated vaults.
All changes are generator-side + regenerated output — no schema migration.

## 7. Blast radius

- `compliance/lib/vault.py` (generator), `Index.md` template,
  new `_meta/views/controls.base`, regenerated notes across all example vaults.
- Downstream: none in Increment 1 (verified). Increment 2 tracked separately.
