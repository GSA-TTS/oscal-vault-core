# ADR-036: Generic Markdown-to-Word Narrative Formatting

**Status:** Proposed
**Date:** 2026-11-23
**Deciders:** Project maintainers (review pending); AI-assisted draft
**Builds on:** [ADR-018](ADR-018-instruction-anchored-fillback.md)

## Context

Narrative stays plain Markdown in the vault and in OSCAL. After a template is filled, that
text lands in DOCX table cells as literal `**bold**`, typed `1.` and `-` lines, and
`[text](url)`, and block headers are plain text. A consuming system repository wrote its own
renderer to fix this (issue #87); a second consumer would have to write another.

An early version of that renderer reused numbering ids found in a hand-edited copy of the
document. The ids did not map onto the generated document, so lists continued numbering
across controls.

## Decision

Add `compliance/lib/docx_narrative_format.py`, a render-time-only module with a small API:

- `format_document(doc, header_re)` and `format_cell(cell, header_re)`.
- `strip_table_page_breaks(doc)`: removes `pageBreakBefore` inside tables only.
- `audit_document(doc, header_re, table_re=...)`: counts leftover literal Markdown, plain
  headers and table page breaks, so a consumer can fail CI on any non-zero value.

Rules:

- The caller supplies the header pattern and the audited-table pattern. The module holds no
  provider labels, system names or paths.
- Headers become their own bold, underlined, keep-with-next paragraphs. A header repeated
  immediately with no body between is dropped; a repeated header with body is kept because it
  carries prose.
- `**bold**` and `[text](http(s)://url)` become runs and external hyperlinks. Any other
  scheme, and malformed markup, stays literal text.
- `1.`, `1)`, `-`, `*` items become real Word lists; two leading spaces nest one level.
  **Each list gets a new abstract numbering definition**, so it restarts at 1. Template
  numbering ids are never reused.
- Hard-wrapped lines are rejoined; a blank line is a paragraph boundary.
- Idempotent: a cell that already holds list or hyperlink markup is left alone.

## Consequences

- Vault and OSCAL stay plain Markdown; formatting exists only in the rendered DOCX.
- One implementation to test, instead of one per consumer.
- Consumers keep their own label vocabulary, decide which tables count as control tables, and
  decide what to do with the audit result.
- Each list adds a numbering definition, which grows `numbering.xml` in large documents. This
  is accepted for correct restarts.
- The module does not perform source-document comparison; a consumer should keep its own
  source-to-render gate when adopting it.

## Not in this change

- Wiring the module into `docx_fill` or `assemble_ssp_docx.py`; consumers call it after fill.
- Rendering of Markdown tables, images or inline code.
