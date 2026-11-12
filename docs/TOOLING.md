---
title: "Reproducible contributor tooling"
description: "Pinned dependency, validation, hook and release-note commands"
status: draft
tier: 2
last_updated: "2026-11-12"
---

# Reproducible contributor tooling

AI-assisted tooling configuration requires human review before merge.

Install only this repository's declared tools when working under a parent mise
workspace:

```sh
mise install python node uv pnpm ruff prek git-cliff betterleaks zizmor
mise run setup
MISE_AUTO_INSTALL=0 mise run verify
mise run hooks
mise run changelog
```

`setup` uses frozen uv/pnpm locks and disables Node lifecycle scripts. `verify`
checks lint/format, tests, Mermaid, dependencies, secrets and offline Actions
security. uv audit uses system certificate trust without bypassing TLS validation.
The retained npm lock supports legacy consumers; active installs use pnpm.
Do not update one lock independently of its manifest during dependency upgrades.

`prek run --all-files` runs non-mutating local checks. git-cliff prints a preview,
not a release or tag. Nothing installs hooks into other repositories.

See [ADR-031](ADR/ADR-031-pinned-tooling-and-actions-boundaries.md) for the narrowly
scoped base-only inventory-trigger exception and its tests. Offline Zizmor cannot
prove online audits. Missing optional DOCX fixtures/backends are reported as
skipped by tests, never claimed as validated live integrations.
