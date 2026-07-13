#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from arkts_code_reviewer.knowledge.evaluation import (
    EvaluationKnowledgeBuild,
    build_evaluation_knowledge,
    load_evaluation_annotations_file,
    load_evaluation_extraction_file,
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
            raise ValueError("evaluation output must not already exist") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _counts(build: EvaluationKnowledgeBuild) -> dict[str, int]:
    source_clause_count = sum(
        len(item.rule_ids) for item in build.packet_inventory
    )
    return {
        "source_packets": len(build.packet_inventory),
        "paired_packets": len(build.packet_consensus),
        "missing_packets": len(build.missing_round_packet_ids),
        "source_clauses": source_clause_count,
        "selected_clauses": len(build.clauses),
        "excluded_clauses": len(build.exclusions),
        "api_symbols": len(build.api_symbols),
    }


def _validate_expectations(
    counts: dict[str, int],
    args: argparse.Namespace,
) -> None:
    arguments = {
        "source_packets": args.expect_source_packets,
        "paired_packets": args.expect_paired_packets,
        "missing_packets": args.expect_missing_packets,
        "source_clauses": args.expect_source_clauses,
        "selected_clauses": args.expect_selected_clauses,
        "excluded_clauses": args.expect_excluded_clauses,
        "api_symbols": args.expect_api_symbols,
    }
    mismatches = {
        name: {"expected": expected, "actual": counts[name]}
        for name, expected in arguments.items()
        if expected is not None and counts[name] != expected
    }
    if mismatches:
        raise ValueError(
            "evaluation artifact counts do not match expectations: "
            + json.dumps(mismatches, sort_keys=True)
        )


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use ISO-8601") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-production EvaluationKnowledgeBuild from two audited "
            "Grok campaigns"
        )
    )
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--campaign-base", type=Path, required=True)
    parser.add_argument("--first-round-prefix", required=True)
    parser.add_argument("--second-round-prefix", required=True)
    parser.add_argument("--evaluated-at", type=_parse_datetime, required=True)
    parser.add_argument("--output", type=Path, required=True)
    for name in (
        "source-packets",
        "paired-packets",
        "missing-packets",
        "source-clauses",
        "selected-clauses",
        "excluded-clauses",
        "api-symbols",
    ):
        parser.add_argument(f"--expect-{name}", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        extraction = load_evaluation_extraction_file(args.candidates)
        annotations = load_evaluation_annotations_file(args.annotations)
        result = build_evaluation_knowledge(
            extraction=extraction,
            annotations=annotations,
            packet_root=args.packet_root,
            campaign_base=args.campaign_base,
            first_round_prefix=args.first_round_prefix,
            second_round_prefix=args.second_round_prefix,
            evaluated_at=args.evaluated_at,
        )
        counts = _counts(result)
        _validate_expectations(counts, args)
        _write_exclusive(args.output, result.model_dump_json(indent=2) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        print(f"Evaluation Knowledge build failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "operation": "build-evaluation-knowledge",
                "build_id": result.build_id,
                "production_eligible": result.production_eligible,
                "output": str(args.output),
                **counts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
