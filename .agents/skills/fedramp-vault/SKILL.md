---
name: fedramp-vault
description: Work with the FedRAMP Automation OSCAL+OKF vault system. Use when generating Obsidian vaults from FedRAMP SSP .docx files, running validation (pre-commit hooks, frontmatter, wikilinks, Dataview), generating canonical JSON sidecars, upgrading dependencies, or understanding the type taxonomy (oscal.*, oscal.x.*, okf.*).
---

# FedRAMP Automation Vault System

This skill covers the OSCAL + OKF compliance knowledge management system for
FedRAMP SSP processing. Use it when working with:

- Vault generation from `.docx` SSP files
- Type taxonomy (`oscal.*`, `oscal.x.*`, `okf.*`)
- Canonical JSON sidecar generation
- Pre-commit validation hooks
- Dependency management and test suites
- Schema definitions and crosswalk coverage

## Project Structure (key paths)

```
fedramp-automation/
├── compliance/
│   ├── lib/
│   │   ├── vault.py              # Pure vault builders (source of truth)
│   │   ├── canonical_json.py     # JSON sidecar generation
│   │   ├── canonical_model.py    # CanonicalControl dataclass
│   │   ├── vocab.py              # OSCAL → OSBAPI vocabulary mapping
│   │   ├── oscal_source.py       # docx → CanonicalControl loader
│   │   └── aggregate.py          # ImplementationStatus precedence
│   ├── scripts/
│   │   ├── export_obsidian_vault.py  # CLI: docx → vault
│   │   └── generate_canonical_json.py # CLI: markdown → JSON sidecars
│   └── tests/                        # 85 tests
├── fismatic/                     # .docx → OSCAL parser (primary)
├── scripts/
│   ├── regenerate_all_vaults.py      # Batch: all 10 systems
│   ├── registry.yaml                 # Per-system metadata supplements
│   ├── validate_all.sh               # Full pipeline validation
│   ├── validate_frontmatter.py       # R10: frontmatter schema
│   ├── validate_wikilinks.py         # R10: wikilink integrity
│   └── validate_dataview_queries.py  # R10: Dataview query integrity
├── docs/
│   ├── PROJECT_REFACTORING_PLAN.md   # Authoritative plan + full ontology
│   ├── OBSIDIAN_VAULT_SCHEMA.md      # Vault schema reference
│   ├── CROSSWALK_COVERAGE.md         # OSCAL concept crosswalk
│   └── REFACTORING_PLAN.md           # Raw architecture discussion
├── examples/vault/                   # 10 per-system vaults
│   └── <plan-id>/
│       ├── Controls/                 # oscal.control notes
│       ├── Components/               # oscal.component_definition notes
│       ├── Systems/                  # oscal.ssp (system) notes
│       ├── Plans/                    # oscal.ssp (plan) notes
│       ├── Organizations/            # oscal.party notes
│       ├── Mappings/                 # okf.mapping notes
│       ├── governance/               # okf.schema + validation-rules
│       ├── _meta/templates/          # okf.template notes
│       ├── Index.md                  # okf.index (Dataview dashboards)
│       └── .snapshots/current/       # Canonical JSON sidecars
├── osbapi-oscal-json-schemas.json    # 33 $defs, Draft 2020-12
├── .pre-commit-config.yaml           # 8 hooks
├── pyproject.toml                    # uv-managed deps
└── .github/workflows/validate.yml    # CI workflow
```

## Type Taxonomy

Every vault note has a dotted `type`. Use these conventions:

### Canonical OSCAL Types (`oscal.*`)

| Type | `oscal_model` | Folder |
|------|---------------|--------|
| `oscal.control` | `control` | `Controls/` |
| `oscal.component_definition` | `component-definition` | `Components/` |
| `oscal.ssp` | `system-security-plan` | `Systems/`, `Plans/` |
| `oscal.party` | `party` | `Organizations/` |
| `oscal.poam` | `plan-of-action-and-milestones` | `poam/` |
| `oscal.profile` | `profile` | `profiles/` |
| `oscal.catalog` | `catalog` | `catalogs/` |
| `oscal.assessment_plan` | `assessment-plan` | `assessments/` |
| `oscal.assessment_results` | `assessment-results` | `assessments/` |

### Extension Types (`oscal.x.*`)

| Type | `extension_model` | Folder |
|------|-------------------|--------|
| `oscal.x.diagram` | `diagram` | `diagrams/` |
| `oscal.x.decision` | `decision` | `decisions/` |
| `oscal.x.evidence` | `evidence` | `evidence/` |
| `oscal.x.finding` | `finding` | `findings/` |
| `oscal.x.inheritance` | `inheritance` | `inheritance/` |
| `oscal.x.boundary` | `boundary` | `boundaries/` |
| `oscal.x.runbook` | `runbook` | `runbooks/` |
| `oscal.x.relationship` | `relationship` | `relationships/` |
| `oscal.x.parameter` | `parameter` | `parameters/` |
| `oscal.x.control_part` | `control_part` | `control-parts/` |

### OKF Infrastructure Types (`okf.*`)

| Type | Folder |
|------|--------|
| `okf.schema` | `governance/` |
| `okf.template` | `_meta/templates/` |
| `okf.index` | (root `Index.md`) |
| `okf.validation_rule` | `governance/validation-rules/` |
| `okf.mapping` | `Mappings/` |

### Required fields

Every domain note must have: `type`, `title`, `status`, `owner`.
Every canonical type must have: `oscal_model`, `kind: canonical`.
Every extension type must have: `extension_model`, `kind: extension`.
Every system-scoped note must have: `system_id`.

## Common Workflows

### Generate a vault from a single .docx

```bash
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --docx <ssp>.docx \
  --plan-id <system> \
  --system-name "<System Name>" --fips-199 low \
  --metadata-yaml scripts/registry.yaml \
  --vault examples/vault/<system>/
```

### Regenerate all 10 vaults

```bash
uv run python3 scripts/regenerate_all_vaults.py --force
```

### Generate canonical JSON sidecars

```bash
# All vaults
uv run python3 compliance/scripts/generate_canonical_json.py

# Specific vault
uv run python3 compliance/scripts/generate_canonical_json.py --vault examples/vault/<system>/
```

### Run the full test suite

```bash
uv run pytest -q
```

### Run pre-commit hooks

```bash
# All files
uv run pre-commit run --all-files

# Install for automatic gating
uv run pre-commit install
```

### Run full validation pipeline

```bash
bash scripts/validate_all.sh
```

### Validate vault content only

```bash
uv run python3 scripts/validate_frontmatter.py
uv run python3 scripts/validate_wikilinks.py
uv run python3 scripts/validate_dataview_queries.py
```

## Key Conventions

1. **`uv` for all Python management** — use `uv run`, `uv sync`, `uv lock --upgrade`
2. **Ruff scoped to `compliance/`** only — 49 pre-existing warnings in `fismatic/`, `python-ssp/` are excluded
3. **No `.docx` in git** — tests use golden JSON fixtures under `compliance/tests/fixtures/`
4. **Markdown is single source of truth** — canonical JSON is generated, not edited
5. **Dataview integrity is highest-priority test constraint** — every query block must be regression-tested
6. **Validator handles schema versioning** — no per-document version tags; breaking changes → warnings + converter scripts
7. **Wikilink validator is warn-only by default** — legacy vaults have pre-existing broken links

## Dependency Management

```bash
# Upgrade all deps to latest compatible versions
uv lock --upgrade

# Check lockfile freshness (CI gate)
uv lock --check

# Current versions (as of 2026-07-18):
# jsonschema 4.26.0, pytest 9.1.1, python-docx 1.2.0,
# pyyaml 6.0.3, coverage 7.15.2, ruff 0.15.22,
# pre-commit 4.6.0, GitHub Actions: checkout@v5, setup-uv@v5
```

## Pitfalls

1. **`$ref` resolution in frontmatter validator**: Use `referencing.Registry` with the full schema root when validating sub-schemas standalone. See `scripts/validate_frontmatter.py` for the working pattern.
2. **`oscal.ssp` type ambiguity**: Both system and plan notes use `type: oscal.ssp`. Disambiguate via `note_type: system` vs `note_type: plan`.
3. **Template notes skip validation**: `_meta/templates/*.md` notes are intentionally incomplete.
4. **Wikilink validator warn-only**: Zendesk vault has ~13,400 broken links. Use `--strict` to fail.
5. **Dataview field parser skips aliases**: `Count`, `Status`, `ID`, `Type`, `Model`, `Name` are excluded.
6. **fismatic prints to stdout**: Redirect `sys.stdout` when calling `SSPConverter` from Python.
7. **`uv sync --locked` in CI**: Gates dependency changes through explicit `uv lock --upgrade`.

## Design Decisions (from refactoring)

- **`*_kind` sub-typing**: avoids ontology creep (e.g., `oscal.x.decision` with `decision_kind: decision|assumption|exception`)
- **Validator handles versioning**: no per-file schema version tags; breaking changes → warnings + converters
- **Canonical JSON is generated-only**: markdown is always the editable source
- **4-layer architecture**: Authoring (markdown) → Canonical (JSON sidecars) → Snapshot (time-stamped) → Export (OSCAL JSON/YAML)
- **Dataview most important constraint**: every structural change to frontmatter must pass Dataview query regression tests
