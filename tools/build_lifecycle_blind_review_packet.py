#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    build_lifecycle_holdout_review_packet,
    load_canonical_lifecycle_review_material,
    load_lifecycle_holdout_selection,
    verify_approved_selection_policy,
    verify_candidate_corpus_independence,
    verify_candidate_runtime_bundle,
    verify_evaluation_harness_bundle,
    verify_lifecycle_holdout_checkout,
    verify_selection_development_exclusions,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    load_tag_retrieval_truth,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a candidate-blind lifecycle review packet from an independently "
            "sealed unlabeled selection. This command never loads candidate configuration "
            "or runs FeatureRouter."
        )
    )
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        selection = load_lifecycle_holdout_selection(args.selection)
        development_truth = load_tag_retrieval_truth(DEVELOPMENT_TRUTH)
        verify_selection_development_exclusions(selection, development_truth)
        verify_approved_selection_policy(selection, REPO_ROOT)
        verify_candidate_runtime_bundle(selection.candidate_freeze, REPO_ROOT)
        verify_evaluation_harness_bundle(selection.candidate_freeze, REPO_ROOT)
        checkout = verify_lifecycle_holdout_checkout(selection, args.source_root)
        verify_candidate_corpus_independence(selection, args.source_root)
        contract, policy = load_canonical_lifecycle_review_material(REPO_ROOT)
        packet = build_lifecycle_holdout_review_packet(
            selection,
            checkout,
            target_tag_contract=contract,
            review_policy=policy,
        )
    except (OSError, ValueError) as exc:
        print(f"Lifecycle blind review packet build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
