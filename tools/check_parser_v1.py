from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden" / "parser"
DEFAULT_SOURCE_ROOT = REPO_ROOT.parent / "arkui_ace_engine"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Parser v1 release gate."
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument(
        "--include-candidate-diagnostics",
        action="store_true",
        help="Also score the provisional default candidate subset.",
    )
    parser.add_argument(
        "--require-candidate-evidence",
        action="store_true",
        help="Fail unless provisional candidate evidence satisfies the frozen policy.",
    )
    args = parser.parse_args()

    commands: list[tuple[str, list[str]]] = [
        (
            "strict L0 Golden baseline",
            [
                sys.executable,
                "tools/evaluate_parser_golden.py",
                "--parser",
                "lexical",
                "--baseline",
                str(GOLDEN_ROOT / "baselines" / "lexical.json"),
                "--require-layer",
                "L0",
            ],
        ),
        (
            "perfect strict L1 Golden baseline",
            [
                sys.executable,
                "tools/evaluate_parser_golden.py",
                "--parser",
                "arkts-tree-sitter",
                "--baseline",
                str(GOLDEN_ROOT / "baselines" / "arkts-tree-sitter-merged.json"),
                "--require-layer",
                "L1",
                "--require-perfect",
            ],
        ),
        (
            "Golden external snapshot provenance",
            [
                sys.executable,
                "tools/verify_parser_golden_provenance.py",
                "--source-root",
                str(args.source_root),
            ],
        ),
        (
            "R63 L0 robustness gate",
            [
                sys.executable,
                "tools/run_arkts_parser_batch.py",
                "--engine-root",
                str(args.source_root),
                "--parser",
                "lexical",
                "--require-layer",
                "L0",
            ],
        ),
        (
            "R63 L1 robustness gate",
            [
                sys.executable,
                "tools/run_arkts_parser_batch.py",
                "--engine-root",
                str(args.source_root),
                "--parser",
                "arkts-tree-sitter",
                "--require-layer",
                "L1",
            ],
        ),
    ]
    if args.include_candidate_diagnostics:
        commands.append(
            (
                "provisional 23-case candidate diagnostics",
                [
                    sys.executable,
                    "tools/evaluate_parser_candidates.py",
                    "--source-root",
                    str(args.source_root),
                    "--parser",
                    "arkts-tree-sitter",
                    "--require-layer",
                    "L1",
                ],
            )
        )
    if args.require_candidate_evidence:
        commands.append(
            (
                "provisional candidate evidence policy",
                [
                    sys.executable,
                    "tools/audit_parser_candidate_evidence.py",
                    "--source-root",
                    str(args.source_root),
                ],
            )
        )

    for label, command in commands:
        print(f"\n== {label} ==", flush=True)
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": "src"},
            check=False,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)

    print("\nParser v1 deterministic release gate passed.")


if __name__ == "__main__":
    main()
