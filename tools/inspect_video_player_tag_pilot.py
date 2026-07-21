#!/usr/bin/env python3
"""Inspect the first VideoPlayer Static/DeepSeek/Grok Tag comparison campaign."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from E2E_test_example_1.video_player_tag_pilot import (  # noqa: E402
    build_video_player_tag_pilot,
    render_video_player_tag_pilot_inspection,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild and inspect the pinned VideoPlayer 15-ReviewUnit Static/DeepSeek/Grok "
            "Tag pilot without reading credentials or calling either provider."
        )
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Render canonical compact JSON instead of indented JSON.",
    )
    args = parser.parse_args(argv)
    try:
        pilot = build_video_player_tag_pilot()
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        render_video_player_tag_pilot_inspection(
            pilot.inspection,
            pretty=not args.compact,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
