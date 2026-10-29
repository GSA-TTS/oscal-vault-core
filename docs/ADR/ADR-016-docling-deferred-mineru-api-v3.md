# ADR-016: Docling Deferred; MinerU API Migrated to v3

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R13 (Routing gaps + extra backends)

---

## Context

During the R13 attempt to run the 4-backend benchmark (ADR-015) end-to-end
on the 10-SSP corpus, two integration issues surfaced:

1. **Docling cannot import cleanly** with the lean CPU-only install
   (`docling-slim[format-docx]`).
2. **MinerU v3 removed the `mineru.api.MinerU` class** that the existing
   `MinerUBackend` stub was calling.

Both were discovered only when we actually ran the backends — the
inaugural R12 run only exercised `fismatic`.

## Decision 1: Defer Docling to R14+

`docling.document_converter` transitively imports
`docling.models.stages.chart_extraction.granite_vision` at module-load
time, which itself imports `transformers` and `torch`. The
`docling-slim[format-docx]` extra is not enough — even when we never
call the chart-extraction stage, the import chain breaks.

Three options were considered:

| Option | Cost | Outcome |
|--------|------|---------|
| Add `torch` (CPU-only) + `transformers` to the benchmark group | ~1.5 GB extra | Works but bloats install; per the user's "lean install" mandate, not approved for R13. |
| Stub `torch` via `sitecustomize.py` | ~5 lines | Brittle — breaks when docling's internal calls reach into `torch.backends.mps` etc. Rejected. |
| Use `docling.backend.msword_backend.MsWordDocumentBackend` directly | Rewrite `DoclingBackend.extract()` | Avoids the `DocumentConverter` import chain entirely. **Deferred to R14** to keep R13 scope tight. |

**Outcome:** `docling` is installed (for future use) but reported as
`is_available() == False`. The benchmark runs the 3 other backends and
the docling stub is skipped via the existing `--unavailable -> skip`
logic in `benchmark_extractors.py`.

## Decision 2: Migrate `MinerUBackend` to `mineru.cli.common.do_parse`

The `mineru.api.MinerU` class was the v1/v2 API. v3.x reorganized the
project around a single `do_parse()` function in
`mineru.cli.common`. The migration:

| Old (v2) | New (v3.4.4) |
|----------|--------------|
| `from mineru.api import MinerU` | `from mineru.cli.common import do_parse` |
| `processor = MinerU(backend="pipeline")`<br>`processor.convert(input_path=..., output_path=...)` | `do_parse(output_dir=..., pdf_file_names=[...], pdf_bytes_list=[...], p_lang_list=["en"], backend="pipeline", ...)` |
| Reads only `<stem>.md` | Reads `<stem>.md` + `<stem>_content_list.json` + `images/*.png` |

The new API also emits docx tables as **HTML `<table>...</table>`
blocks** rather than Markdown pipe tables. The `_parse_markdown` helper
was extended to handle both formats.

A side benefit: mineru also writes a `*_content_list.json` (an ordered
list of typed items: title, paragraph, image, table) which lets us
**attribute extracted images to the nearest preceding heading** — a
feature the fismatic backend reconstructs manually and unstructured
does not provide at all.

## Consequences

- **3-backend benchmark ran end-to-end** on all 10 SSPs. Total wall
  clock 591s (vs 337s for fismatic-only inaugural). See
  `benchmark/r13_3backends.md`.
- **MinerU found 76% more headings** than fismatic (1143 vs 648) and
  22% more paragraphs (18162 vs 14870). 6 more images.
- **Unstructured is a poor fit** for FedRAMP SSP structure — only 78
  headings total across 10 docs (vs 648 for fismatic), because
  `unstructured.partition.docx` only emits `Title` for top-level Word
  styles and not for `Heading 2/3/4` paragraph styles. Useful as a
  reference baseline; not a replacement for fismatic.
- **The benchmark group now has 6 deps** (~120 MB total instead of
  the 4.3 GB that `mineru[all]` would have pulled). Python pinned to
  3.13 because mineru and unstructured both require `<3.14`.
- **Docling's `DocumentConverter` is the wrong import path for a
  CPU-only docx benchmark.** R14 will rewrite `DoclingBackend.extract`
  to use `docling.backend.msword_backend.MsWordDocumentBackend`
  directly (or `docling-core`'s lower-level `DoclingDocument` API).

## Bench summary

| Backend | Mean time | Headings | Tables | ¶ | 🖼 | Available |
|---|---|---|---|---|---|---|
| `fismatic` | 4.5s | 648 | 2,578 | 14,870 | 92 | ✅ |
| `mineru` | 16.6s | 1,143 | 2,568 | 18,162 | 98 | ✅ |
| `unstructured` | 13.8s | 78 | 2,578 | 13,462 | 0 | ✅ |
| `docling` | — | — | — | — | — | ❌ (deferred) |

`docling` is still listed in the `benchmark` group for those who want
to install `torch`+`transformers` and try it manually; the harness
gracefully skips unavailable backends.
