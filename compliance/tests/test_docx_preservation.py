"""#43 lossless pipeline: opaque DOCX artifacts are carried forward, not dropped.

The production vault path never read review comments, tracked revisions,
footnotes, endnotes, or drawings. These tests pin that those artifacts now land
in a committed preservation sidecar and that the sidecar is fail-closed
verifiable against the live source.
"""

from __future__ import annotations

import collections
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from compliance.lib.docx_preservation import (
    SIDECAR_RELPATH,
    BUNDLE_RELPATH,
    build_preservation_sidecar,
    collect_opaque_artifacts,
    extract_managed_artifacts,
    verify_managed_artifacts,
    verify_preservation_complete,
    write_preservation_sidecar,
    write_source_manifest,
)
from compliance.lib.synthetic_golden_docx import create_synthetic_golden_docx
from compliance.scripts.preserve_docx_source import main as preserve_source_main


@pytest.fixture(scope="module")
def golden_docx():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "golden.docx"
        create_synthetic_golden_docx(p)
        yield p


def test_opaque_artifacts_cover_every_carried_kind(golden_docx):
    """Comments, both revision kinds, footnote, endnote, and drawing are carried."""
    report = collect_opaque_artifacts(golden_docx)
    kinds = collections.Counter(a.kind for a in report.artifacts)

    for kind in (
        "comment",
        "revision_ins",
        "revision_del",
        "footnote",
        "endnote",
        "drawing",
    ):
        assert kinds[kind] >= 1, f"opaque artifact {kind!r} was dropped"


def test_every_carried_artifact_has_a_stable_locator_and_text(golden_docx):
    report = collect_opaque_artifacts(golden_docx)
    assert report.artifacts
    for artifact in report.artifacts:
        assert "#" in artifact.source_locator  # part_uri#xml_locator
        assert artifact.disposition in {"opaque_preserved", "intentionally_excluded"}
    # The reviewer comment text survives verbatim.
    comment = next(a for a in report.artifacts if a.kind == "comment")
    assert "MFA" in comment.text


def test_sidecar_is_written_under_preservation_dir_and_is_deterministic(golden_docx):
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        report = collect_opaque_artifacts(golden_docx)
        path1 = write_preservation_sidecar(report, vault)
        bytes1 = path1.read_bytes()
        path2 = write_preservation_sidecar(collect_opaque_artifacts(golden_docx), vault)
        assert path1 == vault / SIDECAR_RELPATH
        assert bytes1 == path2.read_bytes()  # stable for identical source bytes
        payload = json.loads(bytes1)
        assert payload["schema_version"] == 1
        assert payload["disposition_counts"]["opaque_preserved"] >= 6


def test_source_manifest_preserves_package_tables_drawings_and_hash(golden_docx):
    with tempfile.TemporaryDirectory() as td:
        output = Path(td)
        report = collect_opaque_artifacts(golden_docx)
        path = write_source_manifest(report, output)
        first = path.read_bytes()
        payload = json.loads(first)

        assert path == output / BUNDLE_RELPATH
        assert payload["source_sha256"] == report.source_sha256
        assert payload["source_size_bytes"] == golden_docx.stat().st_size
        assert payload["package_inventory"]["parts"]
        assert payload["source_tables"]
        assert payload["drawings"]
        assert (
            payload["loss_budget"]["total_nodes"]
            == payload["loss_budget"]["accounted_nodes"]
        )
        assert payload["loss_budget"]["unmapped_error"] == 0
        assert report.loss_budget_findings() == []
        table = payload["source_tables"][0]
        assert table["source_table_id"].startswith("word/document.xml#")
        assert table["cells"][0][0]["cell_locator"]
        assert table["cells"][0][0]["paragraphs"][0]["locator"]

        assert (
            first
            == write_source_manifest(
                collect_opaque_artifacts(golden_docx), output
            ).read_bytes()
        )


def test_source_manifest_cli_writes_bundle(golden_docx, tmp_path):
    assert (
        preserve_source_main(
            ["--docx", str(golden_docx), "--output", str(tmp_path), "--strict"]
        )
        == 0
    )
    payload = json.loads((tmp_path / BUNDLE_RELPATH).read_text(encoding="utf-8"))
    assert payload["source_tables"]
    assert payload["opaque_artifacts"]


def test_loss_budget_fails_strict_on_unmapped_source_node(golden_docx, tmp_path):
    tampered = tmp_path / "tampered.docx"
    with zipfile.ZipFile(golden_docx, "r") as src:
        data = {name: src.read(name) for name in src.namelist()}
    document = data["word/document.xml"].decode("utf-8")
    document = document.replace(
        "</ns0:body>",
        "<ns0:unknownBlock><ns0:r><ns0:t>unmapped value</ns0:t></ns0:r>"
        "</ns0:unknownBlock></ns0:body>",
    )
    data["word/document.xml"] = document.encode("utf-8")
    with zipfile.ZipFile(tampered, "w") as dst:
        for name, payload in data.items():
            dst.writestr(name, payload)

    assert (
        preserve_source_main(
            ["--docx", str(tampered), "--output", str(tmp_path), "--strict"]
        )
        == 1
    )


def test_managed_artifacts_are_checksum_named_and_verified(golden_docx, tmp_path):
    records = extract_managed_artifacts(golden_docx, tmp_path)

    assert records
    media = next(
        record for record in records if record["source_part"].startswith("word/media/")
    )
    path = tmp_path / media["preserved_path"]
    assert media["sha256"][:16] in path.name
    assert path.read_bytes()
    assert verify_managed_artifacts(records, tmp_path) == []
    assert records == extract_managed_artifacts(golden_docx, tmp_path)

    path.write_bytes(b"tampered")
    assert any(
        "checksum differs" in finding
        for finding in verify_managed_artifacts(records, tmp_path)
    )


def test_verify_passes_for_a_faithful_sidecar(golden_docx):
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        sidecar = build_preservation_sidecar(golden_docx, vault)
        assert verify_preservation_complete(golden_docx, sidecar) == []


def test_verify_fails_when_an_artifact_is_dropped_from_the_sidecar(golden_docx):
    """Regression guard: deleting a carried artifact must be detected."""
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        sidecar = build_preservation_sidecar(golden_docx, vault)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        # Drop the comment artifact, simulating a parser that stops carrying it.
        payload["artifacts"] = [
            a for a in payload["artifacts"] if a["kind"] != "comment"
        ]
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        findings = verify_preservation_complete(golden_docx, sidecar)
        assert any("comment" in f for f in findings)


def test_verify_reports_a_missing_sidecar(golden_docx):
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.json"
        findings = verify_preservation_complete(golden_docx, missing)
        assert findings and "missing" in findings[0]


def test_verify_detects_content_drift_at_a_stable_locator(golden_docx):
    """A changed comment/revision at the same locator must be flagged."""
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        sidecar = build_preservation_sidecar(golden_docx, vault)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        # Tamper the recorded text + digest for one artifact, as if the source
        # comment changed but kept its locator.
        target = next(a for a in payload["artifacts"] if a["kind"] == "comment")
        target["text"] = "TAMPERED"
        target["content_sha256"] = "0" * 64
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        findings = verify_preservation_complete(golden_docx, sidecar)
        assert any("content drift" in f for f in findings)


def test_verify_detects_a_recorded_artifact_that_disappeared(golden_docx):
    """An artifact recorded in the sidecar but absent from source is flagged."""
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        sidecar = build_preservation_sidecar(golden_docx, vault)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["artifacts"].append(
            {
                "source_locator": "word/document.xml#body/ghost[999]",
                "kind": "comment",
                "disposition": "opaque_preserved",
                "text": "ghost",
                "payload": {},
                "content_sha256": "f" * 64,
            }
        )
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        findings = verify_preservation_complete(golden_docx, sidecar)
        assert any("no longer present" in f for f in findings)


def test_verify_flags_stale_schema_without_content_hash(golden_docx):
    """A sidecar predating content_sha256 is reported, not silently accepted."""
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        sidecar = build_preservation_sidecar(golden_docx, vault)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        for artifact in payload["artifacts"]:
            artifact.pop("content_sha256", None)
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        findings = verify_preservation_complete(golden_docx, sidecar)
        assert any("content_sha256" in f for f in findings)


def test_unclassified_content_node_fails_closed(golden_docx):
    """An unknown content-bearing body block must fail closed, not vanish.

    Previously the IR walker only enumerated known body children, so an unknown
    block was dropped before classification and package coverage still reported
    1.0. The walker now emits an ``unmapped_error`` node for any unmodeled
    content-bearing body element, so certification aborts.
    """
    with tempfile.TemporaryDirectory() as td:
        tampered = Path(td) / "tampered.docx"
        with zipfile.ZipFile(golden_docx, "r") as src:
            data = {n: src.read(n) for n in src.namelist()}
        doc = data["word/document.xml"].decode("utf-8")
        # The body is serialized with the ns0 prefix bound to the w namespace,
        # so match that prefix and reuse it on the injected element.
        assert "</ns0:body>" in doc
        injected = (
            "<ns0:unknownBlock><ns0:r><ns0:t>orphan payload text"
            "</ns0:t></ns0:r></ns0:unknownBlock>"
        )
        doc = doc.replace("</ns0:body>", injected + "</ns0:body>")
        data["word/document.xml"] = doc.encode("utf-8")
        with zipfile.ZipFile(tampered, "w") as dst:
            for name, payload in data.items():
                dst.writestr(name, payload)

        with pytest.raises(ValueError, match="unclassified"):
            collect_opaque_artifacts(tampered)


def test_known_opaque_artifacts_still_collected_for_clean_doc(golden_docx):
    """A document with no unknown blocks still certifies and carries artifacts."""
    report = collect_opaque_artifacts(golden_docx)
    assert report.artifacts
