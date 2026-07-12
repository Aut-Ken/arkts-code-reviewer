from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from arkts_code_reviewer.file_analysis_validation.golden import (
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "golden" / "file_analysis" / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate Parser v2 FileAnalysis against its independent Golden Set."
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
    except (OSError, ValueError, TypeError) as exc:
        print(f"Invalid FileAnalysis Golden input: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(_format_report(report))
    if args.json_output:
        args.json_output.resolve().write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.require_perfect and not is_perfect(report):
        raise SystemExit(1)


def _format_report(report: dict[str, object]) -> str:
    lines = [
        f"FileAnalysis Golden: {report['case_count']} cases",
        f"  matched: {report['matched_case_count']}",
        f"  mismatched: {report['mismatched_case_count']}",
    ]
    for case in report["cases"]:  # type: ignore[union-attr]
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
        raise ValueError("--json-output must stay outside the FileAnalysis Golden root")


if __name__ == "__main__":
    main()
