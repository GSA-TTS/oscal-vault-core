---
title: "Architecture & Workflow Reference"
description: "End-to-end architecture of the FedRAMP SSP .docx → Obsidian vault / OSCAL / OSBAPI pipeline, with mermaid diagrams for workflow, scripts, extraction, config, output, benchmarks, and intent"
status: canonical
tier: 1
last_updated: "2026-07-22"
related_files:
  - "README.md"
  - "compliance/README.md"
  - "docs/DATA_FLOW.md"
  - "docs/OBSIDIAN_VAULT_SCHEMA.md"
  - "docs/CROSSWALK_COVERAGE.md"
  - "compliance/scripts/validate_workspace.sh"
  - "scripts/registry.yaml"
  - "compliance/datacontracts/fedramp-ssp-v1.yaml"
---

<!-- Diagrams use GitHub-flavored mermaid. They render on github.com and in the
     Obsidian vault (Mermaid is built in). Keep node labels ASCII-safe. -->

# Architecture & Workflow Reference

> **Where things live.** This repository (`oscal-vault-core`) holds the code, schemas, mappings, tests and
> documentation. A *system repository* holds one system's generated vault (`examples/vault/<system>/`), its
> committed snapshots (`.snapshots/`) and its per-system metadata (`scripts/registry.yaml`). Paths below that
> start with `examples/`, `.snapshots/` or `scripts/registry.yaml` refer to the system repository, and the scripts
> in `compliance/scripts/` read them from the directory they are run in.

This document is the single, diagram-first map of how a real-world FedRAMP
System Security Plan (SSP) `.docx` becomes validated, machine-readable
artifacts: an **Obsidian knowledge vault**, **canonical JSON sidecars**,
**strict NIST OSCAL JSON**, and the **OSBAPI / Backstage** projections that
were the original purpose of the fork.

For a focused artifact-level trace from extraction through vault markdown,
canonical sidecars, and OSCAL exports, see
[`docs/DATA_FLOW.md`](DATA_FLOW.md).

- [1. Intent](#1-intent) — why this project exists
- [2. System context](#2-system-context) — the pieces and how they relate
- [3. End-to-end workflow](#3-end-to-end-workflow) — the full pipeline
- [4. Scripts & commands](#4-scripts--commands) — every CLI entry point
- [5. Extraction techniques](#5-extraction-techniques) — how the `.docx` is read
- [6. Configuration & reference](#6-configuration--reference) — the knobs
- [7. Type taxonomy](#7-type-taxonomy) — `oscal.*` / `oscal.x.*` / `okf.*`
- [8. Output structure & files](#8-output-structure--files) — what lands on disk
- [9. Validation](#9-validation) — the gates
- [10. Benchmarks](#10-benchmarks) — extractor comparison
- [11. File & directory reference](#11-file--directory-reference)

---

## 1. Intent

The repository is a narrowly-scoped fork of
[GSA/fedramp-automation](https://github.com/GSA/fedramp-automation). It does
**one thing**: turn messy, human-authored FedRAMP SSP `.docx` documents into
validated, structured, machine-consumable compliance data — without the parent
repo's Schematron/XML build system.

```mermaid
mindmap
  root((FedRAMP SSP<br/>.docx parser))
    Problem
      SSPs are prose Word docs
      Control data buried in tables
      No machine-readable source of truth
      Chapters 1-12 narrative is lost by naive parsers
    Goals
      Deterministic extraction
      Round-trippable to OSCAL 1.1.3
      Human-navigable knowledge vault
      Contract-driven not code-driven mapping
    Consumers
      Service broker OSBAPI
      Backstage catalog
      Compliance adapter
      Auditors via Obsidian
    Non-goals
      Schematron XML validation
      Presentations / unrelated GSA tooling
      Committing source .docx files
```

**Priority order** (inherited from the federal agent contract):
`safety > correctness > compliance > simplicity > performance`. Determinism is
prized over cleverness — the same `.docx` should produce the same artifacts so
diffs are meaningful (the one intentional exception is per-run random
`system_id`/uuid seeds).

---

## 2. System context

Two output "flavors" share the same extraction core:

```mermaid
flowchart TB
    subgraph SRC["Source (never committed)"]
        DOCX[["FedRAMP SSP .docx"]]
        ODS[["Secondary CRM .ods (R23)"]]
    end

    subgraph PARSE["Extraction core"]
        FIS["fismatic<br/>(primary parser)"]
        PSSP["python-ssp<br/>(cross-check parser)"]
        HEAD["heading_parser<br/>(contract-driven, Ch 1-12)"]
        IMG["docx_images"]
        ODP["control_parameters<br/>(ODP recovery)"]
    end

    subgraph MODEL["Canonical seam"]
        OSCALRAW["OSCAL SSP dict"]
        CANON["CanonicalControl records"]
    end

    subgraph OUT1["Vault flavor"]
        VAULT["Obsidian vault<br/>(markdown + media)"]
        CJSON["Canonical JSON<br/>(.snapshots/current)"]
        OSCALEXP["Strict OSCAL 1.1.3 JSON<br/>(.exports)"]
    end

    subgraph OUT2["OSBAPI flavor (fork origin)"]
        CRM["OscalCrmSummary /<br/>FullCrmDocument"]
        BACK["Backstage envelope"]
        ADPT["Compliance adapter response"]
    end

    DOCX --> FIS & HEAD & IMG & ODP
    DOCX -.cross-check.-> PSSP
    ODS --> VAULT
    FIS --> OSCALRAW
    OSCALRAW --> CANON
    CANON & HEAD & IMG & ODP --> VAULT
    VAULT --> CJSON --> OSCALEXP
    CANON --> CRM & BACK & ADPT
```

> **Note:** only `fismatic` is wired into the live pipeline. `python-ssp` was
> an independent second reader retained for cross-checking; it is not part of
> this core. The OSBAPI/Backstage projection (dashed, right) is the
> fork's original purpose and is now **recommended modeling only** — the
> generator scripts are not part of this core either, and the projection is
> documented in `docs/osbapi-backstage-interop/` with its schemas in
> `vault-schemas.json`.

---

## 3. End-to-end workflow

### 3.1 The `.docx → vault` sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User / CI
    participant EX as export_obsidian_vault.py
    participant FC as fismatic.SSPConverter
    participant CP as control_parameters
    participant HP as heading_parser
    participant DI as docx_images
    participant REG as registry.yaml
    participant VB as vault.build_vault
    participant FS as Disk

    U->>EX: uv run ... --docx SSP.docx --plan-id ... --vault DIR/
    EX->>FC: get_oscal_data(docx)
    FC-->>EX: OSCAL SSP dict (Chapter 13 controls)
    EX->>CP: extract_parameters(docx)
    CP-->>EX: ODPs by control (issue #18)
    EX->>HP: extract_from_docx(docx)
    HP-->>EX: Chapters 1-12 typed records
    EX->>DI: extract_images(docx)
    DI-->>EX: {rId: image bytes}
    EX->>REG: _apply_metadata_yaml()
    REG-->>EX: system_owner, isso, components, ...
    EX->>VB: build_vault(oscal, narrative, images, meta)
    VB-->>EX: [VaultNote]
    EX->>FS: wipe + write markdown + media
    FS-->>U: Obsidian vault ready
```

### 3.2 The four-layer data model (ADR-002 / ADR-012)

Each layer has a distinct commit policy, which is what keeps the git history
clean and the exports reproducible:

```mermaid
flowchart LR
    A["<b>Authoring</b><br/>Markdown notes<br/><i>committed</i>"]
    B["<b>Canonical JSON</b><br/>.snapshots/current<br/><i>committed</i>"]
    C["<b>Snapshot history</b><br/>.snapshots/history<br/><i>gitignored</i>"]
    D["<b>Export</b><br/>.exports/oscal-json<br/><i>gitignored</i>"]

    A -->|generate_canonical_json.py| B
    B -->|hash + timestamp| C
    B -->|export_oscal.py| D
    D -->|validate_oscal.py| D
```

---

## 4. Scripts & commands

Bootstrap and verify are one command each — there is no `Makefile`; `uv` is the
task runner.

```mermaid
flowchart TD
    subgraph BOOT["Bootstrap"]
        S1["uv sync"]
    end
    subgraph GEN["Generate"]
        G1["export_obsidian_vault.py<br/>single vault"]
        G2["regenerate_all_vaults.py --force<br/>whole corpus"]
        G3["generate_canonical_json.py<br/>md -> JSON sidecars"]
        G4["export_oscal.py --validate<br/>JSON -> OSCAL 1.1.3"]
    end
    subgraph VERIFY["Verify"]
        V1["compliance/scripts/validate_workspace.sh<br/>(system-repo gate runner)"]
        V2["uv run pytest -q"]
        V3["validate_vault.py --strict"]
    end
    S1 --> G1 --> G3 --> G4
    G2 --> G3
    S1 --> V1
    V1 --> V2 & V3
```

### 4.1 Command reference

| Command | Purpose |
|---|---|
| `uv sync` | Install all dependencies; prepare environment (bootstrap). |
| `uv run pytest -q` | Full test suite (skips gracefully without `.docx` fixtures). |
| `bash <core>/compliance/scripts/validate_workspace.sh` | Run every gate against a **system repository** (run it from there). A gate whose baseline file is absent reports `SKIPPED`. |
| `uv run python3 compliance/scripts/export_obsidian_vault.py …` | Build one vault from a `.docx` (or `--oscal-json`). |
| `uv run python3 <core>/compliance/scripts/regenerate_all_vaults.py --force` | Rebuild every registered system's vault. |
| `uv run python3 compliance/scripts/generate_canonical_json.py [--vault DIR] [--note PATH]` | Emit canonical JSON sidecars; repeat `--note` for targeted, no-clean regeneration. |
| `uv run python3 compliance/scripts/export_oscal.py --validate [--oscal-version 1.1.3]` | Emit strict NIST OSCAL JSON into `.exports/oscal-json/`. |
| `uv run python3 compliance/scripts/validate_vault.py [--strict]` | Headless "open in Obsidian" — frontmatter, wikilinks, dataview, orphans, OKF conformance. |
| `uv run python3 compliance/scripts/benchmark_extractors.py` | Run the 5 extractor backends over the corpus. |

### 4.2 Canonical single-vault invocation

```bash
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --docx <ssp>.docx \
  --plan-id <system> \
  --system-name "<System Name>" \
  --fips-199 low \
  --metadata-yaml scripts/registry.yaml \
  --vault examples/vault/<system>/
```

Key flags: mutually-exclusive `--docx | --oscal-json`; `--plan-id` (req);
`--vault` (req); `--parser-name` (default `fismatic`); `--fips-199 {low,moderate,high}`;
`--metadata-yaml`; `--crm-ods`; `--keep-existing`; `--source-docx`.

---

## 5. Extraction techniques

The parser is a **dual-extractor** design (ADR-008): a hand-tuned primary
parser for the control tables in Chapter 13, and a contract-driven secondary
parser for the narrative Chapters 1-12 that naive parsers drop.

```mermaid
flowchart TB
    DOCX[[".docx"]]

    subgraph PRIMARY["Primary — Chapter 13 controls"]
        DP["docx_parser.get_controls()<br/>python-docx tables"]
        ICT["is_control_table()<br/>keyword + control-ID scoring"]
        PCT["parse_control_table()<br/>checkbox glyphs, roles"]
        SANI["sanitize_responsible_role()<br/>trim paragraph -> role title"]
        DP --> ICT --> PCT --> SANI
    end

    subgraph SECONDARY["Secondary — Chapters 1-12"]
        CATCH["docx_catchall_dump.py<br/>lossless ordered blob"]
        HP["heading_parser.extract(md, contract)"]
        CONTRACT{{"ODCS contract<br/>fedramp-ssp-v1.yaml"}}
        CATCH --> HP
        CONTRACT -. capture strategies .-> HP
    end

    subgraph SIDE["Side-channels"]
        ODP["control_parameters<br/>bracket [ODP] recovery"]
        IMGX["docx_images<br/>blip -> media bytes"]
    end

    DOCX --> DP & CATCH & ODP & IMGX
```

### 5.1 Capture strategies (contract-driven)

The secondary parser routes each destination property through a named
strategy declared in the ODCS contract — **adding a mapping is a contract edit,
not a code change**:

```mermaid
flowchart LR
    H["HeadingSection"] --> R{captureStrategy}
    R --> P1["heading_paragraph[_first]"]
    R --> P2["heading_table_first / all"]
    R --> P3["heading_image_first / all"]
    R --> P4["heading_all"]
    R --> P5["constant / derived"]
    R --> P6["control_table"]
```

Heading matching supports exact, section-number-stripped, `prefix*`,
`regex:` patterns, plus `aliasSourceHeading` (R14).

### 5.2 Checkbox & vocabulary normalization

FedRAMP CIS tables encode Implementation Status / Control Origination as
checkbox glyphs. The parser recognizes `☒ ✓ ⊠`, `[x]`, and bare `x`, then maps
raw text to OSCAL vocab via data-only crosswalk files:

```mermaid
flowchart LR
    RAW["raw cell text<br/>e.g. 'Partially Operational'"] --> M1["implementation-status-map.yaml"]
    RAW --> M2["control-origination-map.yaml"]
    M1 --> S["OSBAPI status enum<br/>(default not-implemented)"]
    M2 --> O["origination enum<br/>(precedence-ranked;<br/>default system-specific)"]
```

### 5.3 Pluggable benchmark backends

For measurement, extraction is abstracted behind an `ExtractorBackend`
protocol that normalizes any engine to `ExtractedDoc(headings, paragraphs,
tables, images)`:

```mermaid
flowchart LR
    subgraph REG["_REGISTRY"]
        B1["fismatic (default)"]
        B2["docling"]
        B3["mineru"]
        B4["unstructured"]
        B5["mammoth"]
    end
    B1 & B2 & B3 & B4 & B5 --> ED["ExtractedDoc<br/>(normalized)"]
```

---

## 6. Configuration & reference

```mermaid
flowchart TB
    subgraph DATA["Data-not-code config"]
        REG["scripts/registry.yaml<br/>per-system metadata"]
        CONTRACT["fedramp-ssp-v1.yaml<br/>ODCS v3.1.0 routing contract"]
        MAP1["implementation-status-map.yaml"]
        MAP2["control-origination-map.yaml"]
        NAMES["nist_sp800-53_rev5_control_names.json"]
    end
    subgraph SCHEMA["Schemas"]
        OSBAPI["vault-schemas.json<br/>(34 $defs)"]
        NIST["schemas/oscal-1.1.3 & 1.0.4"]
        ODCS["odcs-json-schema-v3.1.0.json"]
    end
    subgraph TOOL["Tooling"]
        PYPROJ["pyproject.toml + uv.lock"]
        PRECOMMIT[".pre-commit-config.yaml"]
        CI[".github/workflows/validate.yml"]
    end
    CONTRACT --> OSBAPI
    REG --> CONTRACT
```

**Key configurable knobs**

| Knob | Where | Effect |
|---|---|---|
| Parser choice | `--parser-name` | `fismatic` (default) vs alternatives. |
| FIPS level | `--fips-199` | Drives roundtrip contract + impact fields. |
| Per-system metadata | `scripts/registry.yaml` | Owner, ISSO, AO, components fismatic can't extract. |
| Routing / mappings | `fedramp-ssp-v1.yaml` | 19 objects, 116 `sourceHeading`s; add a mapping without touching code. |
| Vocabulary | `mappings/*.yaml` | raw → OSCAL/OSBAPI enums + precedence. |
| OSCAL version | `--oscal-version` | `1.1.3` (default) or `1.0.4`. |
| Primary vs contract control extraction | `handledByPrimaryParser` (contract) | Whether fismatic or the contract handles a control object. |

---

## 7. Type taxonomy

Three namespaces classify every note (defined in `compliance/lib/vault.py`,
`docs/OBSIDIAN_VAULT_SCHEMA.md`, ADR-001; validated by
`scripts/validate_frontmatter.py`):

```mermaid
flowchart TB
    subgraph OSCAL["oscal.* — canonical NIST OSCAL"]
        C1["oscal.control -> Controls/"]
        C2["oscal.ssp -> Systems/ + Plans/"]
        C3["oscal.component_definition -> Components/"]
        C4["oscal.party -> Organizations/"]
        C5["(planned) poam, profile, catalog,<br/>assessment_plan/results"]
    end
    subgraph EXT["oscal.x.* — extensions"]
        E1["diagram -> Diagrams/"]
        E2["boundary -> Boundaries/"]
        E3["decision -> Decisions/"]
        E4["inheritance -> Inheritance/"]
        E5["relationship -> Relationships/"]
        E6["runbook -> Runbooks/"]
        E7["crm -> CRM/ (R23)"]
    end
    subgraph OKF["okf.* — vault governance"]
        K1["okf.schema -> governance/"]
        K2["okf.template -> _meta/templates/"]
        K3["okf.index -> Index.md"]
        K4["okf.validation_rule -> governance/validation-rules/"]
        K5["okf.mapping -> Mappings/"]
    end
```

Sub-typing via `*_kind` fields (ADR-010) prevents ontology creep — e.g.
`oscal.x.decision.decision_kind ∈ {decision, assumption, exception}`.
Canonical notes carry `oscal_model + kind: canonical`; extensions carry
`extension_model + kind: extension`; system-scoped notes carry `system_id`.

---

## 8. Output structure & files

```mermaid
flowchart TB
    ROOT["<plan-id>/"]
    ROOT --> IDX["Index.md — okf.index dashboards"]
    ROOT --> CTL["Controls/<ID>.md — oscal.control"]
    ROOT --> SYS["Systems/<slug>.md — oscal.ssp (system)"]
    ROOT --> PLN["Plans/<slug>.md — oscal.ssp (plan)"]
    ROOT --> CMP["Components/<slug>.md — oscal.component_definition"]
    ROOT --> ORG["Organizations/<slug>.md — oscal.party"]
    ROOT --> MAP["Mappings/*.md — okf.mapping"]
    ROOT --> GOV["governance/ — okf.schema + validation-rules"]
    ROOT --> META["_meta/ — tags + 9 templates"]
    ROOT --> NARR["Boundaries/ Decisions/ Diagrams/<br/>Inheritance/ Relationships/ Runbooks/<br/>(+ CRM/) — oscal.x.*"]
    NARR --> MEDIA["Diagrams/media/*.png"]
    ROOT --> SNAP[".snapshots/current/**/*.json — canonical (committed)"]
    ROOT --> EXP[".exports/ — OSCAL (gitignored)"]
```

Two committed generations live under `examples/vault/` (19 vaults total): a
**basic / fismatic-only** set (control tables only) and a **rich /
docx-narrative** set (dated names) that also carries the `oscal.x.*` narrative
folders and extracted diagram images.

Canonical JSON sidecars embed a `_meta` block with a `sha256` content hash,
`generated_at`, `generated_from`, and `schema_version`, plus `body_sections`,
`inline_fields`, and flattened frontmatter.

---

## 9. Validation

```mermaid
flowchart TB
    subgraph PC["Pre-commit hooks"]
        H1["ruff check / format"]
        H2["uv lock --check"]
        H3["no-docx guard"]
        H4["validate-frontmatter"]
        H5["validate-wikilinks (warn)"]
        H6["validate-dataview"]
        H7["generate-canonical-json"]
        H8["validate-oscal"]
    end
    subgraph VV["validate_vault.py (R22)"]
        F1["frontmatter schema"]
        F2["wikilink targets"]
        F3["dataview queries"]
        F4["duplicates / orphans / drafts / stale"]
        F5["OKF conformance"]
    end
    subgraph OSCALV["OSCAL export validation (ADR-019)"]
        L1["validate_oscal.py<br/>Layer 1 offline NIST schema"]
        L15["crosscheck_go_oscal.py<br/>Layer 1.5 optional binary"]
    end
    PC --> VV --> OSCALV
```

Error-level checks fail closed; structural/ported checks warn unless
`--strict`. CI (`.github/workflows/validate.yml`) runs
`uv sync --frozen → ruff check → ruff format --check → pytest (966 tests) →
the slow synthetic-sentinel roundtrip`. It needs no system content: the vault
and OSCAL gates above run in a **system repository** through
`compliance/scripts/validate_workspace.sh`.

---

## 10. Benchmarks

All five backends reach parity on headings/tables/images; they differ on speed

```mermaid
flowchart LR
    subgraph SPEED["Mean time / SSP (lower is better)"]
        direction TB
        F["fismatic 4.4s"]
        MI["mineru 17.0s"]
        UN["unstructured 15.0s"]
        MA["mammoth 19.0s"]
        DO["docling 113.3s"]
    end
```

| Backend | Mean time | Headings | Tables | ¶ | 🖼 | Pick when you need |
|---|---:|---:|---:|---:|---:|---|
| `fismatic` | **4.4s** | 658 | 2,578 | 14,860 | 92 | Speed / determinism / diffs |
| `docling` | 113.3s | 677 | 2,580 | 65,394 | **100** | Most images |
| `mineru` | 17.0s | **1,102** | 2,568 | 18,203 | 98 | Most headings (H5/H6) |
| `unstructured` | 15.0s | 657 | 2,578 | 15,491 | 92 | General-purpose baseline |
| `mammoth` | 19.0s | 648 | 2,578 | **84,307** | 93 | Most granular paragraphs / small install |

Contract coverage sits at **66.9% mean fill rate** (R14 baseline); the R15
work improved backend quality, not contract breadth. Coverage-gap ranking is
driven by `scripts/coverage_gap_analysis.py`, which ranks uncaptured headings
against the contract's `sourceHeading` set.

---

## 11. File & directory reference

| Path | Role |
|---|---|
| `fismatic/src/phase_1/fismatic/` | Primary `.docx → OSCAL SSP` parser (`docx_parser.py`, `parser.py`, `ssp_converter.py`). |
| `compliance/lib/` | Core: `vault.py`, `export_oscal.py`, `heading_parser.py`, `control_parameters.py`, `docx_images.py`, `canonical_model.py`, `canonical_json.py`, `oscal_source.py`, `extractor_backend.py`. |
| `compliance/scripts/` | Generation CLIs (`export_obsidian_vault.py`, `generate_canonical_json.py`, `export_oscal.py`, `validate_oscal.py`, `benchmark_extractors.py`). |
| `compliance/datacontracts/` | ODCS routing contract + roundtrip contracts + ODCS schema. |
| `compliance/mappings/` | Vocabulary crosswalk YAMLs. |
| `compliance/data/` | NIST Rev 5 control names, statements, parameter labels, FedRAMP baselines, D3FEND mapping. |
| `compliance/schemas/` | Vendored NIST OSCAL schemas (1.1.3, 1.0.4). |
| `compliance/tests/` | pytest suite + fixtures. |
| `templates/` | The official **blank** GSA Low / Moderate / High SSPP templates (no CUI) — see ADR-021. |
| `tests/` | Public-core governance and OSCAL-source checks. |
| `skills/c4-mermaid/` | Agent skill playbook for the C4 topology generator. |
| `examples/vault/` | Where a **system repository** keeps its generated vault. Nothing is committed here; this repository holds no system content. |
| `docs/rubrics/`, `docs/plans/` | Generator evaluation rubric and active increment plans. |
| `docs/` | `ARCHITECTURE.md`, `INTEROPERABILITY.md`, reference docs, `ADR/`, `osbapi-backstage-interop/`. |
| `vault-schemas.json` | The `$def` schema suite every vault note + projection validates against. |
| `compliance/scripts/validate_workspace.sh` | Runs every gate against a system repository, from that repository. |

---

### How to extend

- **New heading → field mapping:** edit `fedramp-ssp-v1.yaml` (a
  `sourceHeading` + `captureStrategy`), no code change.
- **New vocabulary value:** edit the relevant `mappings/*.yaml`.
- **New system:** add an entry to `scripts/registry.yaml`, then run
  `regenerate_all_vaults.py`.
- **Architecture change:** open an ADR (see `docs/ADR/`) before altering module
  boundaries, dependencies, or data classification.
