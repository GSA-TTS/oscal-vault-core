import yaml

from compliance.scripts import regenerate_all_vaults as regen


def _registry(path, systems):
    path.write_text(yaml.safe_dump({"systems": systems}), encoding="utf-8")


def test_entries_without_a_docx_are_ignored(tmp_path):
    reg = tmp_path / "registry.yaml"
    _registry(reg, [{"plan_id": "a", "docx": "a.docx"}, {"plan_id": "b"}])
    assert [s["plan_id"] for s in regen.load_systems(reg)] == ["a"]


def test_plan_skips_missing_source_and_existing_vault_unless_forced(tmp_path):
    system = {"plan_id": "demo", "docx": "src/demo.docx"}
    assert "docx not found" in regen.plan(system, tmp_path, force=False)[3]

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.docx").write_bytes(b"x")
    assert regen.plan(system, tmp_path, force=False)[3] is None

    (tmp_path / "examples" / "vault" / "demo").mkdir(parents=True)
    assert "already exists" in regen.plan(system, tmp_path, force=False)[3]
    assert regen.plan(system, tmp_path, force=True)[3] is None


def test_main_runs_the_exporter_per_system_from_the_workspace(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.docx").write_bytes(b"x")
    _registry(
        tmp_path / "scripts" / "registry.yaml",
        [
            {
                "plan_id": "demo",
                "system_name": "Demo",
                "fips_199": "low",
                "docx": "src/demo.docx",
            },
            {
                "plan_id": "gone",
                "system_name": "Gone",
                "fips_199": "low",
                "docx": "src/gone.docx",
            },
        ],
    )
    calls = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Done()

    monkeypatch.setattr(regen.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    assert regen.main([]) == 0
    ((cmd, kwargs),) = calls
    assert cmd[1] == str(regen.EXPORTER) and "--plan-id" in cmd
    assert cmd[cmd.index("--plan-id") + 1] == "demo"
    assert cmd[cmd.index("--vault") + 1] == str(
        tmp_path / "examples" / "vault" / "demo"
    )
    assert kwargs["cwd"] == str(tmp_path)


def test_missing_registry_is_a_clear_error(tmp_path, capsys):
    assert regen.main(["--registry", str(tmp_path / "nope.yaml")]) == 2
    assert "registry not found" in capsys.readouterr().err
