#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_TAGS = (
    REPO_ROOT / "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml"
)


def _load_standard_library_preflight() -> Callable[..., tuple[str, str]]:
    namespace = runpy.run_path(str(REPO_ROOT / "tools/lifecycle_holdout_preflight.py"))
    preflight = namespace.get("preflight_formal_holdout")
    if not callable(preflight):
        raise ValueError("standard-library holdout preflight entry point is unavailable")
    return cast(Callable[..., tuple[str, str]], preflight)


def _evaluation_id(report: Mapping[str, object]) -> str:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"lifecycle-owner-role-holdout-evaluation:sha256:{hashlib.sha256(payload).hexdigest()}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen owner-aware lifecycle candidate only after an independent "
            "selection, two review receipts, and consensus have been committed and sealed."
        )
    )
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--consensus", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--seal-revision", required=True)
    parser.add_argument("--omit-cases", action="store_true")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="return zero for a non-ready report; default machine-gate behavior returns one",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.receipt) != 2:
        print("Lifecycle holdout evaluation requires exactly two --receipt files", file=sys.stderr)
        return 2
    try:
        preflight_formal_holdout = _load_standard_library_preflight()
        sealed_revision, dependency_site_packages = preflight_formal_holdout(
            repository_root=REPO_ROOT,
            seal_revision=args.seal_revision,
            artifact_paths=(
                args.selection,
                args.packet,
                *args.receipt,
                args.consensus,
            ),
        )
        sys.path.insert(0, str((REPO_ROOT / "src").resolve(strict=True)))
        sys.path.append(dependency_site_packages)
        from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout_evaluation import (
            evaluate_lifecycle_owner_role_holdout,
        )

        report = evaluate_lifecycle_owner_role_holdout(
            selection_path=args.selection,
            packet_path=args.packet,
            receipt_paths=args.receipt,
            consensus_path=args.consensus,
            source_root=args.source_root,
            repository_root=REPO_ROOT,
            candidate_tags_path=DEFAULT_CANDIDATE_TAGS,
            seal_revision=sealed_revision,
        )
    except Exception as exc:
        print(f"Lifecycle owner-role holdout evaluation failed: {exc}", file=sys.stderr)
        return 2
    if args.omit_cases:
        report.pop("cases", None)
    report["case_details_omitted"] = args.omit_cases
    report["evaluation_id"] = _evaluation_id(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    evidence_gate = cast(Mapping[str, object], report["evidence_gate"])
    if not args.report_only and evidence_gate.get("evidence_ready") is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
