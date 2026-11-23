# ADR-037: Restricted Local DOCX Redline

**Status:** Proposed
**Date:** 2026-11-23
**Deciders:** Project maintainers (review pending); AI-assisted draft

## Context

Reviewers of a generated document sometimes need to see the prose difference between an
approved source DOCX and a regenerated one. A consumer wrote a script that produces a full
DOCX redline, hunk-aligned split DOCX files, escaped HTML and a manifest (issue #90). The
output holds raw document prose, which may be controlled, so the main risk is accidental
publication, not the diff algorithm.

## Decision

Provide the generator in core as `compliance/lib/docx_redline.py` with the command
`compliance/scripts/generate_docx_redline.py`, under these rules:

- **Caller-provided paths.** Two input DOCX files and an explicit `--out-dir`; no defaults.
- **Never inside a repository.** The output directory is rejected if it or any parent holds
  `.git` (a directory, or the file a linked worktree uses). This is stricter than "outside
  this repository": it also covers the consumer's own checkout and the vendored core.
- **No side effects beyond the output directory.** No upload, commit, PR attachment, browser
  launch or office-suite launch. A test asserts the module imports no network, process or
  browser modules.
- **Restricted warning by default.** Every DOCX, the HTML and the manifest carry it.
- **Escaped HTML.** All diff text goes through `html.escape`.
- **Text only.** Formatting and DOCX metadata are not compared.

## Consequences

- One reviewed implementation of the restricted-output rules for every consumer.
- Consumers lose the ability to write redlines into a scratch folder inside their checkout;
  they must use a directory outside any git tree.
- The manifest records absolute input paths and SHA-256 digests, which can reveal local
  directory names. Callers decide where to keep it.

## Not in this change

- Word-level or tracked-changes output.
- Any publication or sharing mechanism.
