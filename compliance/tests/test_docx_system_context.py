"""Tests for the system-directory docx context projector (synthetic data only)."""

from __future__ import annotations

import json

import yaml

from compliance.lib.docx_system_context import (
    _data_flow,
    _network_architecture,
    build_context,
    build_typed_context,
)


def _write_system(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "model").mkdir(exist_ok=True)
    (root / "system.yaml").write_text(
        yaml.safe_dump({"slug": "demo", "name": "Demo System (DEMO)"}),
        encoding="utf-8",
    )
    (root / "sidecar.yaml").write_text(
        yaml.safe_dump({"cpo": {"service_name": "Demo System"}}), encoding="utf-8"
    )
    (root / "model" / "base-dfd.cdx.json").write_text(
        json.dumps({"blueprints": [{"assets": [], "flows": []}]}), encoding="utf-8"
    )
    return root


def test_typed_context_reports_placeholders_for_a_minimal_system(tmp_path):
    root = _write_system(tmp_path / "demo")

    typed = build_typed_context(root)
    report = typed.readiness_report()

    assert typed.render_context() == build_context(root)
    summary = report["summary"]
    assert summary["total_fields"] > 0
    assert summary["placeholder_fields"] > 0
    assert (
        summary["ready_fields"] + summary["placeholder_fields"]
        == (summary["total_fields"])
    )
    assert report["fields"]["data_flow"]["kind"] == "narrative"


def test_cli_writes_context_json(tmp_path, capsys):
    from compliance.lib import docx_system_context

    root = _write_system(tmp_path / "demo")
    out = tmp_path / "ctx.json"

    assert docx_system_context.main([str(root), "--out", str(out)]) == 0

    assert json.loads(out.read_text(encoding="utf-8"))["general_system_description"]


def test_cli_rejects_directory_without_manifest(tmp_path, capsys):
    import pytest

    from compliance.lib import docx_system_context

    with pytest.raises(SystemExit):
        docx_system_context.main([str(tmp_path)])
    assert "system.yaml" in capsys.readouterr().err


class TestNetworkArchitectureJoin:
    def _arch(self):
        return {
            "assets": {"alice": "asset-alice-1", "bob": "asset-bob-2"},
            "flows": {
                "alice_to_bob": {"source_asset": "alice", "destination_asset": "bob"},
            },
            "flow_overrides": {
                "alice_to_bob": {
                    "protocols": ["HTTPS"],
                    "authMechanism": "OAuth 2.0",
                    "crosses_trust_zone": True,
                },
            },
        }

    def _cdx(self, bom_ref: str = "flow-flow-1"):
        return {
            "blueprints": [
                {
                    "flows": [
                        {
                            "bom-ref": bom_ref,
                            "source": "asset-alice-1",
                            "destination": "asset-bob-2",
                        }
                    ]
                }
            ]
        }

    def test_joins_by_endpoint_bomrefs_not_flow_bomref(self):
        # The CDX flow's own bom-ref ("flow-flow-1") intentionally does NOT
        # match the architecture.yaml flow alias key ("alice_to_bob") — the
        # join must still succeed via (source, destination) asset bomrefs.
        result = _network_architecture(self._cdx(), self._arch())
        assert "protocols=HTTPS" in result
        assert "auth=OAuth 2.0" in result
        assert "crosses trust zone=yes" in result
        assert "unspecified" not in result

    def test_missing_override_falls_back_to_unspecified(self):
        cdx = self._cdx()
        arch = self._arch()
        arch["flow_overrides"] = {}
        result = _network_architecture(cdx, arch)
        assert "protocols=unspecified" in result
        assert "auth=unspecified" in result

    def test_duplicate_endpoint_pair_disambiguated_by_name(self):
        # Two distinct flows between the same asset pair (e.g. a deploy call
        # and a generic data flow) must each get their own override, matched
        # by flow `name` as the tiebreaker.
        arch = {
            "assets": {"alice": "asset-alice-1", "bob": "asset-bob-2"},
            "flows": {
                "named_flow": {
                    "source_asset": "alice",
                    "destination_asset": "bob",
                    "name": "Named Flow",
                },
                "unnamed_flow": {
                    "source_asset": "alice",
                    "destination_asset": "bob",
                },
            },
            "flow_overrides": {
                "named_flow": {"protocols": ["HTTPS"], "authMechanism": "OAuth 2.0"},
                "unnamed_flow": {"protocols": ["SFTP"], "authMechanism": "SSH key"},
            },
        }
        cdx = {
            "blueprints": [
                {
                    "flows": [
                        {
                            "bom-ref": "flow-a",
                            "source": "asset-alice-1",
                            "destination": "asset-bob-2",
                            "name": "Named Flow",
                        },
                        {
                            "bom-ref": "flow-b",
                            "source": "asset-alice-1",
                            "destination": "asset-bob-2",
                        },
                    ]
                }
            ]
        }
        result = _network_architecture(cdx, arch)
        assert "Named Flow: protocols=HTTPS; auth=OAuth 2.0" in result
        assert "flow-b: protocols=SFTP; auth=SSH key" in result


class TestDataFlowProjection:
    def test_uses_actual_flow_endpoints_and_security_metadata(self):
        cdx = {
            "blueprints": [
                {
                    "assets": [
                        {"bom-ref": "a", "name": "Source"},
                        {"bom-ref": "b", "name": "Destination"},
                    ],
                    "flows": [
                        {
                            "name": "Exchange",
                            "source": "a",
                            "destination": "b",
                        }
                    ],
                }
            ]
        }
        architecture = {
            "flows": {"exchange": {"name": "Exchange"}},
            "flow_overrides": {
                "exchange": {
                    "protocols": ["HTTPS"],
                    "authMechanism": "OAuth",
                    "data_classification": "CUI",
                }
            },
        }

        result = _data_flow(cdx, architecture)

        assert "Source to Destination" in result
        assert "protocols=HTTPS" in result
        assert "authentication=OAuth" in result
        assert "classification=CUI" in result
        assert "Mechanisms" not in result

    def test_joins_alias_without_display_name_by_endpoints(self):
        cdx = {
            "blueprints": [
                {
                    "assets": [
                        {"bom-ref": "a", "name": "Source"},
                        {"bom-ref": "b", "name": "Destination"},
                    ],
                    "flows": [
                        {
                            "name": "Display Name From DFD",
                            "source": "a",
                            "destination": "b",
                        }
                    ],
                }
            ]
        }
        architecture = {
            "context_projection": {"flow_override_resolution": "endpoints"},
            "assets": {"source": "a", "destination": "b"},
            "flows": {
                "stable_alias": {
                    "source_asset": "source",
                    "destination_asset": "destination",
                }
            },
            "flow_overrides": {
                "stable_alias": {
                    "protocols": ["SFTP"],
                    "authMechanism": "SSH key",
                    "data_classification": "CUI candidate",
                }
            },
        }

        result = _data_flow(cdx, architecture)

        assert "protocols=SFTP" in result
        assert "authentication=SSH key" in result
        assert "classification=CUI candidate" in result
        assert "unresolved" not in result
