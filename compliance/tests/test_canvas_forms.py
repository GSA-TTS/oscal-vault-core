from compliance.lib.canvas_forms import (
    MEDIA_TYPE,
    canvas_visualization,
    normalize_legacy_visualizations,
    to_legacy_visualizations,
)

CANVAS = {"nodes": [{"id": "n1"}], "edges": []}


def _document():
    return {
        "blueprints": [
            {
                "bom-ref": "bp",
                "visualizations": [
                    canvas_visualization(CANVAS, name="D", ref="bp"),
                    {"type": {"type": "architecture"}, "name": "Keep"},
                ],
            }
        ]
    }


def test_standard_form_is_a_base64_attachment():
    viz = canvas_visualization(CANVAS, name="D", ref="bp")
    assert viz["attachment"]["mediaType"] == MEDIA_TYPE
    assert viz["attachment"]["encoding"] == "base64"


def test_round_trip_restores_the_canvas_and_keeps_other_visualizations():
    legacy, count = to_legacy_visualizations(_document())
    assert count == 1
    first, second = legacy["blueprints"][0]["visualizations"]
    assert first == {
        "type": "precogly-dfd",
        "name": "D",
        "diagramType": "level1",
        "data": CANVAS,
    }
    assert second["name"] == "Keep"
    assert to_legacy_visualizations(legacy)[1] == 0
    back, n = normalize_legacy_visualizations(legacy)
    assert n == 1
    assert back["blueprints"][0]["visualizations"][0]["type"] == {"type": "data-flow"}


def test_conversion_does_not_mutate_its_input():
    document = _document()
    to_legacy_visualizations(document)
    assert document["blueprints"][0]["visualizations"][0]["attachment"]
