#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arkts_code_reviewer.retrieval.embeddings import FastEmbedProvider
from arkts_code_reviewer.retrieval.runtime import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
)
from arkts_code_reviewer.retrieval_validation.embedding_candidate import (
    evaluate_embedding_candidate,
    render_embedding_candidate_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a real local embedding model on Retrieval hybrid Golden cases"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/golden/retrieval/manifest.json"),
    )
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--dimensions", type=int, default=DEFAULT_EMBEDDING_DIMENSIONS)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache/arkts-code-reviewer/fastembed",
    )
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--require-thresholds", action="store_true")
    parser.add_argument("--min-recall-at-5", type=float, default=0.80)
    parser.add_argument("--min-precision-at-5", type=float, default=0.65)
    parser.add_argument("--min-mrr", type=float, default=0.80)
    parser.add_argument("--max-forbidden-hits", type=int, default=0)
    args = parser.parse_args()
    try:
        provider = FastEmbedProvider(
            model_id=args.model,
            dimensions=args.dimensions,
            cache_dir=args.cache,
            local_files_only=args.local_files_only,
        )
        report = evaluate_embedding_candidate(args.manifest, provider)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Embedding candidate evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(render_embedding_candidate_report(report), end="")
    if args.require_thresholds and (
        report.recall_at_5 < args.min_recall_at_5
        or report.precision_at_5 < args.min_precision_at_5
        or report.mrr < args.min_mrr
        or report.forbidden_hits > args.max_forbidden_hits
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
