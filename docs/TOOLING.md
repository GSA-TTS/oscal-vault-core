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

## DOCX narrative formatting

`compliance/lib/docx_narrative_format.py` renders Markdown narrative in filled DOCX table cells
as Word formatting at render time (headers, `**bold**`, http(s) links, real lists that restart
at 1). The caller supplies the header and audited-table patterns; the module holds no system
labels. See ADR-036.

```python
import re
from docx import Document
from compliance.lib import docx_narrative_format as nf

doc = Document("filled.docx")
header_re = re.compile(r"(?:Customer|Provider):")
nf.format_document(doc, header_re)
nf.strip_table_page_breaks(doc)
assert not any(nf.audit_document(doc, header_re).values())
doc.save("formatted.docx")
```

## DFD network and data-flow figures

`compliance/scripts/render_dfd_diagram.py` draws the SSP network (`--view network`) or
data-flow (`--view data-flow`) figure from a CycloneDX threat-model DFD as SVG, or as PNG
through `resvg-py`. No imaging library is needed. An optional architecture YAML supplies flow
overrides and `diagram_layout.mode` (`legacy` or `authorization-boundary`). See ADR-038.

```bash
python compliance/scripts/render_dfd_diagram.py --dfd model/base-dfd.cdx.json \
  --architecture model/architecture.yaml --view network \
  --title "Figure 9-1. Network Diagram" --output build/network.png
```

## Restricted local DOCX redline

`compliance/scripts/generate_docx_redline.py` writes a full DOCX redline, split DOCX files,
escaped HTML and a manifest comparing the extracted text of two DOCX files. The output holds
raw prose, so `--out-dir` must be outside any git repository or worktree, every artifact carries
a restricted-review warning, and nothing is uploaded, committed or opened. See ADR-037.

```bash
python compliance/scripts/generate_docx_redline.py before.docx after.docx \
  --out-dir ~/review-output/redline
```

## GSA assessment workbooks to canonical JSON

Converts the GSA Control Tailoring Workbook and the NIST SP 800-53A test-case workbook (ODS)
into schema-validated canonical JSON. See ADR-034. The converted JSON ships in
`compliance/data/gsa/` (see its `PROVENANCE.md`); the ODS files are never committed.

```bash
# convert (writes <workspace>/data/catalog/*.canonical.json; add --bundled to refresh the shipped data)
python compliance/scripts/convert_gsa_workbooks.py \
  --tailoring-workbook CTW.ods --test-case-workbook TESTS.ods

# prove committed JSON still matches the workbooks (exit 1 on drift)
python compliance/scripts/convert_gsa_workbooks.py \
  --tailoring-workbook CTW.ods --test-case-workbook TESTS.ods --check

# structural check only; no ODS needed
python compliance/scripts/convert_gsa_workbooks.py --validate data/catalog/*.canonical.json
```

## GSA A&A assessment engine

Builds a system's Assessment and Attestation from a neutral system input plus the bundled
canonical workbook JSON (override with `--tailoring` and `--test-cases`). See ADR-035 and `skills/gsa-aa-assessment/SKILL.md`. The core never reads a
system's own files; the system repository emits the input.

```bash
python compliance/scripts/build_gsa_assessment.py --system-input build/assessment/<slug>-assessment-input.json
python compliance/scripts/build_gsa_assessment.py --system-input ... --check      # exit 1 on drift
python compliance/scripts/build_poam_issues.py --assessment ... --work-items ...   # dry run
```
