# ADR-020: Control-Table Structural Fill (R17) — Closing the Roundtrip Coverage Gap, and Decommissioning the TypeScript docx Generator

**Status:** Accepted (amended by ADR-022)
**Date:** 2026-09-22
**Deciders:** Project architecture agent + user
**Phase:** R17 (control-table roundtrip fill)

---

## Context

A fidelity evaluation of the docx -> vault -> docx roundtrip pipeline (this
session) measured two independent generator paths against the goal of 99%
fidelity + content coverage:

1. **`tools/docx-ts-template/`** — a side-by-side TypeScript port (using
   `dolanmiu/docx`) of the Moderate SSPP template, built as a diagnostic
   parity harness, never a production generator (its own README says so).
   Direct measurement against the Python-generated Moderate output it
   mirrors: **13.7% of the file size** (137KB vs 1MB), a single hardcoded
   `Normal` style vs. 452 real style definitions in the source template, no
   headers/footers/images/fonts/numbering/docxtpl-tag support, and
   ragged-table cell-count divergence (`dolanmiu/docx` pads every row to the
   max column count; the source keeps ragged rows). It cannot recreate the
   original templating or instructions by construction — it rebuilds from a
   flattened `outline.json` that has already discarded all of that
   information during extraction.

2. **`compliance/lib/docx_jinjafier.py` + `docx_fill.py`** (Phase R16) — DOES
   preserve full template fidelity (verified directly: headers, footers,
   fonts, numbering, TOC fields, bookmarks, styles, instruction-block colors
   all survive; `render_check.py` confirms LibreOffice renders the output
   cleanly). But it only fills a **hand-curated narrative-paragraph subset**
   per template (Low: 7 slots, Moderate: 52, High: 7, CRM: 2) against
   **150-410 control-summary (CIS) tables per template** that carry the
   actual FedRAMP control-implementation content (Implementation Status,
   Control Origination, per-part narrative). The write-side scaffolding for
   table fills (`writeStrategy: table_after_instruction`, `_inject_table_tag`)
   existed in code but was **never used by any committed contract** — every
   `writeStrategy` value across all four roundtrip contracts was
   `paragraph_after_instruction`. Live measurement against a real filled
   Moderate output (`fpdf-moderate-sspp.docx`) confirmed **0 of 175 CIS
   tables** had any checkbox touched by this path.

A third, separate generator (`compliance/lib/docx_generator.py`) already
losslessly round-trips control-implementation data (proven by
`test_docx_generator.py`'s full-corpus regression test) — but by rebuilding a
**new, minimal** document from scratch with plain `python-docx`. It has zero
template/instruction/styling fidelity. The two halves — full-fidelity
narrative fill (R16) and lossless control-data round-trip (docx_generator) —
had never been merged into a single pipeline that does both.

### Why the TS path can't be fixed in place

`docx-ts-template`'s architecture is fundamentally extraction-lossy: its
`outline.json` fixture strips styling, headers/footers, images, and section
properties *before* the TS side ever sees the document, then rebuilds a new
document via `dolanmiu/docx`'s API from that flattened representation. There
is no path to recovering fidelity without either (a) porting the full OOXML
manipulation surface `python-docx` already has in this codebase to
TypeScript, which duplicates ~2,500 lines of proven Python across two
languages and two dependency ecosystems for no functional gain, or (b)
changing its scope to "mutate an existing template in place" the way R17
does — at which point it's a second implementation of exactly what
`control_table_fill.py` now does, in a second language, with no offsetting
benefit (no consumer of this codebase's outputs requires a Node/TS runtime;
`docxtpl`/`python-docx` are already the load-bearing dependency for R16).

## Decision

**1. Decommission `tools/docx-ts-template/` in the parent
a downstream system repository.** It is deleted (source, fixtures,
`package.json`/lockfile, generated `build/` artifacts) as part of this
change. It was never wired into any CI gate, `Makefile` target, or
production path — grepping the parent repo's `Makefile` and
`.github/workflows/` confirms zero references — so removal has no
functional impact beyond deleting the tool itself.

**2. Fully implement the Python jinjafier/fill path for Low, Moderate, and
High templates by adding a control-table structural fill (Phase R17), which
is additive to (not a replacement for) the R16 narrative-slot pipeline.**

Concretely, three new modules in `compliance/lib/`:

- **`control_checkbox_vocab.py`** — the write-direction mirror of
  `fismatic.parser`'s `_STATUS_KEYWORDS` / `_ORIGINATION_KEYWORDS` regexes
  and `_status_to_oscal_state` / `_origination_to_oscal_list` mappings.
  Deliberately does **not** hardcode a fixed checkbox label *set* (the
  official templates don't all offer the same options per block — e.g. no
  official template's Implementation Status block has a "Configured by
  Customer" checkbox even though `fismatic.parser` recognizes that state on
  read); instead it matches each checkbox's own printed label live against
  the same regexes the read path uses, so read and write can never drift on
  *which labels exist* — only ever on *whether a box is checked*.

- **`control_table_anchors.py`** — a pure structural detector for CIS tables
  (keyed on row 1 col 0 starting with `"Implementation Status:"`, which is
  stable across every committed template and every row/column-count
  variant), returning cell handles for the status/origination checkbox
  blocks, any "Part `<key>`" narrative rows, and the single blank
  fully-merged answer row present when a control has no Part breakdown.
  Verified directly against all four committed templates: 159/331/410/1 CIS
  tables detected in Low/Moderate/High/CRM respectively, matching the
  corpus baselines in `docs/PARSING_ACCURACY_NOTES.md`.

- **`control_table_fill.py`** — fills an **existing** CIS table's checkboxes
  and narrative **in place** (no cloning, no Jinja injection — the blank
  templates already have exactly the right table shape per control), from a
  list of `CanonicalControl` records. Matches controls to tables via a
  zero-padding-insensitive control-ID key (`"AC-2(1)"` == `"AC-02(01)"`,
  since the official templates print zero-padded headings while parsed-SSP
  `CanonicalControl.control_id` values typically aren't).

One subtlety, discovered empirically and documented in the module docstring:
the official templates' checkbox runs are frequently wrapped in a
Google-Docs-artifact `<w:sdt>` (structured-document-tag) element with no
actual checkbox semantics. Mutating a `<w:t>`'s text *inside* an untouched
`<w:sdt>` wrapper silently failed to persist through `python-docx`'s
save/reload cycle (reproduced directly — the in-memory lxml element showed
the new text correctly, but reloading the saved file reverted it). The fix
(`_unwrap_sdt_runs`) promotes the `<w:sdtContent>`'s children up to the
paragraph and discards the wrapper before mutating — a lossless transform
since the wrapper carried no visible content of its own.

**Round-trip proof, not assumption:** `compliance/tests/test_control_table_fill.py`
fills every CIS table in the committed Low template (159 tables) with a
synthesized status/origination combination per control, using only the
checkbox options that are structurally present in that template (see the
vocab module's docstring on why the label *set* varies by template), then
re-parses via **fismatic's own, completely unmodified** `parse_control_table`
+ `extract_implementation_details` and asserts every single control's
status, origination, and per-part narrative matches exactly. Lighter
detection-coverage tests cover Moderate (331 tables) and High (410 tables).
A LibreOffice `render_check.py` pass confirms the filled output remains a
valid, renderable `.docx`.

**3. `roundtrip_demo.py` now runs both fill passes** for each registered
template: the existing R16 narrative-slot fill + re-extract, and the new R17
control-table fill + fismatic-readback verification (toggleable via
`--skip-control-tables`).

### Two pre-existing, independently-confirmed quirks (not R17 regressions)

While building the full-corpus round-trip test, two issues were found that
predate this work and are **not** caused by `control_table_fill.py`:

1. **A handful of duplicate `control_id` values per template** (`RA-03`
   appears twice in Low/Moderate; `high-sspp` additionally has `IA-08(04)`,
   `SA-22`, `SA-03` collide with a differently-keyed heading nearby). These
   are pre-existing ambiguities in the *blank template's own row-0 heading
   text* (verified: the table's `row0.cells[0].text` is genuinely identical
   for both instances even though their preceding `Heading 3` context
   differs, e.g. `RA-03` vs `RA-03(01)`) — not something this module's
   detection or matching introduces. The test explicitly documents and
   skips strict comparison for these known duplicates.
2. **`fismatic.parser`'s own origination read-regex has a pre-existing
   false-positive**: its `service_provider_corporate` pattern is a bare
   `re.search(r"common\s+control|service\s+provider\s+corporate", ...)`
   with no anchor, and the Low/Moderate/High templates' own placeholder text
   for the "inherited" checkbox is *"Inherited from the Common Control
   Catalog (CCC)"* — which contains the literal substring "Common Control"
   regardless of what any writer puts there. Reproduced independently of
   this module (directly feeding that exact string, checked, into
   `fismatic.parser.parse_control_origination`). Filed as a fismatic
   follow-up (not fixed here, since it's a pre-existing read-side bug
   orthogonal to R17's write-side scope); the round-trip test tolerates it
   explicitly with a comment explaining why.

## Consequences

**Positive**
- Closes the primary coverage gap identified in the fidelity evaluation:
  previously ~7-52 narrative tags per template were fillable; now every
  detected CIS table (159/331/410 in Low/Moderate/High) is fillable, while
  *keeping* 100% of the R16 narrative-slot capability and 100% of the
  original template's styling (fonts, headers/footers, numbering, TOC,
  bookmarks) — verified by the same `render_check.py` LibreOffice gate R16
  already used.
- The two previously-disjoint halves (full-fidelity-but-narrative-only R16,
  and lossless-control-data-but-no-fidelity `docx_generator.py`) are now
  both available against the **same** output document: R16 fills the
  system-level narrative slots, R17 fills the per-control CIS tables, on the
  identical jinjafied `.docx`.
- Zero new runtime dependencies (pure `python-docx`, same as everything else
  in `compliance/lib/`).
- Removes an unmaintained, admittedly-non-production diagnostic tool
  (`docx-ts-template`) and its separate `node`/`npm` toolchain requirement
  from the repository, simplifying the dependency surface with no loss of
  capability (nothing consumed its output; it was never wired into any
  build/test/CI path).

**Negative / risks**
- Control-table fill only covers what `CanonicalControl` already carries
  (status, origination, statement text via the new
  `statement_text_map_from_oscal_ssp` helper, responsible role is not yet
  wired into the write path — `CanonicalControl.responsible_role` exists but
  no committed template in this corpus has a "Responsible Role" row to write
  it to; see `docs/PARSING_ACCURACY_NOTES.md`). It does **not** address the
  five extraction-side gaps identified in the fidelity evaluation (`props[]`,
  `links[]`, multi-component breakdown, `back-matter.resources`,
  `system-implementation.users`) — those require new *extraction*, not
  writeback, and are unaffected by this change.
- The two pre-existing quirks above (duplicate control IDs, the
  `service-provider-corporate` false-positive) remain open; they're
  documented and tolerated in the test, not fixed, since they're
  independent of this ADR's scope (write-path structural fill) and fixing
  the read-side regex requires touching `fismatic.parser`, a separately
  versioned sub-package.
- `_unwrap_sdt_runs`'s workaround (promoting `<w:sdtContent>` children,
  discarding the wrapper) is a targeted fix for one observed failure mode;
  if a future template revision wraps checkbox runs in an `<w:sdt>` that
  *does* carry real form-field semantics (an actual `<w:checkBox>`), this
  would need to change to preserve that semantics instead of discarding the
  wrapper. Not currently observed in any of the four committed templates
  (confirmed via direct XML inspection: zero `<w:checkBox>` elements in any
  of them).

## Follow-up work

1. Wire `CanonicalControl.responsible_role` into `control_table_fill.py`
   once a template with a "Responsible Role" CIS row is added to the
   corpus (none of the four current templates have one — confirmed via
   direct search).
2. File the `fismatic.parser` `service_provider_corporate` regex
   false-positive as its own fismatic-side fix (anchor the pattern or
   special-case the literal placeholder text), independent of this ADR.
3. Extend the roundtrip datacontracts' write metadata (`writeStrategy`) with
   a `control_table_block` value that documents (rather than replaces) the
   R17 structural-fill path, so a future contract-driven-only refactor has a
   declared seam instead of `control_table_fill.py`'s current
   convention-over-configuration detection.
4. Consider whether `docx_generator.py` (the from-scratch generator) is now
   fully superseded by R16+R17 combined for any caller that needs both
   fidelity and control data; if so, evaluate deprecating it in a future
   phase (out of scope here — it still has its own passing regression test
   and no caller has been migrated).
