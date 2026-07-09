from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.models import FileHunk, FileInput


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze ArkTS files into review units.")
    parser.add_argument("files", nargs="+", help="ArkTS source files to analyze.")
    parser.add_argument(
        "--hunk",
        action="append",
        default=[],
        metavar="PATH:START:LINES",
        help="Diff hunk for a file. Example: src/pages/A.ets:40:8",
    )
    parser.add_argument("--token-budget", type=int, default=8000)
    args = parser.parse_args()

    hunks_by_path = _parse_hunks(args.hunk)
    file_inputs: list[FileInput] = []
    for file_name in args.files:
        path = Path(file_name)
        content = path.read_text(encoding="utf-8")
        key = str(path).replace("\\", "/")
        file_inputs.append(
            FileInput(path=key, content=content, hunks=hunks_by_path.get(key, []))
        )

    mode = "diff" if any(item.hunks for item in file_inputs) else "full"
    result = CodeAnalyzer().analyze_files(file_inputs, mode=mode, token_budget=args.token_budget)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def _parse_hunks(raw_hunks: list[str]) -> dict[str, list[FileHunk]]:
    hunks: dict[str, list[FileHunk]] = {}
    for raw in raw_hunks:
        try:
            path, start, lines = raw.rsplit(":", 2)
        except ValueError as exc:
            raise SystemExit(f"Invalid --hunk value: {raw}") from exc
        key = path.replace("\\", "/")
        hunks.setdefault(key, []).append(FileHunk(new_start=int(start), new_lines=int(lines)))
    return hunks


if __name__ == "__main__":
    main()

