#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    build_tag_truth_v2_consensus,
    load_tag_truth_v2_review_receipt,
)

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    load_tag_truth_v2_review_packet,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build Tag Truth v2 consensus from exactly two candidate-blind human receipts."
        )
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.receipt) != 2:
        print("Tag Truth v2 consensus requires exactly two --receipt files", file=sys.stderr)
        return 2
    try:
        packet = load_tag_truth_v2_review_packet(args.packet)
        receipts = tuple(load_tag_truth_v2_review_receipt(path) for path in args.receipt)
        consensus = build_tag_truth_v2_consensus(packet, receipts)
    except (OSError, ValueError) as exc:
        print(f"Tag Truth v2 consensus build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            consensus.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if consensus.consensus_status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
