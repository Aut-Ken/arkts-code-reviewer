#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from arkts_code_reviewer.knowledge.review_consensus_build import (
    build_knowledge_review_consensus_campaign,
)


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o644)
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise ValueError("consensus build output must not already exist") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay two Grok campaigns and build deterministic Knowledge consensus"
    )
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--campaign-base", type=Path, required=True)
    parser.add_argument("--first-round-prefix", required=True)
    parser.add_argument("--second-round-prefix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = build_knowledge_review_consensus_campaign(
        packet_root=args.packet_root,
        campaign_base=args.campaign_base,
        first_round_prefix=args.first_round_prefix,
        second_round_prefix=args.second_round_prefix,
    )
    rendered = result.model_dump_json(indent=2) + "\n"
    _write_exclusive(args.output, rendered)
    print(f"wrote {result.build_id} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
