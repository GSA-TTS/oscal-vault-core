# Suggestions and Improvement Plan

Based on the current state of the pipeline and the various standards/specs
(OSCAL SSP, OSBAPI extension schemas, FedRAMP SSP templates, Backstage
catalog entities), this document identifies gaps and proposes concrete
improvements, organized by theme.

---

## 1. Obsidian Vault Generator (✅ implemented)

**Current state:** The pipeline produces OSCAL SSP JSON, OSBAPI CRM
summaries, Backstage entity YAML, and compliance adapter responses — all
machine-to-machine formats. There is no human-readable/human-editable
output optimized for compliance analysts, auditors, or system owners.

**Resolution:** ✅ Implemented in Phase 18 (`compliance/scripts/export_obsidian_vault.py`
+ `compliance/lib/vault.py`). See `docs/OBSIDIAN_VAULT_SCHEMA.md` for the
schema, `examples/vault/` for the sample output (160 notes from a real
Low SSP), and `examples/oscal/ssp-source.example.json` for a committed
synthetic input that lets the vault regenerate in any fresh clone.

**Outcome:**
- Compliance analysts can review/edit control narratives in a familiar
  editor (Obsidian) with wikilinks to related systems, components, and
  evidence.
- The vault's graph view visualizes the compliance posture (which controls
  are inherited from which component, which systems share an org, etc.).
- Dataview queries replace ad-hoc spreadsheet exports for status
  rollups, gap analysis, and POA&M tracking.
- The vault can be version-controlled alongside the pipeline outputs or
  published via Obsidian Publish / Dendron / GitHub Pages.

**Generator usage:**
```bash
uv run python3 compliance/scripts/export_obsidian_vault.py \
  --oscal-json examples/oscal/ssp-source.example.json \
  --plan-id sample-system-ssp-2024 \
  --vault examples/vault/
```

---

## 2. Controlled vocabulary documentation as vault mappings ✅ Done

**Current state:** Implementation-status and control-origination mapping
tables exist as YAML data files in `compliance/mappings/`. They are
machine-readable but not discoverable as human-readable reference docs.

**Resolution:** ✅ The vault generator now emits `Mappings/*.md` notes
that render each mapping table as a markdown table with commentary, sourced
from the actual `compliance/mappings/*.yaml` files (see
`compliance/lib/vault.py`'s `build_mapping_notes()`).

**Value:** Vault users can see at a glance why "partially-implemented"
maps to "under-development" or why "hybrid" maps to "shared", with the
remark that explains the judgment call.

---

## 3. SSP diff / change-tracking

**Current state:** Running the pipeline on a new version of an SSP .docx
overwrites the previous OSCAL output. There is no structured diff between
SSP versions (which controls changed status? which origination changed?).

**Proposal:** Add a `scripts/ssp_diff.py` or `compliance/scripts/ssp_diff.py`
that takes two OSCAL SSP JSON files and emits a structured diff at the
control level:
- Controls whose `implementation_status` changed
- Controls whose `control_origination` changed
- Controls that were added or removed
- Statements whose narrative content changed

**Value:** Reviewers can see exactly what changed between SSP versions
without re-reading the entire document. This is particularly useful during
annual ATO renewals, significant change requests, and FedRAMP package
updates.

**Implementation note:** This could reuse the CanonicalControl comparison
already proven in `compliance/tests/test_docx_generator.py`'s
`_compare_controls()` function.

---

## 4. Multiple-parser cross-validation

**Current state:** The pipeline primarily uses `fismatic` as its parser.
`python-ssp` is available as a second parser but is not exercised through
the OSBAPI pipeline — only `fismatic` feeds `compliance/lib/oscal_source.py`.

**Proposal:** Add a `compliance/lib/multi_parser.py` module that runs both
`fismatic` and `python-ssp` on the same document and reports control-level
differences, flagging any control where the two parsers disagree on
implementation-status or control-origination.

**Value:** Cross-validation catches parser-specific bugs (e.g. the
checkbox character issue found in Phase 12 would have been surfaced
immediately if a second parser had flagged those controls).

**Implementation note:** This is only possible when both parsers have a
working test fixture for the same document.

---

## 5. Schema conformance dashboard

**Current state:** Schema validation runs per-file at generation time
(via `validate_against_schema()`). There is no aggregate report of
schema conformance across all generated outputs.

**Proposal:** Add a `scripts/schema_conformance_report.py` that loads
every generated JSON/YAML in `examples/`, runs each against its
corresponding `vault-schemas.json` `$def/*` schema, and
emits a summary table:

| File | Target schema | Valid | Errors |
|---|---|---|---|
| crm-summary.example.json | OscalCrmSummary | ✅ | 0 |
| crm-summary.example.full.json | FullCrmDocument | ✅ | 0 |
| entity-response.example.json | ComplianceAdapterEntityResponse | ✅ | 0 |

**Value:** Provides a single-source-of-truth for schema concurrency status
across all pipeline outputs.

---

## 6. Baseline coverage matrix

**Current state:** The golden fixture tests (`test_golden_fixtures.py`)
validate that blank Low/Moderate/High baselines produce schema-valid
output with minimum control counts, but they don't test for *completeness*
(e.g. "all controls in FedRAMP's official High baseline must be present").

**Proposal:** Add `compliance/tests/fixtures/fedramp-baseline-controls-low.json`
(etc.) — lists of expected control IDs per baseline level, sourced from
FedRAMP's official baseline spreadsheets. Then add parametrized tests
asserting that every expected control ID appears in the golden fixtures.

**Value:** Catches missing controls (parser dropped an entire family?) and
unexpected extras (parser mistook a table row for a control that isn't
one?).

---

## 7. CI workflow (deferred, still open)

**Deferred from earlier phase; now actionable** since Phases 8-10 removed
the original blockers (the full CI plan is not exported to the public core). Phase 18
wires `ruff check` into `scripts/validate_all.sh` and creates the workflow.

```yaml
name: Validate OSBAPI generation pipeline
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run pytest -q
      - run: bash scripts/validate_all.sh
```

All 77 tests pass with zero external fixtures (the golden fixtures are
committed JSON). No `.docx` files are needed.

---

## 8. Pre-commit framework integration

**Current state:** `scripts/pre-commit-check.sh` is a standalone shell
script that blocks `.docx` staging. It works but is not discoverable or
auto-installable.

**Proposal:** Add a `.pre-commit-config.yaml` using the
[pre-commit](https://pre-commit.com) framework with hooks for:
- `ruff check` (Python lint)
- `ruff format` (Python format check)
- Custom `scripts/pre-commit-check.sh` (docx guard)
- `uv lock --check` (lockfile freshness)

**Value:** Standardized, auto-installable pre-commit hooks via
`pre-commit install`.

---

## 9. Multi-document federation

**Current state:** The pipeline processes one SSP at a time. There is no
concept of a "federation" of SSPs across multiple systems (e.g. a parent
SSP for the platform and child SSPs for tenant systems).

**Proposal:** Define a `compliance/federation.yaml` schema that maps
relationships between multiple SSPs:

```yaml
version: "1.0"
systems:
  - system_id: example-system
    acronym: SYSTEM-ACRONYM
    ssp: <ssp>.docx
    inherits_from:
      - system_id: example-platform
        controls:
          - AC-1
          - AC-2
          - AU-4
    children:
      - system_id: gsa-tenant-1
```

**Value:** Enables end-to-end processing of FedRAMP authorization packages
where the cloud service provider's SSP inherits a control baseline from a
leveraged system (e.g. the platform provider's FedRAMP JAB P-ATO), and tenant systems
inherit from the CSP. The pipeline could flatten inheritance chains and
report which controls still need CSP-specific implementation.

---

## 10. OSBAPI to OSCAL back-mapping

**Current state:** The `vault-schemas.json` extension fields
(`oscal_implementation_status`, `oscal_origination`) carry raw OSCAL
values through the OSBAPI projection, enabling round-trips. This is
proven at the `CanonicalControl` → `ControlSummary` level.

**Proposal:** Extend the back-mapping to the adapter response layer:
`ComplianceAdapterEntityResponse` should carry the raw OSCAL values in
its `summary.controls[*]` entries (already done in Phase 17). But the
`implemented_requirements` detail block in the same response should also
carry them, so a downstream tool consuming the adapter response can
reconstruct full OSCAL without re-fetching from the OSCAL store.

Already partially implemented in Phase 17 (`build_full_crm_document`
populates `oscal_origination`/`oscal_implementation_status` in
`by_components`); complete coverage is tracked as a follow-up to that
phase.
