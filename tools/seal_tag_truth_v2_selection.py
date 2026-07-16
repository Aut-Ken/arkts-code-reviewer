#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    load_tag_truth_v2_development_exclusion_snapshot,
    seal_tag_truth_v2_selection_payload,
    verify_tag_truth_v2_development_exclusions,
    verify_tag_truth_v2_selection_checkout,
    verify_tag_truth_v2_selection_exposure,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_draft(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"selection draft must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Tag Truth v2 selection draft {path}: {exc}") from exc
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise ValueError("Tag Truth v2 selection draft must be an object with string keys")
    return cast(dict[str, object], payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seal an independently prepared, unlabeled Tag Truth v2 selection. "
            "This command never selects cases, loads candidate configuration, or runs a matcher."
        )
    )
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        selection = seal_tag_truth_v2_selection_payload(_load_draft(args.draft))
        development_truth = load_tag_truth_v2_development_exclusion_snapshot(DEVELOPMENT_TRUTH)
        verify_tag_truth_v2_development_exclusions(selection, development_truth, args.source_root)
        verify_tag_truth_v2_selection_checkout(selection, args.source_root)
        verify_tag_truth_v2_selection_exposure(selection, args.source_root)
    except (OSError, ValueError) as exc:
        print(f"Tag Truth v2 selection seal failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            selection.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
