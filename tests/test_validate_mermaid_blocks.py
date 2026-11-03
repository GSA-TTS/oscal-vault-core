from pathlib import Path

from scripts.validate_mermaid_blocks import collect_blocks, node_modules, run_parser


def test_collect_blocks_labels_each_fence(tmp_path: Path):
    document = tmp_path / "diagrams.md"
    document.write_text(
        "before\n```mermaid\nflowchart LR\n A --> B\n```\n"
        "```mermaid\nsequenceDiagram\n A->>B: hi\n```\n",
        encoding="utf-8",
    )

    assert collect_blocks([document]) == [
        {"id": f"{document}#mermaid-1", "text": "flowchart LR\n A --> B"},
        {"id": f"{document}#mermaid-2", "text": "sequenceDiagram\n A->>B: hi"},
    ]


def test_real_parser_rejects_invalid_mermaid():
    modules = node_modules()
    assert modules is not None, "run npm ci before the Mermaid parser tests"

    (result,) = run_parser(
        [{"id": "broken.md#mermaid-1", "text": "flowchart LR\n A -- B"}], modules
    )

    assert result["ok"] is False
    assert result["id"] == "broken.md#mermaid-1"
