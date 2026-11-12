---
title: "Use pinned mise tooling and preserve Actions trust boundaries"
description: "Proposed reproducible dependency, hook, security and release-note tooling"
status: draft
tier: 2
date: "2026-11-12"
decision_makers: ["Repository maintainers"]
nist_controls: ["AC-6", "CM-3", "CM-6", "SI-10", "SA-11", "SR-3"]
impact_level: moderate
ato_relevance: yes-internal
risk_treatment: mitigate
---

# Use pinned mise tooling and preserve Actions trust boundaries

## Context and decision drivers

Local and CI setup used separate Python/Node actions and lacked one reproducible
security gate. Exact versions, frozen lockfiles, no implicit lifecycle scripts,
least privilege and human release review are required.

## Considered options

1. Continue separate installations: more drift and incomplete local checks.
2. Rely on global tools: unverifiable versions.
3. Pin tools with mise, run the same named checks locally and in CI.

## Proposed outcome

Use option 3. `mise run setup` installs locked dependencies; `mise run verify`
runs Ruff, tests/Mermaid, uv/pnpm audit, BetterLeaks and offline Zizmor. `prek`
uses local hooks; git-cliff only previews release notes and never publishes.
The npm lock remains for compatibility; pnpm is the active installer.

The inventory gate intentionally retains `pull_request_target`: it executes only
base-branch code, checks out no head code, uses read-only permissions and reads
changed paths through REST. Its single Zizmor trigger exception is scoped to
that workflow and protected by regression tests. No other rule is ignored.

## Consequences

Tool downloads and vulnerability lookups require approved network access.
Offline Zizmor checks do not prove online rule results. Missing tools and scan
failures block verification. Human review of this draft and any release remains
required. Supports AC-6, SI-10, CM-6, SA-11 and SR-3.

## Tracking

Tooling work: repository issue #68. AI assistance: GPT 6.1 Sol.
