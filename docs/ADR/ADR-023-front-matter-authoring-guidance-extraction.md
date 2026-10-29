# ADR-023: Chapters 1-12 Front-Matter Authoring Guidance Extraction (R19)

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Project architecture agent + user
**Phase:** R19 (front-matter authoring-guidance capture)

---

## Context

ADR-022 (R18) added `compliance/lib/control_guidance.py` to recover the blue
(`#0070C0`) / `Instruction:`-prefixed authoring guidance and attach it as a
folded callout on each **control** note. That extractor walks the document body
but only attributes guidance to a control once the first control heading (the
Chapter 13 control section) has been seen; any instruction paragraph that
appears **before** the first control heading was silently dropped.

Measuring the Moderate blank template showed this gap is large: of the 567
instruction paragraphs in the document, **143 live in the Chapters 1-12 front
matter** (before the control section) and were never surfaced anywhere in the
vault. These front-matter instructions are exactly the prompts that tell an SSP
author how to fill the narrative sections — network architecture, data flow,
types of users, external services, DevSecOps, container, and AWS runbook
sections, digital-identity/IPv6 decisions, and so on. The narrative notes
(`Boundaries/`, `Relationships/`, `Runbooks/`, `Decisions/`, `Inheritance/`)
carried the recovered *content* (via `heading_parser`) but not the *authoring
guidance* that accompanies it in the template.

## Decision

Extend `compliance/lib/control_guidance.py` with a companion **read-only**
extractor, `extract_section_guidance()`, that walks the document body **up to**
the first control heading and groups every instruction paragraph under its
nearest preceding section heading (Word `Heading N`/`Title` style or a numbered
heading like `9.5 Network Architecture`). It returns a
`{heading_text: SectionGuidance}` map, mirroring the control-guidance shape.

`build_narrative_notes` (in `compliance/lib/vault.py`) now accepts an optional
`section_guidance` map. For each rendered narrative section whose heading
matches a recovered guidance section, the section's instructions are appended
as a folded `> [!note]- Template guidance` callout — the same R16.6 hidden-
instruction convention ADR-022 uses for control notes. Heading matching is
fuzzy: `_normalize_heading_key` lowercases and strips leading section numbers so
`"Network Architecture"` on the note matches `"9.5 Network Architecture"` in the
template.

`export_obsidian_vault.py` calls `extract_section_guidance(args.docx)` in the
`.docx` branch and threads it into `build_vault(section_guidance=...)`, which
forwards it to `build_narrative_notes`. When no `.docx` is supplied (the
fismatic-only / OSCAL-JSON path) the map is empty and behavior is unchanged.

## Consequences

- **Coverage:** the ~140 front-matter instruction sections per template are now
  recovered and rendered as folded callouts on the corresponding narrative
  notes (Boundaries, Relationships, Runbooks, Decisions, Inheritance) instead of
  being dropped. Regenerated example template vaults carry the new callouts.
- **Non-destructive:** extraction never mutates the document; the fill/roundtrip
  pipeline (R16/R17, ADR-020) and the control-guidance extractor (R18, ADR-022)
  are unaffected.
- **Folded by default:** as with control guidance, the section guidance stays
  collapsed so narrative notes keep their clean layout.
- **Completes ADR-022** by covering the Chapters 1-12 front matter that the
  control-keyed extractor structurally could not reach.

## Alternatives considered

- *Lower the R18 extractor's start guard so front-matter instructions attach to
  the "current" (still-None) control* — rejected: front-matter guidance is not
  control-scoped; keying it by section heading is the correct model and keeps
  the two extractors independent and testable.
- *Render front-matter guidance on the System note only* — rejected: the
  guidance spans many distinct narrative sections that already map to separate
  extension notes (Boundaries/Relationships/Runbooks/Decisions/Inheritance);
  attaching each section's guidance to its own note preserves locality.
