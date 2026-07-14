#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth import (
    assert_strict_tag_truth_baseline,
    evaluate_tag_truth_suite,
    load_tag_truth_feature_config,
    load_tag_truth_suite,
    verify_tag_truth_checkout,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "tests/tag_truth/relational_database/manifest.json"
DEFAULT_TAGS_CONFIG = (
    REPO_ROOT / "tests/fixtures/feature_routing/tag_config_rdb_shadow_v1.yaml"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate an owner-level Feature Tag shadow candidate against a pinned "
            "read-only source checkout."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--tags-config", type=Path, default=DEFAULT_TAGS_CONFIG)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict-baseline", type=Path)
    parser.add_argument("--require-contract-perfect", action="store_true")
    parser.add_argument("--require-review-package-ready", action="store_true")
    parser.add_argument("--require-activation-ready", action="store_true")
    return parser


def _write_report(path: Path, manifest: Path, report: dict[str, object]) -> None:
    if path.is_symlink():
        raise ValueError("JSON output path must not be a symlink")
    try:
        output = path.resolve(strict=False)
        truth_root = manifest.resolve(strict=True).parent
    except OSError as exc:
        raise ValueError(f"cannot resolve JSON output path: {exc}") from exc
    if output == truth_root or output.is_relative_to(truth_root):
        raise ValueError("JSON output must not overwrite the truth package")
    if not output.parent.is_dir():
        raise ValueError("JSON output parent directory must already exist")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _metric(value: object) -> str:
    if value is None:
        return "undefined"
    if not isinstance(value, int | float):
        raise ValueError("metric value must be numeric or null")
    return f"{float(value):.3f}"


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.require_activation_ready and args.strict_baseline is None:
            raise ValueError(
                "--require-activation-ready requires --strict-baseline"
            )
        suite = load_tag_truth_suite(args.manifest)
        checkout = verify_tag_truth_checkout(suite, args.source_root)
        feature_config = load_tag_truth_feature_config(suite, args.tags_config)
        report = evaluate_tag_truth_suite(suite, checkout, feature_config)
        if args.strict_baseline is not None:
            assert_strict_tag_truth_baseline(report, args.strict_baseline)
        if args.json_output is not None:
            _write_report(args.json_output, args.manifest, report)
    except (OSError, ValueError) as exc:
        print(f"Tag truth evaluation error: {exc}", file=sys.stderr)
        return 2

    contract = report["contract"]
    metrics = report["provisional_semantic_metrics"]
    metrics_by_split = report["provisional_semantic_metrics_by_split"]
    decision = report["quality_decision"]
    assert isinstance(contract, dict)
    assert isinstance(metrics, dict)
    assert isinstance(metrics_by_split, dict)
    assert isinstance(decision, dict)
    print(f"Tag truth {report['suite_id']}: {report['case_count']} cases")
    print(
        "  contract matched/mismatched: "
        f"{contract['matched_case_count']}/{contract['mismatched_case_count']}"
    )
    print(f"  file_hint promotions: {contract['file_hint_promotion_count']}")
    print(
        "  provisional TP/FP/FN/TN: "
        f"{metrics['true_positive']}/{metrics['false_positive']}/"
        f"{metrics['false_negative']}/{metrics['true_negative']}"
    )
    print(
        "  provisional precision/recall/F1: "
        f"{_metric(metrics['precision'])}/{_metric(metrics['recall'])}/"
        f"{_metric(metrics['f1'])}"
    )
    holdout = metrics_by_split["acceptance_holdout"]
    assert isinstance(holdout, dict)
    print(
        "  holdout precision/recall: "
        f"{_metric(holdout['precision'])}/{_metric(holdout['recall'])}"
    )
    print(
        "  dataset ready for human review: "
        f"{decision['dataset_ready_for_human_review']}"
    )
    print(f"  metrics status: {decision['metrics_status']}")
    print(f"  activation ready: {decision['activation_ready']}")
    for failure in decision["activation_failures"]:
        print(f"    - {failure}")

    if args.require_contract_perfect and contract["perfect"] is not True:
        return 1
    if (
        args.require_review_package_ready
        and decision["dataset_ready_for_human_review"] is not True
    ):
        return 1
    if args.require_activation_ready and decision["activation_ready"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
