#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arkts_code_reviewer.knowledge.sample_guidance import (
    build_sample_guidance,
    render_sample_guidance_build,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests/fixtures/applications_app_samples_v1.json"
DEFAULT_CHECKOUT = Path("/home/autken/Code/applications_app_samples")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build non-normative searchable passages from whitelisted app sample READMEs.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--checkout", type=Path, default=DEFAULT_CHECKOUT)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        build = build_sample_guidance(arguments.manifest, arguments.checkout)
        rendered = render_sample_guidance_build(build)
        if arguments.output is not None:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
