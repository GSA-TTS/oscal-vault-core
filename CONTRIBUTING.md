# Contributing to oscal-vault

Contributions are welcome. This project uses a standard GitHub fork and pull
request workflow.

## Branch workflow

This project follows a **one-phase-per-branch** convention, established from
the beginning (see `git log --all --oneline --graph` for the full history):

- Each unit of work gets its own branch (e.g. `phase-8-python-ssp-api-fix`).
- When the work is complete and tests pass, the branch is merged into `main`
  via fast-forward (`git merge --ff-only`).
- Branches are rebased onto `main` before merge if `main` has moved, to
  keep history linear.
- Broken out into named phases in `archive/plans/NEXT_STEPS_PLAN.md`; new branches
  should follow the `phase-N-<short-description>` convention.

## Getting started

1. Fork the repository to your personal workspace.
2. Create a feature branch for your changes.
3. Make your changes, adding or updating tests as appropriate (see
   `compliance/README.md` and `scripts/validate_all.sh` for how this
   project is tested).
4. Run `uv sync && uv run pytest -q` locally before opening a pull request.
5. If the full end-to-end pipeline is relevant, also run
   `bash scripts/validate_all.sh` (requires `.docx` fixtures locally).
6. Open a pull request describing the change and its motivation.

## A note on `.docx` files

This repo's `.gitignore` excludes all `*.docx` files. Please do not add
FedRAMP SSP source documents (customer-specific or blank templates) to any
commit or pull request -- only derived artifacts (JSON/YAML/etc.) belong in
git history. See README.md's "No source `.docx` files are committed"
section for details and how tests handle this.

A local pre-commit hook (`scripts/pre-commit-check.sh`) can also be
installed to reject accidental `.docx` staging:

```bash
ln -sf ../../scripts/pre-commit-check.sh .git/hooks/pre-commit
```

## Code style

- `utils/`-flavored auto-formatting is available via `ruff format`.
- `ruff check` is used for linting (added as a `uv` dev dependency).
- New `compliance/` code is typed from the start (`pyright` / `mypy`
  compatible).

## Testing

Tests use `pytest`, managed through `uv`:

```bash
uv run pytest -q                       # full suite
uv run pytest compliance/tests -v      # vault/OSCAL pipeline tests
uv run pytest fismatic/src/phase_1/fismatic/test -v   # fismatic parser tests
```

The test suite is designed to run without any `.docx` fixtures present --
tests that depend on `.docx` files skip gracefully (via `pytest.mark.skipif`
or `conftest.py` hooks).

## Licensing

This project is in the public domain within the United States, and
copyright and related rights in the work worldwide are waived through the
[CC0 1.0 Universal public domain](https://creativecommons.org/publicdomain/zero/1.0/)
dedication (see `LICENSE.md`). By submitting a pull request, you are
agreeing to comply with this waiver of copyright interest.
