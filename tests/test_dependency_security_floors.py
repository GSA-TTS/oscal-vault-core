"""Security floors and optional-backend boundaries in the cross-version lock."""

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_security_constraints_cover_supported_python_versions():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = set(project["tool"]["uv"]["constraint-dependencies"])

    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert project["tool"]["ruff"]["target-version"] == "py313"
    assert "filelock>=4.2.1" in constraints
    assert "urllib3>=2.8.0" in constraints


def test_benchmark_group_uses_patched_pillow_and_excludes_unstructured():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    benchmark = project["dependency-groups"]["benchmark"]

    assert "pillow>=13.0.0" in benchmark
    assert not any(requirement.startswith("unstructured") for requirement in benchmark)

    from compliance.lib.extractor_backend import get_backend

    assert get_backend("unstructured").name == "unstructured"


def test_lock_contains_only_safe_security_floor_versions():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'name = "filelock"\nversion = "3.20.3"' not in lock
    assert 'name = "filelock"\nversion = "4.2.7"' in lock
    assert 'name = "urllib3"\nversion = "2.8.0"' in lock
    assert 'name = "pillow"\nversion = "13.0.0"' in lock
    assert 'name = "unstructured"' not in lock
