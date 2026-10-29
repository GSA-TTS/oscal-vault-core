from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

from compliance.lib.canonical_json import (
    generate_canonical_data,
    normalize_canonical_record,
    parse_frontmatter,
    parse_inline_fields,
    parse_markdown_sections,
    validate_canonical_sidecar,
)
from compliance.scripts.generate_canonical_json import (
    clean_orphaned_snapshots,
    find_vault_notes,
    main as generate_canonical_main,
)


def test_parse_frontmatter():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "test.md"

        # Valid frontmatter
        tmp_path.write_text(
            "---\ntype: oscal.control\ncontrol_id: AC-2\ntags:\n  - test\n---\n# Body\nContent here.",
            encoding="utf-8",
        )
        fm, body = parse_frontmatter(tmp_path)
        assert fm == {"type": "oscal.control", "control_id": "AC-2", "tags": ["test"]}
        assert body.strip() == "# Body\nContent here."

        # Missing delimiter
        tmp_path.write_text("No frontmatter here", encoding="utf-8")
        with pytest.raises(ValueError, match="delimiter"):
            parse_frontmatter(tmp_path)

        # Unclosed delimiter
        tmp_path.write_text("---\ntype: test\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Unclosed"):
            parse_frontmatter(tmp_path)


def test_parse_markdown_sections():
    body = """Some initial content.

# Section 1
Content of section 1.
More text.

## Subsection 1.1
Subcontent.

# Section 2
Content of section 2.
"""
    sections = parse_markdown_sections(body)
    assert len(sections) == 4

    assert sections[0]["heading"] is None
    assert sections[0]["content"] == "Some initial content."

    assert sections[1]["heading"] == "Section 1"
    assert sections[1]["content"] == "Content of section 1.\nMore text."

    assert sections[2]["heading"] == "Subsection 1.1"
    assert sections[2]["content"] == "Subcontent."

    assert sections[3]["heading"] == "Section 2"
    assert sections[3]["content"] == "Content of section 2."


def test_parse_markdown_sections_preserves_heading_level():
    """An H1 and an H3 with identical text must remain distinguishable (#43)."""
    body = """# Upstream
Top-level content.

### Upstream
Nested content.
"""
    sections = parse_markdown_sections(body)
    assert [(s["heading"], s["level"]) for s in sections] == [
        ("Upstream", 1),
        ("Upstream", 3),
    ]
    # Pre-heading content (if any) carries a null level.
    pre = parse_markdown_sections("intro\n# H\nbody")
    assert pre[0]["level"] is None
    assert pre[1]["level"] == 1


def test_generate_canonical_data_preserves_raw_body_and_levels():
    """The canonical sidecar preserves the verbatim body plus heading levels."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        note = root / "note.md"
        body = "# Title\n\nalpha\n\n### Deep\n\nbeta\n"
        note.write_text(
            f"---\ntype: oscal.control\ncontrol_id: AC-2\n---\n{body}",
            encoding="utf-8",
        )
        data = generate_canonical_data(
            note, root, generated_at_override="2026-01-01T00:00:00Z"
        )
        # raw_body is byte-for-byte the markdown body after frontmatter.
        assert data["raw_body"] == body
        levels = [s["level"] for s in data["body_sections"] if s["heading"]]
        assert levels == [1, 3]


def test_generate_canonical_data_carries_instruction_provenance_metadata(tmp_path):
    source_hash = "a" * 64
    note = tmp_path / "note.md"
    note.write_text(
        "---\ntype: oscal.control\n---\n"
        '<!-- oscal-vault-instruction {"source_hash": "'
        + source_hash
        + '", "source_locator": "word/document.xml#body/p[1]"} -->\n\n'
        "> [!note]- Instruction\n> Instruction: describe the system.\n",
        encoding="utf-8",
    )
    data = generate_canonical_data(
        note, tmp_path, generated_at_override="2026-01-01T00:00:00Z"
    )
    expected = [
        {
            "source_locator": "word/document.xml#body/p[1]",
            "source_hash": source_hash,
        }
    ]
    assert data["instruction_sources"] == expected
    assert data["_meta"]["instruction_sources"] == expected
    assert validate_canonical_sidecar(data) == []


def test_generate_canonical_data_derives_stable_id_and_system_reference(tmp_path):
    note = tmp_path / "relationship.md"
    note.write_text(
        "---\n"
        "type: oscal.x.relationship\n"
        "relationship_id: rel-example\n"
        "system_id: system-example\n"
        "---\n# Relationship\n",
        encoding="utf-8",
    )

    data = generate_canonical_data(note, tmp_path)

    assert data["stable_id"] == "rel-example"
    assert data["typed_references"] == [
        {"relation": "scoped_to", "id": "system-example", "type": "oscal.ssp"}
    ]
    assert validate_canonical_sidecar(data) == []


def test_generate_canonical_data_embeds_versioned_oscal_lineage_summary(tmp_path):
    note = tmp_path / "party.md"
    note.write_text(
        "---\ntype: oscal.party\nname: Example Agency\n---\n# Party\n",
        encoding="utf-8",
    )

    data = generate_canonical_data(note, tmp_path)

    assert data["oscal_mapping_capabilities"]["schema_version"] == 1
    assert data["oscal_mapping_capabilities"]["native"] == 1
    assert data["oscal_mapping_capabilities"]["extension_bridge"] == 0
    assert data["oscal_mapping_capabilities"]["mapping_sources"] == ["oscal_party.name"]
    assert validate_canonical_sidecar(data) == []


def test_raw_body_detects_section_splitter_loss():
    """raw_body changes when body content changes even if sections look stable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        note = root / "note.md"
        base = "---\ntype: oscal.control\ncontrol_id: AC-2\n---\n# H\nalpha\n"
        note.write_text(base, encoding="utf-8")
        first = generate_canonical_data(
            note, root, generated_at_override="2026-01-01T00:00:00Z"
        )
        # Add trailing content that a lossy section splitter could discard.
        note.write_text(base + "\n<!-- trailing comment -->\n", encoding="utf-8")
        second = generate_canonical_data(
            note, root, generated_at_override="2026-01-01T00:00:00Z"
        )
        assert first["raw_body"] != second["raw_body"]
        assert first["_meta"]["content_hash"] != second["_meta"]["content_hash"]


def test_parse_inline_fields():
    body = """
statement_part:: a
inheritance_mode:: local
implementation_status:: implemented
[custom_field:: custom_value]
(another_field:: another_value)
duplicate_field:: val1
duplicate_field:: val2
"""
    fields = parse_inline_fields(body)
    assert fields["statement_part"] == "a"
    assert fields["inheritance_mode"] == "local"
    assert fields["implementation_status"] == "implemented"
    assert fields["custom_field"] == "custom_value"
    assert fields["another_field"] == "another_value"
    assert fields["duplicate_field"] == ["val1", "val2"]


def test_generate_canonical_data_stability():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_root = Path(tmpdir)
        note_path = vault_root / "Controls" / "AC-2.md"
        note_path.parent.mkdir(parents=True)

        # Write version A
        note_path.write_text(
            "---\ntype: oscal.control\ncontrol_id: AC-2\nstatus: active\n---\n# Upstream\nControl text.\nstatement_part:: a\n",
            encoding="utf-8",
        )

        res_a = generate_canonical_data(
            note_path, vault_root, generated_at_override="2026-07-18T00:00:00Z"
        )

        # Write version B: different frontmatter order, same values
        note_path.write_text(
            "---\nstatus: active\ntype: oscal.control\ncontrol_id: AC-2\n---\n# Upstream\nControl text.\nstatement_part:: a\n",
            encoding="utf-8",
        )

        res_b = generate_canonical_data(
            note_path, vault_root, generated_at_override="2026-07-18T00:00:00Z"
        )

        # Verify hash stability and identical structure
        assert res_a["_meta"]["content_hash"] == res_b["_meta"]["content_hash"]
        assert res_a == res_b


def test_cli_execution_and_orphan_cleanup():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_dir = Path(tmpdir)
        controls_dir = vault_dir / "Controls"
        controls_dir.mkdir(parents=True)

        # 1. Create notes
        note1 = controls_dir / "AC-1.md"
        note1.write_text(
            "---\ntype: oscal.control\ncontrol_id: AC-1\n---\n# Content\n",
            encoding="utf-8",
        )

        note2 = controls_dir / "AC-2.md"
        note2.write_text(
            "---\ntype: oscal.control\ncontrol_id: AC-2\n---\n# Content\n",
            encoding="utf-8",
        )

        # Run helper to find notes
        notes = find_vault_notes(vault_dir)
        assert len(notes) == 2

        # 2. Mock generation manually for sidecars
        snapshot_dir = vault_dir / ".snapshots" / "current" / "Controls"
        snapshot_dir.mkdir(parents=True)

        sc1 = snapshot_dir / "AC-1.json"
        sc1.write_text("{}", encoding="utf-8")

        sc2 = snapshot_dir / "AC-2.json"
        sc2.write_text("{}", encoding="utf-8")

        sc3 = snapshot_dir / "AC-3.json"  # Orphaned sidecar (no AC-3.md exists)
        sc3.write_text("{}", encoding="utf-8")

        # 3. Clean orphans
        cleaned = clean_orphaned_snapshots(
            vault_dir, [Path("Controls/AC-1.md"), Path("Controls/AC-2.md")]
        )
        assert cleaned == 1
        assert sc1.exists()
        assert sc2.exists()
        assert not sc3.exists()


def test_cli_preserves_timestamp_for_unchanged_sidecar(tmp_path, monkeypatch):
    note = tmp_path / "Controls" / "AC-1.md"
    note.parent.mkdir()
    note.write_text(
        "---\ntype: oscal.control\ncontrol_id: AC-1\n---\n# Content\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv", ["generate_canonical_json.py", "--vault", str(tmp_path)]
    )
    assert generate_canonical_main() == 0
    sidecar = tmp_path / ".snapshots" / "current" / "Controls" / "AC-1.json"
    first = sidecar.read_text(encoding="utf-8")

    assert generate_canonical_main() == 0
    assert sidecar.read_text(encoding="utf-8") == first


def test_cli_targeted_note_updates_only_selected_sidecar(tmp_path, monkeypatch):
    controls = tmp_path / "Controls"
    controls.mkdir()
    for control_id in ("AC-1", "AC-2"):
        (controls / f"{control_id}.md").write_text(
            f"---\ntype: oscal.control\ncontrol_id: {control_id}\n---\n# Original\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "sys.argv", ["generate_canonical_json.py", "--vault", str(tmp_path)]
    )
    assert generate_canonical_main() == 0
    snapshot = tmp_path / ".snapshots" / "current" / "Controls"
    untouched = (snapshot / "AC-2.json").read_text(encoding="utf-8")

    (controls / "AC-1.md").write_text(
        "---\ntype: oscal.control\ncontrol_id: AC-1\n---\n# Changed\n",
        encoding="utf-8",
    )
    orphan = snapshot / "orphan.json"
    orphan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate_canonical_json.py",
            "--vault",
            str(tmp_path),
            "--note",
            "Controls/AC-1.md",
        ],
    )

    assert generate_canonical_main() == 0
    assert (snapshot / "AC-2.json").read_text(encoding="utf-8") == untouched
    assert orphan.exists()
    assert "# Changed" in (snapshot / "AC-1.json").read_text(encoding="utf-8")


def test_cli_rejects_unsafe_targeted_note(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate_canonical_json.py",
            "--vault",
            str(tmp_path),
            "--note",
            "../outside.md",
        ],
    )

    assert generate_canonical_main() == 1


def test_validate_canonical_sidecar_and_normalize():
    """Canonical sidecars validate against schema and normalize records (#49)."""
    raw = {
        "_meta": {
            "schema_version": 1,
            "generated_from": "Controls/AC-02.md",
            "generated_at": "2026-10-08T00:00:00Z",
            "content_hash": "sha256:" + "a" * 64,
            "exportable": True,
        },
        "type": "oscal.control",
        "title": "Account Management",
        "control_id": "AC-02",
        "date": "2026-10-08",
        "statement_parts": [
            {"id": "a", "text": "Prose for part a"},
            {"id": "b", "text": "Prose for part b"},
        ],
        "related_controls": [
            {
                "control_id": "AC-03",
                "relationship": "depends_on",
                "scope": "local-vault",
                "note": "[[Controls/AC-03]]",
            }
        ],
        "attachments": [
            {
                "id": "arch-diagram-1",
                "path": "media/diagram.png",
                "media_type": "image/png",
                "sha256": "b" * 64,
                "disposition": "rendered",
            }
        ],
    }

    errors = validate_canonical_sidecar(raw)
    assert not errors, f"Validation errors: {errors}"

    norm = normalize_canonical_record(raw)
    assert norm["related_controls"][0]["control_id"] == "AC-03"
    assert norm["attachments"][0]["id"] == "arch-diagram-1"

    # Negative test: missing required _meta field
    invalid = dict(raw)
    del invalid["_meta"]
    errs = validate_canonical_sidecar(invalid)
    assert len(errs) > 0
    assert any("_meta" in e for e in errs)
