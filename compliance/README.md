# `compliance/`

[← README](../README.md) · [Architecture](../docs/ARCHITECTURE.md) ·
[Data flow](../docs/DATA_FLOW.md) · [Vault schema](../docs/OBSIDIAN_VAULT_SCHEMA.md) ·
[Validation](../docs/VAULT_VALIDATION.md) · [ADR index](../docs/ADR/README.md)

The core library and CLIs that turn a FedRAMP SSP `.docx` (parsed by
`fismatic/`) into an Obsidian vault, canonical JSON, and validated NIST OSCAL.
Vocabulary tables normalize control status/origination into an enum that is
also OSBAPI-compatible; the OSBAPI/Backstage *projection* itself is documented
under `docs/osbapi-backstage-interop/` (the earlier generator scripts are not
part of this core — see the repo root `README.md`).

> Paths below that begin with `examples/` or `scripts/registry.yaml` refer to a
> **system repository**, not this one. These CLIs read them from whichever
> directory they are run in; this repository holds no system content.

## Layout

```text
compliance/
├── lib/                     # core library (importable)
│   ├── vault.py             # pure vault note builders (source of truth)
│   ├── heading_parser.py    # SECONDARY parser: contract-driven heading extractor
│   ├── export_oscal.py      # canonical JSON -> NIST OSCAL SSP export
│   ├── canonical_json.py    # markdown -> hash-stable canonical JSON sidecars
│   ├── canonical_model.py   # CanonicalControl dataclass (parser-agnostic seam)
│   ├── vocab.py             # loads mappings/*.yaml: raw -> normalized enum
│   ├── oscal_source.py      # docx / OSCAL SSP JSON -> CanonicalControl list
│   ├── control_parameters.py# ODP recovery -> OSCAL set-parameters
│   ├── docx_images.py       # embedded diagram-image extraction
│   ├── validate_oscal.py    # Layer-1 NIST schema validation
│   └── ...                  # roundtrip fill-back helpers (docx_fill/jinjafier/...)
├── mappings/                # vocabulary mapping tables (data, not code)
│   ├── implementation-status-map.yaml
│   └── control-origination-map.yaml
├── datacontracts/           # ODCS v3.1.0 data contracts (to/from routing config)
│   ├── fedramp-ssp-v1.yaml          # source headings -> vault note types
│   ├── *-sspp-roundtrip.yaml        # OSCAL -> .docx fill-back contracts
│   └── schemas/odcs-json-schema-v3.1.0.json
├── schemas/                 # vendored NIST OSCAL 1.0.4 / 1.1.3 schemas
├── templates/*.jinja.docx   # committed roundtrip templates (no CUI)
├── scripts/                 # CLIs (see below)
└── tests/                   # regression tests; schema-validate generated shapes
```

## Core CLIs

```bash
# docx -> Obsidian vault
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --docx path/to/ssp.docx --plan-id my-system-2026 \
  --system-name "My System" --fips-199 low \
  --metadata-yaml scripts/registry.yaml \
  --vault examples/vault/my-system-2026/

# vault markdown -> canonical JSON sidecars
uv run python3 compliance/scripts/generate_canonical_json.py --vault examples/vault/my-system-2026/

# canonical JSON -> strict NIST OSCAL (+ Layer-1 schema validation)
uv run python3 compliance/scripts/export_oscal.py --vault examples/vault/my-system-2026/ --validate
```

Sidecar regeneration preserves an existing `generated_at` when its
`content_hash` is unchanged, avoiding timestamp-only diffs. Repeat `--note`
with vault-relative paths for targeted regeneration:

```bash
uv run python3 compliance/scripts/generate_canonical_json.py \
  --vault examples/vault/my-system-2026/ \
  --note governance/schema.md \
  --note Controls/AC-01.md
```

Targeted runs never clean unrelated sidecars. Use a full-vault run when orphan
cleanup is intended.

### Real-DOCX instruction reconciliation

DOCX exports write source-located `okf.instruction` notes under `guidance/`.
After generating canonical sidecars, reconcile the protected source against the
actual vault Markdown and sidecars:

```bash
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --docx /protected/source.docx --plan-id example \
  --vault /tmp/example-vault
uv run python3 compliance/scripts/generate_canonical_json.py \
  --vault /tmp/example-vault
uv run python3 compliance/scripts/instruction_ledger.py \
  --docx /protected/source.docx --output-dir /tmp/example-vault \
  --strict --require-canonical --json /tmp/example-instruction-ledger.json
```

Controlled CI jobs may add `--skip-missing` when the fixture is intentionally
unavailable. Explicit local runs remain fail-closed by default.

### Portable reviewer report

Review typed table coverage and instruction provenance without Obsidian:

```bash
uv run python3 compliance/scripts/build_reviewer_report.py \
  --vault examples/vault/my-system-2026 \
  --json /tmp/my-system-review.json \
  --html /tmp/my-system-review.html
```

Both outputs are deterministic and read canonical sidecars only.

### Cross-repository reconciliation

System repositories declare review-safe automation sources in a versioned YAML
configuration. Inventory a checkout pinned to an explicit commit:

```bash
uv run python compliance/scripts/inventory_reconciliation_source.py \
  --repo /path/to/system-repository \
  --config /path/to/reconciliation-sources.yaml \
  --commit <40-character-sha> \
  --output /tmp/automation-source.json
```

Validate a domain-aware overlay with:

```bash
uv run python compliance/scripts/validate_reconciliation_overlay.py overlay.yaml
```

Add `--require-ready` to fail while any operation remains unapproved. The
generic engine owns schemas, path safety, deterministic hashing, and review
gates; system-specific repositories own only declarative source configuration,
and authorization-package repositories own pins and approval evidence.

### Control decision-support packets

Convert a guidance workbook once into normalized, review-safe JSON:

```bash
uv run python compliance/scripts/export_assessment_reference.py \
  /path/to/rev5-assessment-workbook.ods \
  --reference-id <system>-rev5 \
  --output /path/to/<system>-rev5.assessment-reference.json
```

The JSON snapshot is the normal runtime datasource; the spreadsheet remains a
source-acquisition artifact. Core fields are stable and source-specific content
can be retained under `extensions`. Historical observation prose is omitted by
default and represented by presence plus SHA-256.

Build cited packets from OSCAL implementation responses, the normalized
assessment reference, source instructions, and an optional pinned MITRE D3FEND
mapping:

```bash
uv run python compliance/scripts/build_control_decision_packets.py \
  --ssp /path/to/system-security-plan.json \
  --assessment-reference /path/to/<system>-rev5.assessment-reference.json \
  --instruction-ledger /path/to/instruction-ledger.json \
  --output-dir /tmp/control-packets
```

Run deterministic prerequisite grading:

```bash
uv run python compliance/scripts/grade_control_decision_packets.py \
  /tmp/control-packets --output-dir /tmp/control-grades
```

Optional Bedrock grading uses Converse structured tool output:

```bash
uv run --with boto3 python compliance/scripts/grade_control_decision_packets.py \
  /tmp/control-packets --output-dir /tmp/control-grades \
  --provider bedrock --region us-east-1 \
  --model-id us.anthropic.claude-sonnet-4-6 --max-tokens 4096
```

Packets omit historical observation prose by default because workbooks can
contain internal URLs, screenshot names, or assessment details. The LLM receives
objective/method prerequisites, presence flags, and hashes. Use
`--include-historical-observations` only in an approved processing environment.

MITRE D3FEND data must conform to
`d3fend_nist_mapping_schema_v1.json` and include a pinned source hash. CRM
countermeasures are not treated as D3FEND. If no mapping is supplied, packets
report D3FEND as unavailable.

Grades never update OSCAL status. Every deterministic or LLM-assisted grade is
schema-validated, citation-constrained, and emitted with human review pending.

### Lossless DOCX source manifest

Generate a deterministic package/IR preservation bundle before projecting a
DOCX into Markdown:

```bash
uv run python3 compliance/scripts/preserve_docx_source.py \
  --docx /protected/source.docx \
  --output /tmp/source-preservation
```

`_preservation/docx-source-manifest.json` contains the whole-package checksum,
part and relationship inventory, typed source nodes, complete source-table
grids (cell spans, paragraph locators, colors), drawing metadata, and opaque
artifacts. The source table is authoritative. Markdown tables and Obsidian
inline/frontmatter properties are queryable projections linked back to stable
source table/row/cell IDs; they are not treated as lossless replacements for
OOXML structures such as merged cells or nested paragraphs.

Every source table also receives an `okf.source_table` review note under
`_source/tables/`, including complexity and projection counts. The generated
`_meta/views/source-tables.base` separates all tables, fully projected tables,
and complex preserved-only tables. A table without merged cells may still be
preserved-only when it lacks unique headers or a safe rectangular data shape.

## Why a canonical intermediate model

`fismatic` produces raw control implementation status/origination values.
Rather than have every consumer special-case that raw output, everything
funnels through one seam:

```text
docx or OSCAL SSP JSON --(oscal_source.py)--> CanonicalControl --(vault.py / export_oscal.py)--> vault + OSCAL
```

`CanonicalControl` (see `compliance/lib/canonical_model.py`) expresses its
`implementation_status`/`origination` in a normalized enum vocabulary, so a
new source parser only needs to plug into `oscal_source.py`.

## Vocabulary mapping tables

`compliance/mappings/*.yaml` map the raw values `fismatic`'s parser produces
(`implemented`, `partially-implemented`, `hybrid`, `inherited`, etc.) onto a
normalized `ImplementationStatus`/`ControlOrigination` enum (the same
vocabulary used by the OSBAPI CRM profile, so the OSCAL export can be projected
downstream — see `docs/osbapi-backstage-interop/`). These are **data, not
code**, on purpose — reviewable/editable by compliance/GRC staff without
touching parsing logic. Every lossy or judgment-call mapping carries an
explanatory `remark`.

## Secondary parser: ODCS data contract + heading-driven extraction

The primary `fismatic` parser only extracts Chapter 13 control implementation
tables. Everything else in the source `.docx` (system identity, parties,
architecture, data flow, inheritance, interconnections, references, all
images, all paragraph text) is silently dropped. The secondary parser fills
that gap:

```text
FedRAMP SSP (.docx)
        │  scripts/docx_catchall_dump.py            (primary catchall, all content)
        ▼
Markdown catchall blob  (examples/docx_catchall/<basename>.catchall.md)
        │  compliance/lib/heading_parser.py          (secondary parser)
        │  + compliance/datacontracts/fedramp-ssp-v1.yaml  (ODCS v3.1.0 contract)
        ▼
Routed records JSON     (one record per destination note type, per the contract)
        │
        ▼
Vault notes (oscal.*, oscal.x.*, okf.*) via compliance/scripts/export_obsidian_vault.py
```

### The contract is the configuration

`compliance/datacontracts/fedramp-ssp-v1.yaml` is an [Open Data Contract
Standard (ODCS) v3.1.0](https://bitol-io.github.io/open-data-contract-standard/)
YAML. It documents the to/from relationship for every source heading and
its destination note type. Adding or changing a mapping is a contract edit,
**not a code change**.

Conventions used in `customProperties` (ODCS standard escape hatch for
domain-specific metadata):

- **Object-level**: `oscalType` (e.g. `oscal.party`), `oscalModel`,
  `noteType`, `destinationFolder` (e.g. `Organizations/`), `required`,
  `multiple`, `isSource`.
- **Property-level**: `sourceHeading` (exact / `prefix*` / `regex:...`),
  `sourceKind` (paragraph | table | image | constant | derived),
  `captureStrategy` (heading_paragraph | heading_table_first |
  heading_table_all | heading_image_first | heading_image_all | heading_all |
  constant | derived), `captureScope` (immediate_only | until_next_same_or_higher),
  `oscalPath` (dotted path in the OSCAL model), `note`, `provenance`.

Schema-level `relationships` express foreign keys between destination types
(e.g. `oscal.ssp.responsible-parties[system-owner].party-uuid` →
`oscal.party.uuid`).

### Usage

```bash
# Validate the contract against the cached ODCS v3.1.0 JSON schema
uv run python3 compliance/scripts/extract_by_contract.py validate

# Extract routed records from a catchall markdown blob
uv run python3 compliance/scripts/extract_by_contract.py extract \
  --markdown examples/docx_catchall/<ssp>.catchall.md \
  --output /tmp/routed.json

# Extract directly from a .docx (dumps to markdown first)
uv run python3 compliance/scripts/extract_by_contract.py extract \
  --docx <ssp>.docx \
  --output /tmp/routed.json

# Print only the fill-count summary
uv run python3 compliance/scripts/extract_by_contract.py extract \
  --markdown examples/docx_catchall/<ssp>.catchall.md \
  --summary-only

# Use a custom contract
uv run python3 compliance/scripts/extract_by_contract.py extract \
  --markdown path/to/blob.md \
  --contract compliance/datacontracts/fedramp-ssp-v1.yaml
```

### Producing the catchall blob

See `scripts/docx_catchall_dump.py` (repo root) and
`examples/docx_catchall/INDEX.md` for the catchall dumper and the
routing-decision review surface that informed this contract.

```bash
# Default sample (a Low SSP)
uv run python3 scripts/docx_catchall_dump.py

# All local .docx files under fismatic/
uv run python3 scripts/docx_catchall_dump.py --all
```

## Feedback pipeline (reverse: contract as template config)

The contract doubles as the configuration for a **reverse pipeline** that
renders extracted records back into a compact skeleton for human review. The
skeleton is a TOC showing, per source heading, whether it was routed, to
which destinations, and whether the routed properties were filled. Designed
for an agent to scan and take back to the parsing logic + datacontract to
investigate and fix — not a verbose per-section packet.

```text
.docx
  │ scripts/docx_catchall_dump.py            (catchall blob)
  ▼
markdown blob
  │ compliance/lib/heading_parser.py         (forward: extract via contract)
  ▼
routed records
  │ compliance/lib/docx_feedback.py          (reverse: contract as template config)
  │ compliance/lib/markdown_feedback.py      (reverse: skeleton markdown)
  ▼
<basename>.feedback.docx  +  <basename>.feedback.md
  │
  ▼
human reviewer → notes → agent → back to parsing logic + datacontract
```

### Status glyphs

| glyph | meaning |
|---|---|
| ✅ | routed & filled (extracted value matches original content) |
| 🟡 | routed & partial (some destinations filled, some not) |
| ❌ | routed & empty (heading matched but capture returned nothing) |
| ⚠️ | unrouted (heading has original content but no destination in contract) |

### Usage

```bash
# From a .docx (dumps to markdown first, then extracts + renders):
uv run python3 compliance/scripts/generate_feedback.py \
  --docx <ssp>.docx \
  --output-dir examples/feedback/<system>/

# From an existing catchall markdown blob:
uv run python3 compliance/scripts/generate_feedback.py \
  --markdown examples/docx_catchall/<ssp>.catchall.md \
  --output-dir examples/feedback/<system>/

# Markdown skeleton only (skip .docx):
uv run python3 compliance/scripts/generate_feedback.py \
  --markdown path/to/blob.md --output-dir examples/feedback/x/ --markdown-only

# .docx skeleton only (skip .md):
uv run python3 compliance/scripts/generate_feedback.py \
  --markdown path/to/blob.md --output-dir examples/feedback/x/ --docx-only
```

Output is written under `examples/feedback/<basename>/` (gitignored, since
real-SSP content can be sensitive). Each SSP gets:

- `<basename>.feedback.md` — compact markdown TOC with per-heading routing
  status and notes fields
- `<basename>.feedback.docx` — same skeleton as a Word document with
  checkboxes (built with `docxtpl` using a programmatically-generated
  template; no binary template file committed)
- `_feedback_template.docx` — the reusable docxtpl template (regenerated if
  absent)

### What an agent does with a filled-in form

The skeleton is designed to be consumed by an agent that investigates and
fixes parsing issues. For each heading with notes:

1. **Unrouted content** → add a property to the relevant destination object
   in `fedramp-ssp-v1.yaml` with `sourceHeading` matching the heading.
2. **Routed but unfilled** → check whether the `sourceHeading` pattern
   matches the real .docx heading (regex/prefix/exact), and whether the
   `captureStrategy` is appropriate for the content kind (paragraph/table/
   image).
3. **Wrong destination** → edit the property's `oscalPath` or move the
   property to a different destination object.
4. **Capture strategy too narrow/broad** → adjust `captureStrategy` or
   `captureScope` in the contract.
5. **Parser bug** (not routing) → file an issue with the form attached.

All routing fixes are contract edits — no code change required.
