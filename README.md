# oscal-vault

Turn real-world FedRAMP System Security Plan (SSP) `.docx` documents into a
navigable **Obsidian knowledge vault** and validated **NIST OSCAL** JSON.

`oscal-vault` parses the messy, human-authored Word tables and narrative of a
FedRAMP SSP and produces:

- an **Obsidian vault** of typed, cross-linked markdown notes
  (`oscal.*` / `oscal.x.*` / `okf.*` — controls, system, components, parties,
  boundaries, diagrams, decisions, …);
- deterministic, hash-stable **canonical JSON** sidecars;
- strict, schema-valid **NIST OSCAL 1.1.3** system-security-plan JSON.

It is public-domain (CC0) and focused on one pipeline:

```text
FedRAMP SSP (.docx)
        │  fismatic (primary control-table parser)
        │  + heading_parser (contract-driven Chapters 1-12 narrative)
        ▼
OSCAL SSP dict  ──►  Obsidian vault (markdown + media)
                          │  generate_canonical_json.py
                          ▼
                     canonical JSON (.snapshots/current/)
                          │  export_oscal.py
                          ▼
                     NIST OSCAL 1.1.3 JSON  (schema-validated)
```

See **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** for a diagram-first tour
of the whole pipeline, **[`docs/DATA_FLOW.md`](docs/DATA_FLOW.md)** for the
extraction-to-vault-to-OSCAL artifact flow, and
**[`docs/INTEROPERABILITY.md`](docs/INTEROPERABILITY.md)** for how the standards
(FedRAMP SSP ↔ ODCS datacontract ↔ OSCAL) map together and how lossless coverage
is measured.

## Quickstart (uv)

Managed with [`uv`](https://docs.astral.sh/uv/):

```bash
# Install dependencies (creates .venv, resolves against uv.lock)
uv sync

# Run the test suite
uv run pytest -q

# Full local validation (lint + format + tests + roundtrip + vault + OSCAL)
bash scripts/validate_all.sh

# Build a vault from a FedRAMP SSP .docx you supply locally
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --docx path/to/your-ssp.docx \
  --plan-id my-system-2026 \
  --system-name "My System" --fips-199 low \
  --metadata-yaml scripts/registry.yaml \
  --vault examples/vault/my-system-2026/

# Regenerate canonical JSON sidecars, then export + validate OSCAL
uv run python3 compliance/scripts/generate_canonical_json.py --vault examples/vault/my-system-2026/
uv run python3 compliance/scripts/export_oscal.py --vault examples/vault/my-system-2026/ --validate
```

## Directory layout

| Path | Purpose |
| --- | --- |
| `fismatic/` | The primary FedRAMP SSP `.docx` → OSCAL SSP parser (regex/heuristic control-table parsing). |
| `compliance/lib/` | Core library: vault builders, contract-driven `heading_parser`, OSCAL export, canonical JSON, validators. |
| `compliance/scripts/` | CLIs: `export_obsidian_vault.py`, `generate_canonical_json.py`, `export_oscal.py`, `validate_oscal.py`, and the roundtrip/benchmark tools. |
| `compliance/datacontracts/` | ODCS v3.1.0 routing contract (`fedramp-ssp-v1.yaml`) + roundtrip fill-back contracts. |
| `compliance/mappings/` | Vocabulary crosswalk tables (raw → normalized status/origination enums). |
| `compliance/schemas/` | Vendored NIST OSCAL 1.0.4 / 1.1.3 schemas (Layer-1 validation). |
| `compliance/templates/*.jinja.docx` | Committed docxtpl templates for the OSCAL → `.docx` roundtrip (no CUI). |
| `scripts/` | Orchestration + vault validators (`validate_frontmatter/wikilinks/dataview/vault.py`), `regenerate_all_vaults.py`, `registry.yaml`. |
| `examples/vault/` | Committed example vaults generated from real SSPs (derived markdown + JSON only). |
| `benchmark/` | Extractor-comparison benchmark reports (research harness; optional deps). |
| `vault-schemas.json` | JSON Schema `$defs` for every vault note's frontmatter (used by the validators). |
| `docs/` | Architecture, interoperability, vault schema, crosswalk coverage, ADRs. |
| `docs/osbapi-backstage-interop/` | Recommended OSBAPI / Backstage projection modeling (future interoperability; see below). |
| `archive/` | Quarantined legacy material kept for provenance (see below). |

## No source `.docx` files are committed

This repository **never commits FedRAMP SSP `.docx` source files** — they are
often sensitive, large, and not ours to redistribute. `*.docx` is excluded
repo-wide via `.gitignore` (the only exception is the CUI-free
`compliance/templates/*.jinja.docx` roundtrip templates).

Consequently:

- Tests that need a real `.docx` fixture **skip gracefully** rather than fail.
- `scripts/validate_all.sh` and CI validate the **committed example vaults**
  and their OSCAL exports, which need no source document.
- To build a vault from scratch, supply your own SSP `.docx` (or a FedRAMP
  public blank baseline template) and point the CLI at it.
- The checked-in `examples/vault/**` markdown + JSON are **derived** output,
  committed so downstream consumers have a realistic reference.

## OSBAPI / Backstage interoperability (recommended projection)

The vault's normalized `normalized_status` / `normalized_origination` control
vocabulary is deliberately aligned with the
[OSBAPI](https://oscal-project.github.io/) CRM profile so the exported OSCAL
can be projected into an Open Service Broker API summary or a Backstage catalog
entity. That projection is documented as **modeling guidance** under
[`docs/osbapi-backstage-interop/`](docs/osbapi-backstage-interop/) and the
[`vault-schemas.json`](vault-schemas.json) `$defs` (`OscalCrmSummary`,
`BackstageEntityComplianceEnvelope`, `ComplianceAdapterEntityResponse`). The
earlier generator scripts for it have been archived (see `archive/`) pending a
datacontract-driven reimplementation; the schemas and docs remain the
source of truth for future interoperability work.

## License

This project is dedicated to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/) — see
[`LICENSE.md`](LICENSE.md). It originates from GSA public-domain work.

## Acknowledgements

`oscal-vault` builds on and was inspired by prior open parsers of FedRAMP SSPs:

- **[fismatic](https://github.com/GSA/fismatic)** — the regex/heuristic
  control-table parser this project vendors and extends (GSA, public domain).
  It is the sole live extractor in the pipeline.
- **[python-ssp](https://github.com/brasky/python-ssp)** (Elliot DeMatteis,
  MIT) — a second, independent `.docx` SSP reader that informed the canonical
  model's design as a cross-check parser. It is not used at runtime and has
  been moved to `archive/python-ssp/` for provenance.

This repository is itself a narrowly-scoped descendant of
[GSA/fedramp-automation](https://github.com/GSA/fedramp-automation); it
excludes that project's Schematron/XML validation build system and unrelated
tooling.

## `archive/`

To keep the OSS surface minimal, legacy and research material is quarantined
under `archive/` (still in git history for provenance):

- `archive/osbapi-projection/` — the earlier CRM/Backstage/adapter generator
  scripts and their golden fixtures (superseded by the modeling docs above).
- `archive/python-ssp/` — the MIT-licensed cross-check parser.
- `archive/fismatic-aux/` — fismatic notebooks and one-off conversion scripts.
- `archive/legacy-vaults/` — earlier example vaults whose source `.docx` are no
  longer in the corpus.
- `archive/plans/` — historical planning documents.
- `archive/root-smoke-scripts/` — the pre-pytest debug scripts.
- `archive/rt_docx/`, `archive/regenerate_all_docx.py` — legacy roundtrip
  feedback outputs and their driver.
