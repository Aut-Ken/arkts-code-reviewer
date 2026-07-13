#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.knowledge.parsing.golden_subject import (
    current_knowledge_subject,
)
from arkts_code_reviewer.knowledge_validation.golden import (
    ANNOTATION_FIELDS,
    FULL_FIELDS,
    STRUCTURE_FIELDS,
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Knowledge v1 Golden suite")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/golden/knowledge/manifest.json"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("tests/golden/knowledge/baselines/current.json"),
    )
    parser.add_argument("--write-current", action="store_true")
    parser.add_argument("--strict-baseline", action="store_true")
    parser.add_argument("--require-perfect", action="store_true")
    parser.add_argument(
        "--scope",
        choices=("full", "structure", "annotation"),
        default="full",
        help="K-3 structure compares Clause/API; full also compares K-4 annotations",
    )
    args = parser.parse_args()

    if args.scope != "full" and (args.write_current or args.strict_baseline):
        parser.error("baseline operations require --scope full")

    suite = load_golden_suite(args.manifest)
    fields: tuple[str, ...]
    if args.scope == "structure":
        fields = STRUCTURE_FIELDS
    elif args.scope == "annotation":
        fields = ANNOTATION_FIELDS
    else:
        fields = FULL_FIELDS
    report = evaluate_golden_suite(
        suite,
        current_knowledge_subject,
        implementation="knowledge-annotation-v1",
        fields=fields,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.write_current:
        write_current_baseline(report, suite, args.baseline)
    if args.strict_baseline:
        assert_strict_baseline(report, suite, args.baseline)
    if args.require_perfect and not is_perfect(report):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
