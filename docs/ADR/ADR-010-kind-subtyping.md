# ADR-010: `*_kind` Sub-Typing to Prevent Ontology Creep

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R1 (Type Taxonomy), R4 (Extension Types)

---

## Context

Adding a new top-level `type` for every compliance specialization would
cause ontology explosion. For example, distinguishing a "risk decision" from
a "security exception" from an "architectural assumption" would require three
separate types if done naively — each demanding its own template, dashboard,
and frontmatter schema `$def`.

## Decision

Specialization is achieved through `*_kind` fields on existing types, not
through new top-level types. The kind field carries a controlled vocabulary
specific to that type.

Examples:

| `type` | Kind field | Kind values |
|--------|-----------|-------------|
| `oscal.x.decision` | `decision_kind` | `decision`, `assumption`, `exception` |
| `oscal.x.evidence` | `evidence_kind` | `api_export`, `screenshot`, `policy_doc`, `log`, `attestation`, `scan_result`, `interview_notes`, `test_output` |
| `oscal.x.diagram` | `diagram_kind` | `architecture`, `dataflow`, `sequence`, `network-boundary`, `trust-boundary`, `deployment` |
| `oscal.x.runbook` | `runbook_kind` | `collection`, `review`, `remediation`, `attestation`, `deployment`, `incident-response` |
| `oscal.ssp` | `ssp_kind` | `system-plan`, `leveraged-authorization` |
| `oscal.control` | `control_kind` | `core`, `enhancement` |

Kind-specific fields are **only valid when** the corresponding `*_kind` is
set. Schema validation enforces this through `if/then` JSON Schema constructs.

The rule for introducing a new type:

> **No new type is added unless it solves a repeated query or repeated
> authoring problem that `*_kind` cannot solve.**

## Alternatives Considered

- **Flat `note_type` with all variants** — Rejected: same collision/ambiguity
  problems as the pre-R1 architecture.
- **New top-level type per specialization** — Rejected: N types × M dashboard
  queries × M templates = combinatorial maintenance burden.
- **Inheritance-based schemas (JSON Schema `$ref` + discriminator)** — Partially
  adopted: JSON Schema `$defs` use `if/then` for kind-specific field activation.

## Consequences

- **Positive:** Adding a new `evidence_kind: scan_result` requires only a
  YAML vocabulary entry and a template note — no Python code changes.
- **Positive:** Dataview queries can filter by `decision_kind: exception`
  without knowing the full type hierarchy.
- **Negative:** Kind values are not validated at the Obsidian authoring surface
  (no live autocomplete); validation only occurs at commit time via the
  frontmatter hook.
- **Ongoing constraint:** Every `*_kind` vocabulary extension must be added to:
  1. The frontmatter JSON Schema (`vault-schemas.json`)
  2. The corresponding `okf.template` note
  3. At least one Dataview query example

## References

- `vault-schemas.json` (`$defs` with `if/then` kind constraints)
- the project refactoring plan (not exported to the public core) §§ Full Ontology
- `examples/vault/*/_meta/templates/` (kind-specific template files)
