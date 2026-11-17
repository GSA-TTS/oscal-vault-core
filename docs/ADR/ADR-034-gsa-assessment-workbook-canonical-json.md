# ADR-034: Canonical JSON for GSA Assessment Workbooks

**Status:** Proposed
**Date:** 2026-11-17
**Deciders:** Project maintainers (review pending); AI-assisted draft

## Context

Two system repositories (an FPDF SSPP repository and gsa-pages) each carried a private copy
of a converter that turns two GSA workbooks into JSON: the Control Tailoring Workbook
(baselines, overlays, GSA parameter values) and the NIST SP 800-53A test-case workbook
(assessment objectives with Examine, Interview and Test methods). The copies had already
drifted, including a local re-implementation of control-ID normalization. The existing
`control_decision` pipeline reads a different, guidance-style workbook and does not cover
either of these.

## Decision

Provide the converter and its schemas in the core, and keep the data out of it.

- `compliance/lib/gsa_workbooks.py` reads the ODS with the standard library, normalizes IDs
  with `compliance.lib.control_identity`, redacts URLs, e-mail addresses and CUI banner
  tokens, and validates output against two JSON Schemas under
  `compliance/schemas/assessment/`.
- `compliance/scripts/convert_gsa_workbooks.py` converts, checks drift (`--check`), or
  validates existing JSON (`--validate`). It writes to the workspace and never into a
  vendored core (ADR-028).
- Validation goes beyond the schema: summary counts must match the rows, IDs must be
  canonical and unique, and expected test-case counts must match the extracted objectives.
- Tests use small synthetic ODS packages built in the test. No GSA workbook, and no JSON
  converted from one, is committed to the core.

The canonical JSON is a local build product, not a version-controlled artifact. A consuming
repository regenerates it from the ODS workbooks (which it keeps out of git) and should
gitignore it. The ODS conversion is deterministic, so `--check` against the workbooks, not a
committed copy, is what proves the JSON is faithful. Anything derived from it that carries
workbook text (such as a rendered assessment) is treated the same way. Whether any
workbook-derived data may be published is a classification and release decision for humans;
this ADR does not make it.

## Consequences

- Consumers drop their private converters and call the core; drift between them ends.
- A consumer can prove its committed JSON is faithful with `--check` and structurally valid
  with `--validate`, and the second needs no ODS.
- The schemas set a stable contract (`kind`, `version`, closed property sets) for the
  assessment engine that follows in a separate change.
- Workbook layout changes (column order, sheet names) fail loudly rather than silently.

## Not in this change

- The assessment engine (crosswalk, proposed results, findings, attestation template).
- A skill for it, and any adapter for system-specific prose.
- Migration of the consuming repositories.
