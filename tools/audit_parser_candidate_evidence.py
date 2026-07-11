from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.parser_validation.candidates import (  # noqa: E402
    DEFAULT_REGISTRY,
    DEFAULT_REVIEWED_GROUPS,
    audit_candidate_evidence,
    load_candidate_suite,
)

DEFAULT_CANDIDATE_DIR = REPO_ROOT / "tests" / "Grok_Expected"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit provisional Parser candidate evidence against the frozen policy."
    )
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument(
        "--groups",
        nargs="+",
        default=list(DEFAULT_REVIEWED_GROUPS),
        help="Candidate groups, separated by spaces or commas.",
    )
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
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
        report = audit_candidate_evidence(suite, args.candidate_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    counts = Counter(issue["code"] for issue in report["issues"])
    print("Parser candidate evidence audit")
    print(f"  truth_status: {report['truth_status']}")
    print(f"  groups: {report['groups']}")
    print(f"  issue_count: {report['issue_count']}")
    print(f"  issue_codes: {dict(sorted(counts.items()))}")
    print(f"  suite_fingerprint: {report['suite_fingerprint']}")
    print(f"  annotation_fingerprint: {report['annotation_fingerprint']}")

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if report["issue_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
