"""Render deterministic network and data-flow figures from a CycloneDX threat-model DFD.

The figures are drawn as SVG with no imaging library. ``rasterize_png`` converts the SVG
to PNG through ``resvg`` (the ``resvg-py`` wheel) so a DOCX can embed it; the same input
always yields the same bytes.

Inputs are the first blueprint of a CycloneDX TM-BOM (``zones``, ``assets``, ``flows``,
optional ``boundaries``) and an optional system architecture mapping that supplies asset
aliases, diagram ordering, flow overrides and ``diagram_layout.mode``. Two layouts exist:

``legacy`` (default)
    Trust-zone cards in a grid for data flow; zones on an ellipse for the network view.
``authorization-boundary``
    The declared authorization boundary is drawn as a central enclave. The boundary is
    ``diagram_layout.boundary`` when set, otherwise the first blueprint boundary that
    lists zones.

The module holds no system names; every label comes from the inputs.
"""

from __future__ import annotations

import math
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

COLORS = {
    "ink": "#172B4D",
    "muted": "#526581",
    "edge": "#B42318",
    "edge_internal": "#526581",
    "zone_fill": "#F4F7FA",
    "zone_border": "#7895B2",
    "actor": "#FFF1C7",
    "system": "#DCEEFF",
    "component": "#E4F5E9",
    "data-store": "#F2E4FF",
    "unassigned": "#FFF4F2",
    "white": "#FFFFFF",
    "boundary_frame": "#F7FAFD",
    "boundary_zone": "#E7F1FA",
    "boundary_border": "#285F8F",
}
FONT_FAMILY = "DejaVu Sans, Liberation Sans, Arial, sans-serif"
# Average advance width of DejaVu Sans relative to the font size, used to size label
# backgrounds without a font engine. Slightly generous so labels never overflow.
_CHAR_WIDTH = 0.6
_LINE_HEIGHT = 1.17
_ASCENT = 0.93
LAYOUTS = ("legacy", "authorization-boundary")
VIEWS = ("network", "data-flow")


@dataclass
class DiagramModel:
    """Normalized diagram inputs."""

    blueprint: dict[str, Any]
    zones: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    flows: list[dict[str, Any]]
    layout: str = "legacy"
    boundary: str | None = None


@dataclass
class _Canvas:
    width: int
    height: int
    items: list[str] = field(default_factory=list)

    def rect(self, box, *, radius, fill, outline, width, opacity=None):
        x0, y0, x1, y1 = box
        extra = f' fill-opacity="{opacity}"' if opacity is not None else ""
        self.items.append(
            f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(x1 - x0)}" '
            f'height="{_n(y1 - y0)}" rx="{_n(radius)}" fill="{fill}"{extra} '
            f'stroke="{outline}" stroke-width="{_n(width)}"/>'
        )

    def polyline(self, points, *, stroke, width):
        coords = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
        self.items.append(
            f'<polyline points="{coords}" fill="none" stroke="{stroke}" '
            f'stroke-width="{_n(width)}" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )

    def polygon(self, points, *, fill):
        coords = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
        self.items.append(f'<polygon points="{coords}" fill="{fill}"/>')

    def text(
        self,
        xy,
        value,
        *,
        size,
        bold=False,
        fill=COLORS["ink"],
        wrap=None,
        center=False,
        spacing=4,
    ):
        lines = _wrap(value, wrap)
        if not lines:
            return
        step = size * _LINE_HEIGHT + spacing
        x, y = xy
        if center:
            top = y - (step * len(lines) - spacing) / 2
        else:
            top = y
        weight = ' font-weight="bold"' if bold else ""
        anchor = ' text-anchor="middle"' if center else ""
        spans = []
        for index, line in enumerate(lines):
            baseline = top + index * step + size * _ASCENT
            spans.append(
                f'<tspan x="{_n(x)}" y="{_n(baseline)}">{escape(line)}</tspan>'
            )
        self.items.append(
            f'<text font-family="{FONT_FAMILY}" font-size="{size}"{weight} '
            f'fill="{fill}"{anchor}>{"".join(spans)}</text>'
        )

    def svg(self, title: str) -> str:
        body = "\n".join(self.items)
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img">\n'
            f"<title>{escape(title)}</title>\n"
            f'<rect width="{self.width}" height="{self.height}" '
            f'fill="{COLORS["white"]}"/>\n'
            f"{body}\n</svg>\n"
        )


def _n(value: float) -> str:
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:g}"


def _wrap(value: Any, width: int | None) -> list[str]:
    text = str(value)
    if width is None:
        return text.split("\n")
    return textwrap.wrap(text, width=width) or [""]


def _text_box(lines: list[str], size: int, spacing: int) -> tuple[float, float]:
    longest = max((len(line) for line in lines), default=0)
    width = longest * size * _CHAR_WIDTH
    height = len(lines) * size * _LINE_HEIGHT + (len(lines) - 1) * spacing
    return width, height


def build_model(
    dfd: dict[str, Any], architecture: dict[str, Any] | None = None
) -> DiagramModel:
    """Merge a CycloneDX DFD with optional architecture metadata."""
    architecture = architecture or {}
    blueprint = (dfd.get("blueprints") or [{}])[0]
    zones = blueprint.get("zones") or []
    assets = blueprint.get("assets") or []
    flows = blueprint.get("flows") or []

    asset_ref_by_alias = architecture.get("assets") or {}
    asset_order = {
        asset_ref_by_alias[alias]: index
        for index, alias in enumerate(
            (architecture.get("diagram_order") or {}).get("assets") or []
        )
        if alias in asset_ref_by_alias
    }
    assets = [
        {**asset, "_diagram_order": asset_order.get(asset.get("bom-ref"), 10_000)}
        for asset in assets
    ]
    candidates: dict[tuple[Any, Any], list[tuple[str, dict]]] = defaultdict(list)
    for alias, spec in (architecture.get("flows") or {}).items():
        source = asset_ref_by_alias.get(spec.get("source_asset"))
        destination = asset_ref_by_alias.get(spec.get("destination_asset"))
        if source and destination:
            candidates[(source, destination)].append((alias, spec))
    overrides = architecture.get("flow_overrides") or {}
    enriched = []
    for flow in flows:
        options = candidates.get((flow.get("source"), flow.get("destination")), [])
        alias, spec = next(
            (
                item
                for item in options
                if item[1].get("name", flow.get("name", "")) == flow.get("name", "")
            ),
            options[0] if options else (None, {}),
        )
        override = overrides.get(alias) or {}
        enriched.append(
            {
                **flow,
                "alias": alias,
                "display_name": flow.get("name")
                or spec.get("name")
                or alias
                or "Data flow",
                "protocols": override.get("protocols") or flow.get("protocols") or [],
                "auth": override.get("authMechanism") or "",
                "classification": override.get("data_classification") or "",
                "encrypted": override.get("encrypted", flow.get("encrypted")),
                "crossing": override.get("crosses_trust_zone"),
            }
        )
    layout_spec = architecture.get("diagram_layout") or {}
    layout = layout_spec.get("mode") or "legacy"
    if layout not in LAYOUTS:
        raise ValueError(f"unknown diagram_layout.mode {layout!r}; expected {LAYOUTS}")
    return DiagramModel(
        blueprint=blueprint,
        zones=zones,
        assets=assets,
        flows=enriched,
        layout=layout,
        boundary=layout_spec.get("boundary"),
    )


def _boundary(model: DiagramModel) -> tuple[str | None, set[str]]:
    for boundary in model.blueprint.get("boundaries") or []:
        if model.boundary and boundary.get("name") != model.boundary:
            continue
        zones = set(boundary.get("zones") or [])
        if zones:
            return boundary.get("name"), zones
    return model.boundary, set()


def _zone_assignments(zones, assets):
    zone_names = {zone.get("bom-ref"): zone.get("name") for zone in zones}
    result = defaultdict(list)
    for asset in assets:
        result[asset.get("zone") or "unassigned"].append(asset)
    zone_names["unassigned"] = "External / Unassigned"
    return zone_names, result


def _ordered_zones(zone_names, assets_by_zone):
    return sorted(
        assets_by_zone,
        key=lambda ref: (
            ref == "unassigned",
            -(len(assets_by_zone[ref])),
            zone_names.get(ref) or ref,
        ),
    )


def _asset_sort_key(asset):
    return (asset.get("_diagram_order", 10_000), asset.get("name", ""))


def _arrow(canvas, start, end, *, fill, width=4):
    canvas.polyline([start, end], stroke=fill, width=width)
    _arrow_head(canvas, start, end, fill=fill)


def _arrow_head(canvas, start, end, *, fill):
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length, spread = 15, 0.55
    canvas.polygon(
        [
            end,
            (
                end[0] - length * math.cos(angle - spread),
                end[1] - length * math.sin(angle - spread),
            ),
            (
                end[0] - length * math.cos(angle + spread),
                end[1] - length * math.sin(angle + spread),
            ),
        ],
        fill=fill,
    )


def _edge_label(canvas, midpoint, label, size=15):
    if not label:
        return
    lines = textwrap.wrap(label, width=28)
    width, height = _text_box(lines, size, 2)
    x, y = midpoint
    canvas.rect(
        (x - width / 2 - 5, y - height / 2 - 3, x + width / 2 + 5, y + height / 2 + 3),
        radius=5,
        fill=COLORS["white"],
        opacity="0.91",
        outline="#D5DCE5",
        width=1,
    )
    canvas.text(midpoint, "\n".join(lines), size=size, center=True, spacing=2)


def _box_edge_point(box, center, toward):
    dx, dy = toward[0] - center[0], toward[1] - center[1]
    half_width = (box[2] - box[0]) / 2
    half_height = (box[3] - box[1]) / 2
    scale = min(
        half_width / abs(dx) if dx else float("inf"),
        half_height / abs(dy) if dy else float("inf"),
    )
    return center[0] + dx * scale, center[1] + dy * scale


def _legend(canvas, y):
    x = 45
    for asset_type in ("actor", "system", "component", "data-store"):
        canvas.rect(
            (x, y, x + 26, y + 20),
            radius=4,
            fill=COLORS[asset_type],
            outline=COLORS["ink"],
            width=1,
        )
        canvas.text(
            (x + 34, y + 1),
            asset_type.replace("-", " ").title(),
            size=15,
            fill=COLORS["muted"],
        )
        x += 180
    canvas.polyline([(x, y + 10), (x + 45, y + 10)], stroke=COLORS["edge"], width=4)
    canvas.text((x + 55, y + 1), "Directed data flow", size=15, fill=COLORS["muted"])


def _zone_card(canvas, box, name, *, unassigned, width=4):
    canvas.rect(
        box,
        radius=18,
        fill=COLORS["unassigned"] if unassigned else COLORS["zone_fill"],
        outline=COLORS["zone_border"],
        width=width,
    )
    canvas.text((box[0] + 20, box[1] + 17), name, size=23, bold=True)


def _asset_node(canvas, box, asset):
    asset_type = asset.get("type") or "component"
    canvas.rect(
        box,
        radius=12,
        fill=COLORS.get(asset_type, COLORS["component"]),
        outline=COLORS["ink"],
        width=2,
    )
    canvas.text(
        (box[0] + 13, box[1] + 13),
        asset.get("name") or asset.get("bom-ref"),
        size=19,
        bold=True,
        wrap=27,
    )
    canvas.text(
        (box[0] + 13, box[1] + 62),
        asset_type.replace("-", " "),
        size=15,
        fill=COLORS["muted"],
    )


def _flow_edges(canvas, flows, asset_boxes):
    labels = []
    for index, flow in enumerate(flows):
        source_box = asset_boxes.get(flow.get("source"))
        target_box = asset_boxes.get(flow.get("destination"))
        if not source_box or not target_box or source_box == target_box:
            continue
        sx, sy = source_box[2], (source_box[1] + source_box[3]) // 2
        tx, ty = target_box[0], (target_box[1] + target_box[3]) // 2
        if tx < sx:
            sx, tx = source_box[0], target_box[2]
        bend = 18 + (index % 5) * 8
        mid_x = (sx + tx) // 2
        points = [(sx, sy), (mid_x, sy + bend), (mid_x, ty - bend), (tx, ty)]
        color = (
            COLORS["edge"]
            if flow.get("crossing") is not False
            else COLORS["edge_internal"]
        )
        canvas.polyline(points, stroke=color, width=3)
        _arrow_head(canvas, points[-2], points[-1], fill=color)
        detail = " / ".join(flow.get("protocols") or [])
        label = flow["display_name"] + (f" [{detail}]" if detail else "")
        labels.append(((mid_x, (sy + ty) // 2), label))
    return labels


def _data_flow(model: DiagramModel, title: str) -> _Canvas:
    zone_names, assets_by_zone = _zone_assignments(model.zones, model.assets)
    ordered = _ordered_zones(zone_names, assets_by_zone)
    boundary_layout = model.layout == "authorization-boundary"
    columns, card_width, node_width, node_height = 3, 760, 320, 86
    primary = None
    if boundary_layout and ordered and len(assets_by_zone[ordered[0]]) > 4:
        primary = ordered[0]
    remaining = [ref for ref in ordered if ref != primary]
    primary_height = (
        85 + math.ceil(len(assets_by_zone[primary]) / 4) * 118 if primary else 0
    )
    grid_rows = [
        remaining[index : index + columns]
        for index in range(0, len(remaining), columns)
    ]
    if boundary_layout:
        row_heights = [203 for _row in grid_rows]
    else:
        heights = {
            ref: 85 + max(1, math.ceil(len(assets_by_zone[ref]) / 2)) * 118
            for ref in remaining
        }
        row_heights = [max(heights[ref] for ref in row) for row in grid_rows]
    height = (
        165
        + primary_height
        + (55 if primary else 0)
        + sum(row_heights)
        + 55 * len(grid_rows)
        + 110
    )
    canvas = _Canvas(2460, height)
    canvas.text((45, 30), title, size=36, bold=True)
    _legend(canvas, 88)
    asset_boxes: dict[Any, tuple] = {}
    placed: list[tuple[tuple, dict]] = []
    y = 135
    if primary:
        _zone_card(
            canvas,
            (45, y, 45 + 2370, y + primary_height),
            zone_names.get(primary) or primary,
            unassigned=False,
            width=5,
        )
        for index, asset in enumerate(
            sorted(assets_by_zone[primary], key=_asset_sort_key)
        ):
            node_x = 45 + 28 + (index % 4) * 580
            node_y = y + 65 + (index // 4) * 112
            box = (node_x, node_y, node_x + node_width, node_y + node_height)
            asset_boxes[asset.get("bom-ref")] = box
            placed.append((box, asset))
        y += primary_height + 55
    sort_key = _asset_sort_key if boundary_layout else (lambda a: a.get("name", ""))
    for row_index, row in enumerate(grid_rows):
        for column_index, zone_ref in enumerate(row):
            x = 45 + column_index * 800
            _zone_card(
                canvas,
                (x, y, x + card_width, y + row_heights[row_index]),
                zone_names.get(zone_ref) or zone_ref,
                unassigned=zone_ref == "unassigned",
            )
            for index, asset in enumerate(
                sorted(assets_by_zone[zone_ref], key=sort_key)
            ):
                node_x = x + 28 + (index % 2) * 365
                node_y = y + 65 + (index // 2) * 112
                box = (node_x, node_y, node_x + node_width, node_y + node_height)
                asset_boxes[asset.get("bom-ref")] = box
                placed.append((box, asset))
        y += row_heights[row_index] + 55
    if not boundary_layout:
        for box, asset in placed:
            _asset_node(canvas, box, asset)
    labels = _flow_edges(canvas, model.flows, asset_boxes)
    if boundary_layout:
        for box, asset in placed:
            _asset_node(canvas, box, asset)
    for midpoint, label in labels:
        _edge_label(canvas, midpoint, label)
    return canvas


def _network(model: DiagramModel, title: str) -> _Canvas:
    zone_names, assets_by_zone = _zone_assignments(model.zones, model.assets)
    asset_zone = {
        asset.get("bom-ref"): asset.get("zone") or "unassigned"
        for asset in model.assets
    }
    boundary_name, boundary_zones = (
        _boundary(model) if model.layout == "authorization-boundary" else (None, set())
    )
    boundary_zones &= set(assets_by_zone)
    zone_refs = sorted(assets_by_zone, key=lambda ref: zone_names.get(ref) or ref)
    width, height = 2200, 1600
    canvas = _Canvas(width, height)
    canvas.text((45, 30), title, size=36, bold=True)
    center = (width // 2, height // 2 + 50)
    centers: dict[str, tuple[float, float]] = {}
    boxes: dict[str, tuple] = {}
    inside = sorted(boundary_zones, key=lambda ref: zone_names.get(ref) or ref)
    outside = [ref for ref in zone_refs if ref not in boundary_zones]
    for index, zone_ref in enumerate(inside):
        offset = (index - (len(inside) - 1) / 2) * 430
        cx, cy = center[0] + offset, center[1] + 25
        centers[zone_ref] = (cx, cy)
        boxes[zone_ref] = (cx - 190, cy - 150, cx + 190, cy + 150)
    for index, zone_ref in enumerate(outside):
        angle = -math.pi / 2 + 2 * math.pi * index / max(1, len(outside))
        cx = center[0] + 820 * math.cos(angle)
        cy = center[1] + 570 * math.sin(angle)
        centers[zone_ref] = (cx, cy)
        boxes[zone_ref] = (cx - 180, cy - 72, cx + 180, cy + 72)
    if boundary_zones:
        frame = (center[0] - 510, center[1] - 285, center[0] + 510, center[1] + 285)
        canvas.rect(
            frame,
            radius=30,
            fill=COLORS["boundary_frame"],
            outline=COLORS["boundary_border"],
            width=8,
        )
        canvas.text(
            (frame[0] + 24, frame[1] + 20),
            boundary_name or "Authorization Boundary",
            size=21,
            bold=True,
        )
    aggregate: dict[tuple[str, str], list] = defaultdict(list)
    for flow in model.flows:
        source_zone = asset_zone.get(flow.get("source"), "unassigned")
        target_zone = asset_zone.get(flow.get("destination"), "unassigned")
        if (
            source_zone != target_zone
            and source_zone in centers
            and target_zone in centers
        ):
            aggregate[(source_zone, target_zone)].append(flow)
    for edge_index, ((source_zone, target_zone), edge_flows) in enumerate(
        sorted(aggregate.items())
    ):
        start_center, end_center = centers[source_zone], centers[target_zone]
        dx, dy = end_center[0] - start_center[0], end_center[1] - start_center[1]
        distance = max(1, math.hypot(dx, dy))
        if boundary_zones:
            start = _box_edge_point(boxes[source_zone], start_center, end_center)
            end = _box_edge_point(boxes[target_zone], end_center, start_center)
        else:
            start = (
                start_center[0] + dx / distance * 185,
                start_center[1] + dy / distance * 78,
            )
            end = (
                end_center[0] - dx / distance * 185,
                end_center[1] - dy / distance * 78,
            )
        _arrow(canvas, start, end, fill=COLORS["edge"], width=4)
        protocols = sorted(
            {protocol for flow in edge_flows for protocol in flow.get("protocols", [])}
        )
        label = f"{len(edge_flows)} flow{'s' if len(edge_flows) != 1 else ''}"
        if protocols:
            label += f" · {', '.join(protocols)}"
        normal = (-dy / distance, dx / distance)
        shift = 20 + edge_index % 3 * 10
        midpoint = (
            (start[0] + end[0]) / 2 + normal[0] * shift,
            (start[1] + end[1]) / 2 + normal[1] * shift,
        )
        _edge_label(canvas, midpoint, label)
    for zone_ref in zone_refs:
        is_boundary = zone_ref in boundary_zones
        if is_boundary:
            fill, outline, radius = (
                COLORS["boundary_zone"],
                COLORS["boundary_border"],
                24,
            )
        elif zone_ref == "unassigned":
            fill, outline, radius = COLORS["unassigned"], COLORS["zone_border"], 18
        else:
            fill, outline, radius = COLORS["zone_fill"], COLORS["zone_border"], 18
        canvas.rect(boxes[zone_ref], radius=radius, fill=fill, outline=outline, width=4)
        cx, cy = centers[zone_ref]
        canvas.text(
            (cx, cy - 21),
            zone_names.get(zone_ref) or zone_ref,
            size=21,
            bold=True,
            wrap=25,
            center=True,
        )
        count = len(assets_by_zone[zone_ref])
        plural = "s" if count != 1 else ""
        if boundary_zones:
            caption = (
                f"IN-BOUNDARY · {count} component{plural}"
                if is_boundary
                else f"EXTERNAL · {count} component{plural}"
            )
        else:
            caption = f"{count} component{plural}"
        canvas.text((cx, cy + 39), caption, size=15, fill=COLORS["muted"], center=True)
    canvas.text(
        (45, height - 55),
        "Arrows show cross-boundary direction; labels aggregate protocols by "
        "trust-zone pair.",
        size=15,
        fill=COLORS["muted"],
    )
    return canvas


def render_svg(model: DiagramModel, *, title: str, view: str = "data-flow") -> str:
    """Return the figure for ``view`` as a standalone SVG document."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; expected {VIEWS}")
    canvas = _network(model, title) if view == "network" else _data_flow(model, title)
    return canvas.svg(title)


def stats(model: DiagramModel, view: str) -> dict[str, Any]:
    return {
        "view": view,
        "zones": len(model.zones),
        "assets": len(model.assets),
        "flows": len(model.flows),
        "cross_boundary_flows": sum(
            flow.get("crossing") is True for flow in model.flows
        ),
        "labeled_flows": sum(bool(flow.get("display_name")) for flow in model.flows),
    }


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def default_font_files() -> list[Path]:
    """Return installed DejaVu Sans regular/bold files, so output is reproducible."""
    return [Path(path) for path in _FONT_CANDIDATES if Path(path).is_file()]


def rasterize_png(svg: str, *, font_files: Iterable[str | Path] | None = None) -> bytes:
    """Rasterize ``svg`` to PNG bytes with resvg.

    ``font_files`` pins the fonts used for text; when omitted, the installed DejaVu Sans
    files are used if present, otherwise system fonts.
    """
    try:
        import resvg_py
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "PNG output needs the resvg-py package (pip install resvg-py)"
        ) from exc
    fonts = [str(path) for path in (font_files or default_font_files())]
    data = resvg_py.svg_to_bytes(
        svg_string=svg,
        background=COLORS["white"],
        font_files=fonts or None,
        skip_system_fonts=bool(fonts),
        sans_serif_family="DejaVu Sans",
    )
    return bytes(data)


def render_file(
    dfd: dict[str, Any],
    output: Path,
    *,
    title: str,
    architecture: dict[str, Any] | None = None,
    view: str = "data-flow",
    font_files: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Render ``dfd`` to ``output`` (``.svg`` or ``.png``) and return figure stats."""
    model = build_model(dfd, architecture)
    svg = render_svg(model, title=title, view=view)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".svg":
        output.write_text(svg, encoding="utf-8")
    elif output.suffix.lower() == ".png":
        output.write_bytes(rasterize_png(svg, font_files=font_files))
    else:
        raise ValueError(f"unsupported output type {output.suffix!r}; use .svg or .png")
    return stats(model, view)
