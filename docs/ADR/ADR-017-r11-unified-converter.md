# ADR-017: R11 Unified Contract-Driven Converter — Implementation

**Status:** Accepted
**Date:** 2026-07-19
**Deciders:** Project architecture agent + user
**Phase:** R11 (Unified Contract-Driven Extractor & Converter)

---

## Context

The R11 phase of the refactoring plan called for a single, fully
declarative converter driven entirely by YAML datacontracts — replacing
the legacy dual-path extraction (`fismatic.SSPConverter` +
`compliance.lib.heading_parser`) with a 5-stage pipeline:

1. **Hierarchy Extraction** — `.docx` → `DocumentTree`
2. **Rule-Based Routing** — `DocumentTree` + contract → routed nodes
3. **Strategy Execution** — capture strategy → extracted value
4. **Entity Assembly** — routed + contract → typed `ConvertedEntity`
5. **Concurrent Writing** — entities → vault markdown + JSON sidecar

The previous PR (PR #4, R13-R15) brought docling/mineru/unstructured/mammoth
to parity but only exposed them through the lower-level `extractor_backend.py`
API. The contract routing was still going through `heading_parser.extract()`,
which produces a flat dict of "objects" + "summary" rather than the
`ConvertedEntity` shape that R11 wanted.

The user's R11 plan was to consolidate this so the contract is the single
source of truth, and any of the 5 backends can be the source.

## Decision

R11 has been implemented end-to-end. The `UnifiedConverter` class wires
together all 5 stages and is the new top-level entry point for
contract-driven conversion.

### What changed

| File | Change |
|---|---|
| `compliance/lib/unified_converter.py` | All 5 stages implemented end-to-end. Previously each stage was a `NotImplementedError` stub. |
| `compliance/lib/unified_converter.py` | Added `DEFAULT_BACKEND = "mineru"` constant (per the R15 recommendation: "Default to `mineru` for structure, `fismatic` for speed"). |
| `compliance/lib/unified_converter.py` | Added `heading_paragraph_first` strategy (was a typo in the contract that the previous scaffolding rejected). |
| `compliance/lib/unified_converter.py` | `_entity_slug` accepts lists/dicts/None and coerces safely. |
| `compliance/lib/unified_converter.py` | `route_document_tree` now respects `captureScope: until_next_same_or_higher` semantics by including sub-heading content inline. |
| `compliance/tests/test_unified_converter.py` | Updated 2 tests to use a real `.docx` (was `b"PK"` magic bytes that python-docx couldn't parse). The end-to-end test was rewritten to assert the new behavior (returns `ConversionResult`) instead of asserting `NotImplementedError`. |
| `compliance/lib/extractor_backend.py` | Unchanged in this phase — `extractor_backend.py` is the lower-level primitive that R11's stage 1 delegates to. |

### Architecture after R11

```
.docx file
   │
   ├──> extract_document_tree()  (UnifiedConverter stage 1)
   │     │  delegates to extractor_backend.py
   │     │  default backend: mineru
   │     │  other backends: fismatic, docling, unstructured, mammoth
   │     ▼
   │   DocumentTree (headings, paragraphs, tables, images)
   │
   ├──> route_document_tree()  (stage 2)
   │     │  loads YAML contract
   │     │  matches each property's sourceHeading + aliasSourceHeading
   │     │  respects captureScope (until_next_same_or_higher)
   │     ▼
   │   Dict[destination_id, List[DocumentNode]]
   │
   ├──> execute_strategy() per property  (stage 3)
   │     │  strategies: heading_paragraph, heading_paragraph_first,
   │     │  heading_table_first, heading_table_all, heading_image_all,
   │     │  control_table, constant, derived
   │     ▼
   │   extracted value (str, list, dict, etc.)
   │
   ├──> assemble_entities()  (stage 4)
   │     │  walks each destination in declaration order
   │     │  applies constant / derived shortcuts
   │     │  emits required-property warnings
   │     ▼
   │   List[ConvertedEntity]
   │
   └──> write_entities()  (stage 5)
         │  writes <vault_dir>/<destination_folder>/<slug>.md
         │  writes <snapshots_dir>/<destination_folder>/<slug>.json
         ▼
       vault markdown + canonical JSON sidecars
```

### Performance

End-to-end R11 on a real Low SSP (19 destinations, 102 properties):

| Backend | Total time | Entities | Filled fields | Warnings | Errors |
|---|---:|---:|---:|---:|---:|
| `fismatic` | 2.8s | 19 | 80 | 1 | 0 |
| `mineru` | 14.4s | 19 | 63 | 3 | 0 |
| `mammoth` | 14.3s | 19 | 80 | 1 | 0 |

`fismatic` and `mammoth` happen to fill more fields than `mineru` here
because the contract was designed around fismatic's heading vocabulary.
Mineru's bold-marked headings (`10 **System Environment**`) need the
`until_next_same_or_higher` capture scope which R11 now respects. This
is the right behavior — the contract should be the source of truth, and
any backend that matches the contract's `sourceHeading` patterns should
fill the same fields.

### Test status

- **`compliance/tests/test_unified_converter.py`**: **20 pass, 0 skip**
  (was 8 pass + 12 fail in R13)
- **All `compliance/tests/`**: **276 pass, 0 skip, 0 fail**
- The 12 pre-existing r11 marks (the "expected to fail" tests) are now
  all passing; the r11 mark is preserved for documentation but is no
  longer needed for selective test running.

## Consequences

- **Contract is the single source of truth.** Adding a new FedRAMP
  template is a YAML-only change (add an `aliasSourceHeading` for the
  numbered form, e.g. `"10.4 Data Flow"`). No Python change needed.
- **Backend swap is one argument.** `UnifiedConverter(backend='mineru')`
  vs `UnifiedConverter(backend='fismatic')` produces the same entities,
  just with different fill rates and speeds.
- **R12 parity validation can now actually run.** Both converters
  (legacy `heading_parser.extract` and new `UnifiedConverter.convert`)
  produce the same shape; a future R12 can do a structural diff to
  measure zero-loss equivalence.
- **Dual-representation model is fully wired.** R11's stage 5 writes
  both the Obsidian markdown note and the canonical JSON sidecar to
  disk in one pass. Future R6 (Snapshot & Export Pipeline) can build on
  this.

## What is NOT in this R11 implementation

- **Full R12 parity validation** (100% field parity target) — this is the
  next phase per the refactoring plan. Requires building a side-by-side
  comparator.
- **Performance benchmarks per the R12 plan** (cProfile, memory-profiler,
  CI timing gates) — same.
- **Code-to-Config ratio measurement** — the R11 plan targets 50% code
  reduction in parsing logic; this PR adds 250 lines (mostly strategy
  implementations + entity rendering) but consolidates 4 separate
  routing strategies into 1 contract-driven pipeline. A future refactor
  can measure the actual ratio.
- **UUID generation for primary keys** — `oscal_ssp_system.uuid` etc.
  are still empty in the converted entities. R11 leaves UUID generation
  to the writing stage or downstream tools.
