from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.parser_validation.golden import (  # noqa: E402
    load_golden_suite,
    verify_external_snapshot_provenance,
)

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "golden" / "parser" / "manifest.json"
DEFAULT_SOURCE_ROOT = REPO_ROOT.parent / "arkui_ace_engine"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Parser Golden external snapshots against the pinned checkout."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    try:
        suite = load_golden_suite(args.manifest)
        verified = verify_external_snapshot_provenance(suite, args.source_root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    report = {
        "schema_version": "parser-golden-provenance-v1",
        "suite_id": suite.suite_id,
        "source_root": str(args.source_root.resolve()),
        "verified_count": len(verified),
        "snapshots": verified,
    }
    print("Parser Golden provenance report")
    print(f"  suite_id: {report['suite_id']}")
    print(f"  verified_count: {report['verified_count']}")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
