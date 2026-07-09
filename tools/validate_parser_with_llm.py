from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from arkts_code_reviewer.code_analysis.parser_factory import PARSER_CHOICES  # noqa: E402
from arkts_code_reviewer.parser_validation.glm_judge import (  # noqa: E402
    DryRunJudgeClient,
    GlmJudgeClient,
    result_to_json_line,
)
from arkts_code_reviewer.parser_validation.manifest import (  # noqa: E402
    load_manifest,
    select_samples,
)
from arkts_code_reviewer.parser_validation.packager import build_validation_request  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "arkui_ace_engine_samples.json"
DEFAULT_ENGINE_ROOT = REPO_ROOT.parent / "arkui_ace_engine"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "parser_validation"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ArkTS parser output with GLM judge.")
    parser.add_argument("--engine-root", type=Path, default=DEFAULT_ENGINE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--parser", choices=PARSER_CHOICES, default="arkts-tree-sitter")
    parser.add_argument("--category")
    parser.add_argument("--sample-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-source-lines", type=int, default=240)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--retry-attempts", type=int, default=None)
    parser.add_argument("--retry-base-delay-seconds", type=float, default=None)
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--pretty-output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    samples = select_samples(
        load_manifest(args.manifest),
        category=args.category,
        sample_id=args.sample_id,
        limit=args.limit,
    )
    output_path = args.output or _default_output_path(args.parser)
    completed = _completed_sample_ids(output_path) if args.resume else set()
    client = (
        DryRunJudgeClient()
        if args.dry_run
        else GlmJudgeClient(
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.max_tokens,
            retry_attempts=args.retry_attempts,
            retry_base_delay_seconds=args.retry_base_delay_seconds,
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    with output_path.open("a", encoding="utf-8") as output:
        for index, sample in enumerate(samples, start=1):
            if sample.sample_id in completed or sample.path in completed:
                _log(f"[{index}/{len(samples)}] skip completed {sample.sample_id}")
                skipped += 1
                continue
            _log(f"[{index}/{len(samples)}] package {sample.sample_id} ({sample.path})")
            request = build_validation_request(
                engine_root=args.engine_root,
                sample=sample,
                max_source_lines=args.max_source_lines,
                parser_name=args.parser,
            )
            _log(
                f"[{index}/{len(samples)}] validate {sample.sample_id} "
                f"parser_layer={request.parser_output.parser_layer}"
            )
            if processed > 0 and args.request_delay_seconds > 0:
                _log(f"sleep {args.request_delay_seconds:.1f}s before next GLM request")
                time.sleep(args.request_delay_seconds)
            result = client.validate(request)
            output.write(result_to_json_line(result) + "\n")
            output.flush()
            processed += 1
            _log(
                f"[{index}/{len(samples)}] wrote {sample.sample_id} "
                f"verdict={result.verdict} findings={len(result.findings)}"
            )

    if args.pretty_output:
        pretty_count = _write_pretty_output(output_path, args.pretty_output)
        _log(f"wrote pretty output {args.pretty_output} rows={pretty_count}")

    print(
        json.dumps(
            {
                "selected": len(samples),
                "processed": processed,
                "skipped": skipped,
                "parser": args.parser,
                "output": str(output_path),
                "pretty_output": str(args.pretty_output) if args.pretty_output else None,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _default_output_path(parser_name: str) -> Path:
    normalized = parser_name.replace("-", "_")
    return DEFAULT_OUTPUT_DIR / f"glm_findings_{normalized}.jsonl"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _write_pretty_output(jsonl_path: Path, pretty_path: Path) -> int:
    rows: list[object] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    pretty_path.parent.mkdir(parents=True, exist_ok=True)
    pretty_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(rows)


def _completed_sample_ids(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    completed: set[str] = set()
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "sample_id" in data:
            completed.add(str(data["sample_id"]))
        if "source_path" in data:
            completed.add(str(data["source_path"]))
    return completed


if __name__ == "__main__":
    main()
