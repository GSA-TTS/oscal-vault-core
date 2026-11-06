# Vendored NIST OSCAL JSON Schemas

Layer 1 of the two-layer OSCAL validation strategy (ADR-019, phase R17) uses
these **vendored** NIST OSCAL JSON Schemas so the primary conformance gate runs
fully offline — no network, Docker, or database at validation time.

## Provenance

- **Upstream:** https://github.com/usnistgov/OSCAL
- **Releases:** `v1.1.3` (default target) and `v1.0.4` (legacy target for
  agency/FedRAMP portals still pinned to a 1.0.x baseline).
- **Format:** JSON Schema **draft-07** (self-contained; all `$ref`s resolve
  within each file's `definitions`).
- **Downloaded:** 2026-07-20

## Files

### `oscal-1.1.3/` (default)

| File | SHA-256 | Source asset |
|------|---------|--------------|
| `oscal_ssp_schema.json` | `da5f452b9e7bdf85246b79ed32475cd419321eb600e6928439ae67aef5a63e53` | [oscal_ssp_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.1.3/oscal_ssp_schema.json) |
| `oscal_component_schema.json` | `9bde069f8f65ec82ea626348cc40ae5d42b0f74c1a2a8b9289a1604bf521a15c` | [oscal_component_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.1.3/oscal_component_schema.json) |
| `oscal_poam_schema.json` | `e404043fef9cc6108c0e895932f513043d54f28457b4eb02e74dc0cff1215e16` | [oscal_poam_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.1.3/oscal_poam_schema.json) |
| `oscal_assessment-results_schema.json` | `d9e34757f0c12aff61f52b821f0b8f83ba0ba75b3a149a202b08ba82f82bc4c3` | [oscal_assessment-results_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.1.3/oscal_assessment-results_schema.json) |

### `oscal-1.0.4/` (legacy)

| File | SHA-256 | Source asset |
|------|---------|--------------|
| `oscal_ssp_schema.json` | `5c54cdce8c703617fc7a8069efc63082abb7c7b8b7d7c8690954c1f7c5975fa4` | [oscal_ssp_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.0.4/oscal_ssp_schema.json) |
| `oscal_component_schema.json` | `55c57a34adb70319e691cdce3caa657dbbf84f54e5721928ef12b0ceb02ffad6` | [oscal_component_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.0.4/oscal_component_schema.json) |
| `oscal_poam_schema.json` | `43f396d88cc73332e47b6b064014f958095e5f215266992b8a7ee7f2af0bcd62` | [oscal_poam_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.0.4/oscal_poam_schema.json) |
| `oscal_assessment-results_schema.json` | `ccbb55a80f8b5753e9f6dfbc317e7b35a2800f013ce17cb04ee69e6ad4cd6a9e` | [oscal_assessment-results_schema.json](https://github.com/usnistgov/OSCAL/releases/download/v1.0.4/oscal_assessment-results_schema.json) |

The SSP and component-definition models are what this project **exports**.
The `plan-of-action-and-milestones` and `assessment-results` schemas are
vendored for consumers that emit those documents (for example GSA Pages) and
are **schema-validated only**: `semantic=True` fails closed for them because
the reference-graph checks are specific to SSP/component-definition layouts.
Additional models (catalog, profile, assessment-plan) can be added from the
same release when needed.

### Version deltas the exporter handles

The one schema difference that affects our output: OSCAL **1.0.x** requires the
confidentiality/integrity/availability impact triad on each
`system-information.information-types[]` entry; **1.1.x** relaxed that. The
exporter (`compliance/lib/export_oscal.py`, `_information_type`) emits the triad
only when targeting a 1.0.x version, so a single code path satisfies both.

The set of vendored versions is declared in
`compliance/lib/validate_oscal.py` (`SUPPORTED_OSCAL_VERSIONS`).

## Updating

To refresh or add a schema, download the release asset and record its SHA-256
here:

```bash
curl -sL -o oscal-1.1.3/oscal_<model>_schema.json \
  https://github.com/usnistgov/OSCAL/releases/download/v1.1.3/oscal_<model>_schema.json
sha256sum oscal-1.1.3/oscal_<model>_schema.json
```

Then wire the wrapper key in `compliance/lib/validate_oscal.py`
(`_WRAPPER_TO_SCHEMA`).

## License

NIST OSCAL is a work of the U.S. Government and is in the public domain
(consistent with this repository's CC0 dedication).
