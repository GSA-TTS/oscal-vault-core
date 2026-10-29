# ADR-022: Per-Control Authoring Guidance Extraction (R18)

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Project architecture agent + user
**Phase:** R18 (control authoring-guidance capture)

---

## Context

The GSA rev5 blank SSP templates render two kinds of *authoring guidance* that
the docx -> vault export had been silently dropping:

1. **Section-level instruction blocks** — paragraphs in blue (`#0070C0`) or
   prefixed with `Instruction:`/`Note:` that explain how to fill a control
   family. In the source `.docx` these appear immediately after a control
   heading (e.g. the AC-02 heading is followed by the blue "Require [...] for
   group and role membership; ..." block).
2. **Per-control CIS-table prompts** — inside every control-summary (CIS)
   table, the row that reads `"<CID> Describe how the control is
   implemented."`.

The vault control notes (`Controls/<CID>.md`) captured the *structure* of each
control (parameters, statement parts, CRM guidance, status) but not this
light-blue authoring guidance. The user confirmed the `#0070C0` instruction
text is important to extract and retain, because it tells an SSP author *what*
each control expects — information that is lost once the blue text is stripped.

## Decision

Add `compliance/lib/control_guidance.py`, a **read-only** extractor that walks
the source `.docx` body in order and builds a
`{normalized_control_id: ControlGuidance}` map:

- Instruction paragraphs (detected via the existing
  `compliance.lib.instruction_anchors` color/prefix rules) that appear between
  one control heading and the next are attributed to that control.
- The CIS-table "Describe how..." prompt is attributed to the control whose id
  matches the table heading.

`build_control_note` renders the guidance as a **collapsed** Obsidian callout
(`> [!note]- Template guidance`), matching the R16.6 hidden-instruction style,
so the guidance travels with each control note but stays folded by default and
does not clutter the note body.

Guidance is re-keyed through `canonical_control_id` in `build_vault` so the
extractor's raw ids (`IA-02(2)`, `SI-012`) line up with the canonical ids the
control notes use (`IA-02(02)`, `SI-12`).

## Consequences

- **Coverage:** Low 158, Moderate 330, High 406 control notes now carry the
  recovered authoring guidance callout.
- **Non-destructive:** extraction never mutates the document; the fill/roundtrip
  pipeline (R16/R17, ADR-020) is unaffected.
- **Folded by default:** readers only see the guidance when they expand the
  callout, preserving the clean control-note layout.
- **Amends ADR-020** by closing the remaining *content* gap (authoring
  guidance) alongside the structural fill gap that ADR-020 addressed.

## Alternatives considered

- *Emit guidance as a normal `# Guidance` section* — rejected: it would push the
  Upstream/Parameters/Statement-Parts sections down and visually dominate the
  note. A folded callout keeps parity with the existing hidden-instruction
  convention.
- *Store guidance only in the canonical JSON sidecar* — rejected: the guidance
  is meant for human SSP authors reading the Obsidian note, so it belongs in the
  rendered markdown (and flows into the sidecar automatically).
