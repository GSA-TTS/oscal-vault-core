# Architecture Decision Records (ADRs)

[← README](../../README.md) · [Documentation](../ARCHITECTURE.md) ·
[Contributing](../../CONTRIBUTING.md)

This directory documents the key architectural decisions made in this project.
The README's roadmap board maps its *Shipped* lane back to the records below.

Each ADR captures: the decision, the context that drove it, the
alternatives considered, and the consequences.

Anything that changes the taxonomy, the datacontract shape, the export target,
or a validation gate belongs here. Add the file and the index row together.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| ADR-001 | Dotted Type Taxonomy (`oscal.*`, `oscal.x.*`, `okf.*`) | Accepted | 2026-07-18 |
| ADR-002 | Markdown-First, Canonical-JSON-Sidecar Architecture | Accepted | 2026-07-18 |
| ADR-003 | `uv` as Sole Python Dependency Manager | Accepted | 2026-07-18 |
| ADR-004 | Ruff Linting Scoped to `compliance/` Only | Accepted | 2026-07-18 |
| ADR-005 | Pre-Commit Hook Strategy (8 Gates) | Accepted | 2026-07-18 |
| ADR-006 | Dataview Integrity as Highest-Priority Test Constraint | Accepted | 2026-07-18 |
| ADR-007 | Per-File YAML Datacontracts (ODCS v3.1.0) | Accepted | 2026-07-18 |
| ADR-008 | Dual-Extractor Transitional Architecture | Accepted | 2026-07-18 |
| ADR-009 | Gitignore Strategy — No `.docx` in VCS | Accepted | 2026-07-18 |
| ADR-010 | `*_kind` Sub-Typing to Prevent Ontology Creep | Accepted | 2026-07-18 |
| ADR-011 | ODCS JSON Schema v3.1.0 as Contract Interchange Format | Accepted | 2026-07-18 |
| ADR-012 | Snapshot Layer — `.snapshots/current/` in Git, `history/` gitignored | Accepted | 2026-07-18 |
| ADR-013 | Feedback Reports Co-Located with Round-Trip Outputs | Accepted | 2026-07-18 |
| ADR-014 | Wikilink Validator Warn-Only by Default | Accepted | 2026-07-18 |
| ADR-015 | Extractor Benchmark Harness | Accepted | 2026-07-18 |
| ADR-016 | Docling Deferred / MinerU API v3 | Accepted | 2026-07-18 |
| ADR-017 | R11 Unified Contract-Driven Converter | Accepted | 2026-07-19 |
| ADR-018 | Instruction-Anchored Fill-Back (docxtpl Roundtrip) | Accepted | 2026-07-20 |
| ADR-019 | Two-Layer OSCAL Validation (NIST Schema + CCF API oracle) | Accepted | 2026-07-20 |
| ADR-020 | Control-Table Structural Fill (R17) & TS Generator Decommission | Accepted (amended by ADR-022) | 2026-09-22 |
| ADR-021 | Commit Blank GSA SSP Templates to VCS | Accepted | 2026-09-22 |
| ADR-022 | Per-Control Authoring Guidance Extraction (R18) | Accepted (completed by ADR-023) | 2026-09-23 |
| ADR-023 | Chapters 1-12 Front-Matter Authoring Guidance Extraction (R19) | Accepted | 2026-09-23 |
| ADR-024 | CUI Classification + Provenance Datacontract Extension (R20) | Accepted | 2026-09-28 |
| ADR-025 | Vault Ontology + Graph Optimization (R21) | Accepted | 2026-09-28 |
| ADR-026 | Derive Control Vocabulary From Raw OSCAL Fields (R21 Increment 2b) | Accepted | 2026-09-28 |
| ADR-027 | [CycloneDX Topology + C4 Mermaid From One Parsed Topology](ADR-027-topology-generators-cyclonedx-c4.md) | Proposed (feasibility build) | 2026-10-29 |
