"""Write generated Mermaid into a vault Diagrams note without touching hand-written content.

Generated blocks sit between ``<!-- generated:<id> begin -->`` and ``<!-- generated:<id> end -->``
markers, inside a ``## Generated C4`` section appended to the note. Re-running replaces only
what is between the markers; hand-written Mermaid and embedded images are never edited. A
note that does not exist is not created (the pipeline owns that).
"""

from __future__ import annotations

import re
from pathlib import Path

SECTION = "## Generated C4 (from Ports, Protocols, and Services)"
_TITLES = {
    "c4-context": "C4 Level 1 - System Context",
    "c4-container": "C4 Level 2 - Containers",
}


def _block(block_id: str, mermaid: str) -> str:
    return (
        f"<!-- generated:{block_id} begin -->\n"
        f"### {_TITLES.get(block_id, block_id)}\n\n"
        f"```mermaid\n{mermaid.rstrip()}\n```\n"
        f"<!-- generated:{block_id} end -->"
    )


def apply_blocks(text: str, blocks: dict[str, str]) -> str:
    """Return ``text`` with each generated block inserted or replaced."""
    for block_id, mermaid in blocks.items():
        pattern = re.compile(
            rf"<!-- generated:{re.escape(block_id)} begin -->.*?<!-- generated:{re.escape(block_id)} end -->",
            re.S,
        )
        new = _block(block_id, mermaid)
        if pattern.search(text):
            text = pattern.sub(lambda _m: new, text, count=1)
        elif SECTION in text:
            text = text.rstrip("\n") + "\n\n" + new + "\n"
        else:
            text = text.rstrip("\n") + f"\n\n{SECTION}\n\n" + new + "\n"
    return text


def write_generated_blocks(system_dir: Path, blocks: dict[str, str]) -> bool:
    """Update ``Diagrams/<slug>-diagram.md``. Returns True when the file changed."""
    target = next(iter(sorted((system_dir / "Diagrams").glob("*.md"))), None)
    if target is None:
        return False
    before = target.read_text(encoding="utf-8")
    after = apply_blocks(before, blocks)
    if after == before:
        return False
    target.write_text(after, encoding="utf-8")
    return True
