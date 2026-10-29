# ADR-011: ODCS JSON Schema v3.1.0 as Contract Interchange Format

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R11 (Unified Contract-Driven Converter)

---

## Context

Datacontracts need a publicly-specified, versioned schema format so they can
be validated, tooled, and exchanged outside this repository. Using an
ad-hoc internal YAML schema would create a lock-in where only this repo's
tooling can understand the contracts.

## Decision

Datacontracts conform to the **Open Data Contract Standard (ODCS) v3.1.0**
JSON Schema, cached locally at:
```
compliance/datacontracts/schemas/odcs-json-schema-v3.1.0.json
```

Key ODCS v3.1.0 constraints honored by all contracts in this repo:

| ODCS Constraint | How we honor it |
|----------------|----------------|
| `additionalProperties: false` + `unevaluatedProperties: false` at top level | All domain-specific metadata goes in `customProperties` |
| `SchemaObject.logicalType` must be `"object"` | All destination nodes use `logicalType: object` |
| `StableId` pattern `^[A-Za-z0-9_-]+$` | All destination node IDs are kebab-case identifiers |
| `customProperties` items: `{id?, property, value, description?}` with camelCase property names | `sourceHeading`, `sourceKind`, `captureStrategy`, etc. use camelCase |
| `relationships` at schema level for FK links | Used for `oscal.party.uuid ← oscal.ssp.responsible-parties.party-uuid` |

The `customProperties` extension mechanism is the ODCS-standard way to attach
domain-specific metadata (like `sourceHeading` regex patterns) without
violating the top-level `additionalProperties: false` constraint.

## Alternatives Considered

- **Custom YAML schema** — Rejected: no external validation tooling, no
  community, no migration path.
- **dbt contracts (dbt-core)** — Rejected: SQL-oriented, not suitable for
  document parsing declarations.
- **AsyncAPI / OpenAPI schemas** — Rejected: API-centric, not data contract-
  centric; no concept of source-heading routing.
- **Frictionless Data Specs** — Considered; ODCS is more recent and has
  explicit support for quality and custom properties.

## Consequences

- **Positive:** Contracts can be validated against the published ODCS JSON
  Schema using any standard JSON Schema validator.
- **Positive:** The ODCS `customProperties` extension point is a stable,
  future-proof mechanism for parser-specific metadata.
- **Negative:** ODCS v3.1.0 has `unevaluatedProperties: false`, which means
  any parser-specific properties must be placed in `customProperties` rather
  than at the top level — adds one level of nesting.
- **Ongoing constraint:** When ODCS releases a new major version, contracts
  must be migrated. The cached schema file makes it easy to detect version
  drift.

## References

- `compliance/datacontracts/schemas/odcs-json-schema-v3.1.0.json`
- `compliance/datacontracts/fedramp-ssp-v1.yaml` (base contract)
- https://github.com/bitol-io/open-data-contract-standard (ODCS upstream)
