#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    assess_tag_truth_v2_constructibility,
    load_tag_truth_v2_candidate_freeze,
    load_tag_truth_v2_development_exclusion_snapshot,
    load_tag_truth_v2_selection_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assess a policy-sized, structurally selectable source-capacity lower bound after a "
            "frozen candidate exposure. Proxy-stratum capacity remains not measured. This never "
            "loads FeatureRouter, selects cases, or creates Truth."
        )
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-freeze", type=Path, required=True)
    parser.add_argument("--selection-policy", type=Path, required=True)
    parser.add_argument("--selection-revision", required=True)
    parser.add_argument("--report-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        candidate_freeze = load_tag_truth_v2_candidate_freeze(args.candidate_freeze)
        development_truth = load_tag_truth_v2_development_exclusion_snapshot(DEVELOPMENT_TRUTH)
        selection_policy = load_tag_truth_v2_selection_policy(args.selection_policy)
        report = assess_tag_truth_v2_constructibility(
            args.source_root,
            candidate_freeze=candidate_freeze,
            development_truth=development_truth,
            selection_policy=selection_policy,
            selection_revision=args.selection_revision,
        )
    except (OSError, ValueError) as exc:
        print(f"Tag Truth v2 constructibility check failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.verified_selectable_capacity_satisfied or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
