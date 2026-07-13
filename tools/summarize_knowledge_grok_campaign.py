#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arkts_code_reviewer.knowledge.review_campaign import (
    summarize_knowledge_grok_campaign,
)

DEFAULT_PACKET_ROOT = Path(
    "/home/autken/Code/arkts-review-data/reports/knowledge-review/"
    "knowledge-seed-v1-grok-4.5-auditor-v4"
)
DEFAULT_CAMPAIGN_BASE = Path(
    "/home/autken/Code/arkts-review-data/reports/knowledge-review-responses/"
    "knowledge-seed-v1/grok-4.5/auditor-v4"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay and summarize a fail-closed Grok Knowledge audit campaign"
    )
    parser.add_argument("--packet-root", type=Path, default=DEFAULT_PACKET_ROOT)
    parser.add_argument("--campaign-base", type=Path, default=DEFAULT_CAMPAIGN_BASE)
    parser.add_argument("--round-prefix", default="round-1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = summarize_knowledge_grok_campaign(
        packet_root=args.packet_root,
        campaign_base=args.campaign_base,
        round_prefix=args.round_prefix,
    )
    rendered = summary.model_dump_json(indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        except FileExistsError as exc:
            raise ValueError(
                "campaign summary output must be a new regular file"
            ) from exc
        print(f"wrote {summary.summary_id} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
