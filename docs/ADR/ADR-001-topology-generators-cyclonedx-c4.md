# ADR-001: Generate CycloneDX topology and C4 Mermaid from one parsed topology

**Status:** Proposed (feasibility build)
**Date:** 2026-10-29
**Deciders:** Project architecture agent + user
**Phase:** Topology generators

## Context

The downstream SSPP repository parses each system's Ports, Protocols, and Services (PPS)
table into a typed, zoned data-flow diagram for Precogly. Separately, people draw C4
Mermaid diagrams by hand in the vault's `Diagrams/*.md` notes (Notify has two). The two
views answer different questions and drift apart. The parser, the classification rules
and the CycloneDX shape live in the downstream repo, so every consumer of the vault has to
reimplement them.

## Decision

Make topology generation a core feature of oscal-vault-core:

1. `compliance/lib/topology.py`: parse PPS tables into a neutral `Topology` (typed
   components, trust zones, flows with protocol, port, encryption, boundary crossing).
2. `compliance/lib/topology_cyclonedx.py`: render the CycloneDX blueprint members
   (`zones`, `assets`, `dataStores`, `flows`). Canvas layout stays with the consuming tool.
3. `compliance/lib/topology_c4.py`: render C4 Level 1 (Context) and Level 2 (Container) as
   Mermaid. Levels 3 and 4 are not derivable from a PPS table and are not generated.
4. `compliance/lib/topology_vault.py`: write generated Mermaid into a system's Diagrams note
   between `<!-- generated:<id> begin/end -->` markers. Hand-written content and embedded
   images are never edited; re-running is idempotent.
5. `compliance/lib/topology_eval.py` and `compliance/scripts/evaluate_topology_diagrams.py`:
   parse hand-written C4, compare it with the generated model, and score both generators
   against `docs/rubrics/topology-generator-rubric.yaml`.

Both renderers read the one `Topology`, so they cannot disagree about components or flows;
the rubric checks this as `cross_generator_consistency`.

## Consequences

- The downstream repo can drop its own parser and import this one (separate change).
- A comparison on one real system (kept in its restricted repository) showed the PPS table
  alone reaches about 46% element recall against a hand-drawn container view. Missing items (egress proxies, routers, load balancer,
  per-app Redis and S3 stores, analytics) appear only in SSP prose. Closing that gap needs
  a reviewed enrichment input, not guessing; this ADR does not add one.
- The rubric makes generator changes comparable: re-run, diff against a baseline report,
  accept only if no dimension regresses.
- The modules are generic and tested only with a synthetic table. Real-system vault content
  is read as input by the evaluator and never copied into this repository.

## Not decided

- Enrichment source for components absent from the PPS table.
- Whether generated blocks are written by the main export pipeline or only on demand.
- Mermaid syntax validation in CI (checked manually with the Mermaid parser for this build).
