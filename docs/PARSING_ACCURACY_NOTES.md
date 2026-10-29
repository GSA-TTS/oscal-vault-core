# Parsing Accuracy Notes

This document tracks aggregate, anonymized findings from running the OSBAPI
generation pipeline against the corpus of real FedRAMP SSP `.docx` files
available under `fismatic/`. It is updated manually by running
`bash scripts/corpus_report.sh` and reviewing the per-document metrics.

These findings inform parser improvements and regression tests. No
per-organization or per-system specifics that shouldn't be public are
included.

## Corpus summary (last run: 2026-07-05)

| Document | Controls | % Operational | Notes |
|---|---|---|---|
| <SSP> | 159 | 86.2% | Reference baseline; good parsing |
| Site Scanner_Low-SSPP-Draft | 158 | 11.4% | ~140/158 show not-implemented → checkbox recognition issue |
| <Moderate SSPP> | 402 | 0.2% | Near-total parse failure; uses ✓ (checkmark) instead of ☒. ✅ **Fixed** in Phase 12 (`49245b2`) -- the parser's `_is_checkbox_line()` now recognizes `✓`, `⊠`, `x`/`X`, and `[x]`/`[X]` in addition to the standard `☒`. Re-run the corpus report to confirm improved coverage. |
| May2024 USE THIS Working Copy of SSB SSPP | — | — | Uses ⊠ (squared checkmark) and x (plain-x) characters. ✅ Same Phase 12 fix covers these glyphs. |

## Known parsing gaps

### 1. Non-standard checkbox characters  ✅ Fixed in Phase 12 (`49245b2`)

The parser's `_is_checkbox_line()` function in `fismatic/src/phase_1/fismatic/parser.py`
**now** recognizes all of the following as "checked":

- `☒` (U+2612, Ballot Box with X) — standard FedRAMP template character
- `✓` (U+2713, Check Mark) — used in some GSA SSP versions
- `⊠` (U+22A0, Squared Check Mark) — used in some newer templates
- `[x]` / `[X]` (bracket-enclosed X) — common alt format
- `x` / `X` (bare) — used in text-based checkbox blocks (with word-boundary guard)

The fix was applied in three locations: `_is_checkbox_line()`, `parse_implementation_status()`,
and `parse_control_origination()`. Re-run `bash scripts/corpus_report.sh` against the
corpus to get post-fix metrics.

### 2. Control ID format variations

Several real SSPs use `AC-01` (two-digit control numbering) instead of the
standard `AC-1`. The `text_matcher.py` regex `([A-Z]{2}-\\d+)` handles this
(one or more digits), but case-by-case testing is needed.

### 3. Uppercase count differences

The blank Low baseline yields ~100 controls, Moderate ~200, High ~300. The
a real Low SSP yielded 159 (includes enhancements). These counts
vary by template version and which controls the system has scoped in.

### 4. Narrative statement loss

`fismatic`'s parser drops narrative "Part a/b/c" rows that are embedded
inside the CIS table row structure (rows 4+), only capturing the
controlled-vocabulary fields (status, origination, role) from rows 0-3.
The `compliance/lib/docx_generator.py` module reconstructs these as
separate single-row continuation tables, achieving better narrative
round-trip than the source document's own layout — but the original
document's narrative is still lost at the first parse step.

This is acceptable for the OSBAPI pipeline's current scope, since
controlled-vocabulary fields are the primary signal for CRM/Backstage
projections. Narrative preservation is a future-hardening concern.

## Common fix patterns

When adding support for a new checkbox character, make the same change in
these three locations (or the two functions they feed into):

1. `parser._is_checkbox_line()` — recognizes whether a line has a checked
   box at all (used to skip unchecked lines).
2. `parser.parse_implementation_status()` — checks `line.startswith("☒")`
   or matching regex to distinguish checked→unchecked.
3. `parser.parse_control_origination()` — same check as above.

Search for `"☒"` and `"\\[ [xX]\\]"` in `parser.py` to find all locations.
