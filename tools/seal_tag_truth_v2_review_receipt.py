#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2ReviewReceipt,
    review_receipt_payload_with_id,
    validate_tag_truth_v2_review_receipt,
)

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import canonical_json
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    load_tag_truth_v2_review_packet,
)


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
            "Seal one independently authored Tag Truth v2 review receipt. This command "
            "validates decisions but never creates labels or runs the candidate."
        )
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--draft", type=Path, required=True)
    return parser


def _load_draft(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"review draft must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Tag Truth v2 review draft {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Tag Truth v2 review draft must be a JSON object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        packet = load_tag_truth_v2_review_packet(args.packet)
        payload = review_receipt_payload_with_id(_load_draft(args.draft))
        receipt = TagTruthV2ReviewReceipt.model_validate_json(canonical_json(payload))
        validate_tag_truth_v2_review_receipt(receipt, packet)
    except (OSError, ValueError) as exc:
        print(f"Tag Truth v2 review receipt seal failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
