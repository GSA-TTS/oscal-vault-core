#!/usr/bin/env python3
"""Extract control responses from a FedRAMP SSPP .docx into structured YAML.

Uses the vendored ``fismatic`` DocxParser (with the FedRAMP SSP data contract)
as the primary parser and emits four artifacts under ``--out-dir``:

* ``matrix.yaml``         - rows of the master control matrix table
* ``controls.yaml``       - per-control responses parsed by fismatic
* ``countermeasures.yaml``- one countermeasure entry per response part
* ``summary.yaml``        - status, origination, and placeholder counts
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if "docx" not in sys.modules:
    _saved_path = sys.path[:]
    sys.path = [p for p in sys.path if p != _SCRIPT_DIR]
    try:
        import docx  # noqa: F401
    finally:
        sys.path = _saved_path

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]

MATRIX_KEYS = [
    "control_id",
    "control_name",
    "moderate_baseline",
    "inheritance_type",
    "providing_service",
    "applicability",
]

INHERITANCE_TO_ORIGINATION = {
    "common": "inherited",
    "fully inherited": "inherited",
    "inherited": "inherited",
    "hybrid": "hybrid",
    "common/hybrid": "hybrid",
    "system-specific": "system_specific",
    "system specific": "system_specific",
}

DEFAULT_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "tenant application implementation",
    "system implementation",
)

DEFAULT_INHERITANCE_ASSERTIONS: tuple[str, ...] = (
    "see the provider tenant crm",
    "inherited from",
)


def _default_vault_root() -> Path:
    if (_ROOT / "fismatic" / "src" / "phase_1").is_dir():
        return _ROOT
    repo_core = Path(__file__).resolve().parents[3] / "vendor" / "oscal-vault-core"
    if (repo_core / "fismatic" / "src" / "phase_1").is_dir():
        return repo_core
    return _ROOT


def resolve_vault_root() -> Path:
    return Path(
        os.environ.get(
            "VAULT_ROOT",
            os.environ.get("OSCAL_VAULT_ROOT", str(_default_vault_root())),
        )
    ).resolve()


def cell_text(cell) -> str:
    return "\n".join(p.text for p in cell.paragraphs).strip()


def parse_master_matrix(doc) -> list[dict]:
    for tbl in doc.tables:
        if not tbl.rows:
            continue
        hdr = " ".join(cell_text(c) for c in tbl.rows[0].cells).lower()
        if "control id" in hdr and "moderate baseline" in hdr:
            rows: list[dict] = []
            for row in tbl.rows[1:]:
                vals = [cell_text(c) for c in row.cells]
                if not vals or not vals[0] or vals[0].lower() == "control id":
                    continue
                padded = vals + [""] * (len(MATRIX_KEYS) - len(vals))
                rows.append(dict(zip(MATRIX_KEYS, padded, strict=False)))
            return rows
    return []


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text or "").replace("\r", "").strip()


def _slug(cid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", cid.lower()).strip("-")


def _strip_scaffold(
    prose: str,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
) -> str:
    lo = prose.lower()
    for marker in placeholder_markers:
        lo = lo.replace(marker, " ")
    return re.sub(r"[\s:.,;\-\u2013\u2014]+", "", lo)


def is_placeholder(
    prose: str,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
) -> bool:
    if not prose:
        return True
    return not _strip_scaffold(prose, placeholder_markers=placeholder_markers)


def is_inheritance_assertion(
    prose: str,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    inheritance_assertions: tuple[str, ...] = DEFAULT_INHERITANCE_ASSERTIONS,
) -> bool:
    if not prose:
        return False
    lo = prose.lower()
    if not any(a in lo for a in inheritance_assertions):
        return False
    for a in inheritance_assertions:
        lo = lo.replace(a, " ")
    for marker in placeholder_markers:
        lo = lo.replace(marker, " ")
    return not re.sub(r"[\s:.,;\-\u2013\u2014]+", "", lo)


def _matrix_key(cid: str) -> str:
    return canonical_control_id(cid)


def _parent_key(cid: str) -> str | None:
    key = _matrix_key(cid)
    parent = re.sub(r"\([^)]+\)$", "", key)
    return parent if parent != key else None


def default_status(
    prose: str,
    docx_status: str | None,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    inheritance_assertions: tuple[str, ...] = DEFAULT_INHERITANCE_ASSERTIONS,
) -> str:
    if is_placeholder(prose, placeholder_markers=placeholder_markers):
        return "gap"
    if is_inheritance_assertion(
        prose,
        placeholder_markers=placeholder_markers,
        inheritance_assertions=inheritance_assertions,
    ):
        return "inherited"
    if docx_status == "implemented":
        return "implemented"
    return "planned"


def to_countermeasures(
    cid: str,
    control: dict,
    matrix_row: dict | None,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    inheritance_assertions: tuple[str, ...] = DEFAULT_INHERITANCE_ASSERTIONS,
) -> list[dict]:
    items: list[dict] = []
    parts = control.get("implementation") or {}
    status = control.get("implementation_status")
    origination = list(control.get("origination") or [])

    inheritance = (matrix_row or {}).get("inheritance_type", "").strip().lower()
    if not origination and inheritance in INHERITANCE_TO_ORIGINATION:
        origination = [INHERITANCE_TO_ORIGINATION[inheritance]]

    providing = (matrix_row or {}).get("providing_service", "")
    base = {
        "control_id": cid,
        "implementation_status": status,
        "origination": origination,
        "responsible_role": control.get("responsible_role"),
        "providing_service": providing,
        "inheritance_type": (matrix_row or {}).get("inheritance_type", ""),
        "control_name": (matrix_row or {}).get("control_name", ""),
    }
    if parts:
        for part_key, prose in sorted(parts.items()):
            prose_clean = clean(prose)
            if not prose_clean:
                continue
            items.append(
                {
                    "id": f"cm-{_slug(cid)}-{part_key.lower()}",
                    "name": f"{cid} Part {part_key} response",
                    "description": prose_clean,
                    "part": part_key,
                    "default_status": default_status(
                        prose_clean,
                        status,
                        placeholder_markers=placeholder_markers,
                        inheritance_assertions=inheritance_assertions,
                    ),
                    "is_placeholder": is_placeholder(
                        prose_clean, placeholder_markers=placeholder_markers
                    ),
                    "is_inheritance_assertion": is_inheritance_assertion(
                        prose_clean,
                        placeholder_markers=placeholder_markers,
                        inheritance_assertions=inheritance_assertions,
                    ),
                    **base,
                }
            )
    else:
        items.append(
            {
                "id": f"cm-{_slug(cid)}",
                "name": f"{cid} response",
                "description": "",
                "part": None,
                "default_status": "gap",
                "is_placeholder": True,
                "is_inheritance_assertion": False,
                **base,
            }
        )
    return items


def _summarise(controls: dict, countermeasures: list[dict]) -> dict:
    status_counts = Counter(
        v.get("implementation_status") or "<none>" for v in controls.values()
    )
    origination_counts: Counter[str] = Counter()
    for cm in countermeasures:
        for origin in cm.get("origination") or ["<none>"]:
            origination_counts[origin] += 1
    default_counts = Counter(cm.get("default_status") for cm in countermeasures)
    placeholder_ids = sorted(
        {cm["control_id"] for cm in countermeasures if cm.get("is_placeholder")}
    )
    authored_ids = sorted(
        {cm["control_id"] for cm in countermeasures if not cm.get("is_placeholder")}
    )
    return {
        "controls_total": len(controls),
        "countermeasures_total": len(countermeasures),
        "implementation_status_counts": dict(status_counts),
        "default_status_counts": dict(default_counts),
        "origination_counts": dict(origination_counts),
        "controls_with_authored_prose": authored_ids,
        "controls_with_only_placeholder_prose": placeholder_ids,
    }


def extract(
    docx_path: Path,
    out_dir: Path,
    contract: Path | None,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    inheritance_assertions: tuple[str, ...] = DEFAULT_INHERITANCE_ASSERTIONS,
) -> tuple[int, int, int]:
    from docx import Document

    vault_root = resolve_vault_root()
    fismatic_src = vault_root / "fismatic" / "src" / "phase_1"
    if not fismatic_src.is_dir():
        raise FileNotFoundError(
            f"fismatic source not found at {fismatic_src}. Set VAULT_ROOT to your oscal-vault-core checkout."
        )
    if str(fismatic_src) not in sys.path:
        sys.path.insert(0, str(fismatic_src))
    from fismatic.docx_parser import DocxParser

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(str(docx_path))
    matrix = parse_master_matrix(doc)
    matrix_by_id: dict[str, dict] = {}
    for row in matrix:
        if not row.get("control_id"):
            continue
        key = _matrix_key(row["control_id"])
        if key in matrix_by_id:
            raise ValueError(f"duplicate canonical control ID in master matrix: {key}")
        matrix_by_id[key] = row

    parser = DocxParser(
        str(docx_path), contract_path=str(contract) if contract else None
    )
    with contextlib.redirect_stdout(io.StringIO()):
        control_set = parser.get_control_set()

    controls: dict[str, dict] = {}
    countermeasures: list[dict] = []
    for ctrl in control_set._controls:
        cid = canonical_control_id(ctrl.normalized_name())
        status = ctrl.implementation_status
        if isinstance(status, dict):
            status = status.get("state")
        payload = {
            "responsible_role": ctrl.responsible_role or None,
            "implementation_status": status,
            "origination": ctrl.origination or [],
            "implementation": ctrl.implementation or {},
        }
        if cid in controls:
            raise ValueError(
                f"duplicate canonical control ID in parsed controls: {cid}"
            )
        controls[cid] = payload
        row = matrix_by_id.get(_matrix_key(cid))
        if row is None:
            parent = _parent_key(cid)
            if parent:
                row = matrix_by_id.get(parent)
        countermeasures.extend(
            to_countermeasures(
                cid,
                payload,
                row,
                placeholder_markers=placeholder_markers,
                inheritance_assertions=inheritance_assertions,
            )
        )

    (out_dir / "matrix.yaml").write_text(
        yaml.safe_dump({"source": str(docx_path), "controls": matrix}, sort_keys=False),
        encoding="utf-8",
    )
    (out_dir / "controls.yaml").write_text(
        yaml.safe_dump(
            {"source": str(docx_path), "responses": controls},
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    (out_dir / "countermeasures.yaml").write_text(
        yaml.safe_dump(
            {"source": str(docx_path), "countermeasures": countermeasures},
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )

    summary = _summarise(controls, countermeasures)
    (out_dir / "summary.yaml").write_text(
        yaml.safe_dump(summary, sort_keys=False), encoding="utf-8"
    )
    return len(matrix), len(controls), len(countermeasures)


def main(
    argv: list[str] | None = None,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    inheritance_assertions: tuple[str, ...] = DEFAULT_INHERITANCE_ASSERTIONS,
) -> int:
    vault_root = resolve_vault_root()
    default_contract = (
        vault_root / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("docx", type=Path, help="Path to the SSPP .docx file")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: build/sspp/<stem>/)",
    )
    ap.add_argument(
        "--contract",
        type=Path,
        default=default_contract,
        help=f"Fismatic data contract YAML (default: {default_contract})",
    )
    args = ap.parse_args(argv)

    out_dir = args.out_dir or Path("build/sspp") / args.docx.stem
    try:
        m, c, cm = extract(
            args.docx,
            out_dir,
            args.contract,
            placeholder_markers=placeholder_markers,
            inheritance_assertions=inheritance_assertions,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    print(f"Wrote {out_dir}/matrix.yaml           ({m} rows)")
    print(f"Wrote {out_dir}/controls.yaml         ({c} controls)")
    print(f"Wrote {out_dir}/countermeasures.yaml  ({cm} responses)")
    print(f"Wrote {out_dir}/summary.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
