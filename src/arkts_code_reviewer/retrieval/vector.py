from __future__ import annotations

import math
from dataclasses import dataclass, replace

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.config import RetrievalConfig
from arkts_code_reviewer.retrieval.index import EmbeddingProvider
from arkts_code_reviewer.retrieval.models import (
    ApplicabilityResult,
    EvidenceMatch,
    KnowledgeIndex,
    KnowledgeIndexRecord,
    RetrievalUnitRequest,
    TargetPlatform,
)


@dataclass(frozen=True)
class VectorHit:
    record: KnowledgeIndexRecord
    rank: int
    similarity: float
    matched_by: tuple[EvidenceMatch, ...]
    applicability: ApplicabilityResult
    dimension_overlap: int
    authority_priority: int

    def __post_init__(self) -> None:
        if self.rank < 1 or not math.isfinite(self.similarity):
            raise ValueError("Vector hit rank and score are invalid")
        if self.applicability not in {"applicable", "unknown"}:
            raise ValueError("Vector hit cannot retain an excluded Clause")


def query_embedding_text(unit: RetrievalUnitRequest) -> str:
    # Put real changed code first. A generic always-bound correctness question
    # can dominate small text encoders and make unrelated Clauses look alike,
    # so it remains in the auditable intent but is not repeated in the vector
    # query when code is available. Dimensions are intentionally absent: they
    # are policy/coverage, not semantic evidence.
    parts = (
        [unit.intent_summary]
        if unit.semantic_code_excerpt is None
        else [unit.semantic_code_excerpt]
    )
    signals = unit.exact_signals
    for label, values in (
        ("apis", signals.apis),
        ("components", signals.components),
        ("decorators", signals.decorators),
        ("attributes", signals.attributes),
        ("symbols", signals.symbols),
        ("syntax", signals.syntax),
        ("calls", signals.calls),
        ("resources", signals.resource_references),
    ):
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    feature_config = load_default_feature_config()
    tag_descriptions = tuple(
        feature_config.tags_by_id[tag_id].description for tag_id in unit.exact_tags
    )
    if tag_descriptions:
        parts.append(f"code features: {', '.join(tag_descriptions)}")
    specific_questions = tuple(
        feature_config.review_questions_by_id[question_id].title
        for question_id in unit.review_question_ids
        if question_id != "RQ-correctness"
    )
    if specific_questions:
        parts.append(f"review focus: {', '.join(specific_questions)}")
    return "\n".join(parts)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("cosine vectors must have the same non-zero dimensions")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def search_vector(
    index: KnowledgeIndex,
    unit: RetrievalUnitRequest,
    target: TargetPlatform,
    config: RetrievalConfig,
    embedding_provider: EmbeddingProvider,
) -> tuple[VectorHit, ...]:
    signals = unit.exact_signals
    if not any(
        (
            signals.apis,
            signals.components,
            signals.decorators,
            signals.attributes,
            signals.symbols,
            signals.syntax,
            signals.calls,
            signals.resource_references,
            unit.exact_tags,
            unit.requested_rule_ids,
            unit.semantic_code_excerpt,
        )
    ):
        return ()
    if (
        index.embedding_model is None
        or index.embedding_version is None
        or index.embedding_dimensions is None
    ):
        raise ValueError("Knowledge index has no vector data")
    model_id = embedding_provider.model_id
    provider_version = embedding_provider.version
    dimensions = embedding_provider.dimensions
    if (
        model_id != index.embedding_model
        or provider_version != index.embedding_version
        or dimensions != index.embedding_dimensions
    ):
        raise ValueError("Embedding provider does not match Knowledge index")
    query_vector = embedding_provider.embed_query(query_embedding_text(unit))
    if (
        not isinstance(query_vector, tuple)
        or len(query_vector) != index.embedding_dimensions
        or any(not math.isfinite(value) for value in query_vector)
    ):
        raise ValueError("Embedding provider returned an invalid query vector")

    priorities = config.authority_priority_by_id
    hits: list[VectorHit] = []
    for record in index.records:
        if record.embedding is None:
            raise ValueError("Vector index contains a missing embedding")
        evaluation = evaluate_applicability(record.clause.applicability, target)
        if evaluation.decision == "excluded":
            continue
        similarity = round(_cosine(query_vector, record.embedding), 8)
        if similarity < config.minimum_vector_similarity:
            continue
        dimension_overlap = len(
            set(unit.retrieval_dimension_ids).intersection(
                record.annotation.dimension_ids
            )
        )
        hits.append(
            VectorHit(
                record=record,
                rank=1,
                similarity=similarity,
                matched_by=(
                    EvidenceMatch(
                        kind="vector",
                        value=model_id,
                        scope="semantic",
                    ),
                ),
                applicability=evaluation.decision,
                dimension_overlap=dimension_overlap,
                authority_priority=priorities.get(record.clause.authority, 0),
            )
        )
    ordered = sorted(
        hits,
        key=lambda item: (
            -item.similarity,
            -item.dimension_overlap,
            -item.authority_priority,
            item.record.clause.rule_id,
        ),
    )[: config.vector_candidate_limit]
    return tuple(replace(item, rank=rank) for rank, item in enumerate(ordered, start=1))


__all__ = ["VectorHit", "query_embedding_text", "search_vector"]
