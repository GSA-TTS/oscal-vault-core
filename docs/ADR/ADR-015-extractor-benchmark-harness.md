# ADR-015: Extractor Benchmark Harness — Four-Backend Eval

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R12 (Benchmark & Parity Validation)

---

## Context

The R11 unified converter requires replacing `fismatic.SSPConverter`'s
python-docx extraction with a more capable backend. Three open-source
document-intelligence libraries are candidates:

| Library | Origin | Package | Approach |
|---------|--------|---------|----------|
| **Docling** | IBM Research | `docling>=2.0.0` | Layout-model + DOCX-native parser, per-element confidence, table→DataFrame |
| **MinerU** | OpenDataLab | `mineru[all]>=1.3.0` | LLM/RAG-ready Markdown+JSON output, native DOCX/PPTX/XLSX, CPU (pipeline) or GPU (vlm) backend |
| **Unstructured** | Unstructured-IO | `unstructured[docx]>=0.15.0` | Typed element classification (Title, NarrativeText, Table, Image), HTML table export |

The evaluation must be reproducible and comparable, with all candidates
running in the same uv environment to eliminate interpreter and version skew.

## Decision

Build a **four-backend benchmark harness** in the `compliance/` layer:

```
compliance/lib/extractor_backend.py   — Protocol + 4 backend implementations
compliance/scripts/benchmark_extractors.py — CLI runner, JSON + Markdown report
compliance/tests/test_extractor_backend.py — Protocol tests + r12 integration tests
docs/ADR/ADR-015-extractor-benchmark-harness.md — this document
```

### Backend registry

All backends are registered in a single `_REGISTRY` dict and returned via
`get_backend(name)`. The CLI accepts `--backends fismatic docling mineru
unstructured` to run any subset.

### Shared dependency group

All optional backends are declared in a single `[dependency-groups.benchmark]`
group in `pyproject.toml`:

```toml
[dependency-groups]
benchmark = [
    "docling>=2.0.0",
    "mineru[all]>=1.3.0",
    "unstructured[docx]>=0.15.0",
]
```

Install with: `uv sync --group benchmark`

One environment, no separate venvs, no version skew between candidates.

### Normalised `ExtractedDoc` shape

All backends produce the same `ExtractedDoc` dataclass:

```python
@dataclass
class ExtractedDoc:
    source_path: str
    backend_name: str
    headings: list[tuple[int, str]]    # (level, text)
    paragraphs: list[tuple[str, str]]  # (heading, text)
    tables: list[ExtractedTable]
    images: list[ExtractedImage]
    elapsed_s: float
    metadata: dict[str, Any]           # backend-specific (confidence, timings, etc.)
```

`ExtractedDoc.to_document_tree()` converts to `DocumentTree` so the
contract-driven router can consume any backend's output without changes.

### Metrics collected per (backend, document) pair

| Metric | Description |
|--------|-------------|
| `elapsed_s` | Wall-clock extraction time |
| `heading_count` | Headings detected |
| `paragraph_count` | Text paragraphs detected |
| `table_count` | Tables detected |
| `image_count` | Images/figures detected |
| `heading_recall` | headings ÷ baseline headings (1.0 = parity) |
| `table_recall` | tables ÷ baseline tables |
| `speedup` | baseline_time ÷ challenger_time |
| `status` | `ok` / `error` / `unavailable` |

Backend-specific metadata (Docling confidence scores, MinerU JSON keys,
Unstructured element types) is captured in `doc.metadata` and included in
the JSON report.

### Test strategy

- **No-mark tests** — protocol, registry, data model, and pure-Python parser
  unit tests (MinerU Markdown parser, Unstructured HTML table parser). Run in
  CI without any optional deps.
- **`@pytest.mark.r12` tests** — integration tests with a synthetic `.docx`
  fixture. Auto-skip when the backend is unavailable (`is_available()` check).
  Run locally with `uv run pytest -m r12`.

## Alternatives Considered

- **Separate venvs per backend** — Rejected: creates environment skew and
  adds CI complexity. A single `uv sync --group benchmark` is deterministic.
- **Subprocess invocation (CLI only)** — Rejected: adds process overhead to
  timing measurements, making speedup comparisons unreliable.
- **MarkItDown (Microsoft)** — Noted in MinerU docs as a lightweight
  alternative. Not added to this round because it does not provide structured
  heading/table extraction, only a flat Markdown string. May be added as a
  fifth backend if lightweight output is a desired trade-off.
- **`pdfplumber` / `camelot`** — PDF-oriented; out of scope for `.docx`
  evaluation.

## Consequences

- **Positive:** A single `benchmark_extractors.py` run across all 10 SSPs
  yields a JSON report with per-document and aggregate recall metrics,
  enabling data-driven backend selection for R11.
- **Positive:** `is_available()` + `status: unavailable` means the harness
  degrades gracefully in CI (where only `fismatic` is installed by default).
- **Positive:** Adding a fifth backend requires only implementing
  `ExtractorBackend` protocol + one `_REGISTRY` entry — no changes to the
  CLI or reporter.
- **Negative:** MinerU's pipeline backend has a ~5–30s warm-up time (model
  load). The `elapsed_s` metric includes warm-up on first call; subsequent
  calls are faster. Multi-document benchmarks partially amortise this.
- **Ongoing workflow:**
  1. `uv sync --group benchmark` — install optional backends
  2. `uv run python3 compliance/scripts/benchmark_extractors.py --docx fismatic/*.docx --backends fismatic docling mineru unstructured --output results.json --markdown results.md`
  3. Review `results.md` for recall + speedup across all systems
  4. Select winning backend for R11 implementation

## References

- `compliance/lib/extractor_backend.py`
- `compliance/scripts/benchmark_extractors.py`
- `compliance/tests/test_extractor_backend.py`
- `pyproject.toml` (`[dependency-groups.benchmark]`)
- https://docling-project.github.io/docling/reference/document_converter/
- https://opendatalab.github.io/MinerU/
- https://github.com/Unstructured-IO/unstructured
- ADR-008 (dual-extractor transitional architecture)
- ADR-011 (ODCS contract format)
