# ADR-038: Dependency-Free DFD Diagram Rendering

**Status:** Proposed
**Date:** 2026-11-25
**Deciders:** Project maintainers (review pending); AI-assisted draft
**Builds on:** [ADR-027](ADR-027-topology-generators-cyclonedx-c4.md)

## Context

SSP templates need a network figure (Figure 9-1) and a data-flow figure (Figure 10-1).
A consuming system repository drew both from its CycloneDX threat-model DFD with Pillow.
That made a full imaging library a runtime dependency of DOCX generation, bound the
output to whichever system fonts Pillow found, and kept the renderer out of the core,
where a second consumer could not reuse it. The renderer also hard-coded one system's
authorization boundary name.

## Decision

Add `compliance/lib/dfd_diagram.py` and `compliance/scripts/render_dfd_diagram.py`:

- The figures are produced as plain SVG strings built with the standard library. Layout,
  colours and label wrapping match the consumer renderer they replace.
- PNG output, needed for DOCX embedding, is produced by rasterizing the SVG with
  `resvg-py` (MIT), a single self-contained wheel around the Rust `resvg` renderer. It
  has no system library dependencies. Pillow is not used.
- When no font is given, DejaVu Sans is loaded from the usual system paths and system
  fonts are skipped, so the same input gives the same PNG bytes.
- Inputs are a CycloneDX TM-BOM DFD and an optional architecture mapping (asset
  aliases, `diagram_order`, `flows`, `flow_overrides`, `diagram_layout`).
  `diagram_layout.mode` is `legacy` or `authorization-boundary`; an unknown mode fails.
- The boundary drawn in `authorization-boundary` mode is `diagram_layout.boundary` when
  set, otherwise the first blueprint boundary that lists zones. The module holds no system
  names.

## Consequences

- `resvg-py` becomes a runtime dependency of the core. Pillow stays benchmark-only.
- Consumers can also emit the SVG directly for review or web use.
- Text widths for label backgrounds are estimated from an average glyph width rather than
  measured, so label boxes are slightly wider than the text.
- Rasterized pixels differ from the Pillow output (anti-aliasing and text centring), so a
  consumer's committed PNGs change once on adoption.

## Not in this change

- Automatic layout or edge routing beyond the existing fixed grid and ellipse layouts.
- Wiring the renderer into `assemble_ssp_docx.py`; consumers render figures, then pass the
  image paths in as before.
