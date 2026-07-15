#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.retrieval_validation.lifecycle_blind_holdout import (
    build_lifecycle_holdout_consensus,
    load_lifecycle_holdout_review_packet,
    load_lifecycle_holdout_review_receipt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build lifecycle holdout consensus from exactly two candidate-blind human receipts."
        )
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.receipt) != 2:
        print("Lifecycle blind consensus requires exactly two --receipt files", file=sys.stderr)
        return 2
    try:
        packet = load_lifecycle_holdout_review_packet(args.packet)
        receipts = tuple(load_lifecycle_holdout_review_receipt(path) for path in args.receipt)
        consensus = build_lifecycle_holdout_consensus(packet, receipts)
    except (OSError, ValueError) as exc:
        print(f"Lifecycle blind consensus build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            consensus.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if consensus.release_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
