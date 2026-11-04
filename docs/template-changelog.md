# Starter template changelog

This log records intentional corrections to the committed blank starter
templates and their generated `compliance/templates/*.jinja.docx` counterparts.
It is not a substitute for the authoritative FedRAMP or agency template release
history.

## Low SSPP starter — 2026-11-04

- Corrected the second `RA-03` control-summary table heading to `RA-03(01)`.
  The section was already titled `RA-03(01)` but its table heading duplicated
  `RA-03`, preventing deterministic control-table matching.
- Removed the `CA-08(02)` section and control-summary table. It is absent from
  the versioned 158-control GSA Low assessment reference used for coverage
  reconciliation.
- Regenerated `compliance/templates/low-sspp.jinja.docx` from the corrected
  blank source and the existing Low round-trip contract.

## Moderate, High, and Leveraged SaaS CRM starters

No local corrections have been applied. These entries are retained so every
starter template has an explicit change record.
