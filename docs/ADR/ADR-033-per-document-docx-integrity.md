---
title: Use accountable preservation for per-document DOCX integrity
description: Fail-closed local evidence rather than byte equality or demo exit-code inference
status: draft
tier: 2
date: 2026-11-16
last_updated: 2026-11-16
nist_controls: [AU-12, SA-11, SI-7, SI-10, SC-28]
impact_level: Moderate
ato_relevance: yes-internal
risk_treatment: mitigate
decision_makers: [Maintainer review pending]
related_files: [docs/DOCX_INTEGRITY.md, docs/ADR/README.md]
---

# ADR-033 — Per-document accountable DOCX preservation

## Status

Proposed; human maintainer acceptance pending. AI-assisted implementation.

## Context and problem statement

ZIP-byte identity is not a meaningful fill-back invariant. Existing demos may
return success for absent inputs or tolerate readback mismatches. Review needs
independent per-source evidence with an explicit loss/debt disposition.

## Decision drivers

- Fail closed on missing evidence, ambiguous content, timeout and new loss.
- Keep sources read-only and sensitive artifacts private, ignored and local.
- Separate package accounting, opaque retention, semantic export and fill parity.
- Require exact hash-bound approval for substantive OOXML changes.

## Considered options

1. ZIP-byte equality: rejects legitimate serialization and intentional writes.
2. Trust demo exit codes: risks vacuous/partial passes.
3. Four-layer local evidence and conservative normalized comparison: selected.

## Decision outcome

Add an opt-in single-document harness. Inventory using existing bounded package
inspection and IR. Retain full IR as explicit opaque fallback rather than
claim unverified canonical routing. Run actual canonical/export/semantic parity
validators, require nonempty artifacts, strictly re-extract final written slots
and control data, and compare normalized source/fill packages. Machine result
is independent of mandatory human sign-off. No new dependency or CI change.

## Consequences

Consumer validation must discover files relative to vault roots, not discard a
whole vault because an absolute ancestor is hidden. Canonical drift gates fail
on missing hashes and generation errors. Mapping parsing uses a bounded cache
keyed by contract content, so edits invalidate automatically; cached immutable
records are returned in caller-owned lists. Consumer verification delegates to
core without implicitly resetting the submodule or installing dependencies.

Positive: source hash continuity, reproducible evidence and no silent skipped
success. Negative: conservative part-level comparison can require substantial
human review; v1 opaque coverage is not complete per-field semantic lineage.
Known debt remains blocking for full PASS. Authorization is operator-attested,
not machine-enforced owner identity. Sensitive artifacts need approved local
retention controls. Review this ADR before treating the new gate as canonical.
