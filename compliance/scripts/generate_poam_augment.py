#!/usr/bin/env python3
"""Annotate existing CycloneDX controls with OSCAL POA&M metadata.

Patches existing controls instead of creating new threats or countermeasures.
A POA&M item is modeled as planned metadata attached to the countermeasure for
the matching NIST control.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PoamPatch:
    control_id: str
    control_name: str
    poam_id: str
    scheduled_completion: str
    title: str
    source: str = "oscal-poam"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prop_map(item: dict[str, Any]) -> dict[str, str]:
    return {
        str(prop.get("name")): str(prop.get("value"))
        for prop in item.get("props", [])
        if prop.get("name") and prop.get("value") is not None
    }


def _control_id_from_item(item: dict[str, Any]) -> str:
    props = _prop_map(item)
    for key in (
        "nist:control-id",
        "control-id",
        "control_id",
        "control-id-ref",
        "control",
    ):
        value = item.get(key) or props.get(key)
        if value:
            return str(value).upper()
    related = item.get("related-observations") or item.get("related-risks") or []
    for relation in related:
        value = relation.get("control-id") or relation.get("control-id-ref")
        if value:
            return str(value).upper()
    return ""


def _poam_id_from_item(item: dict[str, Any], index: int) -> str:
    props = _prop_map(item)
    for key in ("poam:id", "id", "uuid", "poam-id"):
        value = item.get(key) or props.get(key)
        if value:
            return str(value)
    return f"POAM-{index + 1}"


def _scheduled_completion_from_item(item: dict[str, Any]) -> str:
    props = _prop_map(item)
    for key in (
        "scheduled-completion-date",
        "scheduled_completion",
        "scheduled-completion",
        "due-date",
        "poam:due-date",
    ):
        value = item.get(key) or props.get(key)
        if value:
            return str(value)
    milestones = item.get("milestones") or []
    dates = [
        m.get("scheduled-completion-date")
        for m in milestones
        if m.get("scheduled-completion-date")
    ]
    return str(max(dates)) if dates else ""


def extract_poam_patches(poam: dict[str, Any]) -> list[PoamPatch]:
    root = poam.get("plan-of-action-and-milestones", poam)
    items = root.get("poam-items") or root.get("risks") or []
    patches: list[PoamPatch] = []
    for idx, item in enumerate(items):
        control_id = _control_id_from_item(item)
        if not control_id:
            continue
        patches.append(
            PoamPatch(
                control_id=control_id,
                control_name=str(
                    item.get("title") or item.get("description") or control_id
                ),
                poam_id=_poam_id_from_item(item, idx),
                scheduled_completion=_scheduled_completion_from_item(item),
                title=str(item.get("title") or ""),
            )
        )
    return patches


def _control_properties(control: dict[str, Any]) -> dict[str, str]:
    return {
        str(prop.get("name")): str(prop.get("value"))
        for prop in control.get("properties", [])
        if prop.get("name") and prop.get("value") is not None
    }


def _control_id(control: dict[str, Any]) -> str:
    props = _control_properties(control)
    return str(props.get("ctrl:id") or props.get("nist:control-id") or "").upper()


def _upsert_property(control: dict[str, Any], name: str, value: str) -> bool:
    props = control.setdefault("properties", [])
    for prop in props:
        if prop.get("name") == name:
            if prop.get("value") == value:
                return False
            prop["value"] = value
            return True
    props.append({"name": name, "value": value})
    return True


def apply_poam_patches(
    cdx: dict[str, Any], patches: list[PoamPatch]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    patched = copy.deepcopy(cdx)
    controls = patched.get("controls", [])
    by_control_id = {
        _control_id(control): control for control in controls if _control_id(control)
    }
    summary: list[dict[str, str]] = []
    for patch in patches:
        control = by_control_id.get(patch.control_id)
        if control is None:
            summary.append(
                {
                    "control_id": patch.control_id,
                    "poam_id": patch.poam_id,
                    "status": "unmatched",
                }
            )
            continue
        changed = False
        changed |= _upsert_property(control, "poam:id", patch.poam_id)
        changed |= _upsert_property(control, "poam:source", patch.source)
        if patch.scheduled_completion:
            changed |= _upsert_property(
                control, "poam:due-date", patch.scheduled_completion
            )
        if patch.title:
            changed |= _upsert_property(control, "poam:title", patch.title)
        summary.append(
            {
                "control_id": patch.control_id,
                "poam_id": patch.poam_id,
                "status": "patched" if changed else "unchanged",
            }
        )
    return patched, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poam", required=True, help="OSCAL POA&M JSON input")
    parser.add_argument("--cdx", required=True, help="Existing CDX JSON input")
    parser.add_argument("--out", help="Write patched CDX JSON to this path")
    parser.add_argument("--summary", help="Write JSON patch summary to this path")
    args = parser.parse_args(argv)

    poam = _load_json(Path(args.poam))
    cdx = _load_json(Path(args.cdx))
    patches = extract_poam_patches(poam)
    patched, summary = apply_poam_patches(cdx, patches)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(patched, indent=2) + "\n", encoding="utf-8")
    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if not args.out and not args.summary:
        print(
            json.dumps(
                {"patches": [asdict(p) for p in patches], "summary": summary}, indent=2
            )
        )

    unmatched = [row for row in summary if row["status"] == "unmatched"]
    if unmatched:
        for row in unmatched:
            print(
                f"WARN: POA&M item {row['poam_id']} did not match control {row['control_id']}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
