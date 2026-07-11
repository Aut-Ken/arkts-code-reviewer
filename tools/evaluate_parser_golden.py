from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.code_analysis.parser_factory import (  # noqa: E402
    PARSER_CHOICES,
    create_code_parser,
)
from arkts_code_reviewer.parser_validation.golden import (  # noqa: E402
    evaluate_golden_suite,
    format_golden_report,
    load_golden_baseline,
    load_golden_suite,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "golden" / "parser" / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate an ArkTS parser against the deterministic Parser Golden Set."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--parser", choices=PARSER_CHOICES, default="lexical")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Fail unless the complete report exactly matches this checked-in baseline.",
    )
    parser.add_argument(
        "--require-perfect",
        action="store_true",
        help="Exit non-zero when any false positive, false negative, or forbidden fact exists.",
    )
    parser.add_argument(
        "--require-layer",
        choices=("L0", "L1", "parse_degraded"),
        help="Exit non-zero unless every successfully evaluated case used this parser layer.",
    )
    args = parser.parse_args()

    suite = load_golden_suite(args.manifest)
    baseline = None
    if args.baseline:
        baseline_parser_id = (
            "arkts-tree-sitter-merged" if args.parser == "arkts-tree-sitter" else args.parser
        )
        try:
            baseline = load_golden_baseline(
                args.baseline,
                suite=suite,
                parser_id=baseline_parser_id,
                sidecar_root=(
                    REPO_ROOT / "sidecars" / "arkts-parser"
                    if args.parser == "arkts-tree-sitter"
                    else None
                ),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Invalid Parser Golden baseline: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
    report = evaluate_golden_suite(suite, create_code_parser(args.parser))
    print(format_golden_report(report))

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if report["crashed"]:
        raise SystemExit(1)
    if baseline is not None and report != baseline["report"]:
        print("Parser Golden report differs from the complete baseline.", file=sys.stderr)
        raise SystemExit(1)
    if args.require_layer and report["parser_layers"] != {args.require_layer: report["case_count"]}:
        raise SystemExit(1)
    if args.require_perfect and not _is_perfect(report):
        raise SystemExit(1)


def _is_perfect(report: dict[str, object]) -> bool:
    fields = report["fields"]
    if not isinstance(fields, dict):
        return False
    return report["must_not_violation_count"] == 0 and all(
        isinstance(score, dict) and score.get("fp") == 0 and score.get("fn") == 0
        for score in fields.values()
    )


if __name__ == "__main__":
    main()
