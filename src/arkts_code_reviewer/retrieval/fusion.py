from __future__ import annotations

import math
from dataclasses import dataclass

from arkts_code_reviewer.retrieval.exact import ExactHit
from arkts_code_reviewer.retrieval.models import (
    ApplicabilityResult,
    EvidenceMatch,
    KnowledgeIndexRecord,
)
from arkts_code_reviewer.retrieval.vector import VectorHit


@dataclass(frozen=True)
class FusedHit:
    record: KnowledgeIndexRecord
    exact_rank: int | None
    vector_rank: int | None
    exact_score: int
    vector_similarity: float | None
    rrf_score: float
    matched_by: tuple[EvidenceMatch, ...]
    applicability: ApplicabilityResult
    dimension_overlap: int
    authority_priority: int

    def __post_init__(self) -> None:
        if self.exact_rank is None and self.vector_rank is None:
            raise ValueError("Fused hit requires at least one retrieval path")
        if any(
            rank is not None and rank < 1
            for rank in (self.exact_rank, self.vector_rank)
        ) or not math.isfinite(self.rrf_score):
            raise ValueError("Fused hit ranks and score are invalid")
        if self.applicability not in {"applicable", "unknown"}:
            raise ValueError("Fused hit cannot retain an excluded Clause")


def _validate_path_hits(
    path: str,
    values: tuple[ExactHit, ...] | tuple[VectorHit, ...],
) -> None:
    rule_ids = [item.record.clause.rule_id for item in values]
    ranks = [item.rank for item in values]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError(f"{path} retrieval path repeats a Clause")
    if len(ranks) != len(set(ranks)):
        raise ValueError(f"{path} retrieval path repeats a rank")


def fuse_hits(
    exact_hits: tuple[ExactHit, ...],
    vector_hits: tuple[VectorHit, ...],
    *,
    rrf_k: int,
) -> tuple[FusedHit, ...]:
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    _validate_path_hits("exact", exact_hits)
    _validate_path_hits("vector", vector_hits)
    exact_by_rule = {item.record.clause.rule_id: item for item in exact_hits}
    vector_by_rule = {item.record.clause.rule_id: item for item in vector_hits}
    rule_ids = sorted(set(exact_by_rule).union(vector_by_rule))
    fused: list[FusedHit] = []
    for rule_id in rule_ids:
        exact = exact_by_rule.get(rule_id)
        vector = vector_by_rule.get(rule_id)
        if exact is None:
            if vector is None:
                raise AssertionError("unreachable empty fusion record")
            record = vector.record
        else:
            record = exact.record
        if exact is not None and vector is not None and exact.record != vector.record:
            raise ValueError("Retrieval paths disagree about a Clause record")
        exact_rank = None if exact is None else exact.rank
        vector_rank = None if vector is None else vector.rank
        score = sum(
            1 / (rrf_k + rank)
            for rank in (exact_rank, vector_rank)
            if rank is not None
        )
        matches = {
            (match.kind, match.scope, match.value): match
            for hit in (exact, vector)
            if hit is not None
            for match in hit.matched_by
        }
        applicability_values = {
            hit.applicability for hit in (exact, vector) if hit is not None
        }
        if len(applicability_values) != 1:
            raise ValueError("Retrieval paths disagree about applicability")
        fused.append(
            FusedHit(
                record=record,
                exact_rank=exact_rank,
                vector_rank=vector_rank,
                exact_score=0 if exact is None else exact.score,
                vector_similarity=None if vector is None else vector.similarity,
                rrf_score=round(score, 8),
                matched_by=tuple(matches[key] for key in sorted(matches)),
                applicability=next(iter(applicability_values)),
                dimension_overlap=max(
                    hit.dimension_overlap
                    for hit in (exact, vector)
                    if hit is not None
                ),
                authority_priority=max(
                    hit.authority_priority
                    for hit in (exact, vector)
                    if hit is not None
                ),
            )
        )
    return tuple(
        sorted(
            fused,
            key=lambda item: (
                -item.rrf_score,
                item.exact_rank is None,
                -item.exact_score,
                -(
                    item.vector_similarity
                    if item.vector_similarity is not None
                    else -1.0
                ),
                item.applicability == "unknown",
                -item.authority_priority,
                item.record.clause.rule_id,
            ),
        )
    )


__all__ = ["FusedHit", "fuse_hits"]
