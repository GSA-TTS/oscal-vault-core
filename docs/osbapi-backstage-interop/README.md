# OSBAPI / Backstage Interoperability (recommended projection)

This directory holds **modeling guidance** for projecting an `oscal-vault`
export into downstream service-catalog representations — an
[Open Service Broker API](https://www.openservicebrokerapi.org/) (OSBAPI) CRM
summary and a [Backstage](https://backstage.io/) catalog entity.

It is **documentation, not a live pipeline.** The vault's core output is the
Obsidian vault + NIST OSCAL JSON (see the repo root `README.md` and
`docs/ARCHITECTURE.md`). The projection described here is the *recommended*
future interoperability path; the earlier generator scripts for it were
archived under `archive/osbapi-projection/` pending a datacontract-driven
reimplementation.

## Why it still lives here

- The vault's raw OSCAL control vocabulary is mapped through the versioned
  `Mappings/implementation-status.md` and `Mappings/control-origination.md`
  notes to OSBAPI CRM enums, so an OSCAL export can be projected with a pure,
  stateless transform. Generated control notes do not store normalized
  duplicates (ADR-026).
- The target shapes are already specified as JSON Schema `$defs` in
  [`../../vault-schemas.json`](../../vault-schemas.json): `OscalCrmSummary`,
  `FullCrmDocument`, `BackstageComplianceAnnotations`,
  `BackstageEntityComplianceEnvelope`, `ComplianceAdapterEntityResponse`.
- The transformation itself is documented as a data contract:
  [`oscal-to-osbapi-projection.yaml`](oscal-to-osbapi-projection.yaml).

## Contents

| File | What it describes |
|---|---|
| `capabilities-as-osbapi-offerings.md` | **How an OSBAPI service offering is modeled in OSCAL core: a `capability` declares the controls it provides, and `by-component.export` (provided/responsibilities) carries the CRM-style inheritance statement — what the consumer must still do to fully inherit.** |
| `osbapi-oscal-architecture.md` | The OSBAPI ↔ OSCAL ↔ Backstage runtime architecture and roles. |
| `osbapi-oscal-workflow-sequences.md` | Provision/read/update sequence diagrams. |
| `osbapi-oscal-detailed-examples.md` | Worked examples of each projected payload. |
| `osbapi-oscal-repo-structure.md` | The target repo/service layout convention. |
| `osbapi-extension-schema-and-yaml.md` | The OSBAPI extension schema + YAML. |
| `oscal-backstage-native-extension-examples.md` | Backstage-native entity extension examples. |
| `oscal-to-osbapi-projection.yaml` | **The recommended OSCAL → OSBAPI/Backstage transform, as a data contract.** |

## Status

Recommended / documented, not implemented in the live pipeline. To revive a
generator, build it against `oscal-to-osbapi-projection.yaml` and the
`vault-schemas.json` `$defs` rather than resurrecting the archived scripts,
which predate the current vault/OSCAL export.
