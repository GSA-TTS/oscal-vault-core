# ADR-032: Official CRM Roundtrip Template Family

**Status:** Proposed
**Date:** 2026-11-13
**Deciders:** Project maintainers

## Context

Leveraged-SaaS customer-responsibility matrices use `CR-#` identifiers rather
than a standard NIST CIS table identity. Local Figma, Snyk, and Zendesk SSPs
show the same six-row structural pattern: identifier, implementation status,
customer responsibility, controls reference, describe-how prompt, and customer
narrative. Those SSPs are system content and cannot be committed to the public
core.

The existing CRM roundtrip registry pointed to an absent local source document,
so a clean checkout could not run its CRM roundtrip path. Treating a CRM table
as a standard CIS table is unsafe because its responsibility and reference rows
are source prose, not control-origination checkbox targets.

## Decision

Commit the public official `Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx`
blank and its generated Jinja template. The blank contains generic `CR-#`
identifiers, standard status labels, bracketed placeholders, and generic
instructions; it contains no system implementation content.

CRM anchors identify the six-row shape, expose only the status and narrative
answer cells as writable, and leave Customer Responsibility and Controls
Reference rows untouched. `CR-#` remains the primary identifier; Controls
Reference is reference data and does not replace that identity.

## Consequences

- A clean checkout can exercise the official CRM-shaped roundtrip without a
  customer SSP.
- No Figma, Snyk, Zendesk, provider, control-reference, or implementation
  narrative is copied into the template artifacts.
- The system-content guard remains fail-closed: the official blank and the
  generated Jinja template are hash-pinned in its protected allowlist.
- This validates the layout and write boundaries, not the semantic correctness
  of any customer responsibility assignment.
