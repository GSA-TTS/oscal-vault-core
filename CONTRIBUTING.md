# Contributing to oscal-vault-core

Contributions are welcome. This project uses a standard GitHub fork and pull
request workflow.

[← README](README.md) · [Architecture](docs/ARCHITECTURE.md) ·
[ADR index](docs/ADR/README.md) · [Security policy](SECURITY.md)

## Branch workflow

- Each unit of work gets its own branch, named for what it does
  (`feat/…`, `fix/…`, `docs/…`, `ci/…`, `test/…`).
- Branches are rebased onto `main` before merge, to keep history linear.
- Open a pull request describing the change and its motivation. CI
  (`.github/workflows/validate.yml`) must pass.

## Getting started

```bash
# 1. Fork, then clone your fork and create a branch.

# 2. Install dependencies (creates .venv, resolves against uv.lock).
uv sync

# 3. Run the gates CI runs, before opening a pull request.
uv run ruff check compliance tests
uv run ruff format --check compliance tests
uv run pytest tests compliance/tests -q -m "not slow"
```

No `.docx` fixture is required: the suite is 966 passing tests on synthetic
fixtures, and tests that need a real source document skip gracefully.

If your change touches the `.docx` → OSCAL roundtrip, also run the slow
sentinel test that renders through the committed blank templates:

```bash
uv run pytest compliance/tests/test_docx_fill_roundtrip.py -q -m slow \
  -k synthetic_contract_sentinel
```

To exercise the full end-to-end pipeline you need a FedRAMP SSP `.docx` of your
own, or one of the blank baselines under `templates/`. See the README quickstart
for the three-command vault → sidecar → OSCAL path.

## Running the gates against a system repository

This repository holds no system content. To validate a repository that does,
run the harness **from that repository**:

```bash
cd /path/to/my-system-repo
bash /path/to/oscal-vault-core/compliance/scripts/validate_workspace.sh
```

A gate whose baseline file is absent reports `SKIPPED` rather than passing
silently. See [`docs/VAULT_VALIDATION.md`](docs/VAULT_VALIDATION.md) for what
each gate enforces.

## A note on `.docx` files

This repo's `.gitignore` excludes all `*.docx` files. Please do not add FedRAMP
SSP source documents to any commit or pull request — only derived artifacts
(JSON/YAML/Markdown) belong in git history. See the README's
"No source `.docx` files are committed" section and
[ADR-009](docs/ADR/ADR-009-no-docx-in-git.md).

Two deliberate exceptions are already committed and are **not** CUI: the blank
GSA Low/Moderate/High templates under `templates/`
([ADR-021](docs/ADR/ADR-021-commit-blank-templates.md)) and the `*.jinja.docx`
roundtrip templates under `compliance/templates/`.

## Code style

- `ruff format` for formatting, `ruff check` for linting. Both are `uv` dev
  dependencies and both run in CI.
- Ruff is scoped to `compliance/` and `tests/` by design — see
  [ADR-004](docs/ADR/ADR-004-ruff-scoped-to-compliance.md). The vendored
  `fismatic/` tree is excluded.
- New `compliance/` code is typed from the start (`pyright` / `mypy`
  compatible).

## Architecture decisions

Anything that changes the taxonomy, the datacontract shape, the export target,
or a validation gate belongs in an ADR. There are 27 of them under
[`docs/ADR/`](docs/ADR/README.md); follow the existing format and add the row to
the index table.

## Licensing

This project is in the public domain within the United States, and copyright and
related rights in the work worldwide are waived through the
[CC0 1.0 Universal public domain](https://creativecommons.org/publicdomain/zero/1.0/)
dedication (see [`LICENSE.md`](LICENSE.md)). By submitting a pull request, you
are agreeing to comply with this waiver of copyright interest.
