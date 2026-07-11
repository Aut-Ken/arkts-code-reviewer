from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.review_unit_validation.golden import (
    TARGET_PHASES,
    evaluate_golden_suite,
    format_golden_report,
    is_perfect,
    load_golden_baseline,
    load_golden_suite,
    make_baseline,
    reports_equal,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "golden" / "review_unit" / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate ReviewUnit selection against its independent Golden Set."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--write-current-baseline",
        type=Path,
        help="Write the complete current report as a baseline; never changes expected truth.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Fail unless the complete report exactly matches this checked-in baseline.",
    )
    parser.add_argument(
        "--require-perfect",
        action="store_true",
        help="Fail when any case differs from its human-reviewed target truth.",
    )
    parser.add_argument(
        "--require-target",
        choices=TARGET_PHASES,
        help="Fail when any case through this implementation phase differs.",
    )
    args = parser.parse_args()

    try:
        if args.json_output:
            _guard_output_path(args.json_output, args.manifest, baseline=False)
        if args.write_current_baseline:
            _guard_output_path(
                args.write_current_baseline,
                args.manifest,
                baseline=True,
            )
        suite = load_golden_suite(args.manifest)
        baseline = (
            load_golden_baseline(args.baseline, suite=suite) if args.baseline else None
        )
        report = evaluate_golden_suite(suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid ReviewUnit Golden input: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(format_golden_report(report))

    if args.json_output:
        _write_json(args.json_output, report)

    if baseline is not None and not reports_equal(report, baseline["report"]):
        print("ReviewUnit Golden report differs from the complete baseline.", file=sys.stderr)
        raise SystemExit(1)
    if args.require_perfect and not is_perfect(report):
        raise SystemExit(1)
    if args.require_target and not is_perfect(report, args.require_target):
        raise SystemExit(1)
    if args.write_current_baseline:
        _write_json(args.write_current_baseline.resolve(), make_baseline(report))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _guard_output_path(
    output_path: Path,
    manifest_path: Path,
    *,
    baseline: bool,
) -> None:
    output = output_path.resolve()
    manifest = manifest_path.resolve()
    golden_root = manifest.parent
    current_baseline_path = golden_root / "baselines" / "current.json"
    current_baseline = current_baseline_path.resolve()
    if baseline:
        if current_baseline_path.is_symlink():
            raise ValueError("refusing to write current baseline through a symlink")
        if output != current_baseline:
            raise ValueError(
                "--write-current-baseline may only write the manifest's "
                "baselines/current.json"
            )
        return
    if output == golden_root or output.is_relative_to(golden_root):
        raise ValueError("--json-output must be outside the ReviewUnit Golden root")


if __name__ == "__main__":
    main()
