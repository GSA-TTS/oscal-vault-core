# Control Decision-Support Pipeline

This pipeline helps reviewers evaluate whether SSP control responses are
complete enough for assessment. It does not authorize systems, approve control
status, or replace assessor judgment.

## Inputs

- OSCAL SSP implementation responses and evidence links.
- NIST Rev. 5 assessment objectives and Examine/Interview/Test objects from a
  normalized JSON reference snapshot. ODS/XLSX files are one-time acquisition
  inputs, not runtime dependencies or canonical SSP data.
- Source-located template instructions from the DOCX instruction ledger.
- Optional pinned MITRE D3FEND-to-NIST mapping conforming to the repository
  schema.

MITRE D3FEND is not inferred from CRM or generic countermeasure data. If a
pinned mapping is absent, packets explicitly report D3FEND as unavailable.

## Decision flow

1. Deterministically construct one cited packet per control.
2. Identify hard prerequisites before any model call:
   - response exists;
   - substantive narrative exists;
   - status is not merely planned/missing;
   - workbook objectives exist;
   - required Examine/Interview/Test evidence classes have current OSCAL links.
3. Produce a deterministic preflight grade and missing-prerequisite list.
4. Optionally send the minimized packet to a structured LLM grader.
5. Reject unknown citations or malformed grades.
6. Require human review for every grade.

Historical workbook observations and assessment results are context only. They
do not establish current effectiveness.

## Reference format

Guidance spreadsheets are acquisition inputs, not canonical SSP data. Convert
them once to `assessment_reference_schema_v1.json`. The snapshot records the
source filename/hash/importer, normalized control/objective IDs, prerequisite
objects, historical context labels, source-text hashes, redaction counts, and
namespaced extension objects.

JSON is the committed and auditable interchange format. TOON, minified JSON, or
other token-optimized forms may be generated transiently from selected packet
content for model invocation, but are not independent source-of-truth artifacts.

## Data handling

By default, historical observation prose is excluded because it may contain
internal URLs, screenshot names, assessment details, or contact information.
Packets retain presence flags and SHA-256 hashes. Explicit inclusion requires
`--include-historical-observations` and an approved processing environment.

The optional Bedrock backend uses Converse with adaptive retries, temperature
zero, explicit `maxTokens`, and forced structured tool output. AWS credentials
and model access are runtime prerequisites, not CI requirements.

## Grade semantics

Grades are recommendations only:

- `complete`
- `substantially_complete`
- `partial`
- `insufficient`
- `not_applicable_claim`
- `review_required`

Every output contains `human_review.required: true` and `status: pending`.
No grade mutates OSCAL implementation status.
