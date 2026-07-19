#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    load_ai_tag_shadow_campaign_inspection,
    render_ai_tag_shadow_campaign_inspection,
)

_MAX_INSPECTION_BYTES = 4_000_000


def _load_inspection(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("campaign inspection path must not be a symlink")
    if not path.is_file():
        raise ValueError("campaign inspection path must identify a regular file")
    if path.stat().st_size > _MAX_INSPECTION_BYTES:
        raise ValueError("campaign inspection exceeds the CLI size limit")
    return path.read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and render a metadata-only AI Tag shadow campaign inspection. "
            "This tool has no execution or credential path."
        )
    )
    parser.add_argument(
        "--inspection",
        type=Path,
        required=True,
        help="Path to one ai-tag-shadow-campaign-inspection-v1 JSON object.",
    )
    args = parser.parse_args(argv)
    try:
        inspection = load_ai_tag_shadow_campaign_inspection(
            _load_inspection(args.inspection)
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(render_ai_tag_shadow_campaign_inspection(inspection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
