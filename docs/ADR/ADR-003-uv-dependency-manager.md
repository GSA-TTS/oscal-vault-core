# ADR-003: `uv` as Sole Python Dependency Manager

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R7 (Schema & Dependency Upgrades)

---

## Context

The project previously used `pip` + `requirements.txt` with multiple virtual
environments scattered across sub-packages (`fismatic/`, `python-ssp/`,
`compliance/`). This caused:

- Dependency version skew between sub-packages
- No lockfile → non-reproducible CI builds
- No cross-package dependency resolution

## Decision

`uv` (Astral's Rust-based Python package manager) is the **sole** package
management tool for this repo. All Python commands are prefixed with `uv run`.

Key rules:
- `uv sync --locked` installs from the lockfile (used in CI)
- `uv lock --upgrade` regenerates the lockfile (explicit, auditable)
- `uv lock --check` validates lockfile freshness (pre-commit gate)
- No bare `pip install` or `python -m venv` — these are banned by convention

Current pinned versions (as of 2026-07-18):

| Package | Version |
|---------|---------|
| jsonschema | 4.26.0 |
| pytest | 9.1.1 |
| python-docx | 1.2.0 |
| pyyaml | 6.0.3 |
| coverage | 7.15.2 |
| ruff | 0.15.22 |
| pre-commit | 4.6.0 |

## Alternatives Considered

- **Poetry** — Rejected: slower resolver, pyproject.toml lock format is
  non-standard; `uv` is a drop-in replacement with 10-100× faster resolution.
- **pip-tools** — Rejected: requires manual `pip-compile` step and doesn't
  integrate with pyproject.toml natively.
- **Conda** — Rejected: overkill for a pure-Python project with no native
  extension dependencies.

## Consequences

- **Positive:** Deterministic builds. `uv sync --locked` in CI guarantees
  bit-for-bit reproducible environments.
- **Positive:** Single `pyproject.toml` + `uv.lock` tracks all deps for all
  sub-packages.
- **Negative:** Contributors unfamiliar with `uv` need a one-time install
  (`curl -Lsf https://astral.sh/uv/install.sh | sh`). Documented in README.
- **Ongoing constraint:** Any new dependency added must go through
  `uv add <package>` + `uv lock`, not direct `pip install`.

## References

- `pyproject.toml`
- `uv.lock`
- `.github/workflows/validate.yml` (uses `astral-sh/setup-uv@v5`)
