#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.retrieval_validation.lifecycle_symbol_leaf import (
    build_lifecycle_symbol_leaf_comparison,
    load_lifecycle_owner_role_candidate_config,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
    load_tag_retrieval_truth,
    observe_tag_retrieval_truth,
    tag_retrieval_truth_fingerprint,
    verify_tag_retrieval_truth_checkout,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"
DEFAULT_CANDIDATE_TAGS = (
    REPO_ROOT / "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the default lifecycle Tag with the explicit owner-aware "
            "shadow candidate against provisional EVAL-TR-01 development-regression Truth."
        )
    )
    parser.add_argument("--truth-manifest", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--candidate-tags", type=Path, default=DEFAULT_CANDIDATE_TAGS)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--include-cases",
        action="store_true",
        help="include all baseline and candidate per-case observation rows",
    )
    parser.add_argument(
        "--require-declared-contract",
        action="store_true",
        help=(
            "return non-zero unless all declared target/co-Tag labels and provenance checks pass"
        ),
    )
    parser.add_argument(
        "--require-candidate-evidence",
        action="store_true",
        help=(
            "return non-zero unless both the declared contract and the stricter "
            "candidate evidence safety gate pass"
        ),
    )
    return parser


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    truth = load_tag_retrieval_truth(args.truth_manifest)
    checkout = verify_tag_retrieval_truth_checkout(truth, args.source_root)
    baseline_config = load_default_feature_config()
    candidate_config = load_lifecycle_owner_role_candidate_config(args.candidate_tags)
    baseline = observe_tag_retrieval_truth(
        truth,
        checkout,
        feature_config=baseline_config,
        observation_schema_version=TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
    )
    candidate = observe_tag_retrieval_truth(
        truth,
        checkout,
        feature_config=candidate_config,
        observation_schema_version=TAG_RETRIEVAL_TRUTH_OBSERVATION_V3_SCHEMA_VERSION,
    )
    comparison = build_lifecycle_symbol_leaf_comparison(
        baseline,
        candidate,
        truth_suite=truth,
    )
    if not args.include_cases:
        baseline = {key: value for key, value in baseline.items() if key != "cases"}
        candidate = {key: value for key, value in candidate.items() if key != "cases"}
    return {
        "schema_version": "lifecycle-owner-role-evaluation-v1",
        "evaluation_role": truth.evaluation_boundary.dataset_role,
        "independent_blind_holdout_available": (
            truth.evaluation_boundary.independent_blind_holdout_available
        ),
        "default_production_routing_changed": False,
        "truth_suite_fingerprint": tag_retrieval_truth_fingerprint(truth),
        "comparison": comparison,
        "baseline_observation": baseline,
        "candidate_observation": candidate,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = _evaluate(args)
    except (OSError, ValueError) as exc:
        print(f"Lifecycle symbol-leaf candidate evaluation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    comparison = cast(Mapping[str, object], report["comparison"])
    declared_gate = cast(
        Mapping[str, object],
        comparison["declared_contract_gate"],
    )
    evidence_gate = cast(
        Mapping[str, object],
        comparison["candidate_evidence_gate"],
    )
    if args.require_declared_contract and declared_gate.get("passed") is not True:
        return 1
    if args.require_candidate_evidence and evidence_gate.get("passed") is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
