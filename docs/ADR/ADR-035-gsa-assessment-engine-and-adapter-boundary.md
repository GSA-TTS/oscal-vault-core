# ADR-035: GSA Assessment Engine and the System-Adapter Boundary

**Status:** Proposed
**Date:** 2026-11-17
**Deciders:** Project maintainers (review pending); AI-assisted draft
**Builds on:** [ADR-034](ADR-034-gsa-assessment-workbook-canonical-json.md)

## Context

Two system repositories each built an engine that crosswalks a system's control
implementation record against the two GSA workbooks and produces proposed results, findings,
a POA&M list and an unsigned attestation. The engines share their rules and have started to
diverge. Each reads the system's own file formats, which the core must not do: it holds no
system content, and its safety policy rejects system-specific names and identifiers.

## Decision

Put the engine in the core and the file reading in the system.

- A system repository runs a small adapter that emits one JSON document conforming to
  `compliance/schemas/assessment/system_assessment_input_schema_v1.json`: per-control status,
  inheritance, authored prose by statement part, evidence references, residual actions with a
  GAP flag, optional test procedures, an evidence list, assessor confirmations, and issue links.
- `compliance/lib/gsa_assessment.py` consumes only that document and the two canonical
  workbook files, which it loads from the core's bundled data by default. Rules cover results, objective-to-part mapping, findings AA-1 to AA-9, and an
  attestation block whose decision, name, signature and date fields are always null.
- `compliance/lib/gsa_assessment_report.py` renders JSON and Markdown deterministically and
  fails on any URL, e-mail address or CUI banner left in the output.
- `compliance/lib/gsa_poam_issues.py` renders one issue body per POA&M-required control from
  a work-items mapping the system team curates. It fails closed unless the mapping covers
  exactly the POA&M controls and each has evidence, validation tests and acceptance criteria.
  Filing issues is opt-in (`--create`) and uses the GitHub CLI.
- Only confirmations in the input count as assessor-confirmed. The engine proposes; it does not
  decide.

## Consequences

- One rule set to review, test and fix, instead of one per system.
- Adding a system means writing an adapter (a few hundred lines), not forking an engine.
- The input schema is a contract; closed property sets make adapter mistakes fail loudly.
- Results are only as good as the adapter. The engine cannot check that prose is true.
- CI and workflow evidence is reported as claimed, never as a passing run.

## Not in this change

- Verification of evidence claims against the repositories that hold the evidence.
- Adapters and migration of the consuming repositories.
- Any publication of workbook-derived or system data.
