#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    build_tag_truth_v2_review_packet,
    load_tag_truth_v2_development_exclusion_snapshot,
    load_tag_truth_v2_selection,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_review_packet,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a path-redacted, candidate-blind full-file Tag Truth v2 review packet. "
            "The packet omits source paths, proxy strata, ranks, and candidate information."
        )
    )
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        selection = load_tag_truth_v2_selection(args.selection)
        development_truth = load_tag_truth_v2_development_exclusion_snapshot(DEVELOPMENT_TRUTH)
        verify_tag_truth_v2_development_exclusions(selection, development_truth, args.source_root)
        checkout = verify_tag_truth_v2_selection_checkout(selection, args.source_root)
        verify_tag_truth_v2_selection_exposure(selection, args.source_root)
        packet = build_tag_truth_v2_review_packet(selection, checkout)
        verify_tag_truth_v2_review_packet(packet, selection)
    except (OSError, ValueError) as exc:
        print(f"Tag Truth v2 review packet build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            packet.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
