# ADR-028: Workspace-relative output, and a guard against system content in the core

**Status:** Proposed
**Date:** 2026-11-02
**Deciders:** Project architecture agent + user
**Phase:** R22 (vendoring safety)

---

## Context

The core is meant to be **vendored** (git submodule or copied tree) by a system repository, which
holds the system's source documents and generated vault. Three gaps worked against that:

1. Most commands already resolved their paths against the current directory, but
   `bundle.py` defaulted to `<core>/.exports/bundles`, so running it from a vendored core wrote
   into the submodule's tree. Nothing stopped any other command from being pointed there.
2. The core's `.gitignore` protected only the core. A consuming repository got none of it.
3. The only protection against source documents was `*.docx` in `.gitignore`. ADR-009 describes a
   `no-docx` pre-commit hook, but no such hook is in the repository. `.gitignore` does not stop
   `git add -f` or a renamed file, and a CI check that lives in the pull request can be edited by
   that pull request.

The intent is: **vendoring the upstream core is the pattern.** A fork that drops a `.docx` in for
a quick experiment must keep working locally, but must not be able to push system content upstream.

## Decision

1. **A single workspace helper** (`compliance/lib/workspace.py`). The workspace is `--workspace`,
   then `OSCAL_VAULT_WORKSPACE`, then the current directory. Commands resolve defaults against it
   instead of each computing its own root.
2. **A write guard.** `guard_output()` refuses any destination inside the core's own tree when the
   core is a submodule or a nested copy. `--allow-write-in-core` or
   `OSCAL_VAULT_ALLOW_WRITE_IN_CORE=1` overrides it. A standalone clone is allowed, with one
   notice per process, so experiments still work. `bundle.py` now defaults to the workspace.
3. **`init_workspace.py`.** Creates `examples/vault/` and `scripts/` and maintains one marked block
   in the workspace `.gitignore` for source documents and extraction intermediates. Re-running
   rewrites only that block. `examples/vault/` stays tracked by default, because it is the output
   of record in repositories such as oscal-vault; `--ignore-vault` ignores it for repositories
   that must not commit source-derived output.
4. **A guard that runs from the base branch.** `check_system_content.py` refuses added office
   documents whose blob hash is not on `compliance/data/blank_template_allowlist.json`, anything
   under `examples/`, `.exports/`, `.snapshots/`, `review-output/`, `roundtrip_review/` or
   `docx_catchall/`, and generated vault notes (`type: oscal.*`) outside `docs/` and test fixtures.
   The `guard-system-content` workflow runs it on `pull_request_target`, so the workflow and the
   allowlist come from the base branch and cannot be edited by the pull request. It reads the PR's
   file list through the REST API and checks out no PR code. It fails closed: an unreadable or
   truncated (3000+ file) list is an error, not a pass.
5. **The core's own `.gitignore`** also ignores `/examples/`, `.exports/`, `review-output/`,
   `roundtrip_review/` and `.obsidian/`. This only stops accidental staging.

## Consequences

- Running a command from a vendored core no longer dirties the submodule.
- The guard catches `git add -f`, renamed office files, and generated output committed under the
  forbidden directories. It does **not** detect system prose pasted into an ordinary document, or a
  binary renamed to look like data. Review remains the backstop.
- The guard is only as strong as the repository settings: the check must be **required** on `main`
  and code-owner review must cover `.github/`, the allowlist and the guard. Those are repository
  settings and are not changed by this ADR.
- Anything already merged is not removed by the guard; only a history rewrite does that.
- ADR-009's `no-docx` hook is superseded in practice by items 4 and 5. ADR-009 is not edited here.
- 24 command scripts now use the workspace helper; behaviour is unchanged when the current
  directory is the workspace, which is how `validate_workspace.sh` already runs.

## Alternatives considered

- **`.gitignore` only.** Rejected: bypassed by `git add -f` and by files already tracked.
- **A `pull_request` workflow.** Rejected: runs the PR's copy of the workflow, which a fork can
  edit or delete.
- **Check out the PR head in `pull_request_target`.** Rejected: runs untrusted code with a token.
- **Refuse all writes inside the core, even standalone.** Rejected: would break local experiments.
- **Ignore `examples/vault/` by default in `init_workspace.py`.** Rejected: would silently break
  repositories where the vault is the tracked output of record.
