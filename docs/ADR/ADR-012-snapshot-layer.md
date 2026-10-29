# ADR-012: Snapshot Layer — `.snapshots/current/` in Git, `history/` Gitignored

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R6 (Snapshot & Export Pipeline), R2 (Canonical JSON Sidecar Generation)

---

## Context

The canonical JSON sidecars serve two purposes:
1. **Current state** — the latest machine-readable view of vault content,
   used by downstream tooling and diff-based cache invalidation
2. **Historical audit** — immutable time-stamped snapshots for compliance
   audit trails

Storing both in git would make the repository impractically large over time
(thousands of snapshots × hundreds of notes per system × 10 systems).

## Decision

Two-tier snapshot strategy:

| Tier | Path | Git tracked? | Purpose |
|------|------|-------------|---------|
| Current | `examples/vault/*/.snapshots/current/` | ✅ Yes | Latest canonical JSON; used by CI and downstream tools |
| History | `examples/vault/*/.snapshots/history/` | ❌ No (gitignored) | Time-stamped immutable captures; local audit trail only |

The `current/` layer is committed to git so that:
- CI can diff sidecars against the previous commit to detect silent regressions
- PRs include sidecar diffs as a human-readable record of extraction changes
- Downstream tools can reference a stable path without running the generator

The `history/` layer is gitignored because:
- Time-stamped snapshots accumulate indefinitely (unbounded size)
- They are generated locally on-demand by the snapshot CLI

## Alternatives Considered

- **Both layers in git** — Rejected: repository size grows unbounded as
  snapshot history accumulates.
- **Neither layer in git (generate on demand)** — Rejected: CI would need to
  run the full vault generator on every run, making CI slow and fragile when
  `.docx` fixtures are absent.
- **Git LFS for history** — Considered; rejected as unnecessary complexity
  for a local audit trail that reviewers don't need to access via git.

## Consequences

- **Positive:** PRs that change extraction logic show diffs of affected
  sidecars, making review concrete and auditable.
- **Positive:** CI can detect sidecar hash changes without re-parsing `.docx`
  files (which are gitignored).
- **Negative:** `current/` sidecars must be manually regenerated and
  committed after any extraction logic change. The pre-commit hook automates
  this for vault `.md` changes but not for `compliance/lib/` code changes.
- **Ongoing workflow:** After changing extraction code, run
  `uv run python3 scripts/regenerate_all_vaults.py --force` then commit
  the updated `current/` sidecars.

## References

- `compliance/lib/canonical_json.py`
- `compliance/lib/snapshot.py`
- `compliance/scripts/generate_canonical_json.py`
- `.gitignore` (`.snapshots/history/` rule)
