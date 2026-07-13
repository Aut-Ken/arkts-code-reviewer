#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arkts_code_reviewer.knowledge.build import NormalizedKnowledgeBuild
from arkts_code_reviewer.knowledge.extraction import build_knowledge_extraction

DEFAULT_INPUT = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/normalized.json"
)
DEFAULT_OUTPUT = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/candidates.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract deterministic Knowledge Clause and API candidates"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    normalized = NormalizedKnowledgeBuild.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    build = build_knowledge_extraction(normalized)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build.model_dump_json(indent=2) + "\n", encoding="utf-8")
    clause_count = sum(len(item.clauses) for item in build.documents)
    api_count = sum(len(item.api_symbols) for item in build.documents)
    availability_count = sum(
        len(symbol.availability)
        for item in build.documents
        for symbol in item.api_symbols
    )
    diagnostic_count = sum(len(item.diagnostics) for item in build.documents)
    print(
        f"built {clause_count} Clause candidates and {api_count} API declarations "
        f"({availability_count} mode-specific availability records) with "
        f"{diagnostic_count} actionable diagnostics as {build.build_id} "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
