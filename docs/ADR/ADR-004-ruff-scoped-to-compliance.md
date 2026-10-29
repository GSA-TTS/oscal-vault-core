# ADR-004: Ruff Linting Scoped to `compliance/` Only

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R7 (Schema & Dependency Upgrades), R10 (Shift-Left Validation)

---

## Context

The repository contains three distinct Python codebases with different
provenance and maintenance SLAs:

| Directory | Provenance | State |
|-----------|------------|-------|
| `compliance/` | Actively maintained, new code | Clean, under strict linting |
| `fismatic/` | Legacy FedRAMP parser, semi-maintained | ~49 pre-existing ruff warnings |
| `python-ssp/` | Upstream OSBAPI reference impl | Upstream code, not our responsibility |

Enforcing strict linting across all three would block all PRs until legacy
code is cleaned up — a large, risky, and low-value migration.

## Decision

Ruff is configured to **lint only `compliance/`**. The `fismatic/` and
`python-ssp/` directories are excluded from all ruff runs.

In CI (`validate.yml`):
```yaml
- run: uv run ruff check compliance/
```

In pre-commit:
```yaml
entry: uv run ruff check compliance/
```

## Alternatives Considered

- **Lint all directories** — Rejected: ~49 pre-existing `fismatic/` warnings
  would break CI immediately with no safety benefit.
- **Per-directory ruff config files** — Considered but adds per-directory
  `ruff.toml` complexity; a single scoped CLI invocation achieves the same
  result more transparently.
- **Noqa suppressions in legacy code** — Rejected: adds noise to legacy
  files we don't actively maintain.

## Consequences

- **Positive:** New code in `compliance/` is held to strict linting standards
  without legacy drag.
- **Positive:** CI never breaks on `fismatic/` warnings outside our control.
- **Negative:** `fismatic/` code quality is not enforced. If `fismatic/` is
  adopted into the unified converter (R11), it will need a full lint pass before
  merge into `compliance/`.
- **Ongoing constraint:** Any code migrated from `fismatic/` or `python-ssp/`
  into `compliance/` must be ruff-clean before the PR merges.

## References

- `pyproject.toml` (ruff configuration)
- `.pre-commit-config.yaml` (hook entry)
- `.github/workflows/validate.yml`
