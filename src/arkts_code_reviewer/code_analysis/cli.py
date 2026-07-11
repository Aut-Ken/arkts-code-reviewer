from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.models import AnalysisMode, FileHunk, FileInput


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

    source_root = Path.cwd().resolve()
    hunks_by_path = _parse_hunks(args.hunk, source_root=source_root)
    file_inputs: list[FileInput] = []
    source_paths: dict[str, str] = {}
    for file_name in args.files:
        path, key = _resolve_source_path(file_name, source_root=source_root)
        previous = source_paths.get(key)
        if previous is not None:
            raise SystemExit(
                f"Duplicate source path {key!r}: {previous!r} and {file_name!r}"
            )
        source_paths[key] = file_name
        content = path.read_text(encoding="utf-8")
        file_inputs.append(
            FileInput(path=key, content=content, hunks=hunks_by_path.get(key, []))
        )

    unknown_hunk_paths = sorted(set(hunks_by_path) - set(source_paths))
    if unknown_hunk_paths:
        raise SystemExit(
            "--hunk path is not present in the source files: "
            + ", ".join(repr(path) for path in unknown_hunk_paths)
        )

    mode: AnalysisMode = "diff" if any(item.hunks for item in file_inputs) else "full"
    result = CodeAnalyzer().analyze_files(file_inputs, mode=mode, token_budget=args.token_budget)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def _parse_hunks(
    raw_hunks: list[str],
    *,
    source_root: Path | None = None,
) -> dict[str, list[FileHunk]]:
    root = (source_root or Path.cwd()).resolve()
    hunks: dict[str, list[FileHunk]] = {}
    for raw in raw_hunks:
        try:
            path, start, lines = raw.rsplit(":", 2)
            hunk = FileHunk(new_start=int(start), new_lines=int(lines))
        except ValueError as exc:
            raise SystemExit(f"Invalid --hunk value: {raw}") from exc
        _, key = _resolve_source_path(path, source_root=root)
        hunks.setdefault(key, []).append(hunk)
    return hunks


def _resolve_source_path(raw_path: str, *, source_root: Path) -> tuple[Path, str]:
    """Resolve a CLI path while retaining only its cwd-relative logical identity."""

    portable_input = raw_path.replace("\\", "/")
    candidate = Path(portable_input)
    if not candidate.is_absolute():
        candidate = source_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Invalid source path {raw_path!r}: {exc}") from exc
    if not resolved.is_file():
        raise SystemExit(f"Source path is not a file: {raw_path!r}")
    try:
        relative = resolved.relative_to(source_root)
    except ValueError as exc:
        raise SystemExit(
            f"Source path must stay inside the current working directory: {raw_path!r}"
        ) from exc
    logical_path = relative.as_posix()
    if logical_path in {"", "."}:
        raise SystemExit(f"Source path must identify a file below the cwd: {raw_path!r}")
    return resolved, logical_path


if __name__ == "__main__":
    main()
