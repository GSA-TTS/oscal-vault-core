"""A consumer can dispatch to the core without resetting or installing it."""

import shutil
import subprocess

import yaml


def test_consumer_task_dispatch_preserves_initialized_core(tmp_path):
    workspace = tmp_path / "consumer"
    core = workspace / "vendor/oscal-vault-core"
    (core / ".venv/bin").mkdir(parents=True)
    (core / ".venv/bin/python").touch()
    (core / ".venv/bin/python").chmod(0o700)
    taskfile = {
        "version": "3",
        "vars": {"CORE": "vendor/oscal-vault-core"},
        "tasks": {
            "verify": {
                "preconditions": ["test -x {{.CORE}}/.venv/bin/python"],
                "cmds": [
                    'OSCAL_VAULT_WORKSPACE="{{.ROOT_DIR}}" mise -C {{.CORE}} run workspace-verify'
                ],
            },
        },
    }
    (workspace / "Taskfile.yml").write_text(yaml.safe_dump(taskfile))
    task = shutil.which("task")
    assert task, "task is required for consumer dispatch integration"
    result = subprocess.run(
        [task, "--dry", "verify"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rendered = result.stdout + result.stderr
    assert "workspace-verify" in rendered
    assert "OSCAL_VAULT_WORKSPACE" in rendered
    assert "submodule update" not in rendered
    assert "install" not in rendered
