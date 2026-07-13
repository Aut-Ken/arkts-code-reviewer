from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.models import (
    AnnotationChange,
    ClauseModelReview,
    KnowledgeClause,
    KnowledgeModelReview,
    ModelReviewEvidence,
)

ROOT = Path(__file__).resolve().parents[1]


def test_exported_knowledge_schemas_match_models() -> None:
    clause_schema = json.loads(
        (ROOT / "schemas/knowledge/knowledge-clause.schema.json").read_text(encoding="utf-8")
    )
    review_schema = json.loads(
        (ROOT / "schemas/knowledge/grok-review-output.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert clause_schema == KnowledgeClause.model_json_schema()
    assert review_schema == KnowledgeModelReview.model_json_schema()


def test_non_accept_model_review_requires_source_evidence() -> None:
    with pytest.raises(ValidationError, match="requires issue codes and evidence"):
        ClauseModelReview(
            rule_id="RESOURCE/TIMER/R-01",
            decision="uncertain",
            issue_codes=(),
            evidence=(),
            annotation_changes=(),
            rationale="The packet does not provide version evidence.",
        )


def test_accept_model_review_rejects_hidden_changes() -> None:
    evidence = ModelReviewEvidence(
        source_id="openharmony-docs",
        relative_path="zh-cn/example.md",
        start_line=4,
        end_line=4,
        exact_quote="example",
    )
    change = AnnotationChange(
        annotation_kind="tag",
        current_value="has_async",
        proposed_action="remove",
        proposed_value=None,
        reason_code="tag_not_applicable",
    )
    with pytest.raises(ValidationError, match="accepted Clause review must not carry"):
        ClauseModelReview(
            rule_id="RESOURCE/TIMER/R-01",
            decision="accept",
            issue_codes=("tag_error",),
            evidence=(evidence,),
            annotation_changes=(change,),
            rationale="The candidate requires a Tag correction.",
        )


def test_unknown_grok_model_cannot_accept_packet() -> None:
    payload = {
        "schema_version": "knowledge-model-review-v1",
        "packet_id": "knowledge-review-packet:sha256:" + "0" * 64,
        "reviewer": {
            "kind": "model",
            "provider": "xai",
            "model": "unknown",
            "prompt_version": "grok-knowledge-auditor-v1",
        },
        "packet_decision": "accept",
        "clause_reviews": [],
        "missing_clauses": [],
        "duplicate_groups": [],
        "conflicts": [],
        "summary": {
            "accepted": 0,
            "rejected": 0,
            "uncertain": 0,
            "with_corrections": 0,
        },
    }
    with pytest.raises(ValidationError, match="packet_decision does not match"):
        KnowledgeModelReview.model_validate(payload)
