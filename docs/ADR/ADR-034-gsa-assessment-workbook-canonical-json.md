# ADR-034: Canonical JSON for GSA Assessment Workbooks

**Status:** Proposed
**Date:** 2026-11-17
**Deciders:** Project maintainers (review pending); AI-assisted draft

## Context

Two system repositories each carried a private copy
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
- Converter tests use small synthetic ODS packages built in the test. No ODS file is committed.

The converted JSON ships in the core under `compliance/data/gsa/`, so every consumer
assesses against one question set and nothing depends on a copy in a system repository.

- The source workbooks carry a CUI banner. The requesting maintainer stated that the converted
  content is cleared for public release; `compliance/data/gsa/PROVENANCE.md` records that
  statement, the redactions applied, and the refresh procedure. It is not an independent
  classification review, and the pull request that adds the data needs a human reviewer to
  confirm it.
- The ODS files themselves are never committed. The system-content guard rejects office
  documents, and the data is added only as JSON.
- Only converted workbook content is allowed there. The test-case file ships with every
  observation, result and recommendation blank, and tests fail on system names, URLs, e-mail
  addresses, UUIDs, IP addresses or `CUI//` markings.
- Consumers load the bundled data by default (`load_bundled`) and may point at their own
  files. Consumers do not need to commit or regenerate it.

## Consequences

- Consumers drop their private converters and call the core; drift between them ends.
- A consumer can prove its committed JSON is faithful with `--check` and structurally valid
  with `--validate`, and the second needs no ODS.
- Refreshing the data is a reviewed change: `--bundled`, update `PROVENANCE.md`, open a PR.
- The schemas set a stable contract (`kind`, `version`, closed property sets) for the
  assessment engine that follows in a separate change.
- Workbook layout changes (column order, sheet names) fail loudly rather than silently.

## Not in this change

- The assessment engine (crosswalk, proposed results, findings, attestation template).
- A skill for it, and any adapter for system-specific prose.
- Migration of the consuming repositories.
