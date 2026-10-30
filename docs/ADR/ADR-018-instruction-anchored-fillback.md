# ADR-018: Instruction-Anchored Fill-Back (docxtpl Roundtrip)

**Status:** Accepted
**Date:** 2026-07-20
**Deciders:** Project architecture agent + user
**Phase:** R16 (Instruction-Anchored Fill-Back)

---

## Context

The pipeline could parse FedRAMP SSP `.docx` files into OSCAL/OSBAPI/vault
outputs (the **read** direction), but had no way to **write** structured
content back into an authentic, authoring-ready `.docx`. The two existing
"write" surfaces were insufficient for a true roundtrip:

- `compliance/lib/docx_generator.py` rebuilds a *minimal* control-table-only
  SSP from OSCAL with plain python-docx — it does not reproduce the official
  GSA template's headings, instructions, examples, or styling.
- `compliance/lib/docx_feedback.py` renders a docxtpl *review skeleton*, not
  a filled SSP.

The user's requirement: target the **official FedRAMP blank templates** as
both extraction targets and write/output targets, using
[docxtpl](https://docxtpl.readthedocs.io/) as the fill mechanism. The
templates mark authoring guidance with **instruction blocks** — blue text
(`#0070C0` in the primary High/Moderate/Low/Lightweight GSA rev5 templates)
whose *following* body item is the writable slot. Content should be read from,
and written to, the region **immediately after** each instruction block.

A survey of all five committed-locally blanks confirmed the pattern and one
important nuance: the **Leveraged-SaaS-CRM** template does **not** use a single
`#0070C0`; it uses a small palette of blues (`#1F497D`, `#366091`, `#0070C0`).
No prior code had any color / instruction-block awareness (parsers keyed only
off checkbox glyphs + heading text).

## Decision

Add **Phase R16**: an instruction-anchored fill-back capability built as four
small, composable modules, driven by the existing ODCS v3.1.0 datacontract
mechanism (ADR-007 / ADR-011) so onboarding a new template stays YAML-only.

1. **`compliance/lib/instruction_anchors.py`** — a pure structural pass that
   walks the `.docx` body in document order and emits one `InstructionAnchor`
   per contiguous instruction block, recording the writable slot
   (paragraph/table) that follows. Instruction color is a **per-template
   configurable set** (`instructionColors`, default `{0070C0}`) with an
   independent text-prefix fallback (`Instruction`/`Instructions`/`Note`/
   `Example`) — *not* a hardcoded constant, because of the CRM finding.

2. **Contract write-extension** — four new `customProperties` on the *same*
   destination properties that already carry the read metadata:
   `templateTag`, `writeStrategy`, `instructionAnchor`, `writeScope` (plus
   object-level `instructionColors` / `instructionTextPrefixes` /
   `blankTemplatePath`). A single contract therefore drives extract **and**
   fill. Two demo contracts added: `low-sspp-roundtrip.yaml`,
   `leveraged-saas-crm-roundtrip.yaml`.

3. **`compliance/lib/docx_jinjafier.py`** — clones the official blank and
   injects Jinja `{{ tag }}` placeholders into the writable slot after each
   contract-referenced instruction, preserving all instructions/examples/
   styling. **Fails closed** (`JinjafyError`) on an ambiguous (multi-match)
   anchor rather than guessing.

4. **`compliance/lib/docx_fill.py`** — renders a template with a context dict
   (docxtpl) and re-extracts each tag's slot value from the filled document to
   assert **roundtrip content parity**.

5. **`compliance/lib/instruction_extract.py`** (R16.6) — pairs each slot's
   **instruction** with its extracted **content** and renders the instruction
   as **hidden markdown** for vaults. Encoding is configurable
   (`collapsed_callout` default — an Obsidian `> [!note]- Instruction`
   collapsed callout; also `html_comment`, `plain`, `none`) and the instruction
   is placed **before** its content (document order). The jinjafier's
   `strip_instructions` flag (and `build_jinja_template.py --strip-instructions`)
   makes instruction removal from the *output* template configurable: keep them
   (default, teaches the author) or strip them (clean fill target).

### Committing the jinjafied templates

The jinja templates (`compliance/templates/*.jinja.docx`) are **committed** via
a narrow `.gitignore` exception (`!compliance/templates/*.jinja.docx`) and a
matching update to `scripts/pre-commit-check.sh`. They are the official *blank*
templates with Jinja tags injected — **no CUI / customer content** — and let a
fresh clone run the roundtrip demo (fill + re-extract half) with zero local
source `.docx`. The official source blanks (`fismatic/*.docx`) remain
gitignored (ADR-009 unchanged for source documents).

## Alternatives Considered

- **Rebuild a clean template from scratch** (like `docx_generator.py`) —
  Rejected: loses the authentic GSA look, the instruction anchors, and the
  authoring guidance the user explicitly wanted to preserve.
- **Hardcode `#0070C0`** — Rejected: the CRM template uses other blues; a
  per-template color set + text fallback is required.
- **Store fill metadata in a separate write-contract** — Rejected: splits the
  read/write mapping across two files; co-locating on the same property keeps
  one source of truth per slot.

## Consequences

- **Positive:** True roundtrip — the official blank is the target for both
  read and write; live demo shows 100% slot-parity (Low 4/4, CRM 2/2).
- **Positive:** New template = new YAML contract + `build_jinja_template.py`
  run; no Python change (ADR-007 preserved).
- **Positive:** Fresh clone runs the roundtrip via committed jinja templates.
- **Negative:** docxtpl render on the large Low template is slow (~45s) because
  it re-serializes the full document; the full-blank parity tests are
  fixture-gated and skip without the blank.
- **Ongoing constraint:** `instructionAnchor` patterns must stay specific
  enough to match exactly one block (the jinjafier enforces this by failing
  closed).

## What is NOT in R16 (tracked follow-ups)

- ~~Fan-out to High / Moderate / Lightweight-Moderate templates~~ — **High and
  Moderate delivered in R16.7** (100% parity each). Lightweight-Moderate
  remains (issue #8).
- Collapsing the 10 byte-identical per-project contract clones (issue #9).
- Optional headless-LibreOffice render check to catch docxtpl tag corruption
  Word would reject (issue #10).
- Table-cell fill (`table_after_instruction`) is scaffolded but the demo
  exercises paragraph slots only.

## References

- `compliance/lib/instruction_anchors.py`
- `compliance/lib/docx_jinjafier.py`
- `compliance/lib/docx_fill.py`
- `compliance/scripts/build_jinja_template.py`
- `compliance/scripts/roundtrip_demo.py`
- `compliance/datacontracts/low-sspp-roundtrip.yaml`
- `compliance/datacontracts/leveraged-saas-crm-roundtrip.yaml`
- `compliance/templates/*.jinja.docx`
- the roundtrip template plan (not exported to the public core)
- ADR-007 (per-file datacontracts), ADR-009 (no .docx in git),
  ADR-011 (ODCS format), ADR-013 (feedback co-location)
