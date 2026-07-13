#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation import build_knowledge_annotations
from arkts_code_reviewer.knowledge.annotation_config import (
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild

DEFAULT_INPUT = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/candidates.json"
)
DEFAULT_OUTPUT = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/annotations.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic Knowledge candidate annotations"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    extraction = KnowledgeExtractionBuild.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    feature_config = load_default_feature_config()
    annotation_config = load_knowledge_annotation_config(
        args.config,
        feature_config=feature_config,
    )
    build = build_knowledge_annotations(
        extraction,
        feature_config=feature_config,
        config=annotation_config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tagged = sum(bool(item.tags) for item in build.annotations)
    dimensioned = sum(bool(item.dimension_ids) for item in build.annotations)
    print(
        f"built {len(build.annotations)} annotations ({tagged} tagged, "
        f"{dimensioned} dimensioned) as {build.build_id} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
