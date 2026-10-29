---
title: "Extraction to Vault to OSCAL Data Flow"
description: "Mermaid-backed reference for how FedRAMP SSP source documents become Obsidian vault notes, canonical JSON snapshots, and validated OSCAL exports."
status: canonical
tier: 1
last_updated: "2026-07-24"
related_files:
  - "README.md"
  - "docs/ARCHITECTURE.md"
  - "docs/OBSIDIAN_VAULT_SCHEMA.md"
  - "scripts/regenerate_all_vaults.py"
  - "scripts/registry.yaml"
  - "compliance/scripts/export_obsidian_vault.py"
  - "compliance/scripts/generate_canonical_json.py"
  - "compliance/scripts/export_oscal.py"
  - "compliance/lib/vault.py"
  - "compliance/lib/canonical_json.py"
  - "compliance/lib/export_oscal.py"
  - "vault-schemas.json"
---

# Extraction to Vault to OSCAL Data Flow

This document follows the data, not just the commands. It describes how a local
FedRAMP SSP `.docx` source is extracted, enriched, written as an Obsidian vault,
converted to canonical JSON sidecars, and exported as NIST OSCAL JSON.

Source `.docx` files remain local and gitignored. The committed artifacts are
the derived vault markdown, extracted media, and canonical `.snapshots/current`
JSON sidecars. Generated OSCAL exports under `.exports/oscal-json/` are
reproducible and gitignored.

## End-to-End Artifact Flow

```mermaid
flowchart LR
    DOCX[["Local FedRAMP SSP .docx<br/>(gitignored)"]]
    REG["scripts/registry.yaml<br/>docx path + plan metadata"]
    BATCH["scripts/regenerate_all_vaults.py"]
    SINGLE["export_obsidian_vault.py"]
    VAULT["examples/vault/<plan-id>/<br/>markdown + media"]
    SNAP[".snapshots/current/**/*.json<br/>canonical sidecars"]
    EXPORT[".exports/oscal-json/*.json<br/>generated OSCAL"]
    NIST["NIST OSCAL schema validation"]
    STRUCT["oscalkit structural validation"]

    REG --> BATCH
    DOCX --> BATCH
    BATCH --> SINGLE
    DOCX --> SINGLE
    SINGLE --> VAULT
    VAULT -->|generate_canonical_json.py| SNAP
    SNAP -->|export_oscal.py --validate| EXPORT
    EXPORT --> NIST
    EXPORT --> STRUCT
```

The registry is now the batch source of truth. Each entry with a `docx` field is
eligible for regeneration; missing source documents are skipped so a clean clone
can still validate the already committed derived artifacts.

## Extraction Stage

`export_obsidian_vault.py` orchestrates four extraction channels before calling
the vault builder.

```mermaid
flowchart TB
    DOCX[["SSP .docx"]]

    subgraph CONTROL["Control implementation extraction"]
        FIS["fismatic.SSPConverter"]
        RAWOSCAL["OSCAL SSP dict<br/>metadata, system-characteristics,<br/>implemented-requirements"]
        FIS --> RAWOSCAL
    end

    subgraph NARRATIVE["Narrative extraction"]
        CONTRACT["ODCS routing contract<br/>fedramp-ssp-v1.yaml"]
        HP["heading_parser"]
        NARR["typed Chapter 1-12 records<br/>boundaries, runbooks, diagrams,<br/>relationships, CRM, privacy"]
        CONTRACT --> HP --> NARR
    end

    subgraph SIDE["Side-channel recovery"]
        ODP["control_parameters<br/>organization-defined parameters"]
        IMG["docx_images<br/>embedded image bytes"]
    end

    subgraph META["Metadata enrichment"]
        REG["registry.yaml entry"]
        SUPP["owners, ISSO, AO,<br/>components, FIPS level"]
        REG --> SUPP
    end

    DOCX --> FIS
    DOCX --> HP
    DOCX --> ODP
    DOCX --> IMG
    DOCX -.plan_id lookup.-> REG
```

The fismatic parser is the primary live extractor. The heading parser fills the
gap left by table-only parsing by routing prose and tables from Chapters 1-12
into typed extension notes.

## Vault Build Stage

`compliance/lib/vault.py` converts extracted data into typed notes. Frontmatter
is the machine-readable index; markdown bodies remain human-reviewable in
Obsidian.

```mermaid
flowchart TB
    RAWOSCAL["OSCAL SSP dict"]
    NARR["Narrative records"]
    IMG["Extracted images"]
    SUPP["Registry supplements"]

    BUILD["build_vault()"]

    subgraph NOTES["Vault notes"]
        SYS["Systems/*.md<br/>oscal.ssp system"]
        PLAN["Plans/*.md<br/>oscal.ssp plan"]
        CTRL["Controls/*.md<br/>oscal.control"]
        COMP["Components/*.md<br/>oscal.component_definition"]
        ORG["Organizations/*.md<br/>oscal.party"]
        EXT["Boundaries, Decisions, Diagrams,<br/>Inheritance, Relationships, Runbooks,<br/>CRM, Privacy<br/>oscal.x.*"]
        GOV["governance/, Mappings/, _meta/<br/>okf.*"]
    end

    RAWOSCAL --> BUILD
    NARR --> BUILD
    IMG --> BUILD
    SUPP --> BUILD
    BUILD --> SYS & PLAN & CTRL & COMP & ORG & EXT & GOV
```

Generated relationship fields connect notes as a navigable graph and are
validated by `vault-schemas.json`:

| Field | Emitted on | Meaning |
| --- | --- | --- |
| `up_plan` | `Systems/*.md` | Parent SSP plan for the system. |
| `child_components` | `Systems/*.md` | Components belonging to the system. |
| `child_systems` | `Plans/*.md`, `Organizations/*.md` | Systems described by a plan or owned by an organization. |
| `child_controls` | `Plans/*.md` | Controls implemented under the plan. |
| `up_system` | `Components/*.md` | Owning system for a component. |

## Canonical Snapshot Stage

`generate_canonical_json.py` reads vault markdown as the source of truth and
emits deterministic JSON sidecars under `.snapshots/current/`.

```mermaid
sequenceDiagram
    autonumber
    participant CLI as generate_canonical_json.py
    participant MD as Vault markdown note
    participant FM as YAML frontmatter
    participant BODY as Markdown body sections
    participant CJ as canonical_json.py
    participant DISK as .snapshots/current

    CLI->>MD: read note
    MD->>FM: parse frontmatter
    MD->>BODY: split body sections + inline fields
    CLI->>CJ: build canonical document
    CJ-->>CLI: JSON with _meta.sha256
    CLI->>DISK: write mirrored .json path
```

Canonical sidecars preserve the note shape while making it easy for scripts to
consume vault content without reparsing markdown every time.

```mermaid
flowchart LR
    MD["Controls/AC-02.md"] --> JSON[".snapshots/current/Controls/AC-02.json"]
    JSON --> META["_meta<br/>hash, generated_from,<br/>schema_version"]
    JSON --> FM["frontmatter fields"]
    JSON --> BODY["body_sections"]
    JSON --> INLINE["inline_fields"]
```

## OSCAL Export Stage

`export_oscal.py` groups canonical JSON sidecars by `type` and emits strict
OSCAL documents.

```mermaid
flowchart TB
    SNAP[".snapshots/current"]

    subgraph GROUP["Grouped canonical sidecars"]
        SSP["oscal.ssp<br/>plan + system"]
        CTRL["oscal.control"]
        PARTY["oscal.party"]
        COMP["oscal.component_definition"]
    end

    subgraph CONVERT["OSCAL converters"]
        CSSP["canonical_to_oscal_ssp()"]
        CCOMP["canonical_to_oscal_component_definition()"]
    end

    subgraph OUT["Generated exports"]
        SSPJSON["<plan-id>-ssp.json"]
        COMPJSON["<component>-component.json"]
    end

    SNAP --> GROUP
    SSP --> CSSP
    CTRL --> CSSP
    PARTY --> CSSP
    COMP --> CCOMP
    CSSP --> SSPJSON
    CCOMP --> COMPJSON
```

The SSP export is assembled from multiple vault note types: plan metadata,
system characteristics, control implementations, and party/responsible-role
metadata. Component-definition exports are generated separately from component
notes.

## Validation Gates

The same flow is checked at multiple layers so a generated note graph can be
reviewed before trusting its OSCAL export.

```mermaid
flowchart TB
    VAULT["Vault markdown"]
    SNAP["Canonical sidecars"]
    EXPORT["OSCAL exports"]

    FM["validate_frontmatter.py<br/>vault-schemas.json"]
    DV["validate_dataview_queries.py"]
    WL["validate_wikilinks.py"]
    VV["validate_vault.py<br/>consolidated headless vault check"]
    NIST["export_oscal.py --validate<br/>offline NIST schema"]
    OKIT["oscalkit validate<br/>Layer-2 structural checks"]

    VAULT --> FM
    VAULT --> DV
    VAULT --> WL
    FM --> VV
    DV --> VV
    WL --> VV
    VAULT --> SNAP
    SNAP --> EXPORT
    EXPORT --> NIST
    EXPORT --> OKIT
```

Current non-strict vault warnings are informational review targets, not export
blockers: orphan extension notes, draft markers from source/template text, and
thin generated organization notes. Error-level checks fail the pipeline.

## Batch Regeneration Contract

The batch script intentionally skips missing `.docx` files. That keeps CI and a
fresh clone independent of source documents while still letting maintainers
regenerate the full local corpus.

```mermaid
flowchart TD
    REG["registry.yaml systems[]"]
    FILTER{"entry has docx?"}
    EXISTS{"source file exists?"}
    VAULT{"vault exists and no --force?"}
    GEN["export_obsidian_vault.py"]
    SKIP1["skip: metadata-only entry"]
    SKIP2["skip: missing local source"]
    SKIP3["skip: existing vault"]
    OUT["examples/vault/<plan-id>/"]

    REG --> FILTER
    FILTER -- no --> SKIP1
    FILTER -- yes --> EXISTS
    EXISTS -- no --> SKIP2
    EXISTS -- yes --> VAULT
    VAULT -- yes --> SKIP3
    VAULT -->|"no or --force"| GEN --> OUT
```

## Review Checklist

When a new source document is added to the local `fismatic/` corpus:

- Add a `scripts/registry.yaml` entry with `docx`, `plan_id`, `system_name`, and `fips_199`.
- Run `uv run python3 scripts/regenerate_all_vaults.py` or `--force` when refreshing existing vaults.
- Run `uv run python3 compliance/scripts/generate_canonical_json.py`.
- Run `uv run python3 scripts/validate_vault.py` and review any warning groups.
- Run `uv run python3 compliance/scripts/export_oscal.py --validate`.
- Run `bash scripts/validate_all.sh` before opening a PR.
