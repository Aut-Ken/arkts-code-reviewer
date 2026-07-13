#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arkts_code_reviewer.knowledge.build import build_normalized_seed
from arkts_code_reviewer.knowledge.registry import (
    DEFAULT_SOURCE_REGISTRY,
    build_source_bundle,
    load_source_registry,
)
from arkts_code_reviewer.knowledge.seed import load_knowledge_seed

DEFAULT_OUTPUT = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/normalized.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic normalized Knowledge seed")
    parser.add_argument("--registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    registry = load_source_registry(args.registry)
    seed = load_knowledge_seed(args.seed)
    bundle, verified = build_source_bundle(registry, seed.source_ids)
    build = build_normalized_seed(
        seed,
        bundle,
        {item.source.id: item for item in verified},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"built {len(build.documents)} documents as {build.build_id} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
