"""Unit tests for the generic vault-export intake digest and sanitization helpers."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REPO_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
for _p in (ROOT, REPO_SCRIPTS):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vault_intake = _load("vault_intake", SCRIPTS / "vault_intake.py")


def test_aggregate_digest_excludes_notice_and_ds_store(tmp_path: Path) -> None:
    (tmp_path / "Controls").mkdir()
    (tmp_path / "Controls" / "AC-02.md").write_text("# AC-2\n", encoding="utf-8")
    digest_a, entries_a = vault_intake.aggregate_digest(tmp_path)

    (tmp_path / vault_intake.NOTICE_NAME).write_text("notice\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"ignore")
    digest_b, entries_b = vault_intake.aggregate_digest(tmp_path)

    assert digest_a == digest_b
    assert entries_a == entries_b
    assert [rel for rel, _ in entries_a] == ["Controls/AC-02.md"]


def test_sanitize_vault_text_normalizes_wikilinks_and_redacts_tokens() -> None:
    counts: dict[str, int] = {}
    scheme = "https" + "://"
    url = scheme + "internal.example.test/path"
    email = "owner" + "@" + "example.test"
    ip = "10." + "20.30.40/24"
    raw = (
        f"System Implementation: See [[Controls/AC-02(01)|AC-2(1)]] and "
        f"[[Runbooks/incident-plan]] at {url} contact {email} from 0.0.0.0/0 or {ip}."
    )
    cleaned = vault_intake.sanitize_vault_text(
        raw,
        counts,
        label_re=re.compile(r"System Implementation:\s*"),
    )

    assert "AC-2(1)" in cleaned
    assert "runbook incident-plan" in cleaned
    assert "<REDACTED_URL>" in cleaned
    assert "<REDACTED_EMAIL>" in cleaned
    assert "any-address CIDR" in cleaned
    assert "<REDACTED_IP>" in cleaned
    assert counts["wiki_links"] == 2
    assert counts["urls"] == 1
    assert counts["emails"] == 1
    assert counts["open_world_cidr_reworded"] == 1
    assert counts["ipv4"] == 1
