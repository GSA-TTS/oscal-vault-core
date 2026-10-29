"""Convert a DFD canvas between the standard TM-BOM attachment form and the legacy inline form.

Threat-modeling tools that predate the TM-BOM importer read only ``type: precogly-dfd`` with
inline ``data``; newer ones read a base64 attachment with media type
``application/vnd.precogly.dfd+json``. These helpers convert between the two without touching
the canvas itself (layout is the consuming tool's concern).
"""

from __future__ import annotations

import base64
import copy
import json
from typing import Any

MEDIA_TYPE = "application/vnd.precogly.dfd+json"


def _ref(item: dict[str, Any]) -> str:
    return str(item.get("bom-ref") or item.get("bomRef") or "")


def canvas_visualization(
    canvas: dict[str, Any], *, name: str, ref: str, primary: bool = True
) -> dict[str, Any]:
    """Wrap a React Flow canvas in the standard TM-BOM visualization (base64 attachment)."""
    content = base64.b64encode(
        json.dumps(canvas, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {
        "bom-ref": f"visualization-{ref}",
        "type": {"type": "data-flow"},
        "name": name,
        "attachment": {
            "mediaType": MEDIA_TYPE,
            "encoding": "base64",
            "content": content,
        },
        "properties": [
            {"name": "precogly:diagram-type", "value": "level1"},
            {"name": "precogly:primary", "value": "true" if primary else "false"},
        ],
    }


def normalize_legacy_visualizations(
    document: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Rewrite legacy ``type: precogly-dfd`` visualizations into the standard attachment form.

    The importer ignores the legacy shape (inline ``data`` / ``diagramType``), so a model
    that still carries it imports with a near-empty canvas.  Returns a copy and the count.
    """
    result = copy.deepcopy(document)
    converted = 0
    for blueprint in result.get("blueprints", []) or []:
        if not isinstance(blueprint, dict):
            continue
        out = []
        for index, item in enumerate(blueprint.get("visualizations", []) or []):
            if (
                isinstance(item, dict)
                and item.get("type") == "precogly-dfd"
                and isinstance(item.get("data"), dict)
            ):
                out.append(
                    canvas_visualization(
                        item["data"],
                        name=str(item.get("name") or "Data Flow Diagram"),
                        ref=f"{_ref(blueprint) or 'bp'}-{index + 1}",
                        primary=converted == 0,
                    )
                )
                converted += 1
            else:
                out.append(item)
        if blueprint.get("visualizations") is not None:
            blueprint["visualizations"] = out
    return result, converted


def to_legacy_visualizations(document: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Rewrite standard attachment canvases into the legacy ``precogly-dfd`` form.

    Precogly generations before the TM-BOM importer (v0.3.0, the hosted tenant, the old
    ``.precogly`` clone) read only ``type: precogly-dfd`` with inline ``data``; they ignore the
    attachment form and import with no diagram. Returns a copy and the count converted.
    """
    result = copy.deepcopy(document)
    converted = 0
    for blueprint in result.get("blueprints", []) or []:
        if not isinstance(blueprint, dict):
            continue
        out = []
        for item in blueprint.get("visualizations", []) or []:
            attachment = item.get("attachment") if isinstance(item, dict) else None
            if (
                isinstance(attachment, dict)
                and attachment.get("mediaType") == MEDIA_TYPE
            ):
                props = {
                    p.get("name"): p.get("value")
                    for p in item.get("properties", []) or []
                    if isinstance(p, dict)
                }
                out.append(
                    {
                        "type": "precogly-dfd",
                        "name": item.get("name") or "Data Flow Diagram",
                        "diagramType": props.get("precogly:diagram-type") or "level1",
                        "data": json.loads(base64.b64decode(attachment["content"])),
                    }
                )
                converted += 1
            else:
                out.append(item)
        if blueprint.get("visualizations") is not None:
            blueprint["visualizations"] = out
    return result, converted
