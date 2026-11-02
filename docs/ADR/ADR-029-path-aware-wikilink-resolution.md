# ADR-029: Path-aware wikilink resolution and a vault graph audit

**Status:** Proposed
**Date:** 2026-11-02
**Deciders:** Project architecture agent + user
**Phase:** R22 (vault graph, PR 1 of 5)

---

## Context

A review of the 27 generated vaults in `oscal-vault` (5,836 notes, 30,816 resolved links) found two
problems with how the graph is measured, before any question of how it is built.

1. **Stem matching.** `validate_vault.check_orphan_notes` and `check_duplicate_note_names` matched a
   link to a note by `Path(target).stem`. Every vault has a System and a Component with the same
   name (`Systems/data.gov`, `Components/data.gov`), so a link to either counted as a link to both.
   A note could be reported connected only because a same-named sibling was linked. The duplicate
   check had the opposite weakness: it only looked for bare targets and could not say which
   candidates a bare link was ambiguous between.
2. **No baseline.** Nothing recorded the graph's shape, so a change that doubled the orphans or
   added hundreds of dangling links would pass. ADR-014 keeps wikilink checks warn-only, which is
   right for legacy debt, but a warning with no number cannot be tracked.

Measured with the new resolver on the current `oscal-vault` `main`:

| Metric (27 vaults) | Value |
| --- | --- |
| Content orphans (no inbound, no outbound) | 155 (Decisions, Diagrams, Inheritance, Runbooks 26 each; Boundaries, Relationships 23; Privacy 4; CRM 1) |
| ... of which only embed images | 26 (the Diagram notes) |
| Exempt orphans (Dataview/Bases-surfaced infrastructure notes, templates) | 235 |
| Unresolved links | 20 (26 `[[diagrams/]]` template slots excluded; the rest are enhancement controls absent from that system, e.g. `Controls/AU-02(03)`) |
| Ambiguous bare links | 0 |

## Decision

1. **`compliance/lib/vault_graph.py`** resolves links as Obsidian does for one vault: case-insensitive,
   path-qualified or suffix-qualified targets, bare targets by file name, `|alias`, `#heading` and
   `^block` ignored, fenced code blocks ignored, embeds counted, and notes tried before attachments
   (so `data.gov` is a note, not a missing `.gov` file). A bare name that matches several notes is
   **ambiguous**: it is reported with its candidates and creates no edge. It is never resolved to
   an arbitrary one of them.
2. **`validate_vault.py`** `check_orphan_notes` and `check_duplicate_note_names` use the resolver.
   They stay advisory (warnings, errors under `--strict`), per ADR-014. The exemption set is
   unchanged: `okf.*` infrastructure types, `gardener-ignore`, template notes.
3. **`audit_vault_graph.py`** prints per-vault metrics, writes them as JSON (`--out`), and with
   `--baseline` exits 1 if `orphans_content`, `unresolved_links` or `ambiguous_links` got worse for
   any vault present in the baseline. A vault absent from the baseline is not a regression. A
   missing or unreadable baseline is an error, not a pass.
4. Attachment-only notes are reported separately (`orphans_attachment_only`). Obsidian's graph hides
   attachments by default, so a note whose only links are images looks disconnected there even
   though the stem check used to count the embed as an outbound link.

## Consequences

- Orphan counts go **up** where the old check was wrong: 129 -> 155 on the current vaults. Each
  vault gains one orphan, the Diagram note, which the old check counted as linked because of its
  image embeds. This is a more accurate number, not a regression in the vaults, and the first
  baseline is taken with the new resolver.
- The duplicate-name check finds nothing new on the current vaults (0 ambiguous), because the
  generator already path-qualifies same-named links. It now guards that property, and names the
  candidates when it fails.
- No generated output changes in this ADR. Fixing the orphans (linking extension notes to their
  system, attaching providers) is a separate decision (R22 PRs 2 and 3).
- Standard library plus PyYAML. No dependency on Obsidian.
- Obsidian's actual shortest-path rules for ambiguous bare links are approximated, not reproduced.
  A bare link that Obsidian would resolve (for example by proximity to the linking note) is
  reported here as ambiguous. That errs toward reporting, and the generator path-qualifies its
  links so it does not arise in generated output.

## Alternatives considered

- **Keep stem matching and special-case System/Component.** Rejected: it hides the next collision.
- **Resolve an ambiguous bare link to the closest note.** Rejected: guesses, and a guess in a
  compliance graph is worse than a reported ambiguity.
- **Fail the build on orphans.** Rejected for now (ADR-014): 155 known content orphans. The
  baseline gate stops it growing, and can be tightened once PR 2 links them.
