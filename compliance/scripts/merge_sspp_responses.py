#!/usr/bin/env python3
"""Fold authored or composed response prose into DOCX-parsed countermeasures.

Reads:
- ``data/responses/<system>.yaml`` — drafted or composed responses keyed by
  ``control_id`` + ``part``, each entry providing an ``implementation`` string.
- ``build/sspp/<stem>/countermeasures.yaml`` — parsed SSPP responses.

For each countermeasure whose ``(control_id, part)`` matches a drafted entry,
the implementation prose is appended after the existing scaffold description,
``default_status`` is flipped ``gap`` -> ``planned``, and ``is_placeholder``
is set to ``false``. Drafts that have no matching DOCX row are injected as new
countermeasure entries.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]


def _norm(cid: str) -> str:
    return canonical_control_id(cid) if cid and cid.strip() else ""


def _part_key(part) -> str:
    if part is None:
        return "-"
    return str(part).strip().lower()


def _slug(cid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", cid.lower()).strip("-")


def _load_drafts(
    path: Path, *, legacy_implementation_keys: tuple[str, ...] = ()
) -> dict[tuple[str, str], str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    drafts: dict[tuple[str, str], str] = {}
    for cid, parts in (data.get("responses") or {}).items():
        for entry in parts or []:
            key = (_norm(cid), _part_key(entry.get("part")))
            prose = entry.get("implementation")
            if prose is None:
                for alt_key in legacy_implementation_keys:
                    if alt_key in entry:
                        prose = entry.get(alt_key)
                        break
            drafts[key] = (prose or "").strip()
    return drafts


def merge(
    cm_path: Path,
    drafts_path: Path,
    out_path: Path | None = None,
    *,
    legacy_implementation_keys: tuple[str, ...] = (),
) -> dict:
    doc = yaml.safe_load(cm_path.read_text(encoding="utf-8"))
    drafts = _load_drafts(
        drafts_path, legacy_implementation_keys=legacy_implementation_keys
    )

    hits: list[str] = []
    injected: list[str] = []
    seen: set[tuple[str, str]] = set()
    for cm in doc.get("countermeasures", []) or []:
        cid = _norm(cm.get("control_id", ""))
        cm["control_id"] = cid
        key = (cid, _part_key(cm.get("part")))
        if key in seen:
            raise ValueError(f"duplicate countermeasure identity: {cid}::{key[1]}")
        seen.add(key)
        prose = drafts.pop(key, None)
        if prose is None:
            continue
        existing = (cm.get("description") or "").rstrip()
        if existing and not existing.endswith((".", ":", ";")):
            existing += "."
        merged = (
            f"{existing}\n\nImplementation (composed): {prose}"
            if existing
            else f"Implementation (composed): {prose}"
        )
        cm["description"] = merged
        cm["is_placeholder"] = False
        cm["response_source"] = str(drafts_path)
        if cm.get("default_status") == "gap":
            cm["default_status"] = "planned"
        hits.append(f"{cm.get('control_id')}::{cm.get('part') or '-'}")

    for (cid_key, part_key), prose in list(drafts.items()):
        entry = {
            "id": f"cm-{_slug(cid_key)}",
            "name": f"{cid_key} response",
            "description": f"Implementation (composed): {prose}",
            "part": None if part_key == "-" else part_key,
            "default_status": "planned",
            "is_placeholder": False,
            "is_inheritance_assertion": False,
            "control_id": cid_key,
            "implementation_status": "planned",
            "origination": ["system_specific"],
            "responsible_role": None,
            "providing_service": "Tenant Application",
            "inheritance_type": "System-Specific",
            "control_name": "",
            "response_source": str(drafts_path),
            "injected_by_merger": True,
        }
        doc.setdefault("countermeasures", []).append(entry)
        injected.append(f"{cid_key}::{part_key}")

    target = out_path or cm_path
    target.write_text(
        yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True),
        encoding="utf-8",
    )
    return {"matched": hits, "injected": injected, "out": str(target)}


def main(
    argv: list[str] | None = None,
    *,
    legacy_implementation_keys: tuple[str, ...] = (),
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "countermeasures", type=Path, help="Path to countermeasures.yaml to merge into"
    )
    ap.add_argument(
        "--drafts",
        type=Path,
        required=True,
        help="Path to composed/drafted responses YAML (e.g. data/responses/<system>.yaml)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output path (defaults to in-place)",
    )
    args = ap.parse_args(argv)

    if not args.countermeasures.exists():
        print(f"error: {args.countermeasures} not found", file=sys.stderr)
        return 2
    if not args.drafts.exists():
        print(f"error: {args.drafts} not found", file=sys.stderr)
        return 2

    result = merge(
        args.countermeasures,
        args.drafts,
        args.out,
        legacy_implementation_keys=legacy_implementation_keys,
    )
    print(f"Wrote {result['out']}")
    print(f"  merged {len(result['matched'])} response parts")
    if result["injected"]:
        print(f"  injected {len(result['injected'])} new countermeasures:")
        for k in result["injected"]:
            print(f"    + {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
