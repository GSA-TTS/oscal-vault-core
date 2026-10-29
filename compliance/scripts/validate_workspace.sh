#!/usr/bin/env bash
# Run the core's pipeline gates against a system repository (the current directory).
#
#   bash <core>/compliance/scripts/validate_workspace.sh
#
# Expects ./examples/vault/<system>/ and, optionally, ./scripts/{registry.yaml,
# vault_debt_baseline.json,progressive_coverage_baseline.json,table_coverage_baseline.json,
# lineage_operator_attestations_v1.json}. A gate whose baseline file is absent is reported as
# SKIPPED rather than passing silently. Exit code is non-zero if any gate that ran failed.
set -uo pipefail

CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$CORE/compliance/scripts"
PY="${PYTHON:-python3}"
fail=0

gate() {
  local name="$1"; shift
  if "$@" >/tmp/gate.$$ 2>&1; then
    echo "PASS    $name"
  else
    echo "FAIL    $name"; sed 's/^/        /' /tmp/gate.$$ | tail -15; fail=1
  fi
}
needs() { # needs <file> <name> <cmd...>
  local file="$1" name="$2"; shift 2
  if [ -f "$file" ]; then gate "$name" "$@"; else echo "SKIPPED $name (missing $file)"; fi
}

[ -d examples/vault ] || { echo "ERROR: ./examples/vault not found (run from the system repository)" >&2; exit 2; }

gate  "canonical sidecar drift"              "$PY" "$S/generate_canonical_json.py" --check
needs scripts/vault_debt_baseline.json "vault validation (no new debt)" \
      "$PY" "$S/validate_vault.py" --no-new-debt --baseline-file scripts/vault_debt_baseline.json
gate  "CUI/provenance sidecars"              "$PY" "$S/validate_cui_contracts.py"
gate  "OSCAL mapping governance"             "$PY" "$S/validate_oscal_mappings.py"
gate  "OSCAL export + schema validation"     bash -c "$PY '$S/generate_canonical_json.py' >/dev/null && $PY '$S/export_oscal.py' --validate"
gate  "OSCAL semantic + parity"              "$PY" "$S/validate_oscal.py" --all-exports --semantic --parity
needs scripts/progressive_coverage_baseline.json "progressive coverage debt" \
      "$PY" "$S/progressive_coverage.py"
needs scripts/table_coverage_baseline.json "table coverage debt" \
      "$PY" "$S/table_coverage.py"
needs scripts/lineage_operator_attestations_v1.json "lineage attestations" \
      "$PY" "$S/validate_lineage_attestations.py"

rm -f /tmp/gate.$$
[ "$fail" -eq 0 ] && echo "All gates that ran passed." || echo "One or more gates FAILED."
exit "$fail"
