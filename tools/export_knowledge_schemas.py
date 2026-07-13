#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.knowledge.models import KnowledgeClause, KnowledgeModelReview


def _write_schema(path: Path, schema: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export frozen Knowledge v1 JSON schemas")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("schemas/knowledge"),
        help="schema output directory",
    )
    args = parser.parse_args()

    _write_schema(
        args.output_dir / "knowledge-clause.schema.json",
        KnowledgeClause.model_json_schema(),
    )
    _write_schema(
        args.output_dir / "grok-review-output.schema.json",
        KnowledgeModelReview.model_json_schema(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
