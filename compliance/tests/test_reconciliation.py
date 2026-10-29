import subprocess
from pathlib import Path

import pytest
import yaml

from compliance.lib.reconciliation import (
    inventory_source,
    load_source_config,
    validate_overlay,
)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    (repo / "workflows").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "workflows/build.yml").write_text("name: Build\n", encoding="utf-8")
    (repo / "tests/control.robot").write_text(
        "[Tags]    ac-2    ca-7\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=x@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    return repo, commit


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "system_id": "example-system",
                "repository": "example/source",
                "technologies": ["github-actions", "robot-framework"],
                "sources": [
                    {"path": "workflows", "include": "*.yml", "kind": "workflow"},
                    {
                        "path": "tests",
                        "include": "*.robot",
                        "kind": "robot",
                        "extract_control_ids": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_inventory_is_deterministic_and_pinned(tmp_path: Path):
    repo, commit = _repo(tmp_path)
    config = load_source_config(_config(tmp_path))

    first = inventory_source(repo, config, commit)
    second = inventory_source(repo, config, commit)

    assert first == second
    assert first["summary"]["control_ids"] == ["AC-2", "CA-7"]
    assert first["summary"]["kinds"] == {"robot": 1, "workflow": 1}
    with pytest.raises(ValueError, match="must be"):
        inventory_source(repo, config, "0" * 40)


def test_source_config_rejects_parent_escape(tmp_path: Path):
    path = _config(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["sources"][0]["path"] = "../outside"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError):
        load_source_config(path)


def test_overlay_validation_is_review_gated():
    overlay = {
        "schema_version": 1,
        "system_id": "example-system",
        "baseline": {},
        "automation_source": {
            "repository": "example/source",
            "commit": "a" * 40,
            "inventory_sha256": "b" * 64,
        },
        "authority": {},
        "changes": [
            {
                "operation_id": "add-component",
                "operation": "add",
                "target_type": "oscal.component_definition",
                "target_id": "component",
                "field": "title",
                "status": "pending",
                "source_evidence": [
                    {"path": "component.json", "sha256": "c" * 64, "kind": "oscal"}
                ],
                "rationale": "Reviewed candidate",
                "review": {"required": True},
            }
        ],
    }

    assert validate_overlay(overlay) == []
    assert any(
        "require approval" in error
        for error in validate_overlay(overlay, require_ready=True)
    )
