#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arkts_code_reviewer.retrieval_validation.tag_retrieval_fixture import (
    load_tag_retrieval_knowledge_fixture,
    load_tag_retrieval_truth,
    observe_tag_retrieval_truth,
    tag_retrieval_knowledge_fingerprint,
    tag_retrieval_truth_fingerprint,
    verify_tag_retrieval_knowledge_checkout,
    verify_tag_retrieval_truth_checkout,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUTH = REPO_ROOT / "tests/evaluation/tag_retrieval/manifest.json"
DEFAULT_KNOWLEDGE = REPO_ROOT / "tests/evaluation/tag_retrieval/knowledge_fixture.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the provisional Active Tag Retrieval Truth and official-docs "
            "knowledge fixture against pinned read-only checkouts."
        )
    )
    parser.add_argument("--truth-manifest", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--knowledge-fixture", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument(
        "--skip-observation",
        action="store_true",
        help="verify identities and content only; do not run Parser-to-Tag observation",
    )
    parser.add_argument(
        "--include-cases",
        action="store_true",
        help="include all 48 per-case observation rows in JSON output",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        truth = load_tag_retrieval_truth(args.truth_manifest)
        truth_checkout = verify_tag_retrieval_truth_checkout(truth, args.source_root)
        knowledge = load_tag_retrieval_knowledge_fixture(args.knowledge_fixture)
        knowledge_checkout = verify_tag_retrieval_knowledge_checkout(
            knowledge,
            args.docs_root,
        )
        observation = (
            None if args.skip_observation else observe_tag_retrieval_truth(truth, truth_checkout)
        )
        if observation is not None and not args.include_cases:
            observation = {key: value for key, value in observation.items() if key != "cases"}
    except (OSError, ValueError) as exc:
        print(f"Tag Retrieval fixture check failed: {exc}", file=sys.stderr)
        return 2

    report = {
        "schema_version": "tag-retrieval-fixture-check-v1",
        "truth": {
            "suite_id": truth.suite_id,
            "truth_status": truth.truth_status,
            "fingerprint": tag_retrieval_truth_fingerprint(truth),
            "source_count": len(truth_checkout.source_text_by_alias),
            "case_count": len(truth.cases),
        },
        "knowledge": {
            "fixture_id": knowledge.fixture_id,
            "fixture_role": knowledge.fixture_role,
            "truth_status": knowledge.truth_status,
            "source_authority": knowledge.source_authority,
            "fingerprint": tag_retrieval_knowledge_fingerprint(knowledge),
            "document_count": len(knowledge_checkout.document_bytes_by_alias),
            "clause_count": len(knowledge.clauses),
        },
        "observation": observation,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
