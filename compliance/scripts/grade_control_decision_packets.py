#!/usr/bin/env python3
"""Grade control packets deterministically or with optional Bedrock assistance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.control_decision import (  # noqa: E402
    BedrockGrader,
    deterministic_grade,
    validate_packet,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--provider", choices=["deterministic", "bedrock"], default="deterministic"
    )
    parser.add_argument("--model-id")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    if args.provider == "bedrock" and not args.model_id:
        parser.error("--model-id is required with --provider bedrock")
    grader = (
        BedrockGrader(args.model_id, args.region, max_tokens=args.max_tokens)
        if args.provider == "bedrock"
        else None
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grades = []
    for path in sorted(args.packet_dir.glob("*.packet.json")):
        packet = json.loads(path.read_text(encoding="utf-8"))
        validate_packet(packet)
        grade = grader.grade(packet) if grader else deterministic_grade(packet)
        output = args.output_dir / path.name.replace(".packet.json", ".grade.json")
        output.write_text(
            json.dumps(grade, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        grades.append(grade)
    summary = {
        "schema_version": 1,
        "method": "llm_assisted" if grader else "deterministic_preflight",
        "grades": len(grades),
        "by_grade": {
            grade: sum(item["grade"] == grade for item in grades)
            for grade in sorted({item["grade"] for item in grades})
        },
        "human_review_pending": len(grades),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
