# ADR-001: Dotted Type Taxonomy (`oscal.*`, `oscal.x.*`, `okf.*`)

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R1 (Type Taxonomy Migration)

---

## Context

The original vault used a flat string `note_type` field (e.g. `control`,
`organization`, `system`) with no namespace, leading to three problems:

1. **Ambiguity** — `type: system` conflated the _SSP document_ with the
   _technical system_ it describes.
2. **Collision risk** — Adding new concept types (diagrams, findings,
   evidence) would pollute the same flat namespace.
3. **Tool opacity** — Dataview queries couldn't distinguish canonical OSCAL
   types from project extensions or vault infrastructure notes.

## Decision

Adopt a three-namespace dotted type taxonomy:

| Namespace | Covers | Example |
|-----------|--------|---------|
| `oscal.*` | Canonical NIST OSCAL model concepts | `oscal.control`, `oscal.ssp`, `oscal.party` |
| `oscal.x.*` | Project-specific extensions beyond NIST OSCAL | `oscal.x.diagram`, `oscal.x.evidence`, `oscal.x.runbook` |
| `okf.*` | Vault governance / Open Knowledge Format infrastructure | `okf.schema`, `okf.index`, `okf.validation_rule`, `okf.mapping` |

Every note also carries:
- `oscal_model` (for `oscal.*` types) or `extension_model` (for `oscal.x.*`)
- `kind: canonical | extension`
- Backward-compat `note_type: <flat-string>` retained during migration

## Alternatives Considered

- **Keep flat `note_type`** — Rejected: collision and opacity problems remain.
- **Two namespaces (`oscal.*` + `ext.*`)** — Rejected: loses the semantic
  distinction between "extension of OSCAL" (`oscal.x.*`) and "vault
  infrastructure" (`okf.*`), making Dataview dashboards harder to scope.
- **Full OWL/RDF ontology** — Rejected: overkill for a local Obsidian vault;
  Dataview can't query RDF triples.

## Consequences

- **Positive:** Dataview queries can scope by `type: oscal.*` vs `oscal.x.*`
  vs `okf.*`, enabling separate dashboards per concern.
- **Positive:** Adding new sub-types doesn't require schema changes — just add
  a new `oscal.x.<name>` entry with a template.
- **Negative:** Existing vault notes required a migration pass to add the
  new fields (one-time cost, handled in R8 corpus regeneration).
- **Ongoing constraint:** Every new type must have an `okf.template` and at
  least one Dataview dashboard query — **enforced by CI**.

## References

- `archive/plans/PROJECT_REFACTORING_PLAN.md` §§ Full Ontology, Appendix: Full Type Registry
- `docs/OBSIDIAN_VAULT_SCHEMA.md`
- `vault-schemas.json` (33 `$defs`)
