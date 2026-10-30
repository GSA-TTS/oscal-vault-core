# ADR-007: Per-File YAML Datacontracts (ODCS v3.1.0)

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R11 (Unified Contract-Driven Converter) — initiated

---

## Context

The extraction pipeline originally used hardcoded Python logic in
`fismatic/SSPConverter` to parse specific Word document structures. Adapting
parsing for a new document (e.g., a different agency's SSP template) required
modifying Python source code and re-running tests. There was no mechanism for
non-engineers to adjust parsing rules without code changes.

Additionally, the two extractors (`fismatic/SSPConverter` for primary OSCAL
control parsing, `compliance/lib/heading_parser.py` for secondary metadata
sections) operated independently with no shared configuration surface.

## Decision

Every source `.docx` document has a corresponding YAML **datacontract** file
in `compliance/datacontracts/`. Contracts declare:

- `destinations` — typed extraction targets (ODCS `SchemaObject` nodes) with:
  - `sourceHeading`: heading pattern to scope extraction (literal, glob, or `regex:...`)
  - `sourceKind`: what content to capture (`heading_paragraph`, `heading_table_all`, `control_table`, etc.)
  - `captureStrategy`: how to traverse the document tree
  - `captureScope`: boundary control (`until_next_same_or_higher`, `single_heading`, etc.)
  - `multiple: true` for repeated entities (e.g., one node per control family)

The contract format conforms to **ODCS (Open Data Contract Standard) v3.1.0**,
cached locally at `compliance/datacontracts/schemas/odcs-json-schema-v3.1.0.json`.
Key ODCS constraints honored:
- `SchemaObject.logicalType: "object"` for all destination nodes
- `StableId` pattern `^[A-Za-z0-9_-]+$`
- Domain-specific metadata in `customProperties` (ODCS standard)
- `relationships` for FK links between destination types

File layout:
```
compliance/datacontracts/
  fedramp-ssp-v1.yaml        # Base template (all standard FedRAMP SSP sections)
  analytics-usa-gov-ssp.yaml # Per-system overrides / extensions
  api-data-gov-ssp.yaml
  ...
  schemas/
    odcs-json-schema-v3.1.0.json
```

## Alternatives Considered

- **Shared single contract for all documents** — Rejected: different agencies'
  SSPs have slightly different heading numbering, section names, and table
  structures. Per-document contracts enable fine-grained per-system tuning.
- **Custom DSL (domain-specific language)** — Rejected: adds parser complexity;
  ODCS YAML is well-specified with a published JSON Schema.
- **Database-driven configuration** — Rejected: breaks git-based version
  control and review workflows; YAML diffs are human-readable.
- **Inline Python configuration** — Rejected: requires code changes for rule
  tweaks; YAML contracts allow non-engineers to adjust regex patterns.

## Consequences

- **Positive:** New document templates can be onboarded by writing a new YAML
  contract — zero Python code changes.
- **Positive:** Contracts are readable documentation of the expected docx XML
  structure for each section, including regex patterns and table layouts.
- **Positive:** Contract diffs in PRs provide an auditable record of parsing
  rule changes.
- **Negative:** Contracts must be kept synchronized with any changes to the
  heading parser's `captureStrategy` vocabulary.
- **Ongoing constraint:** The `fedramp-ssp-v1.yaml` base contract documents the
  docx XML structure in sufficient detail (row layouts, regex patterns, column
  semantics) to enable regex tweaking without reading Python source.

## References

- `compliance/datacontracts/fedramp-ssp-v1.yaml`
- `compliance/lib/heading_parser.py`
- `compliance/scripts/extract_by_contract.py`
- `compliance/datacontracts/schemas/odcs-json-schema-v3.1.0.json`
- the project refactoring plan (not exported to the public core) §§ Phase R11
