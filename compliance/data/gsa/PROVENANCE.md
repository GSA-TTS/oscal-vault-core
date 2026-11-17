# Provenance: bundled GSA workbook JSON

Canonical JSON converted from two GSA workbooks, shipped with the core so every consumer
assesses against the same question set instead of regenerating it.

| File | Source workbook | Version | Authority | Content |
| --- | --- | --- | --- | --- |
| `gsa-control-tailoring-workbook.canonical.json` | GSA Control Tailoring Workbook | 2026-08-01 | GSA OCISO ISA Division | 1,194 controls, baseline and overlay flags, 131 HVA overlay entries |
| `gsa-lato-low-test-cases.canonical.json` | GSA NIST SP 800-53A Rev 5 LATO-Low Test Case Workbook | 10/16/2024 | GSA OCISO ISP Division | 34 controls, 191 assessment objectives (Examine, Interview, Test) |

## Release determination

The source workbooks carry a CUI banner. The determination to release the converted content
publicly was made by the requesting maintainer, who stated that the content is cleared for
public release. This file records that statement; it is not an independent classification
review. A reviewer should confirm it before merge, and CODEOWNERS review applies.

## What was done to the content

- Converted with `compliance/scripts/convert_gsa_workbooks.py` (ADR-034); deterministic.
- URLs became `<REDACTED_URL>`, e-mail addresses `<POC_EMAIL>`, and `CUI//` banner tokens
  were removed. The workbook file names in `source_document` keep their original banner text.
- Checked for: no URLs, e-mail addresses, UUIDs, IP addresses, or `CUI//` markings remain.
- Validated against `compliance/schemas/assessment/` (closed schemas, count and ID checks).
- Historical assessment observations and results from the source workbooks are empty in the
  test-case file (the workbook ships blank), so no system assessment content is included.

## Refreshing

Maintainers only, from a standalone checkout, with the ODS files outside the repository:

```bash
python compliance/scripts/convert_gsa_workbooks.py \
  --tailoring-workbook CTW.ods --test-case-workbook TESTS.ods --bundled
```

Update this file (versions, counts) and open a reviewed pull request. `--bundled --check`
proves the committed JSON still matches the workbooks. Never commit the ODS files; the
system-content guard rejects office documents.

## Do not add

System names, assessor observations, evidence, internal URLs or identifiers, or any
system-specific assessment result. Those belong in a system repository.
