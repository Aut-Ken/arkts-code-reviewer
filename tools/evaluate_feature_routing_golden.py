#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "tests/golden/feature_routing/manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the independent Feature Routing Golden fixtures."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-current-baseline", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    suite = load_golden_suite(args.manifest)
    report = evaluate_golden_suite(suite)
    if args.json_output is not None:
        args.json_output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if args.write_current_baseline is not None:
        write_current_baseline(report, suite, args.write_current_baseline)
    if args.baseline is not None:
        assert_strict_baseline(report, suite, args.baseline)

    metrics = report["metrics"]
    print(f"Feature Routing Golden: {report['case_count']} cases")
    print(f"  matched: {report['matched_case_count']}")
    print(f"  mismatched: {report['mismatched_case_count']}")
    print(
        "  exact tag precision/recall: "
        f"{metrics['exact_tag_precision']:.3f}/{metrics['exact_tag_recall']:.3f}"
    )
    print(
        "  routing tag precision/recall: "
        f"{metrics['routing_tag_precision']:.3f}/{metrics['routing_tag_recall']:.3f}"
    )
    print(
        "  dimension precision/recall: "
        f"{metrics['dimension_precision']:.3f}/{metrics['dimension_recall']:.3f}"
    )
    print(f"  input-order stability: {metrics['input_order_stability']:.3f}")
    for row in report["cases"]:
        if row["matched"] is not True:
            print(f"  {row['case_id']}:")
            for difference in row["differences"][:8]:
                print(f"    - {difference}")
    if args.require_perfect and not is_perfect(report, suite):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
