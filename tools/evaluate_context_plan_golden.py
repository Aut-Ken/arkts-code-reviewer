from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from arkts_code_reviewer.context_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "golden" / "context_plan" / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the independent RU-5 ContextPlan Golden fixtures."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--write-current-baseline",
        type=Path,
        help="Write current behavior only; never changes human-reviewed expected truth.",
    )
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()

    try:
        suite = load_golden_suite(args.manifest)
        if args.json_output:
            _guard_json_output(args.json_output, suite.manifest_path.parent)
        report = evaluate_golden_suite(suite)
        if args.baseline:
            assert_strict_baseline(report, suite, args.baseline)
        if args.write_current_baseline:
            write_current_baseline(report, suite, args.write_current_baseline)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Invalid ContextPlan Golden input: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(_format_report(report))
    if args.json_output:
        args.json_output.resolve().write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.require_perfect and not is_perfect(report, suite):
        raise SystemExit(1)


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        f"ContextPlan Golden: {report['case_count']} cases",
        f"  matched: {report['matched_case_count']}",
        f"  mismatched: {report['mismatched_case_count']}",
        f"  primary coverage: {report['metrics']['primary_coverage']:.3f}",
        f"  relation precision/recall: "
        f"{report['metrics']['relation_precision']:.3f}/"
        f"{report['metrics']['relation_recall']:.3f}",
        f"  required context recall: "
        f"{report['metrics']['required_context_recall_at_budget']:.3f}",
        f"  required context insufficient: "
        f"{int(report['metrics']['required_context_insufficient_count'])}",
        f"  distractor rejection: {report['metrics']['distractor_rejection']:.3f}",
        f"  dispatchable budget utilization: "
        f"{report['metrics']['budget_utilization']:.3f}",
        f"  input-order stability: "
        f"{report['metrics']['input_order_stability']:.3f}",
    ]
    for case in report["cases"]:
        if not case["matched"]:
            lines.append(f"  - {case['case_id']}: mismatch")
            lines.extend(f"      {item}" for item in case["differences"][:5])
            lines.extend(
                f"      invariant: {item}"
                for item in case["invariant_violations"][:5]
            )
    return "\n".join(lines)


def _guard_json_output(path: Path, golden_root: Path) -> None:
    output = path.resolve()
    root = golden_root.resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError("--json-output must stay outside the ContextPlan Golden root")


if __name__ == "__main__":
    main()
