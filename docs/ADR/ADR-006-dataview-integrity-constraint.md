# ADR-006: Dataview Integrity as Highest-Priority Test Constraint

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R10 (Shift-Left Validation), R5 (OKF Infrastructure Types)

---

## Context

Obsidian Dataview queries are the primary interface for compliance analysts
to interact with vault data (dashboards, coverage gaps, evidence status,
POA&M burndown). A broken Dataview query silently returns empty results
without throwing an error visible to the analyst. Field renames, type
taxonomy changes, or folder moves can silently break dashboards.

## Decision

**Dataview query correctness is the highest-priority test constraint in the
vault.** Every structural change to frontmatter fields, note types, or folder
paths must be accompanied by:

1. Regression tests that verify every `Index.md` Dataview query block:
   - query syntax parses cleanly
   - `FROM` and `WHERE` clauses reference existing folders / fields
   - rendered result is non-empty for a non-empty vault
2. Schema cross-reference tests asserting all field names in Dataview queries
   exist in at least one frontmatter `$def` in `vault-schemas.json`
3. No silent breakage: any field rename caught by CI before merge

Additionally:
- Every `oscal.*` and `oscal.x.*` type must have at least one Dataview query
  in `Index.md` or a dedicated dashboard note — **enforced by CI**.

## Alternatives Considered

- **No automated Dataview testing** — Rejected: Dataview errors are silent
  empty tables, meaning broken queries would go unnoticed until a compliance
  analyst manually discovered the gap.
- **Database-backed query engine (e.g., DuckDB)** — Rejected: breaks the
  Obsidian plugin ecosystem; Dataview is the community standard.
- **Test at CI time only** — Partially accepted (CI also validates); pre-commit
  validation on `Index.md` changes catches breakage faster.

## Consequences

- **Positive:** Analysts can trust that dashboards showing "0 controls" mean
  genuinely 0 controls, not a broken query.
- **Positive:** Field renames are caught before they reach `main`.
- **Negative:** Every new note type requires a corresponding dashboard query
  and template before it can be merged — adds overhead to type additions.
- **Ongoing constraint:** `validate_dataview_queries.py` runs in both
  pre-commit (on `Index.md` changes) and CI (full run via `validate_all.sh`).

## References

- `scripts/validate_dataview_queries.py`
- `archive/plans/PROJECT_REFACTORING_PLAN.md` §§ Dataview Integrity Constraint, Constraint Recommendations
- `examples/vault/*/Index.md` (dashboards per system)
