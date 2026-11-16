"""Conservative, hash-only OOXML comparison; never equate rewrites with losslessness."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from compliance.lib.docx_package import inspect_package, strict_findings

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
VOLATILE_ATTRIBUTES = {
    f"{{{W}}}rsidR",
    f"{{{W}}}rsidRPr",
    f"{{{W}}}rsidDel",
    f"{{{W}}}rsidP",
    f"{{{W}}}rsidRDefault",
    "{http://schemas.microsoft.com/office/word/2010/wordml}paraId",
    "{http://schemas.microsoft.com/office/word/2010/wordml}textId",
}
VOLATILE_ELEMENTS = {
    "{http://purl.org/dc/terms/}created",
    "{http://purl.org/dc/terms/}modified",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}revision",
    f"{{{W}}}rsids",
}
CATEGORIES = (
    "volatile_serialization",
    "template_boilerplate",
    "catalog_reference",
    "structured_preserved",
    "opaque_preserved",
    "known_baselined_gap",
    "new_loss",
    "ambiguous",
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _signature(element: ET.Element, relationships: dict[str, str]) -> object:
    """Use expanded names, retaining order, visible whitespace and formatting."""
    attrs = {
        key: relationships.get(value, value) if key.startswith(f"{{{R}}}") else value
        for key, value in element.attrib.items()
        if key not in VOLATILE_ATTRIBUTES
    }
    text = element.text or ""
    # XML indentation is not displayed. Space inside w:t is content, however.
    if (
        element.tag.startswith(f"{{{W}}}")
        and not text.strip()
        and element.tag not in {f"{{{W}}}t", f"{{{W}}}delText", f"{{{W}}}instrText"}
    ):
        text = ""
    return [
        element.tag,
        sorted(attrs.items()),
        text,
        element.tail if (element.tail or "").strip() else "",
        [
            _signature(child, relationships)
            for child in element
            if child.tag not in VOLATILE_ELEMENTS
        ],
    ]


def _hash_signature(value: object) -> str:
    return digest(json.dumps(value, sort_keys=True).encode())


def normalized_package(path: Path) -> dict:
    """Apply the existing ZIP/XML limits before reading any package payload."""
    inventory = inspect_package(path)
    rel_maps: dict[str, dict[str, str]] = {}
    for rel in inventory["relationships"]:
        identity = json.dumps(
            [rel["type"], rel["target_mode"], rel["resolved_target"] or rel["target"]]
        )
        rel_maps.setdefault(rel["source_part"], {})[rel["id"]] = identity
    parts = {}
    with zipfile.ZipFile(path) as archive:
        for part in inventory["parts"]:
            name = part["path"]
            payload = archive.read(name)
            record = {"sha256": digest(payload), "invariants": {}}
            if name.endswith((".xml", ".rels")):
                root = ET.fromstring(payload)
                mapping = rel_maps.get(name, {})
                if name.endswith(".rels"):
                    signature = sorted(
                        tuple(
                            sorted((k, v) for k, v in child.attrib.items() if k != "Id")
                        )
                        for child in root
                    )
                elif name == "[Content_Types].xml":
                    signature = sorted(
                        (child.tag, tuple(sorted(child.attrib.items())))
                        for child in root
                    )
                else:
                    signature = _signature(root, mapping)
                record["normalized_sha256"] = _hash_signature(signature)
                for label, tag in (
                    ("sections", "sectPr"),
                    ("table_properties", "tblPr"),
                    ("table_grids", "tblGrid"),
                    ("cell_properties", "tcPr"),
                    ("content_controls", "sdtPr"),
                    ("drawings", "drawing"),
                    ("numbering_mappings", "numPr"),
                ):
                    record["invariants"][label] = [
                        _hash_signature(_signature(node, mapping))
                        for node in root.iter(f"{{{W}}}{tag}")
                    ]
                record["invariants"]["table_shape"] = [
                    [
                        len(row.findall(f"{{{W}}}tc"))
                        for row in table.findall(f"{{{W}}}tr")
                    ]
                    for table in root.iter(f"{{{W}}}tbl")
                ]
            else:
                record["normalized_sha256"] = record["sha256"]
            parts[name] = record
    return {"inventory": inventory, "parts": parts}


def compare_packages(
    source: dict, filled: dict, decisions: list[dict] | None = None
) -> dict:
    """A reviewed decision must bind the exact locator AND both observed hashes."""
    decisions = decisions or []
    if not isinstance(decisions, list) or any(
        not isinstance(d, dict) for d in decisions
    ):
        raise ValueError("decisions must be a list of records")
    for decision in decisions:
        if decision.get("category") not in CATEGORIES or any(
            not isinstance(decision.get(key), str) or not decision[key].strip()
            for key in ("locator", "reviewer", "date", "reason", "id")
        ):
            raise ValueError("invalid integrity decision metadata")
        for key in ("before_sha256", "after_sha256"):
            value = decision.get(key)
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("invalid integrity decision digest")
    identities = [
        (d.get("locator"), d.get("before_sha256"), d.get("after_sha256"))
        for d in decisions
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate/conflicting decisions")
    deltas = []
    for name in sorted(source["parts"].keys() | filled["parts"].keys()):
        before = source["parts"].get(name, {})
        after = filled["parts"].get(name, {})
        if before == after:
            continue
        old = before.get("normalized_sha256")
        new = after.get("normalized_sha256")
        category = "volatile_serialization" if old == new else "new_loss"
        delta = {
            "locator": name,
            "before_sha256": old,
            "after_sha256": new,
            "category": category,
            "severity": "info" if category == "volatile_serialization" else "error",
            "invariant_changes": [
                key
                for key in sorted(
                    before.get("invariants", {}).keys()
                    | after.get("invariants", {}).keys()
                )
                if before.get("invariants", {}).get(key)
                != after.get("invariants", {}).get(key)
            ],
        }
        for decision in decisions:
            if all(
                decision.get(key) == delta[key]
                for key in ("locator", "before_sha256", "after_sha256")
            ):
                if (
                    decision.get("category") not in CATEGORIES
                    or not decision.get("reviewer")
                    or not decision.get("date")
                    or not decision.get("reason")
                    or not decision.get("id")
                ):
                    raise ValueError(
                        "integrity decision lacks category, stable ID or review evidence"
                    )
                if old != new and decision["category"] == "volatile_serialization":
                    raise ValueError(
                        "substantive delta cannot be classified as volatile serialization"
                    )
                delta.update(
                    {key: decision[key] for key in ("category", "id", "reason")}
                )
                delta["severity"] = (
                    "warning"
                    if delta["category"] == "known_baselined_gap"
                    else "error"
                    if delta["category"] in {"new_loss", "ambiguous"}
                    else "info"
                )
                break
        deltas.append(delta)
    findings = strict_findings(source["inventory"]) + strict_findings(
        filled["inventory"]
    )
    unresolved = [
        d
        for d in deltas
        if d["category"] in {"new_loss", "ambiguous", "known_baselined_gap"}
    ]
    return {
        "status": "FAIL" if findings or unresolved else "PASS",
        "findings": findings,
        "deltas": deltas,
    }
