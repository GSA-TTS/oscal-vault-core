# ADR-005: Pre-Commit Hook Strategy (8 Gates)

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R10 (Shift-Left Validation)

---

## Context

Shift-left validation requires catching problems at commit time rather than
at CI time or (worse) in production. The FedRAMP compliance domain has several
classes of easily machine-checkable errors: stale lockfiles, leaked source
documents, invalid vault frontmatter, broken wikilinks, broken Dataview
queries, and out-of-sync canonical JSON sidecars.

## Decision

Eight pre-commit hooks are installed, executed in dependency order:

| # | Hook ID | Trigger | Purpose |
|---|---------|---------|---------|
| 1 | `ruff-check` | `.py` files in `compliance/` | Python lint gate |
| 2 | `ruff-format-check` | `.py` files in `compliance/` | Python format gate |
| 3 | `uv-lock-check` | `pyproject.toml` or `uv.lock` changes | Lockfile freshness |
| 4 | `no-docx` | Any staged file | Prevent `.docx` source docs leaking to git |
| 5 | `validate-frontmatter` | `examples/vault/**/*.md` | JSON Schema frontmatter validation (33 `$defs`) |
| 6 | `validate-wikilinks` | `examples/vault/**/*.md` | Broken wikilink detection (warn-only) |
| 7 | `validate-dataview` | `examples/vault/**/Index.md` | Dataview query syntax validation |
| 8 | `generate-canonical-json` | `examples/vault/**/*.md` | Auto-regenerate JSON sidecars |

The `.docx` guard (`no-docx`) runs before all vault hooks to prevent
accidental staging of real SSP documents.

## Alternatives Considered

- **CI-only validation** — Rejected: too slow; errors discovered minutes
  after commit rather than immediately.
- **All hooks fail-fast** — Considered; wikilink hook retained as warn-only
  (see ADR-014) because legacy vaults have thousands of pre-existing broken
  links.
- **Fewer hooks (lint + test only)** — Rejected: frontmatter schema and
  Dataview integrity are the most impactful compliance-specific constraints
  and require dedicated hooks.

## Consequences

- **Positive:** Schema drift, broken wikilinks, and stale sidecars are caught
  before they reach the branch, never reaching CI.
- **Positive:** Canonical JSON is always in sync with markdown at commit
  time without requiring a manual regeneration step.
- **Negative:** Commits take ~15–30 seconds longer due to sidecar generation
  across all vault files.
- **Ongoing workflow:** Install hooks once with `uv run pre-commit install`.
  Run all hooks manually with `uv run pre-commit run --all-files`.

## References

- `.pre-commit-config.yaml`
- `scripts/pre-commit-check.sh` (docx guard)
- `scripts/validate_frontmatter.py`
- `scripts/validate_wikilinks.py`
- `scripts/validate_dataview_queries.py`
- `compliance/scripts/generate_canonical_json.py`
