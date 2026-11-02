---
title: "Obsidian Vault Validation"
description: "How the FedRAMP vault is validated headlessly, and the provenance of checks adapted from third-party Obsidian tools"
status: canonical
tier: 2
last_updated: "2026-07-20"
---

# Obsidian Vault Validation

The generated Obsidian vaults (`examples/vault/<plan-id>/`) are validated
**headlessly** — no Obsidian GUI, no Node, no network — by
`scripts/validate_vault.py`. This is the authoritative, CI-friendly gate.

> **Why not the official Obsidian CLI?** The Obsidian CLI
> (`obsidian.md/help/cli`) is a URI automation layer that drives the *desktop
> app* (open a vault, run a command, open a note). It has no headless
> "validate this vault" mode and requires the GUI, so it cannot run in an
> automated pipeline. `validate_vault.py` is the headless equivalent.

## What it runs

`validate_vault.py` composes the three focused validators and adds structural +
ported checks:

| Check | Source | Default severity |
|-------|--------|------------------|
| frontmatter schema | `scripts/validate_frontmatter.py` | **error** |
| Dataview query integrity | `scripts/validate_dataview_queries.py` | **error** |
| template-guidance invariant (moderate template 330 callouts) | this project | **error** (ADR-022/ADR-023) |
| wikilink integrity | `scripts/validate_wikilinks.py` | warn (error under `--strict`) |
| duplicate note names (ambiguous bare links) | this project | warn |
| embed targets resolve | this project | warn |
| tag syntax | this project | warn |
| OKF conformance (`type` present) | okf-enforcer | warn |
| orphan notes (no in/out links) | vault-triage | warn |
| draft markers (TODO/FIXME/WIP) | vault-triage | warn |
| thin notes (short body) | vault-triage | warn |
| stale notes (old mtime) | vault-triage | warn |
| `gardener-ignore` opt-out | obsidian-vault-intelligence | (modifier) |

```bash
# validate every vault under examples/vault/
python3 compliance/scripts/validate_vault.py

# one vault, gate on everything
python3 compliance/scripts/validate_vault.py --vault examples/vault/<system> --strict

# tune the ported thresholds
python3 compliance/scripts/validate_vault.py --thin-chars 0 --stale-days 90
```

Exit code is non-zero only when an **error-level** check fails (fail-closed).
All structural + ported checks are **warn-only by default** — generated vaults
carry legitimate advisory findings (e.g. a terse Organization note, or a
narrative citing an out-of-baseline control) that must not break the pipeline —
and become failures under `--strict`.

## Link graph audit (`audit_vault_graph.py`)

Links are resolved by **path**, as Obsidian does for one vault, not by file stem
([ADR-029](ADR/ADR-029-path-aware-wikilink-resolution.md)). `Systems/foo` and `Components/foo` are
different notes, and a bare `[[foo]]` that matches both is reported as ambiguous with its
candidates, never silently resolved. The orphan and duplicate-name checks above use the same
resolver.

```bash
# metrics for every vault in the workspace (or --vault <dir>); --list shows each finding
python <core>/compliance/scripts/audit_vault_graph.py [--list]

# record a baseline, then fail if any vault gets worse
python <core>/compliance/scripts/audit_vault_graph.py --out scripts/graph_baseline.json
python <core>/compliance/scripts/audit_vault_graph.py --baseline scripts/graph_baseline.json
```

The baseline gate compares `orphans_content`, `unresolved_links` and `ambiguous_links` per vault
(higher is worse). A vault missing from the baseline is not a regression; an unreadable baseline
is an error. Open **one system's directory** as the Obsidian vault, not `examples/vault/`.

## Obsidian Bases validation

`validate_dataview_queries.py` also validates every `_meta/views/*.base` file.
It checks the Base document structure, parses the supported recursive
`and`/`or`/`not` filter grammar, evaluates filters against frontmatter, and
prints each view's row count. Equality uses `==`; a single `=` is rejected.

Generated Base YAML contains only Obsidian fields. Empty results are handled by
validator context instead of an `allowEmpty` extension:

- An `All ...` view for a folder that exists must return at least one row.
- All views may be empty when their top-level folder is absent from that vault.
- `Requiring attention` and `Inherited / hybrid` in `controls.base` are
  conditional views and may be empty in a populated `Controls/` folder. This
  explicit policy is maintained in `_OPTIONAL_EMPTY_BASE_VIEWS`.

The committed `compliance/tests/fixtures/bases/obsidian-1.14.4-controls.base`
is the complete expected serialization regression fixture, not evidence of a
fresh GUI run in CI.

### Manual Obsidian 1.14.4 release gate

Electron automation is unavailable in the normal validation environment. For a
release that changes Base generation or validation, a reviewer with Obsidian
1.14.4 must perform this gate and attach screenshots or a short recording to the
release or pull request:

1. Open one generated plan directory, not `examples/vault/`, as the vault root.
2. Enable the built-in Bases core plugin and open each file under
   `_meta/views/`.
3. Confirm every populated-folder `All ...` view renders rows; compare the row
   counts with `uv run python scripts/validate_dataview_queries.py --vault
   examples/vault/<plan-id>`.
4. Confirm empty conditional Controls views show a valid empty table rather
   than a parse/render error.
5. Open a vault that lacks an optional entity folder and confirm its Base opens
   as an empty table without an error.
6. Save `controls.base`, then verify `git diff --exit-code --
   examples/vault/<plan-id>/_meta/views/controls.base` so Obsidian did not
   rewrite the canonical serialization.

This procedure is a release gate when relevant, but it was not executed or
observed by the headless implementation environment documented here.

Record the observation by copying
`compliance/data/obsidian-base-smoke.example.yaml`, filling every field, and
attaching screenshot/recording references. Validate it with:

```bash
uv run python compliance/scripts/verify_obsidian_base_smoke.py \
  path/to/obsidian-base-smoke.yaml --require
```

The committed example remains `status: pending`; it is a schema/template, not
evidence that the GUI gate was performed.

## Provenance & attribution of ported checks

We evaluated four community Obsidian validators and reimplemented their useful,
**deterministic** checks in pure Python (their logic runs against a headless
filesystem, not the Obsidian metadata cache). We did **not** vendor or link any
of their code; the checks were reimplemented from their documented behavior.
Attribution:

| Tool | URL | License | What we adapted |
|------|-----|---------|-----------------|
| okf-enforcer | https://github.com/MartinForReal/okf-enforcer | Apache-2.0 | OKF v0.1 §9 conformance: parseable frontmatter + non-empty `type` |
| vault-triage | https://github.com/israerusan/vault-triage | MIT | orphan (0 in/0 out), thin-note, stale-note, draft-markers |
| obsidian-vault-intelligence | https://github.com/cybaea/obsidian-vault-intelligence | MIT | `gardener-ignore` per-note opt-out from advisory checks |
| vault-inspector | https://github.com/rogerdigital/vault-inspector | MIT | (optional external cross-check; see below) — broken-link/orphan/dup/large-file scanner |

### Project-specific adaptations

- **Orphan check exempts OKF infrastructure notes** (`okf.index`, `okf.mapping`,
  `okf.schema`, `okf.validation_rule`). These are reached via Dataview
  dashboards and tag queries, not wikilinks, which a static link-graph scan
  cannot follow — a limitation vault-triage itself documents.
- **Thin-note default is low (80 chars)** because generated control notes are
  legitimately terse; set `--thin-chars 0` to disable.
- **Stale-note is off by default (`--stale-days 0`)** because generated vaults
  are written all at once (the check is otherwise all-or-nothing by calendar
  day).
- **Template scaffolds (`_meta/templates/`) are skipped** by every structural
  check (they contain intentional Jinja/Dataview slot syntax).

## Optional external cross-check: vault-inspector

`vault-inspector` (a TypeScript Obsidian plugin that also ships an `npx` CLI)
provides a useful *independent* second opinion, including checks we do not
implement (orphan attachments, duplicate-file content hashing, large files,
frontmatter type-drift across notes). It is **advisory, not a gate**:

```bash
npx vault-inspector@0.4.11 examples/vault/<plan-id> \
  --config vault-inspector.config.json --fail-on error
```

It is not wired as a required step because it depends on the Node toolchain
(intentionally out of scope for the offline Python pipeline) and its default
severity policy flags legitimate out-of-baseline control references as errors.
Use it locally when a deeper audit is wanted; `validate_vault.py` remains the
authoritative gate.

## Related producer-side tooling for downstream consumers

Consumers that vendor this repo as a pinned git submodule rely on invariants
this repo owns. Two additional scripts support that relationship:

### CUI/provenance sidecar validation (`scripts/validate_cui_contracts.py`)

Validates optional per-system `examples/vault/<system>/classification/cui.yaml`
sidecars against `compliance/datacontracts/schemas/cui-provenance-v1.schema.json`
(see [ADR-024](ADR/ADR-024-cui-provenance-datacontract.md)). A sidecar is
**optional per-system** — a system without one is not an error. This repo
ships the schema, the validator, and one synthetic `not_cui` example fixture;
it does **not** classify any real system (that is a data-owner/ISSO decision).

```bash
uv run python scripts/validate_cui_contracts.py            # validate present sidecars
uv run python scripts/validate_cui_contracts.py --coverage # report per-system coverage
```

### Pin-advance change summary (`scripts/vault_changelog.py`)

Diffs two refs of *this* repository and reports what a downstream pin advance
would change: systems added/removed, per-system control counts, template-guidance
coverage, and ODP (organization-defined-parameter) value wording. Run it before
advancing a consumer's submodule pin and paste the output into the pin-bump PR.

```bash
uv run python scripts/vault_changelog.py --from <old-ref> --to <new-ref>
```

### Manual template review package (`compliance/scripts/build_template_review_package.py`)

Creates a **local-only manual review artifact** that overlays matching source
SSP control-table content on an official target template. It appends the full
source-control narrative appendix, then creates supplemental control tables at
and target-baseline deltas rather than discarding them. The JSON manifest
classifies source-only controls as `baseline_or_template_delta` or
`legacy_or_custom_framework_control`, records duplicate target anchors, and
makes target-only controls explicit. The output is not an authorization
package or submission artifact.

```bash
uv run python compliance/scripts/build_template_review_package.py \
  --source <ssp>.docx \
  --template "fismatic/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx" \
  --out-dir review-output/<system> \
  --name <system>-in-moderate-review
```

Both the `.docx` and the review manifest are intentionally ignored by git;
they may contain source-derived content. Review the manifest before treating
any template blank as a source-content gap.
