# R14 Equal-Footing Plan

**Goal:** Make the 3 available backends (fismatic, mineru, unstructured) produce
parity on headings + images, so a 3-way benchmark reflects each backend's real
strengths/weaknesses, not config drift.

**Why:** The R13 benchmark found mineru extracts 76% more headings than fismatic
and unstructured extracts only 78 headings total (across 10 SSPs) because
`unstructured.partition.docx` only promotes `Title` style to a heading — and the
FedRAMP SSP template uses `Heading 1/2/3` paragraph styles for its body. Without
equal-footing, the contract-routing comparison is biased.

## 1. Current state (R13 baseline, on Site Scanner SSP)

| Backend | headings | ¶ | tables | images | h-levels |
|---|---:|---:|---:|---:|---|
| `fismatic` | 52 | 1,354 | 189 | 4 | {H1, H2} |
| `mineru` | 66 | 1,674 | 188 | 4 | {H1, H2, H3, H4, H5} |
| `unstructured` | 2 | 1,276 | 189 | 0 | {H1, H2} |

`unstructured` finds 0 images because it doesn't enumerate inline images by
default. `unstructured` finds only 2 headings because it only emits `Title` for
top-level Word styles.

## 2. Customizations needed (per backend)

### 2a. FismaticBackend (was: not customized)
- **Heading-numbering prefix stripping** — the heading `"10 **System Environment**"`
  in mineru's output should match the contract's `"System Environment"` pattern.
  In fismatic's output this isn't an issue (fismatic reads python-docx text
  directly without markdown bold markers), but a `normalize_heading_text()` helper
  on `ExtractedDoc` will let downstream routing match the same heading across
  backends. → **Done in §3 below.**
- **Add inline image detection** — already done in R12 (the `images=[] → 92`
  fix from the inaugural). Status: complete.

### 2b. MinerUBackend (was: customized in R13)
- ✅ Migrated to v3 `do_parse` API
- ✅ HTML table parsing
- ✅ Image extraction via `content_list.json` heading attribution
- ✅ Heading-numbering prefix stripping — but currently doesn't strip the
  `**bold**` markdown markers. → **Done in §3 below.**

### 2c. UnstructuredBackend (was: NOT customized, this is the gap)
- **Augment with python-docx heading-style reads** — for every paragraph in
  the docx, check the `style.name`. If it starts with `"Heading"`, emit a
  heading instead of letting unstructured classify it as `Text`. This is the
  same approach FismaticBackend uses.
- **Add image detection via XML** — same `_detect_image_rids` helper that
  FismaticBackend uses, applied to the docx XML directly (unstructured's
  image support requires `extract_image_block_to_payload=True` and produces
  a different schema).
- **Preserve unstructured's typed elements** — keep the existing
  `partition_docx` output, but **merge** in the python-docx headings. The
  final heading list is a union: python-docx H1–H6 + any unstructured `Title`
  elements that python-docx didn't classify as headings (e.g. cover-page
  titles like "U.S. General Services Administration").
- **Add element-level metadata for paragraphs** so we can keep the "Narrative
  text" vs "List item" distinction that unstructured provides (fismatic
  collapses all of these into "paragraphs").

## 3. Shared helpers (extracted to `ExtractedDoc` class)

- `ExtractedDoc.normalize_headings()` — strips leading chapter-number prefixes
  (`r'^\d+(\.\d+)*\s+'`) and trailing bold markers (`r'\*\*$'`). Pure method,
  no side effects, idempotent.
- `ExtractedDoc.heading_text(level=None)` — flat set of heading texts
  (optionally filtered to one level) for contract-routing comparison.
- `ExtractedDoc.heading_recall(other)` — returns the recall of this doc's
  headings vs another's (used by the benchmark harness to compute
  `heading_recall` in the cross-backend comparison tables).

## 4. Contract-routing changes

The R13 benchmark already showed that 343 unrouted headings is a separate
problem from backend parity — the contract's `sourceHeading` patterns are
tight even after the backends are equal. We will:

1. Add **`alias_sourceHeadings`** to the contract schema — a list of
   alternative heading texts that also satisfy the binding. Implemented as a
   list of additional `sourceHeading`-like patterns on the same property
   (ODCS v3.1.0 doesn't have a native array-of-aliases primitive, so we
   overload `customProperties` with a new key `aliasSourceHeading`).
2. For each of the 9 high-impact gaps from the inaugural report (§4), add
   an `aliasSourceHeading` to the closest existing property:
   - `U.S. General Services Administration` → alias on `oscal_ssp_system.title`
   - `System Environment` → new property on `oscal_ssp_system`
   - `System Data Nature` → new property on `oscal_ssp_system`
   - `Architecture of the CI/CD processes` → alias on `oscal_x_runbook.devops_devsecops`
   - `Cloud Service Models` / `Cloud Deployment Models` → new properties
   - `Personally Identifiable Information Processing and Transparency` → new schema object `oscal_x_privacy`
   - `10.1 Asset Inventory` → new property on `oscal_x_inheritance` or new `oscal_x_asset_inventory`

## 5. Testing strategy

- Update `test_extractor_backend.py` to assert each backend's heading count
  is at least 30 (currently fismatic ~52, mineru ~66, unstructured ~2 → 30+).
- Add new `TestExtractedDocNormalize` class to test the heading-strip helpers.
- Re-run the R13 3-backend benchmark — expect mean fill rate from 69.6% to 75%+.

## 6. Out of scope (deferred)

- Docling — deferred to a future phase (R15+). See ADR-016.
- r11 unified_converter tests — pre-existing failures, not in R14 scope.
