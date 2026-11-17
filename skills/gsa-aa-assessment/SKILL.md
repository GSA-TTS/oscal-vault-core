---
name: gsa-aa-assessment
description: Build and review a system's GSA Assessment and Attestation (A&A) by crosswalking the GSA Control Tailoring Workbook and the GSA NIST SP 800-53A test-case workbook (canonical JSON) against a neutral system input. Proposes desk-review results, findings, a POA&M list and an unsigned attestation; assessor and AO confirm.
---

# GSA A&A assessment

Act as an independent assessor. You propose results from documented evidence. You never
confirm them, and you never fill in an AO decision, name, signature or date.

## Inputs

| Input | Role |
| --- | --- |
| `gsa-lato-low-test-cases.canonical.json` | Assessment objectives (Examine, Interview, Test), the assessment questions. |
| `gsa-control-tailoring-workbook.canonical.json` | Control statement, parameters, baseline and overlay flags. |
| System input JSON | One neutral document emitted by the system repository's adapter. Schema: `compliance/schemas/assessment/system_assessment_input_schema_v1.json`. |

The core never reads a system's own files. If the system has no adapter yet, write one in
the system repository; do not add system-specific code to the core.

## Procedure

```bash
# 1. convert workbooks once (ADR-034), then check committed JSON matches the workbooks
python compliance/scripts/convert_gsa_workbooks.py --tailoring-workbook CTW.ods --test-case-workbook TESTS.ods --check
python compliance/scripts/convert_gsa_workbooks.py --validate data/catalog/*.canonical.json

# 2. build the assessment (workspace-relative output; --check fails on drift)
python compliance/scripts/build_gsa_assessment.py --system-input build/assessment/<slug>-assessment-input.json
python compliance/scripts/build_gsa_assessment.py --system-input ... --check

# 3. POA&M issues: dry run, inspect one, then file (needs gh and a label)
python compliance/scripts/build_poam_issues.py --assessment build/assessment/<slug>-aa-assessment.json --work-items <work-items.yaml>
python compliance/scripts/build_poam_issues.py ... --show AC-3
python compliance/scripts/build_poam_issues.py ... --create --label poam --issue-map <poam-issues.yaml>
```

## Proposed results

| Result | Meaning |
| --- | --- |
| `SATISFIED_PENDING_CONFIRMATION` | Mechanisms documented, no open residual or DRAFT marker. |
| `CONDITIONALLY_SATISFIED` | Documented, but residuals, DRAFT markers, or prose-only evidence. |
| `OTHER_THAN_SATISFIED` | Open GAP residual or not-implemented status. POA&M required. |
| `INHERITED_PENDING_PROVIDER_EVIDENCE` | Fully inherited with no tenant record. Needs provider evidence. |
| `NOT_ASSESSED_NO_EVIDENCE` | No tenant record and nothing to inherit. |
| `NOT_APPLICABLE` | Status not-applicable, or outside the system baseline. |

## Findings

AA-1 High no tenant record for an in-baseline, non-inherited control. AA-2 High open GAP or
not-implemented. AA-3 Medium DRAFT pending ISSO review. AA-4 Medium open residual. AA-5 Medium
status conflicts with residuals or model status. AA-6 Medium objective part has no prose.
AA-7 Low control outside the tailoring workbook. AA-8 Low outside the system baseline but
selected. AA-9 Medium all linked evidence is gap observation. High findings block attestation.

## Boundaries

- Test-case workbooks are categorization-specific. LATO-Low objectives are a floor for a
  Moderate system; other controls get desk review only.
- CI and workflow evidence is listed as claimed. It is not a captured passing run, so the
  `test` method stays pending for an assessor.
- Never put emails, hostnames, IDs or CUI banner text into confirmations or work items; the
  builders fail on URLs, e-mail addresses and CUI banners.
- Do not change responses or records during an assessment. Use remediation work for that.
- Every `OTHER_THAN_SATISFIED` control needs a tracked issue with evidence, validation tests
  and acceptance criteria. Close-out content is curated by the system team, not generated.
