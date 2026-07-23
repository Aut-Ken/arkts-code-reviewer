#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.knowledge.document_first.campaign import (
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT,
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    materialize_document_card_campaign,
    prepare_document_card_campaign,
)
from arkts_code_reviewer.knowledge.registry import DEFAULT_SOURCE_REGISTRY
from arkts_code_reviewer.knowledge.seed import DEFAULT_KNOWLEDGE_SEED


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build and materialize an offline Document Card campaign inspection. "
            "This command has no credential or network execution path."
        )
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--seed", type=Path, default=DEFAULT_KNOWLEDGE_SEED)
    parser.add_argument(
        "--selection",
        type=Path,
        default=DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    )
    parser.add_argument("--prompt", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT,
    )
    args = parser.parse_args(argv)
    try:
        bundle = prepare_document_card_campaign(
            registry_path=args.registry,
            seed_path=args.seed,
            selection_path=args.selection,
            policy_path=args.policy,
            prompt_path=args.prompt,
        )
        output_directory = materialize_document_card_campaign(
            bundle,
            output_root=args.output_root,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = {
        **bundle.inspection.model_dump(mode="json"),
        "artifact_directory": str(output_directory),
    }
    print(json.dumps(rendered, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
