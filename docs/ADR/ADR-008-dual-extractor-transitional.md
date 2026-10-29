# ADR-008: Dual-Extractor Transitional Architecture

**Status:** Accepted (transitional — superseded by R11 when complete)  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** Transition between R6 and R11

---

## Context

The pipeline has two extraction paths:

1. **Primary extractor** — `fismatic/SSPConverter` + `compliance/lib/oscal_source.py`:
   Parses OSCAL control data (Chapter 13 control tables in the docx): control
   ID, implementation status, origination, statement parts, responsible roles,
   parameters, customer responsibilities, related controls.

2. **Secondary extractor** — `compliance/lib/heading_parser.py` driven by YAML
   datacontracts: Parses all non-control metadata sections (system description,
   authorization boundary, parties, diagrams, interconnections, ports/protocols,
   laws/regulations, etc.).

The `handledByPrimaryParser: true` flag in the datacontract destination marks
which sections are owned by the primary extractor, telling the secondary
extractor to skip re-extraction.

## Decision

Accept the dual-extractor architecture as a **transitional state** during
development of the unified contract-driven converter (R11). Both extractors
are authoritative for their respective scopes:

- `fismatic/SSPConverter` owns control-table parsing
- `heading_parser.py` owns all other document sections
- Datacontracts document both scopes, including control destinations marked
  with `handledByPrimaryParser: true`

This avoids a big-bang rewrite risk: the primary extractor has 192 passing
tests and proven accuracy on 10 SSPs. Replacing it immediately without a
parity validation framework would be high-risk.

## Transition Plan (R11)

When the unified converter is built:
1. The contract-driven engine takes over control-table parsing
2. `handledByPrimaryParser` flags are removed
3. `fismatic/SSPConverter` is deprecated (tests retained as golden fixtures)
4. R12 benchmark validation confirms zero-loss parity before `fismatic/` is
   removed

## Consequences

- **Positive:** No regressions in control extraction while building the new
  extractor in parallel.
- **Positive:** Datacontracts document both extractor scopes, making the
  eventual handoff explicit and traceable.
- **Negative:** Two extraction code paths to maintain simultaneously during
  the transition period.
- **Negative:** `heading_parser.py` cannot independently regenerate a full
  vault; it depends on the primary extractor for control data.
- **Ongoing constraint:** Changes to control-table parsing go through
  `fismatic/`; changes to metadata sections go through the contract
  `captureStrategy`/`sourceHeading` configuration.

## References

- `compliance/lib/heading_parser.py` (secondary extractor)
- `fismatic/src/phase_1/fismatic/ssp_converter.py` (primary extractor)
- `compliance/lib/oscal_source.py` (bridge between extractors)
- `compliance/datacontracts/fedramp-ssp-v1.yaml` (`handledByPrimaryParser` flags)
- `archive/plans/PROJECT_REFACTORING_PLAN.md` §§ Phase R11
