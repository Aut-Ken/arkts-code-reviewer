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
from arkts_code_reviewer.parser_validation.candidates import (  # noqa: E402
    DEFAULT_REGISTRY,
    DEFAULT_REVIEWED_GROUPS,
    evaluate_candidate_suite,
    load_candidate_suite,
)
from arkts_code_reviewer.parser_validation.golden import format_golden_report  # noqa: E402

DEFAULT_CANDIDATE_DIR = REPO_ROOT / "tests" / "Grok_Expected"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Provisionally evaluate an ArkTS parser against unreviewed Grok candidate labels."
        )
    )
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument(
        "--groups",
        nargs="+",
        default=list(DEFAULT_REVIEWED_GROUPS),
        help="Candidate groups, separated by spaces or commas.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Explicit pinned arkui_ace_engine checkout; takes priority over --registry.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="Source registry used when --source-root is omitted.",
    )
    parser.add_argument("--parser", choices=PARSER_CHOICES, default="lexical")
    parser.add_argument(
        "--require-layer",
        choices=("L0", "L1", "parse_degraded"),
        help="Exit non-zero unless every successful case used this parser layer.",
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    groups = tuple(
        group.strip()
        for value in args.groups
        for group in value.split(",")
        if group.strip()
    )
    try:
        suite = load_candidate_suite(
            args.candidate_dir,
            groups=groups,
            source_root=args.source_root,
            registry_path=args.registry,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(
        "WARNING: candidate_unreviewed labels; this report is provisional and cannot be used "
        "as a strict Parser Golden baseline.",
        file=sys.stderr,
    )
    report = evaluate_candidate_suite(suite, create_code_parser(args.parser))
    print(format_golden_report(report))
    print(f"  evaluation_status: {report['evaluation_status']}")
    print(f"  suite_fingerprint: {report['suite_fingerprint']}")

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if report["crashed"]:
        raise SystemExit(1)
    if args.require_layer and report["parser_layers"] != {
        args.require_layer: report["case_count"]
    }:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
