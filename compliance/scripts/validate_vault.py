#!/usr/bin/env python3
"""Consolidated Obsidian vault validation (one command, headless, CI-friendly).

The official Obsidian CLI (obsidian.md/help/cli) drives the *desktop app* over a
URI scheme (open vaults, run commands) and has no headless "validate this vault"
mode — and it needs the GUI, so it can't run in CI. This script is the
headless equivalent: it runs the checks that matter when a generated vault is
opened in Obsidian, in one command, over one or more vaults.

It composes the existing focused validators:
  - frontmatter schema        (compliance/scripts/validate_frontmatter.py)
  - wikilink integrity        (compliance/scripts/validate_wikilinks.py)
  - Dataview query integrity  (compliance/scripts/validate_dataview_queries.py)

and adds Obsidian-parity structural checks those don't cover:
  - duplicate note names      (ambiguous shortest-path [[links]])
  - embed targets exist       (![[note]] / ![[image.png]] resolve)
  - tag syntax                (frontmatter `tags:` are valid #tag tokens)

plus checks adapted from third-party Obsidian vault validators (see
docs/VAULT_VALIDATION.md for full provenance & attribution):
  - OKF conformance           (okf-enforcer, Apache-2.0): every non-reserved
                              note has parseable frontmatter + non-empty `type`
  - orphan notes              (vault-triage, MIT): no inbound/outbound links
  - draft markers             (vault-triage, MIT): TODO/FIXME/WIP in body
  - thin notes                (vault-triage, MIT): meaningful body < threshold
  - stale notes               (vault-triage, MIT): mtime older than N days
  - `gardener-ignore` opt-out (obsidian-vault-intelligence, MIT): per-note
                              exemption from the advisory checks

Usage:
    python3 compliance/scripts/validate_vault.py [--vault <dir> ...]
    python3 compliance/scripts/validate_vault.py --vault examples/vault/<system>
    python3 compliance/scripts/validate_vault.py --strict   # promote warnings to failures
    python3 compliance/scripts/validate_vault.py --thin-chars 0 --stale-days 90

Exit status is non-zero if any *error*-level check fails (fail-closed). The
frontmatter and Dataview checks are always error-level; all structural + ported
checks are warn-only by default (generated/legacy vaults carry known advisory
findings) and become errors under --strict.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_SCRIPTS = Path(__file__).resolve().parent

# Wikilinks: [[target]] / [[target|alias]] / [[target#heading]] (not embeds).
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+?)(?:[|#][^\]]+?)?\]\]")
# Embeds: ![[target]] / ![[target|alias]] / ![[target#heading]]
_EMBED_RE = re.compile(r"!\[\[([^\]|#]+?)(?:[|#][^\]]+?)?\]\]")
# Any wikilink (embed or not), used for orphan inbound/outbound counting.
_ANY_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+?)(?:[|#][^\]]+?)?\]\]")
# Frontmatter block delimited by leading ---\n ... \n---
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
# A valid Obsidian tag token (after an optional leading '#').
_TAG_RE = re.compile(r"^#?[\w/\-]+$")
# Inline #tags in body text (word chars, slashes, hyphens).
_INLINE_TAG_RE = re.compile(r"(?:^|\s)#([\w/\-]+)")

# --- ported-check defaults (see docs/VAULT_VALIDATION.md for provenance) ---
# Draft/incomplete markers scanned in note bodies (whole-word, case-insensitive).
_DRAFT_MARKERS = ("TODO", "FIXME", "WIP", "XXX", "TBD", "PLACEHOLDER")
# A note whose meaningful body (frontmatter + H1 stripped) is shorter than this
# is "thin". Generated control notes are legitimately terse, so the default is
# low; raise via --thin-chars.
_DEFAULT_THIN_CHARS = 80
# A note older than this many days is "stale". 0 disables. Off by default
# because generated vaults are all freshly written (would be all-or-nothing).
_DEFAULT_STALE_DAYS = 0
# Frontmatter key that opts a note out of the advisory (ported) checks,
# mirroring obsidian-vault-intelligence's `gardener-ignore`.
_IGNORE_KEY = "gardener-ignore"
# Non-markdown embed extensions Obsidian resolves as attachments.
_ATTACHMENT_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".bmp",
    ".pdf",
    ".mp4",
    ".webm",
    ".mov",
    ".mp3",
    ".wav",
    ".ogg",
}


def _find_md_files(vault: Path) -> List[Path]:
    return sorted(p for p in vault.rglob("*.md") if p.is_file())


def _is_template_note(path: Path, vault: Path) -> bool:
    """Template notes (_meta/templates/) are intentionally-incomplete scaffolds
    containing Jinja/Dataview slot syntax; the focused validators skip them and
    so do the structural checks here."""
    return "_meta/templates/" in str(path.relative_to(vault)).replace("\\", "/")


def _run_subvalidator(name: str, vaults: List[Path], strict: bool) -> Tuple[int, str]:
    """Run one of the existing validator scripts; return (returncode, output)."""
    cmd = [sys.executable, str(_SCRIPTS / name)]
    for v in vaults:
        cmd += ["--vault", str(v)]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_WORKSPACE))
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _supports_strict(name: str) -> bool:
    """Frontmatter/wikilink expose --strict; dataview does not."""
    return name in {"validate_frontmatter.py", "validate_wikilinks.py"}


# ---------------------------------------------------------------------------
# Obsidian-parity structural checks (not covered by the focused validators)
# ---------------------------------------------------------------------------


def check_template_guidance_invariant(vault: Path) -> List[str]:
    """The official blank Moderate SSP template (``moderate-sspp-template-2024``)
    must carry a ``> [!note]- Template guidance`` callout (R18/R19,
    ADR-022/ADR-023) on every one of its 330 control notes. Downstream
    consumers that vendor this repo as a submodule build audit tooling
    directly on this invariant (e.g. the SSPP project's
    ``control-response-review`` skill fail-closes without it) — a silent
    regression here (a re-parse that drops the callout, or a template file
    added/removed without regenerating guidance) breaks that tooling with no
    local signal. This check makes the invariant visible in this repo, where
    the regression would actually originate.

    Scoped to exactly one vault directory name so a differently-templated
    example vault regenerated before R18/R19 landed (e.g.
    ``lightweight-moderate-template-2024``) is never flagged — this is a
    targeted regression guard, not a blanket requirement that every vault
    carry guidance callouts.
    """
    if vault.name != "moderate-sspp-template-2024":
        return []
    controls_dir = vault / "Controls"
    if not controls_dir.is_dir():
        return [f"{vault.name}: no Controls/ directory (expected 330 control notes)"]

    control_files = sorted(controls_dir.glob("*.md"))
    total = len(control_files)
    with_guidance = sum(
        1
        for p in control_files
        if "Template guidance" in p.read_text(encoding="utf-8", errors="replace")
    )
    issues: List[str] = []
    if total != 330:
        issues.append(
            f"{vault.name}: expected 330 control notes, found {total} "
            "(control-count drift — flag in the pin-advance change summary)"
        )
    if with_guidance != total:
        missing = total - with_guidance
        issues.append(
            f"{vault.name}: {missing}/{total} control note(s) missing the "
            "'Template guidance' callout (R18/R19 regression — see "
            "ADR-022/ADR-023)"
        )
    return issues


def check_duplicate_note_names(vault: Path) -> List[str]:
    """Report duplicate note stems only when they are actually reachable by an
    *ambiguous* link.

    Obsidian resolves ``[[Foo]]`` by shortest unique path. A duplicate stem is
    only a real problem if some note links to it by the **bare** name
    (``[[Foo]]``) rather than a path-qualified link (``[[Systems/Foo]]``). This
    project deliberately gives a System and its Component the same stem but
    always links them path-qualified, so those are not flagged; a genuine bare
    ambiguous link still is.
    """
    by_stem: Dict[str, List[Path]] = defaultdict(list)
    for p in _find_md_files(vault):
        by_stem[p.stem].append(p)
    duplicated = {stem for stem, paths in by_stem.items() if len(paths) > 1}
    if not duplicated:
        return []

    # Collect bare (non-path) link targets across the vault.
    bare_targets: set[str] = set()
    for p in _find_md_files(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if "/" not in target:  # bare name -> ambiguous if stem duplicated
                bare_targets.add(target)

    issues: List[str] = []
    for stem in sorted(duplicated & bare_targets):
        locs = ", ".join(str(p.relative_to(vault)) for p in by_stem[stem])
        issues.append(
            f"ambiguous bare link [[{stem}]] -> duplicate note name in: {locs}"
        )
    return issues


def check_embeds_resolve(vault: Path) -> List[str]:
    """Every ![[embed]] must resolve to an existing note or attachment file."""
    md_stems = {p.stem for p in _find_md_files(vault)}
    all_names = {p.name for p in vault.rglob("*") if p.is_file()}
    all_stems = {p.stem for p in vault.rglob("*") if p.is_file()}
    issues: List[str] = []
    for p in _find_md_files(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _EMBED_RE.finditer(text):
            target = m.group(1).strip()
            if not target:
                continue
            tstem = Path(target).stem
            ext = Path(target).suffix.lower()
            resolved = (
                tstem in md_stems
                or target in all_names
                or tstem in all_stems
                or (ext in _ATTACHMENT_EXTS and Path(target).name in all_names)
            )
            if not resolved:
                issues.append(
                    f"{p.relative_to(vault)}: embed ![[{target}]] target not found"
                )
    return issues


def check_tag_syntax(vault: Path) -> List[str]:
    """Frontmatter `tags:` entries must be valid Obsidian tag tokens
    (word chars, '/', '-'; no spaces). Catches malformed tags that Obsidian
    silently drops from the tag pane."""
    issues: List[str] = []
    for p in _find_md_files(vault):
        if _is_template_note(p, vault):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        block = m.group(1)
        in_tags = False
        for line in block.splitlines():
            if re.match(r"^tags:\s*$", line):
                in_tags = True
                continue
            if in_tags:
                lm = re.match(r"^\s*-\s*(.+?)\s*$", line)
                if lm:
                    tag = lm.group(1).strip().strip("'\"")
                    if tag and not _TAG_RE.match(tag):
                        issues.append(
                            f"{p.relative_to(vault)}: invalid tag token '{tag}'"
                        )
                    continue
                # inline list form: tags: [a, b]
                if not line.startswith(" ") and not line.startswith("-"):
                    in_tags = False
            # inline `tags: [a, b, c]`
            inline = re.match(r"^tags:\s*\[(.+)\]\s*$", line)
            if inline:
                for raw in inline.group(1).split(","):
                    tag = raw.strip().strip("'\"")
                    if tag and not _TAG_RE.match(tag):
                        issues.append(
                            f"{p.relative_to(vault)}: invalid tag token '{tag}'"
                        )
    return issues


# ---------------------------------------------------------------------------
# Ported checks (adapted from third-party Obsidian validators; see
# docs/VAULT_VALIDATION.md for provenance & attribution)
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
    """Return (parsed-frontmatter-dict, body). Frontmatter parse failures yield
    an empty dict; body is everything after the block (or all of it)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end() :]
    try:
        import yaml

        data = yaml.safe_load(m.group(1)) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, body


def _meaningful_body(body: str) -> str:
    """Body with the leading H1 title and blank lines stripped — used to judge
    whether a note has real content (thin-note check)."""
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):  # title heading, not content
            continue
        lines.append(s)
    return " ".join(lines)


def _has_ignore_flag(fm: Dict[str, object]) -> bool:
    val = fm.get(_IGNORE_KEY)
    return bool(val) and str(val).lower() not in ("false", "0", "no")


def check_okf_conformance(vault: Path) -> List[str]:
    """OKF v0.1 (§9) hard rule: every non-reserved note has a parseable
    frontmatter block with a non-empty `type`. Reserved files (index.md,
    log.md, readme.md) and template scaffolds are exempt.

    Ported from okf-enforcer (Apache-2.0).
    """
    reserved = {"index.md", "log.md", "readme.md"}
    issues: List[str] = []
    for p in _find_md_files(vault):
        if _is_template_note(p, vault) or p.name.lower() in reserved:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = _split_frontmatter(text)
        if _has_ignore_flag(fm):
            continue
        if not _FRONTMATTER_RE.match(text):
            issues.append(f"{p.relative_to(vault)}: no parseable frontmatter (OKF §9)")
        elif not str(fm.get("type") or "").strip():
            issues.append(f"{p.relative_to(vault)}: empty/missing `type` (OKF §9)")
    return issues


def check_orphan_notes(vault: Path) -> List[str]:
    """A note with zero inbound AND zero outbound wikilinks is an orphan
    (unreachable in the graph). Notes that link out (hubs/indexes) are not
    flagged. Ported from vault-triage (MIT).

    OKF infrastructure notes (``okf.index``, ``okf.mapping``, ``okf.schema``,
    ``okf.validation_rule``) are exempt: they are surfaced through Dataview
    dashboards and tag queries rather than wikilinks, which a static
    link-graph scan cannot follow (a limitation vault-triage itself notes).
    """
    okf_infra = {
        "okf.index",
        "okf.instruction",
        "okf.mapping",
        "okf.schema",
        "okf.table_row",
        "okf.source_table",
        "okf.validation_rule",
    }
    files = [p for p in _find_md_files(vault) if not _is_template_note(p, vault)]
    stems = {p.stem for p in files}
    outbound: Dict[Path, set] = {}
    inbound_count: Dict[str, int] = defaultdict(int)

    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        targets = set()
        for m in _ANY_WIKILINK_RE.finditer(text):
            tstem = Path(m.group(1).strip()).stem
            if tstem and tstem != p.stem:
                targets.add(tstem)
        outbound[p] = targets
        for t in targets:
            if t in stems:
                inbound_count[t] += 1

    issues: List[str] = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = _split_frontmatter(text)
        if _has_ignore_flag(fm):
            continue
        if str(fm.get("type") or "") in okf_infra:
            continue
        has_out = bool(outbound[p])
        has_in = inbound_count.get(p.stem, 0) > 0
        if not has_out and not has_in:
            issues.append(
                f"{p.relative_to(vault)}: orphan (no inbound or outbound links)"
            )
    return issues


def check_thin_notes(vault: Path, min_chars: int) -> List[str]:
    """Notes whose meaningful body (frontmatter + title stripped) is shorter
    than ``min_chars``. Ported from vault-triage (MIT). Disabled when
    ``min_chars <= 0``.
    """
    if min_chars <= 0:
        return []
    issues: List[str] = []
    for p in _find_md_files(vault):
        if _is_template_note(p, vault):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = _split_frontmatter(text)
        if _has_ignore_flag(fm):
            continue
        n = len(_meaningful_body(body))
        if n < min_chars:
            issues.append(
                f"{p.relative_to(vault)}: thin note ({n} chars < {min_chars})"
            )
    return issues


def check_stale_notes(vault: Path, max_days: int) -> List[str]:
    """Notes whose file mtime is older than ``max_days`` days. Ported from
    vault-triage (MIT). Disabled when ``max_days <= 0``; future mtimes (clock
    skew) are never stale.
    """
    if max_days <= 0:
        return []
    import time

    now = time.time()
    cutoff = max_days * 86400
    issues: List[str] = []
    for p in _find_md_files(vault):
        if _is_template_note(p, vault):
            continue
        fm, _ = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if _has_ignore_flag(fm):
            continue
        age = now - p.stat().st_mtime
        if age > cutoff:
            issues.append(
                f"{p.relative_to(vault)}: stale ({int(age // 86400)} days old > {max_days})"
            )
    return issues


def check_draft_markers(vault: Path) -> List[str]:
    """Notes whose body contains a draft/incomplete marker (TODO/FIXME/WIP/...),
    whole-word and case-insensitive, frontmatter ignored. Ported from
    vault-triage (MIT).
    """
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(m) for m in _DRAFT_MARKERS) + r")\b",
        re.IGNORECASE,
    )
    issues: List[str] = []
    for p in _find_md_files(vault):
        if _is_template_note(p, vault):
            continue
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if _has_ignore_flag(fm):
            continue
        found = sorted({m.group(1).upper() for m in pattern.finditer(body)})
        if found:
            issues.append(f"{p.relative_to(vault)}: draft marker(s) {', '.join(found)}")
    return issues


def _resolve_vaults(arg_vaults: List[str] | None) -> List[Path]:
    if arg_vaults:
        return [Path(v).resolve() for v in arg_vaults]
    examples = _WORKSPACE / "examples" / "vault"
    if examples.is_dir():
        return [
            d
            for d in sorted(examples.iterdir())
            if d.is_dir() and not d.name.startswith(".")
        ]
    return []


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consolidated Obsidian vault validation (headless)."
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Vault directory (repeatable; default: every vault in examples/vault/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote structural warnings (links/embeds/dups/tags) to failures.",
    )
    parser.add_argument(
        "--thin-chars",
        type=int,
        default=_DEFAULT_THIN_CHARS,
        help=f"Flag notes with fewer meaningful body chars (0 disables; "
        f"default {_DEFAULT_THIN_CHARS}).",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=_DEFAULT_STALE_DAYS,
        help=f"Flag notes older than N days (0 disables; default "
        f"{_DEFAULT_STALE_DAYS}).",
    )
    parser.add_argument(
        "--no-new-debt",
        action="store_true",
        default=False,
        help="Reject any structural debt (orphans, thin notes, drafts) beyond the recorded baseline (#53).",
    )
    parser.add_argument(
        "--baseline-file",
        default=str(_WORKSPACE / "scripts" / "vault_debt_baseline.json"),
        help="Path to debt baseline JSON file (default: scripts/vault_debt_baseline.json)",
    )
    args = parser.parse_args(argv)

    vaults = _resolve_vaults(args.vault)
    if not vaults:
        print("No vault directories to validate. Use --vault.", file=sys.stderr)
        return 0

    print(f"Validating {len(vaults)} vault(s): {', '.join(v.name for v in vaults)}\n")

    baseline_data: Dict[str, Set[str]] = {}
    if args.no_new_debt:
        bp = Path(args.baseline_file)
        if bp.is_file():
            try:
                with open(bp, encoding="utf-8") as f:
                    raw_b = json.load(f)
                    baseline_data = {k: set(v) for k, v in raw_b.items()}
            except Exception as e:
                print(f"WARN: could not load baseline file: {e}")

    error_level_failures = 0
    warn_count = 0

    # --- 1) Focused sub-validators -----------------------------------------
    for name, error_level in (
        ("validate_frontmatter.py", True),
        ("validate_dataview_queries.py", True),
        ("validate_wikilinks.py", args.strict),
    ):
        strict = args.strict and _supports_strict(name)
        rc, out = _run_subvalidator(name, vaults, strict)
        label = name.replace("validate_", "").replace(".py", "")
        tail = out.splitlines()[-1] if out else "(no output)"
        status = "PASS" if rc == 0 else ("FAIL" if error_level else "WARN")
        print(f"[{status}] {label}: {tail}")
        if rc != 0:
            if error_level:
                error_level_failures += 1
                # surface the detail lines for an error-level failure
                for line in out.splitlines()[:-1]:
                    if line.strip():
                        print(f"        {line}")
            else:
                warn_count += 1

    # --- 2) Template-guidance invariant (error-level regression guard) -----
    # This is a hard invariant, not an advisory: the moderate template must
    # keep its 330 guidance callouts (ADR-022/ADR-023) or downstream audit
    # tooling that consumes this repo as a submodule fails closed. It errors
    # regardless of --strict.
    guidance_issues: List[str] = []
    for v in vaults:
        guidance_issues.extend(check_template_guidance_invariant(v))
    if guidance_issues:
        print(f"[FAIL] template-guidance invariant: {len(guidance_issues)} issue(s)")
        for issue in guidance_issues:
            print(f"        {issue}")
        error_level_failures += 1
    else:
        print("[PASS] template-guidance invariant: 0 issues")

    # --- 3) Obsidian-parity structural checks ------------------------------
    struct_checks = (
        ("duplicate note names", check_duplicate_note_names),
        ("embed targets", check_embeds_resolve),
        ("tag syntax", check_tag_syntax),
        ("OKF conformance", check_okf_conformance),
        ("orphan notes", check_orphan_notes),
        ("draft markers", check_draft_markers),
        ("thin notes", lambda v: check_thin_notes(v, args.thin_chars)),
        ("stale notes", lambda v: check_stale_notes(v, args.stale_days)),
    )
    for label, fn in struct_checks:
        all_issues: List[str] = []
        for v in vaults:
            v_issues = fn(v)
            all_issues.extend(
                f"{v.name}/{i}" if args.no_new_debt else i for i in v_issues
            )
        if not all_issues:
            print(f"[PASS] {label}: 0 issues")
            continue

        check_key = label.replace(" ", "_")
        if args.no_new_debt and check_key in baseline_data:
            known_set = baseline_data[check_key]
            new_issues = [i for i in all_issues if i not in known_set]
            if new_issues:
                print(
                    f"[FAIL] {label}: {len(new_issues)} NEW issue(s) beyond baseline (total {len(all_issues)})"
                )
                for issue in new_issues[:25]:
                    print(f"        {issue}")
                if len(new_issues) > 25:
                    print(f"        ... and {len(new_issues) - 25} more")
                error_level_failures += 1
            else:
                print(
                    f"[PASS (baselined)] {label}: {len(all_issues)} known issue(s) baselined, 0 new debt"
                )
        else:
            status = "FAIL" if args.strict else "WARN"
            print(f"[{status}] {label}: {len(all_issues)} issue(s)")
            for issue in all_issues[:25]:
                print(f"        {issue}")
            if len(all_issues) > 25:
                print(f"        ... and {len(all_issues) - 25} more")
            if args.strict:
                error_level_failures += 1
            else:
                warn_count += 1

    print()
    if error_level_failures:
        print(f"FAILED: {error_level_failures} error-level check(s) failed.")
        return 1
    if warn_count:
        print(
            f"OK with {warn_count} warning group(s). "
            "Run with --strict to treat them as failures."
        )
    else:
        print("OK: all vault checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
