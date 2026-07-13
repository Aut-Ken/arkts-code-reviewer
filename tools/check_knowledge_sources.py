#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.knowledge.registry import (
    DEFAULT_SOURCE_REGISTRY,
    build_source_bundle,
    load_source_registry,
)

DEFAULT_SOURCE_IDS = ("arkui-specs", "interface-sdk-js", "openharmony-docs")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate registered Knowledge sources and print a deterministic bundle"
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        help="source id to include; repeat for multiple values",
    )
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args()

    registry = load_source_registry(args.registry)
    source_ids = DEFAULT_SOURCE_IDS if args.source_ids is None else tuple(args.source_ids)
    bundle, verified = build_source_bundle(
        registry,
        source_ids,
        verify=not args.no_verify,
    )
    payload = {
        "registry_schema_version": registry.schema_version,
        "registry_updated_at": registry.updated_at.isoformat(),
        "registered_source_count": len(registry.sources),
        "knowledge_source_count": sum(
            source.group == "knowledge_source" for source in registry.sources
        ),
        "bundle": bundle.model_dump(mode="json"),
        "verified": [
            {
                "source_id": item.source.id,
                "remote": item.remote,
                "branch": item.branch,
                "head_revision": item.head_revision,
                "resolved_local_path": str(item.resolved_local_path),
            }
            for item in verified
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
