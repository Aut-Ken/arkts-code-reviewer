#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arkts_code_reviewer.retrieval_validation.golden import (
    evaluate_retrieval_golden,
    render_retrieval_golden_report,
    validate_retrieval_golden_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Retrieval v1 Golden set")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/golden/retrieval/manifest.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    parser.add_argument("--strict-baseline", type=Path)
    args = parser.parse_args()

    try:
        report = evaluate_retrieval_golden(args.manifest)
        rendered = render_retrieval_golden_report(report)
        if args.strict_baseline is not None:
            validate_retrieval_golden_baseline(args.strict_baseline, report)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(rendered, end="")
    if args.output is not None:
        if args.output.is_symlink():
            print("Retrieval Golden output must not be a symlink", file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.require_perfect and not report.perfect:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
