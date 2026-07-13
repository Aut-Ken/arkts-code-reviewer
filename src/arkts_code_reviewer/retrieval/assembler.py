from __future__ import annotations

from arkts_code_reviewer.retrieval.config import RetrievalConfig
from arkts_code_reviewer.retrieval.fusion import FusedHit
from arkts_code_reviewer.retrieval.models import (
    EvidenceClause,
    RankDetail,
    RetrievalDiagnostic,
    RetrievalUnitRequest,
    UnitEvidence,
)


def _diagnostic_key(value: RetrievalDiagnostic) -> tuple[str, str, str, str]:
    return (
        value.code,
        value.unit_id or "",
        value.rule_id or "",
        value.detail,
    )


def _select_hits(
    unit: RetrievalUnitRequest,
    hits: tuple[FusedHit, ...],
    config: RetrievalConfig,
) -> tuple[tuple[FusedHit, ...], bool]:
    budget = unit.knowledge_token_budget
    selected_rule_ids: set[str] = set()
    selected: list[FusedHit] = []
    used_tokens = 0
    skipped_for_budget = False

    def add(hit: FusedHit) -> bool:
        nonlocal skipped_for_budget, used_tokens
        rule_id = hit.record.clause.rule_id
        if rule_id in selected_rule_ids or len(selected) >= config.result_limit:
            return False
        if used_tokens + hit.record.token_count > budget:
            skipped_for_budget = True
            return False
        selected_rule_ids.add(rule_id)
        selected.append(hit)
        used_tokens += hit.record.token_count
        return True

    for dimension_id in unit.retrieval_dimension_ids:
        for hit in hits:
            if (
                dimension_id in hit.record.annotation.dimension_ids
                and hit.record.clause.rule_id not in selected_rule_ids
            ):
                if add(hit):
                    break
    for hit in hits:
        add(hit)
    rank_by_rule = {
        hit.record.clause.rule_id: rank for rank, hit in enumerate(hits, start=1)
    }
    selected.sort(key=lambda item: rank_by_rule[item.record.clause.rule_id])
    return tuple(selected), skipped_for_budget


def assemble_unit_evidence(
    unit: RetrievalUnitRequest,
    hits: tuple[FusedHit, ...],
    config: RetrievalConfig,
    *,
    path_diagnostics: tuple[RetrievalDiagnostic, ...] = (),
) -> UnitEvidence:
    diagnostics = list(path_diagnostics)
    if not unit.dispatchable_review_question_ids:
        diagnostics.append(
            RetrievalDiagnostic(
                code="context_dispatch_blocked",
                unit_id=unit.unit_id,
                detail="Context Plan blocked every review question for this Unit.",
            )
        )
        return UnitEvidence(
            unit_id=unit.unit_id,
            profile_id=unit.profile_id,
            requested_dimension_ids=unit.retrieval_dimension_ids,
            routing_dimension_ids=unit.routing_dimension_ids,
            covered_dimension_ids=(),
            uncovered_dimension_ids=unit.retrieval_dimension_ids,
            clauses=(),
            diagnostics=tuple(sorted(set(diagnostics), key=_diagnostic_key)),
        )

    selected, skipped_for_budget = _select_hits(unit, hits, config)
    clauses: list[EvidenceClause] = []
    for rank, hit in enumerate(selected, start=1):
        record = hit.record
        if hit.applicability == "unknown":
            diagnostics.append(
                RetrievalDiagnostic(
                    code="applicability_unknown",
                    unit_id=unit.unit_id,
                    rule_id=record.clause.rule_id,
                    detail="Target platform is incomplete for this Clause applicability.",
                )
            )
        clauses.append(
            EvidenceClause(
                rank=rank,
                rule_id=record.clause.rule_id,
                rule_type=record.clause.rule_type,
                status="Baselined",
                text=record.clause.text,
                heading_path=record.clause.heading_path,
                parent_context=record.clause.parent_context,
                dimension_ids=record.annotation.dimension_ids,
                tags=record.annotation.tags,
                apis=record.annotation.apis,
                components=record.annotation.components,
                decorators=record.annotation.decorators,
                domains=record.domains,
                source_ref=record.clause.source_ref,
                matched_by=hit.matched_by,
                applicability=hit.applicability,
                score=hit.rrf_score,
                rank_detail=RankDetail(
                    exact_rank=hit.exact_rank,
                    vector_rank=hit.vector_rank,
                    exact_score=hit.exact_score,
                    vector_similarity=hit.vector_similarity,
                    rrf_score=hit.rrf_score,
                    authority_priority=hit.authority_priority,
                    dimension_overlap=hit.dimension_overlap,
                ),
                token_count=record.token_count,
            )
        )
    covered = tuple(
        sorted(
            set(unit.retrieval_dimension_ids).intersection(
                dimension_id
                for clause in clauses
                for dimension_id in clause.dimension_ids
            )
        )
    )
    uncovered = tuple(sorted(set(unit.retrieval_dimension_ids) - set(covered)))
    if skipped_for_budget:
        diagnostics.append(
            RetrievalDiagnostic(
                code="budget_exhausted",
                unit_id=unit.unit_id,
                detail="At least one relevant Clause did not fit the knowledge token budget.",
            )
        )
    if not clauses:
        diagnostics.append(
            RetrievalDiagnostic(
                code="empty_result",
                unit_id=unit.unit_id,
                detail="No applicable Clause was selected for this Unit.",
            )
        )
    quality = unit.quality
    if (
        quality.parser_layer != "L1"
        or quality.context_degraded
        or (quality.error_nodes or 0) > 0
        or (quality.missing_nodes or 0) > 0
    ):
        diagnostics.append(
            RetrievalDiagnostic(
                code="parser_degraded",
                unit_id=unit.unit_id,
                detail="Parser or ReviewUnit context quality limits evidence confidence.",
            )
        )
    return UnitEvidence(
        unit_id=unit.unit_id,
        profile_id=unit.profile_id,
        requested_dimension_ids=unit.retrieval_dimension_ids,
        routing_dimension_ids=unit.routing_dimension_ids,
        covered_dimension_ids=covered,
        uncovered_dimension_ids=uncovered,
        clauses=tuple(clauses),
        diagnostics=tuple(sorted(set(diagnostics), key=_diagnostic_key)),
    )


__all__ = ["assemble_unit_evidence"]
