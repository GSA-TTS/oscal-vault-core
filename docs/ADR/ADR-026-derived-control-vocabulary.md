# ADR-026: Derive Control Vocabulary From Raw OSCAL Fields

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** Project architecture agent + user
**Phase:** R21 Increment 2b

## Context

Each generated control note currently duplicates its raw OSCAL vocabulary:
`implementation_status` is mapped into `normalized_status`, and
`origination[]` is mapped into `normalized_origination`. The mappings already
exist as generated `Mappings/implementation-status.md` and
`Mappings/control-origination.md`, so materializing their outputs in every
control duplicates derived data and causes full-corpus churn when a mapping
changes.

The downstream SSPP Precogly projection originally read those normalized
fields directly. SSPP PR #323 changes that reader to derive absent values from

## Decision

SSPP PR #323 merged on 2026-09-28, providing the downstream compatibility
fallback. This repository now:

1. Stop emitting `normalized_status` and `normalized_origination` in generated
   control frontmatter and templates.
2. Keep raw `implementation_status` and `origination[]` as the lossless vault
   source of truth.
3. Keep mapping notes as the visible, versioned derivation authority.
4. Update native Bases and Dataview displays to use raw fields/tags rather than
   the removed duplicates.
5. Regenerate source-derived vault artifacts and canonical sidecars.

## Consequences

- A mapping edit changes two mapping notes rather than every control note.
- Downstream Precogly/CDX output remains identical through the reader fallback
  supplied by SSPP PR #323.
- Legacy human-authored normalized fields remain readable by SSPP, but the
  oscal-vault generator no longer creates them.

## Verification

- The merged SSPP reader must parse a normalized-free regenerated vault.
- Source regeneration, OSCAL export schema validation, and roundtrip parity
  must pass.
- Tests prove generated notes omit normalized fields while raw fields map to
  expected targets through `compliance.lib.vocab`.
- A local SSPP conversion against the regenerated vault must produce unchanged
  normalized Precogly/CDX output.
