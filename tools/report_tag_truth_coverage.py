#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth_coverage import (
    build_tag_truth_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAGS_CONFIG = REPO_ROOT / "config/tags.yaml"
DEFAULT_DIMENSIONS_CONFIG = REPO_ROOT / "config/dimensions.yaml"
DEFAULT_FEATURE_GOLDEN = REPO_ROOT / "tests/golden/feature_routing/manifest.json"
DEFAULT_DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"
DEFAULT_DRAFT_TRUTH = REPO_ROOT / "tests/tag_truth/relational_store_api/manifest.json"
DEFAULT_DRAFT_BASELINE = REPO_ROOT / "tests/tag_truth/relational_store_api/baselines/current.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate the frozen synthetic and real-code Tag Truth inventory without "
            "running Parser, FeatureRouter, a candidate, or an external checkout."
        )
    )
    parser.add_argument("--tags-config", type=Path, default=DEFAULT_TAGS_CONFIG)
    parser.add_argument("--dimensions-config", type=Path, default=DEFAULT_DIMENSIONS_CONFIG)
    parser.add_argument("--feature-golden", type=Path, default=DEFAULT_FEATURE_GOLDEN)
    parser.add_argument("--development-truth", type=Path, default=DEFAULT_DEVELOPMENT_TRUTH)
    parser.add_argument("--draft-truth", type=Path, default=DEFAULT_DRAFT_TRUTH)
    parser.add_argument("--draft-baseline", type=Path, default=DEFAULT_DRAFT_BASELINE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_tag_truth_coverage_report(
            tags_config_path=args.tags_config,
            dimensions_config_path=args.dimensions_config,
            feature_golden_manifest_path=args.feature_golden,
            development_manifest_path=args.development_truth,
            draft_manifest_path=args.draft_truth,
            draft_baseline_path=args.draft_baseline,
        )
    except (OSError, ValueError) as exc:
        print(f"Tag Truth coverage error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
