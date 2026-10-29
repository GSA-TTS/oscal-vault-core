# ADR-024: CUI Classification + Provenance Datacontract Extension

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** Project architecture agent + user
**Phase:** R20 (CUI/provenance sidecar schema)

---

## Context

Downstream consumers that vendor `oscal-vault` as a submodule (e.g. the
a downstream portfolio-catalog project need a governed, machine-checkable
record of two things for every system whose vault content derives from a
real source `.docx`:

1. **CUI classification** — which NARA CUI Registry category (if any)
   applies to the system's extracted content, what banner marking follows
   from that category, and what statutory/regulatory authority backs the
   determination.
2. **Provenance** — a pointer (never the file itself — see ADR-009) from
   every downstream generated artifact back to the source document that
   produced it, so a redaction or takedown request can be traced end-to-end.

Today `compliance/datacontracts/*.yaml` (ADR-007, ODCS v3.1.0) already
describes **how** a `.docx` is parsed into vault content. Nothing in that
contract, or anywhere else in this repo, describes the CUI status or
provenance chain of what was parsed. A downstream consumer (the SSPP
project, see `docs/plans/agent-handoff-oscal-vault.yaml` Need 2 in that
repo) independently improvised a per-system `data/systems/<slug>/cui.yaml`
sidecar to fill this gap — which means the schema lives in the wrong repo:
consumers should not have to invent the shape of a contract the producer
(this repo) should own and validate.

## Decision

Add a **generic, schema-validated CUI/provenance sidecar** at
`examples/vault/<system>/classification/cui.yaml`, following the same
per-file-YAML-datacontract pattern ADR-007 established, but as its own
schema rather than an ODCS extension (CUI classification is not a parsing
contract; forcing it into ODCS's `SchemaObject` shape would be a poor fit).

### Schema

`compliance/datacontracts/schemas/cui-provenance-v1.schema.json` defines:

- `schema_version` (integer, `1`)
- `system_slug` (string, matches the vault directory name)
- `classification`:
  - `cui_category` — a NARA CUI Registry category code (e.g. `ISVI`,
    `SP-PRIV`) **or** the literal `not_cui`
  - `cui_marking` — banner marking string (`not_cui` when uncategorized)
  - `authority` — citing statute/regulation/OMB memo
  - `review_cadence` — one of `quarterly`, `semiannual`, `annual`,
    `per-source-update`
- `ingestion_approval`:
  - `approved_by_role`, `approval_date`, `approval_ref` — who approved
    ingesting the source document and a pointer to that approval record.
    A `TBD-*`-prefixed `approved_by_role` or an `approval_ref` explicitly
    noting "no approval recorded" is valid — the schema records the
    *absence* of sign-off as a first-class state, it does not require one.
- `source_documents[]` — `source_doc_ref`, `sha256` (all-zero placeholder
  permitted until the real hash is recorded), `source_type` (one of
  `ssp-docx`, `poam-xlsx`, `control-workbook`, `manual-source`, `other`)
- `downstream_artifacts[]` — paths this classification governs
- `retention` — `retention_period`, `decontrol_condition`
- `notes` — free text

This is the **generic pattern**, not a real classification: this repo
ships the schema, a validator, and one fully-synthetic example sidecar
(`examples/vault/_example-cui-classification/`) with `cui_category:
not_cui` and placeholder values throughout. It does **not** ship, and this
ADR does not authorize, a real CUI determination for any of the 25+ vault
systems already in `examples/vault/` — that is a data-owner/ISSO decision
that belongs to whoever owns the source `.docx` for that system, tracked in
their own repo (see the "Non-goals" section of the *consuming* project's
issue for this same schema, filed against the vendored copy).

### Validator

`scripts/validate_cui_contracts.py` (new) walks
`examples/vault/*/classification/cui.yaml` where present, validates against
the schema, and reports:

- schema violations (error)
- a `not_cui` classification with a non-empty `authority`/`approval_ref`
  citing a real category is fine — it just means "reviewed and determined
  not CUI," which is itself a useful, trackable state
- coverage: how many of the systems under `examples/vault/` have a sidecar
  at all (informational; **not enforced** — a system with no `Controls/`
  or hand-authored parsing contract has nothing to classify yet)

The sidecar is **optional per-system** and off by default in
`validate_vault.py` (warn-only, matching the R19 pattern) — this repo does
not gate CI on every vendored example system acquiring a real
classification; that is out of scope for a schema-only change.

## Consequences

- Downstream consumers (SSPP, or any future vendor of this vault) stop
  improvising their own CUI-sidecar shape; they point at this repo's
  schema and get schema validation for free by running
  `scripts/validate_cui_contracts.py` against their submodule checkout.
- No real classification decision is made or implied by this ADR — the
  one example sidecar shipped is synthetic (`not_cui`, placeholder
  authority) and exists purely to demonstrate the schema shape and give
  the validator something concrete to test against.
- Downstream consumers still own: the actual per-system classification,
  the ISSO/data-owner sign-off, and any real `sha256`/`source_doc_ref`
  values (none of which this repo has, since source `.docx` files are
  never committed here — ADR-009).

## Alternatives considered

- **Extend the existing ODCS datacontract (`compliance/datacontracts/*.yaml`)
  with a `customProperties.cui` block** — rejected: ODCS's `SchemaObject`
  tree models *parsing destinations*, not classification/provenance
  metadata about the whole system. Bolting classification onto a parsing
  contract would make both harder to read and validate independently.
- **Store classification in the `Systems/<slug>.md` frontmatter directly**
  — rejected: frontmatter is rendered into every exported vault note and
  surfaced in Obsidian; a classification/provenance record with approval
  references and retention metadata is operational/compliance data, not
  content a reader browsing the vault needs inline. A sidecar keeps it
  co-located but separate.
- **Require every vault system to carry a sidecar (fail CI without one)**
  — rejected for this change: most of the 25 example systems in this repo
  are synthetic/historical fixtures with no real source `.docx` and no
  ISSO of record; forcing a classification decision on them here would be
  fabricating compliance data this repo has no authority to assert.
