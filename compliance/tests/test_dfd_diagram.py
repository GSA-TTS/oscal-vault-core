"""Tests for the dependency-free DFD diagram renderer."""

from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET

import pytest

from compliance.lib import dfd_diagram
from compliance.lib.dfd_diagram import build_model, render_file, render_svg

SVG_NS = "{http://www.w3.org/2000/svg}"


def _dfd(boundaries=None):
    blueprint = {
        "zones": [
            {"bom-ref": "external", "name": "External"},
            {"bom-ref": "internal", "name": "System Zone"},
        ],
        "assets": [
            {
                "bom-ref": "a",
                "name": "User & <Partner>",
                "type": "actor",
                "zone": "external",
            },
            {"bom-ref": "b", "name": "API", "type": "system", "zone": "internal"},
        ],
        "flows": [
            {
                "name": "Request",
                "source": "a",
                "destination": "b",
                "protocols": ["HTTPS"],
            }
        ],
    }
    if boundaries is not None:
        blueprint["boundaries"] = boundaries
    return {"blueprints": [blueprint]}


def _architecture(**layout):
    return {
        "assets": {"user": "a", "api": "b"},
        "flows": {
            "user_to_api": {
                "source_asset": "user",
                "destination_asset": "api",
                "name": "Request",
            }
        },
        "flow_overrides": {
            "user_to_api": {"protocols": ["TLS 1.3"], "crosses_trust_zone": True}
        },
        **({"diagram_layout": layout} if layout else {}),
    }


def _texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return ["".join(node.itertext()) for node in root.iter(f"{SVG_NS}text")]


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_build_model_applies_architecture_overrides():
    model = build_model(_dfd(), _architecture())

    assert model.layout == "legacy"
    assert model.flows[0]["alias"] == "user_to_api"
    assert model.flows[0]["protocols"] == ["TLS 1.3"]
    assert model.flows[0]["crossing"] is True


def test_build_model_rejects_unknown_layout():
    with pytest.raises(ValueError, match="diagram_layout.mode"):
        build_model(_dfd(), {"diagram_layout": {"mode": "radial"}})


@pytest.mark.parametrize("view", ["network", "data-flow"])
def test_svg_is_well_formed_escaped_and_deterministic(view):
    model = build_model(_dfd(), _architecture())

    first = render_svg(model, title="Figure <1>", view=view)
    second = render_svg(
        build_model(_dfd(), _architecture()), title="Figure <1>", view=view
    )

    assert first == second
    texts = _texts(first)
    assert "Figure <1>" in texts
    if view == "data-flow":
        assert "User & <Partner>" in texts
        assert "Request [TLS 1.3]" in texts
    else:
        assert "1 flow · TLS 1.3" in texts


def test_data_flow_dimensions_follow_zone_content():
    svg = render_svg(build_model(_dfd()), title="Diagram", view="data-flow")
    root = ET.fromstring(svg)

    assert root.get("width") == "2460"
    assert root.get("height") == str(165 + 203 + 55 + 110)


def test_network_draws_declared_boundary_from_inputs_only():
    dfd = _dfd(
        boundaries=[{"name": "Example Authorization Boundary", "zones": ["internal"]}]
    )
    svg = render_svg(
        build_model(dfd, _architecture(mode="authorization-boundary")),
        title="Network",
        view="network",
    )
    texts = _texts(svg)

    assert "Example Authorization Boundary" in texts
    assert "IN-BOUNDARY · 1 component" in texts
    assert "EXTERNAL · 1 component" in texts
    assert dfd_diagram.COLORS["boundary_border"] in svg


def test_boundary_selection_honours_layout_name():
    dfd = _dfd(
        boundaries=[
            {"name": "Unrelated", "zones": ["external"]},
            {"name": "Chosen", "zones": ["internal"]},
        ]
    )
    model = build_model(
        dfd, _architecture(mode="authorization-boundary", boundary="Chosen")
    )

    assert dfd_diagram._boundary(model) == ("Chosen", {"internal"})


def test_legacy_network_has_no_boundary_frame():
    dfd = _dfd(boundaries=[{"name": "Ignored", "zones": ["internal"]}])
    svg = render_svg(build_model(dfd), title="Network", view="network")

    assert "Ignored" not in _texts(svg)
    assert "1 component" in _texts(svg)


def test_render_file_writes_svg(tmp_path):
    output = tmp_path / "flow.svg"

    stats = render_file(_dfd(), output, title="Diagram", architecture=_architecture())

    assert stats == {
        "view": "data-flow",
        "zones": 2,
        "assets": 2,
        "flows": 1,
        "cross_boundary_flows": 1,
        "labeled_flows": 1,
    }
    ET.fromstring(output.read_text(encoding="utf-8"))


def test_render_file_rejects_unknown_suffix(tmp_path):
    with pytest.raises(ValueError, match="unsupported output type"):
        render_file(_dfd(), tmp_path / "flow.jpg", title="Diagram")


def test_png_rasterization_is_deterministic(tmp_path):
    pytest.importorskip("resvg_py")
    first, second = tmp_path / "a.png", tmp_path / "b.png"

    render_file(_dfd(), first, title="Network", view="network")
    render_file(_dfd(), second, title="Network", view="network")

    assert first.read_bytes() == second.read_bytes()
    assert _png_size(first.read_bytes()) == (2200, 1600)


def test_cli_renders_png(tmp_path):
    pytest.importorskip("resvg_py")
    from compliance.scripts import render_dfd_diagram

    dfd = tmp_path / "dfd.json"
    architecture = tmp_path / "architecture.yaml"
    output = tmp_path / "out" / "flow.png"
    dfd.write_text(json.dumps(_dfd()), encoding="utf-8")
    architecture.write_text(json.dumps(_architecture()), encoding="utf-8")

    assert (
        render_dfd_diagram.main(
            [
                "--dfd",
                str(dfd),
                "--architecture",
                str(architecture),
                "--output",
                str(output),
                "--title",
                "Diagram",
            ]
        )
        == 0
    )
    assert _png_size(output.read_bytes())[0] == 2460
