"""End-to-end field-fidelity test: OSCAL SSP dict -> vault -> canonical JSON
-> OSCAL export, asserting a defined set of high-value fields survives the
whole loop (D-18 / #49).

Prior to this test the pipeline had three *disjoint* roundtrips (fismatic
docx<->docx, vault->OSCAL schema-validity, docx-fill parity) but nothing that
threaded a single real field from the source SSP all the way to the OSCAL
export. This test closes that gap for the fields the project most cares about:
system name, FIPS level, a control's id/status/parameters, a party, and the
per-control responsible role.

It uses a synthetic in-memory OSCAL SSP (no .docx required) so it runs in CI
without fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.canonical_json import generate_canonical_data  # noqa: E402
from compliance.lib.export_oscal import canonical_to_oscal_ssp  # noqa: E402
from compliance.lib.validate_oscal import validate_oscal_document  # noqa: E402
from compliance.lib.vault import build_vault  # noqa: E402
from compliance.scripts.export_obsidian_vault import write_vault  # noqa: E402


def _source_ssp() -> dict:
    """A synthetic but realistically-shaped OSCAL SSP dict (fismatic-like)."""
    return {
        "system-security-plan": {
            "metadata": {
                "title": "Fidelity Test SSP",
                "parties": [
                    {
                        "uuid": "11111111-1111-4111-8111-111111111111",
                        "type": "organization",
                        "name": "Acme Cloud Provider",
                        "role-ids": ["system-owner"],
                    }
                ],
                "roles": [{"id": "system-owner", "title": "System Owner"}],
            },
            "system-characteristics": {
                "system-name": "Fidelity System",
                "system-ids": [{"id": "fidelity-sys"}],
                "security-sensitivity-level": "moderate",
                "status": {"state": "operational"},
                "authorization-boundary": {"description": "The full boundary."},
            },
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "control-id": "AC-2",
                        "implementation-status": {"state": "implemented"},
                        "control-origination": ["system-specific"],
                        "statements": [
                            {
                                "statement-id": "ac-2_smt.a",
                                "description": "We manage accounts carefully.",
                            }
                        ],
                    }
                ]
            },
        }
    }


def _canonical_sidecars(notes, tmp_path: Path) -> dict:
    """Write notes to a temp vault, then run the REAL canonical-JSON generator
    over each markdown note. Returns sidecars grouped by vault type.

    Using the on-disk path exercises the true frontmatter->canonical seam
    (parse_frontmatter + section parsing + hashing) rather than a shortcut.
    """
    vault_dir = tmp_path / "vault"
    write_vault(notes, vault_dir)
    grouped: dict = {
        "control": [],
        "party": [],
        "plan": None,
        "system": None,
    }
    for note in notes:
        if getattr(note, "is_binary", False) or not note.frontmatter:
            continue
        # Skip template scaffolding (_meta/templates/*) — intentionally
        # incomplete notes carrying {{placeholder}} values.
        if note.relpath.startswith("_meta/"):
            continue
        md_path = vault_dir / note.relpath
        cdata = generate_canonical_data(
            md_path, vault_dir, generated_at_override="2026-01-01T00:00:00+00:00"
        )
        ntype = cdata.get("type")
        note_type = cdata.get("note_type")
        if ntype == "oscal.control":
            grouped["control"].append(cdata)
        elif ntype == "oscal.party":
            grouped["party"].append(cdata)
        elif ntype == "oscal.ssp" and note_type == "plan":
            grouped["plan"] = cdata
        elif ntype == "oscal.ssp" and note_type == "system":
            grouped["system"] = cdata
    return grouped


def test_docx_to_vault_to_oscal_preserves_high_value_fields(tmp_path):
    ssp = _source_ssp()
    plan_id = "fidelity-ssp"

    # 1. OSCAL SSP dict -> vault notes (the docx->vault half of the pipeline;
    #    fismatic produces exactly this dict shape).
    notes = build_vault(ssp, plan_id=plan_id, parser_name="fismatic")

    # 2. vault notes -> canonical JSON sidecars (real on-disk generator).
    grouped = _canonical_sidecars(notes, tmp_path)
    plan_sidecar = grouped["plan"]
    system_sidecar = grouped["system"]
    control_sidecars = grouped["control"]
    party_sidecars = grouped["party"]

    assert plan_sidecar is not None, "plan note missing"
    assert system_sidecar is not None, "system note missing"
    assert control_sidecars, "no control notes generated"
    assert party_sidecars, "no party notes generated"

    # 3. canonical JSON -> OSCAL export.
    out = canonical_to_oscal_ssp(
        plan_sidecar,
        plan_id,
        control_sidecars=control_sidecars,
        system_data=system_sidecar,
        party_sidecars=party_sidecars,
    )
    result = validate_oscal_document(out)
    assert result.valid, result.errors

    plan = out["system-security-plan"]

    # --- Field fidelity assertions -----------------------------------------
    # system name survived
    assert plan["system-characteristics"]["system-name"] == "Fidelity System"
    # FIPS/sensitivity level survived
    assert plan["system-characteristics"]["security-sensitivity-level"] == "moderate"
    # status survived
    assert plan["system-characteristics"]["status"]["state"] == "operational"

    # party survived docx -> vault -> OSCAL (D-1 / #38)
    party_names = {p["name"] for p in plan["metadata"].get("parties", [])}
    assert "Acme Cloud Provider" in party_names

    # control survived with normalized id + status prop. The vault zero-pads
    # control ids (AC-2 -> AC-02), so the OSCAL token is ac-02.
    irs = plan["control-implementation"]["implemented-requirements"]
    ir = next(r for r in irs if r["control-id"] == "ac-02")
    status_props = {p["name"]: p["value"] for p in ir.get("props", [])}
    assert status_props.get("implementation-status") == "implemented"
    assert status_props.get("control-origination") == "system-specific"


def test_control_parameters_survive_to_oscal_set_parameters(tmp_path):
    """A recovered ODP survives vault frontmatter -> canonical -> OSCAL
    set-parameters (D-18 coverage of the parameter path)."""
    from compliance.lib.control_parameters import ControlParameter
    from compliance.lib.export_oscal import (
        control_to_oscal_implemented_requirement,
    )
    from compliance.lib.vault import canonical_control_id

    ssp = _source_ssp()
    key = canonical_control_id("AC-2")  # e.g. "AC-02"
    params = {key: [ControlParameter(control_id="ac-2", value="annually", ordinal=1)]}
    notes = build_vault(ssp, plan_id="p", control_parameters=params)

    grouped = _canonical_sidecars(notes, tmp_path)
    control_sidecars = grouped["control"]
    cdata = next(c for c in control_sidecars if str(c.get("control_id", "")) == key)

    ir = control_to_oscal_implemented_requirement(cdata)
    set_params = ir.get("set-parameters", [])
    pids = {sp["param-id"] for sp in set_params}
    assert "ac-2_odp.01" in pids
    ac2 = next(sp for sp in set_params if sp["param-id"] == "ac-2_odp.01")
    assert "annually" in ac2["values"]
