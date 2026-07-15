#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    build_lifecycle_owner_role_candidate_freeze,
    seal_lifecycle_holdout_selection_payload,
    verify_approved_selection_policy,
    verify_candidate_corpus_independence,
    verify_candidate_runtime_bundle,
    verify_evaluation_harness_bundle,
    verify_lifecycle_holdout_checkout,
    verify_selection_development_exclusions,
)
from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    load_tag_retrieval_truth,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seal and validate a candidate-output-blind lifecycle selection, or print "
            "the frozen candidate runtime token that an independent custodian must bind."
        )
    )
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--print-candidate-freeze", action="store_true")
    return parser


def _load_draft(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"selection draft must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid lifecycle selection draft {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("lifecycle selection draft must be a JSON object")
    if not all(isinstance(key, str) for key in payload):
        raise ValueError("lifecycle selection draft keys must be strings")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        freeze = build_lifecycle_owner_role_candidate_freeze(REPO_ROOT)
        if args.print_candidate_freeze:
            print(
                json.dumps(
                    freeze.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.draft is None or args.source_root is None:
            raise ValueError("--draft and --source-root are required when sealing a selection")
        draft = _load_draft(args.draft)
        selection = seal_lifecycle_holdout_selection_payload(draft)
        if selection.candidate_freeze != freeze:
            raise ValueError("selection candidate freeze does not match the frozen runtime token")
        development_truth = load_tag_retrieval_truth(DEFAULT_DEVELOPMENT_TRUTH)
        verify_selection_development_exclusions(selection, development_truth)
        verify_approved_selection_policy(selection, REPO_ROOT)
        verify_candidate_runtime_bundle(selection.candidate_freeze, REPO_ROOT)
        verify_evaluation_harness_bundle(selection.candidate_freeze, REPO_ROOT)
        verify_lifecycle_holdout_checkout(selection, args.source_root)
        verify_candidate_corpus_independence(selection, args.source_root)
        emitted = selection.model_dump(mode="json")
    except (OSError, ValueError) as exc:
        print(f"Lifecycle blind selection seal failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(emitted, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
