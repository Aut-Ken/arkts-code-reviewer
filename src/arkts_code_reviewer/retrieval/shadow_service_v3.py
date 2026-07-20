from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AITagExecutionOutcomeV2,
)
from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.config import (
    RetrievalConfig,
    load_default_retrieval_config,
)
from arkts_code_reviewer.retrieval.index import EmbeddingProvider
from arkts_code_reviewer.retrieval.models import (
    ApplicabilityResult,
    EvidencePack,
    KnowledgeIndex,
    KnowledgeIndexRecord,
    TargetPlatform,
)
from arkts_code_reviewer.retrieval.query_planner_v3 import (
    VerifiedRetrievalRequestV3,
)
from arkts_code_reviewer.retrieval.request_v3 import (
    RetrievalUnitRequestV3,
    render_vector_query_v3,
)
from arkts_code_reviewer.retrieval.service import RetrievalService
from arkts_code_reviewer.retrieval.shadow_models_v3 import (
    SHADOW_POOL_ORDER,
    RetrievalShadowResultV3,
    ShadowArmId,
    ShadowArmResultV3,
    ShadowCandidatePoolV3,
    ShadowDiagnosticV3,
    ShadowEvidenceMatchV3,
    ShadowPoolCandidateV3,
    ShadowPoolId,
    ShadowRankContributionV3,
    ShadowRankedClauseV3,
    ShadowSelectionStatus,
    ShadowUnitComparisonV3,
)
from arkts_code_reviewer.retrieval.shadow_policy_v3 import (
    RetrievalShadowPolicyV3,
    load_retrieval_shadow_policy_v3,
)


class _ImmutableRuntimeObject:
    __slots__ = ("_runtime_sealed",)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__delattr__(self, name)

    def _seal_runtime_object(self) -> None:
        object.__setattr__(self, "_runtime_sealed", True)


@dataclass(frozen=True)
class _PoolHit:
    record: KnowledgeIndexRecord
    pool: ShadowPoolId
    rank: int
    path_score: float
    matched_by: tuple[ShadowEvidenceMatchV3, ...]
    applicability: ApplicabilityResult
    formal_dimension_overlap: int
    authority_priority: int

    def __post_init__(self) -> None:
        if self.rank < 1 or not math.isfinite(self.path_score):
            raise ValueError("Shadow pool hit rank and score are invalid")
        if self.applicability not in {"applicable", "unknown"}:
            raise ValueError("Shadow pool hit cannot retain an excluded Clause")


@dataclass(frozen=True)
class _FusedHit:
    record: KnowledgeIndexRecord
    contributions: tuple[ShadowRankContributionV3, ...]
    matched_by: tuple[ShadowEvidenceMatchV3, ...]
    applicability: ApplicabilityResult
    formal_dimension_overlap: int
    authority_priority: int
    rrf_score: float


def _api_aliases(index: KnowledgeIndex) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for symbol in index.api_symbols:
        for name in (symbol.canonical_name, *symbol.aliases):
            candidates.setdefault(name, set()).add(symbol.canonical_name)
    return {
        name: next(iter(canonical)) for name, canonical in candidates.items() if len(canonical) == 1
    }


def _canonical_query_apis(index: KnowledgeIndex, apis: tuple[str, ...]) -> set[str]:
    aliases = _api_aliases(index)
    return {aliases.get(api, api) for api in apis}


def _rank_pool_hits(
    hits: list[_PoolHit],
    *,
    candidate_limit: int,
) -> tuple[_PoolHit, ...]:
    ordered = sorted(
        hits,
        key=lambda item: (
            -item.path_score,
            -len(item.matched_by),
            -item.formal_dimension_overlap,
            item.applicability == "unknown",
            -item.authority_priority,
            item.record.clause.rule_id,
        ),
    )[:candidate_limit]
    return tuple(
        _PoolHit(
            record=item.record,
            pool=item.pool,
            rank=rank,
            path_score=item.path_score,
            matched_by=item.matched_by,
            applicability=item.applicability,
            formal_dimension_overlap=item.formal_dimension_overlap,
            authority_priority=item.authority_priority,
        )
        for rank, item in enumerate(ordered, start=1)
    )


def _structured_pools(
    index: KnowledgeIndex,
    unit: RetrievalUnitRequestV3,
    target: TargetPlatform,
    config: RetrievalConfig,
    policy: RetrievalShadowPolicyV3,
) -> dict[ShadowPoolId, tuple[_PoolHit, ...]]:
    query_apis = _canonical_query_apis(index, unit.exact_signals.apis)
    formal_tags = set(unit.exact_tags)
    file_hint_tags = set(unit.routing_tags) - formal_tags
    ai_tags = set(unit.ai_inferred_tags)
    searchable_context = " ".join(
        value for value in (unit.intent_summary, unit.semantic_code_excerpt) if value is not None
    ).casefold()
    priorities = config.authority_priority_by_id
    pending: dict[ShadowPoolId, list[_PoolHit]] = {
        pool: [] for pool in SHADOW_POOL_ORDER if pool != "semantic_vector"
    }
    for record in index.records:
        applicability = evaluate_applicability(record.clause.applicability, target)
        if applicability.decision == "excluded":
            continue
        annotation = record.annotation
        dimension_overlap = len(
            set(unit.retrieval_dimension_ids).intersection(annotation.dimension_ids)
        )
        authority_priority = priorities.get(record.clause.authority, 0)
        applicability_bonus = (
            config.weights.applicability_exact if applicability.decision == "applicable" else 0
        )
        dimension_bonus = dimension_overlap * config.weights.dimension_overlap

        formal_matches: set[tuple[str, str]] = set()
        formal_score = 0
        if record.clause.rule_id in unit.requested_rule_ids:
            formal_matches.add(("rule_id", record.clause.rule_id))
            formal_score += config.weights.rule_id
        for api in sorted(query_apis.intersection(annotation.apis)):
            formal_matches.add(("api", api))
            formal_score += config.weights.api
        for component in sorted(
            set(unit.exact_signals.components).intersection(annotation.components)
        ):
            formal_matches.add(("component", component))
            formal_score += config.weights.component
        for decorator in sorted(
            set(unit.exact_signals.decorators).intersection(annotation.decorators)
        ):
            formal_matches.add(("decorator", decorator))
            formal_score += config.weights.decorator
        for tag in sorted(formal_tags.intersection(annotation.tags)):
            formal_matches.add(("tag", tag))
            formal_score += config.weights.exact_tag
        if formal_matches:
            pending["formal_exact"].append(
                _PoolHit(
                    record=record,
                    pool="formal_exact",
                    rank=1,
                    path_score=float(formal_score + dimension_bonus + applicability_bonus),
                    matched_by=tuple(
                        ShadowEvidenceMatchV3(
                            kind=cast(
                                "Literal['rule_id', 'api', 'component', 'decorator', 'tag']",
                                kind,
                            ),
                            value=value,
                            scope="unit_exact",
                        )
                        for kind, value in sorted(formal_matches)
                    ),
                    applicability=applicability.decision,
                    formal_dimension_overlap=dimension_overlap,
                    authority_priority=authority_priority,
                )
            )

        hint_matches = sorted(file_hint_tags.intersection(annotation.tags))
        if hint_matches:
            pending["file_hint"].append(
                _PoolHit(
                    record=record,
                    pool="file_hint",
                    rank=1,
                    path_score=float(
                        len(hint_matches) * config.weights.routing_tag
                        + dimension_bonus
                        + applicability_bonus
                    ),
                    matched_by=tuple(
                        ShadowEvidenceMatchV3(
                            kind="tag",
                            value=tag,
                            scope="file_hint",
                        )
                        for tag in hint_matches
                    ),
                    applicability=applicability.decision,
                    formal_dimension_overlap=dimension_overlap,
                    authority_priority=authority_priority,
                )
            )

        keywords = sorted(
            keyword
            for keyword in set((*annotation.raw_keywords, *annotation.llm_keywords))
            if keyword.casefold() in searchable_context
        )
        if keywords:
            pending["text_keyword"].append(
                _PoolHit(
                    record=record,
                    pool="text_keyword",
                    rank=1,
                    path_score=float(
                        len(keywords) * config.weights.keyword
                        + dimension_bonus
                        + applicability_bonus
                    ),
                    matched_by=tuple(
                        ShadowEvidenceMatchV3(
                            kind="keyword",
                            value=keyword,
                            scope="text_keyword",
                        )
                        for keyword in keywords
                    ),
                    applicability=applicability.decision,
                    formal_dimension_overlap=dimension_overlap,
                    authority_priority=authority_priority,
                )
            )

        inferred_matches = sorted(ai_tags.intersection(annotation.tags))
        if inferred_matches:
            pending["ai_inferred"].append(
                _PoolHit(
                    record=record,
                    pool="ai_inferred",
                    rank=1,
                    path_score=float(len(inferred_matches)),
                    matched_by=tuple(
                        ShadowEvidenceMatchV3(
                            kind="tag",
                            value=tag,
                            scope="ai_inferred",
                        )
                        for tag in inferred_matches
                    ),
                    applicability=applicability.decision,
                    formal_dimension_overlap=dimension_overlap,
                    authority_priority=authority_priority,
                )
            )

    structured_pool_ids: tuple[ShadowPoolId, ...] = (
        "formal_exact",
        "file_hint",
        "text_keyword",
        "ai_inferred",
    )
    return {
        pool: _rank_pool_hits(
            pending[pool],
            candidate_limit=policy.pool_by_id[pool].candidate_limit,
        )
        for pool in structured_pool_ids
    }


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("cosine vectors must have the same non-zero dimensions")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _vector_pool(
    index: KnowledgeIndex,
    unit: RetrievalUnitRequestV3,
    target: TargetPlatform,
    config: RetrievalConfig,
    policy: RetrievalShadowPolicyV3,
    embedding_provider: EmbeddingProvider,
) -> tuple[_PoolHit, ...]:
    query_text = render_vector_query_v3(unit)
    if query_text is None:
        return ()
    if (
        index.embedding_model is None
        or index.embedding_version is None
        or index.embedding_dimensions is None
    ):
        raise ValueError("Knowledge index has no vector data")
    if (
        embedding_provider.model_id != index.embedding_model
        or embedding_provider.version != index.embedding_version
        or embedding_provider.dimensions != index.embedding_dimensions
    ):
        raise ValueError("Embedding provider does not match Knowledge index")
    query_vector = embedding_provider.embed_query(query_text)
    if (
        not isinstance(query_vector, tuple)
        or len(query_vector) != index.embedding_dimensions
        or any(not math.isfinite(value) for value in query_vector)
    ):
        raise ValueError("Embedding provider returned an invalid query vector")

    priorities = config.authority_priority_by_id
    hits: list[_PoolHit] = []
    for record in index.records:
        if record.embedding is None:
            raise ValueError("Vector index contains a missing embedding")
        applicability = evaluate_applicability(record.clause.applicability, target)
        if applicability.decision == "excluded":
            continue
        similarity = round(_cosine(query_vector, record.embedding), 8)
        if similarity < config.minimum_vector_similarity:
            continue
        hits.append(
            _PoolHit(
                record=record,
                pool="semantic_vector",
                rank=1,
                path_score=similarity,
                matched_by=(
                    ShadowEvidenceMatchV3(
                        kind="vector",
                        value=embedding_provider.model_id,
                        scope="semantic",
                    ),
                ),
                applicability=applicability.decision,
                formal_dimension_overlap=len(
                    set(unit.retrieval_dimension_ids).intersection(record.annotation.dimension_ids)
                ),
                authority_priority=priorities.get(record.clause.authority, 0),
            )
        )
    return _rank_pool_hits(
        hits,
        candidate_limit=policy.pool_by_id["semantic_vector"].candidate_limit,
    )


def _validate_pool_hits(pool: ShadowPoolId, hits: tuple[_PoolHit, ...]) -> None:
    ranks = tuple(item.rank for item in hits)
    rules = tuple(item.record.clause.rule_id for item in hits)
    if ranks != tuple(range(1, len(hits) + 1)):
        raise ValueError(f"{pool} pool ranks must be contiguous")
    if len(rules) != len(set(rules)):
        raise ValueError(f"{pool} pool repeats a Clause")
    if any(item.pool != pool for item in hits):
        raise ValueError(f"{pool} pool contains a different source")


def _fuse(
    pools: dict[ShadowPoolId, tuple[_PoolHit, ...]],
    *,
    arm: ShadowArmId,
    policy: RetrievalShadowPolicyV3,
) -> tuple[_FusedHit, ...]:
    active_pools: tuple[ShadowPoolId, ...] = (
        (
            "formal_exact",
            "file_hint",
            "text_keyword",
            "semantic_vector",
        )
        if arm == "static_vector"
        else SHADOW_POOL_ORDER
    )
    for pool in active_pools:
        _validate_pool_hits(pool, pools[pool])
    by_pool = {
        pool: {item.record.clause.rule_id: item for item in pools[pool]} for pool in active_pools
    }
    rule_ids = sorted(set().union(*(set(values) for values in by_pool.values())))
    fused: list[_FusedHit] = []
    for rule_id in rule_ids:
        present = tuple(
            (pool, by_pool[pool][rule_id]) for pool in active_pools if rule_id in by_pool[pool]
        )
        records = {item.record for _, item in present}
        if len(records) != 1:
            raise ValueError("Shadow candidate pools disagree about a Clause record")
        applicability_values = {item.applicability for _, item in present}
        if len(applicability_values) != 1:
            raise ValueError("Shadow candidate pools disagree about applicability")
        contributions = tuple(
            ShadowRankContributionV3(
                pool=pool,
                pool_rank=item.rank,
                rrf_weight=policy.pool_by_id[pool].rrf_weight,
                rrf_contribution=round(
                    policy.pool_by_id[pool].rrf_weight / (policy.rrf_k + item.rank),
                    8,
                ),
            )
            for pool, item in present
        )
        matches = {
            (match.kind, match.scope, match.value): match
            for _, item in present
            for match in item.matched_by
        }
        fused.append(
            _FusedHit(
                record=next(iter(records)),
                contributions=contributions,
                matched_by=tuple(matches[key] for key in sorted(matches)),
                applicability=next(iter(applicability_values)),
                formal_dimension_overlap=max(item.formal_dimension_overlap for _, item in present),
                authority_priority=max(item.authority_priority for _, item in present),
                rrf_score=round(
                    sum(item.rrf_contribution for item in contributions),
                    8,
                ),
            )
        )

    def path_score(item: _FusedHit, pool: ShadowPoolId) -> float:
        hit = by_pool.get(pool, {}).get(item.record.clause.rule_id)
        return -1.0 if hit is None else hit.path_score

    return tuple(
        sorted(
            fused,
            key=lambda item: (
                -item.rrf_score,
                item.record.clause.rule_id not in by_pool.get("formal_exact", {}),
                -path_score(item, "formal_exact"),
                -path_score(item, "semantic_vector"),
                -path_score(item, "file_hint"),
                -path_score(item, "text_keyword"),
                -path_score(item, "ai_inferred"),
                item.applicability == "unknown",
                -item.authority_priority,
                item.record.clause.rule_id,
            ),
        )
    )


def _diagnostic_key(value: ShadowDiagnosticV3) -> tuple[str, str, str]:
    return (value.code, value.rule_id or "", value.detail)


def _dedupe_diagnostics(
    diagnostics: list[ShadowDiagnosticV3],
) -> tuple[ShadowDiagnosticV3, ...]:
    by_key = {_diagnostic_key(item): item for item in diagnostics}
    return tuple(by_key[key] for key in sorted(by_key))


def _select(
    unit: RetrievalUnitRequestV3,
    hits: tuple[_FusedHit, ...],
    *,
    result_limit: int,
) -> tuple[
    dict[str, ShadowSelectionStatus],
    tuple[str, ...],
    int,
    bool,
]:
    selected: list[_FusedHit] = []
    selected_rules: set[str] = set()
    statuses: dict[str, ShadowSelectionStatus] = {}
    used_tokens = 0
    budget_exhausted = False

    def add(hit: _FusedHit) -> bool:
        nonlocal budget_exhausted, used_tokens
        rule_id = hit.record.clause.rule_id
        if rule_id in selected_rules:
            return False
        if statuses.get(rule_id) == "token_budget":
            return False
        if len(selected) >= result_limit:
            statuses.setdefault(rule_id, "result_limit")
            return False
        if used_tokens + hit.record.token_count > unit.knowledge_token_budget:
            statuses[rule_id] = "token_budget"
            budget_exhausted = True
            return False
        selected_rules.add(rule_id)
        selected.append(hit)
        statuses[rule_id] = "selected"
        used_tokens += hit.record.token_count
        return True

    for dimension_id in unit.retrieval_dimension_ids:
        for hit in hits:
            if (
                dimension_id in hit.record.annotation.dimension_ids
                and hit.record.clause.rule_id not in selected_rules
            ):
                if add(hit):
                    break
    for hit in hits:
        add(hit)
    selected_by_rank = tuple(
        hit.record.clause.rule_id for hit in hits if hit.record.clause.rule_id in selected_rules
    )
    return statuses, selected_by_rank, used_tokens, budget_exhausted


def _build_arm(
    unit: RetrievalUnitRequestV3,
    hits: tuple[_FusedHit, ...],
    *,
    arm: ShadowArmId,
    policy: RetrievalShadowPolicyV3,
    path_diagnostics: tuple[ShadowDiagnosticV3, ...],
) -> ShadowArmResultV3:
    if not unit.dispatchable_review_question_ids:
        diagnostic = ShadowDiagnosticV3(
            code="context_dispatch_blocked",
            detail="Context Plan blocked every review question for this Unit.",
        )
        return ShadowArmResultV3(
            arm=arm,
            rrf_k=policy.rrf_k,
            result_limit=policy.result_limit,
            token_budget=unit.knowledge_token_budget,
            used_tokens=0,
            formal_dimension_ids=unit.retrieval_dimension_ids,
            covered_dimension_ids=(),
            uncovered_dimension_ids=unit.retrieval_dimension_ids,
            ranked_clauses=(),
            selected_rule_ids=(),
            diagnostics=(diagnostic,),
        )

    statuses, selected_rule_ids, used_tokens, budget_exhausted = _select(
        unit,
        hits,
        result_limit=policy.result_limit,
    )
    diagnostics = list(path_diagnostics)
    selected_rules = set(selected_rule_ids)
    for hit in hits:
        if hit.record.clause.rule_id in selected_rules and hit.applicability == "unknown":
            diagnostics.append(
                ShadowDiagnosticV3(
                    code="applicability_unknown",
                    rule_id=hit.record.clause.rule_id,
                    detail="Target platform is incomplete for this Clause applicability.",
                )
            )
    if budget_exhausted:
        diagnostics.append(
            ShadowDiagnosticV3(
                code="budget_exhausted",
                detail="At least one ranked Clause did not fit the Unit token budget.",
            )
        )
    if not selected_rule_ids:
        diagnostics.append(
            ShadowDiagnosticV3(
                code="empty_result",
                detail="No applicable Clause was selected for this shadow arm.",
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
            ShadowDiagnosticV3(
                code="parser_degraded",
                detail="Parser or ReviewUnit context quality limits shadow evidence confidence.",
            )
        )

    ranked_clauses = tuple(
        ShadowRankedClauseV3(
            rank=rank,
            rule_id=hit.record.clause.rule_id,
            rule_type=hit.record.clause.rule_type,
            status=cast(
                "Literal['Draft', 'Baselined']",
                hit.record.clause.status,
            ),
            text=hit.record.clause.text,
            heading_path=hit.record.clause.heading_path,
            parent_context=hit.record.clause.parent_context,
            dimension_ids=hit.record.annotation.dimension_ids,
            tags=hit.record.annotation.tags,
            apis=hit.record.annotation.apis,
            components=hit.record.annotation.components,
            decorators=hit.record.annotation.decorators,
            domains=hit.record.domains,
            source_ref=hit.record.clause.source_ref,
            matched_by=hit.matched_by,
            contributions=hit.contributions,
            applicability=hit.applicability,
            rrf_score=hit.rrf_score,
            authority_priority=hit.authority_priority,
            formal_dimension_overlap=hit.formal_dimension_overlap,
            token_count=hit.record.token_count,
            selection_status=statuses[hit.record.clause.rule_id],
        )
        for rank, hit in enumerate(hits, start=1)
    )
    covered = tuple(
        sorted(
            set(unit.retrieval_dimension_ids).intersection(
                dimension_id
                for hit in hits
                if hit.record.clause.rule_id in selected_rules
                for dimension_id in hit.record.annotation.dimension_ids
            )
        )
    )
    uncovered = tuple(sorted(set(unit.retrieval_dimension_ids) - set(covered)))
    return ShadowArmResultV3(
        arm=arm,
        rrf_k=policy.rrf_k,
        result_limit=policy.result_limit,
        token_budget=unit.knowledge_token_budget,
        used_tokens=used_tokens,
        formal_dimension_ids=unit.retrieval_dimension_ids,
        covered_dimension_ids=covered,
        uncovered_dimension_ids=uncovered,
        ranked_clauses=ranked_clauses,
        selected_rule_ids=selected_rule_ids,
        diagnostics=_dedupe_diagnostics(diagnostics),
    )


def _candidate_pool(
    pool: ShadowPoolId,
    hits: tuple[_PoolHit, ...],
    policy: RetrievalShadowPolicyV3,
) -> ShadowCandidatePoolV3:
    return ShadowCandidatePoolV3(
        pool=pool,
        rrf_weight=policy.pool_by_id[pool].rrf_weight,
        candidates=tuple(
            ShadowPoolCandidateV3(
                pool=pool,
                rank=item.rank,
                rule_id=item.record.clause.rule_id,
                path_score=item.path_score,
                matched_by=item.matched_by,
                applicability=item.applicability,
                formal_dimension_overlap=item.formal_dimension_overlap,
                authority_priority=item.authority_priority,
            )
            for item in hits
        ),
    )


def _build_unit_comparison(
    index: KnowledgeIndex,
    unit: RetrievalUnitRequestV3,
    outcome: AITagExecutionOutcomeV2,
    target: TargetPlatform,
    config: RetrievalConfig,
    policy: RetrievalShadowPolicyV3,
    embedding_provider: EmbeddingProvider | None,
) -> ShadowUnitComparisonV3:
    pools: dict[ShadowPoolId, tuple[_PoolHit, ...]] = {pool: () for pool in SHADOW_POOL_ORDER}
    path_diagnostics: tuple[ShadowDiagnosticV3, ...] = ()
    if unit.dispatchable_review_question_ids:
        pools.update(_structured_pools(index, unit, target, config, policy))
        if index.embedding_model is not None:
            if embedding_provider is None:
                path_diagnostics = (
                    ShadowDiagnosticV3(
                        code="embedding_unavailable",
                        detail=("Vector index is present but no embedding provider is available."),
                    ),
                )
            else:
                try:
                    pools["semantic_vector"] = _vector_pool(
                        index,
                        unit,
                        target,
                        config,
                        policy,
                        embedding_provider,
                    )
                except Exception as exc:
                    path_diagnostics = (
                        ShadowDiagnosticV3(
                            code="embedding_unavailable",
                            detail=(
                                "Code-first vector retrieval failed; structured shadow "
                                f"retrieval continued: {type(exc).__name__}."
                            ),
                        ),
                    )
    static_hits = _fuse(pools, arm="static_vector", policy=policy)
    hybrid_hits = _fuse(pools, arm="hybrid", policy=policy)
    static_arm = _build_arm(
        unit,
        static_hits,
        arm="static_vector",
        policy=policy,
        path_diagnostics=path_diagnostics,
    )
    hybrid_arm = _build_arm(
        unit,
        hybrid_hits,
        arm="hybrid",
        policy=policy,
        path_diagnostics=path_diagnostics,
    )
    return ShadowUnitComparisonV3(
        unit_id=unit.unit_id,
        profile_id=unit.profile_id,
        formal_hybrid_analysis_id=unit.formal_hybrid_analysis_id,
        formal_execution_outcome_id=unit.formal_execution_outcome_id,
        formal_ai_result_id=unit.formal_ai_result_id,
        trusted_execution_subject_id=unit.trusted_execution_subject_id,
        trusted_runner_attestation_id=unit.trusted_runner_attestation_id,
        formal_execution_status=outcome.status,
        exact_tags=unit.exact_tags,
        routing_tags=unit.routing_tags,
        ai_inferred_tags=unit.ai_inferred_tags,
        tag_disagreements=unit.tag_disagreements,
        formal_dimension_ids=unit.retrieval_dimension_ids,
        routing_dimension_ids=unit.routing_dimension_ids,
        candidate_dimension_ids=unit.candidate_dimension_ids,
        candidate_dimension_policy="diagnostic_only",
        pools=tuple(_candidate_pool(pool, pools[pool], policy) for pool in SHADOW_POOL_ORDER),
        static_vector=static_arm,
        hybrid=hybrid_arm,
    )


class VerifiedRetrievalShadowResultV3(_ImmutableRuntimeObject):
    """Runtime authority wrapper; its serializable artifact is audit-only."""

    __slots__ = (
        "_artifact",
        "_authority",
        "_base_config",
        "_control_evidence_pack",
        "_expected_result_id",
        "_index",
        "_policy",
    )

    def __init__(
        self,
        *,
        artifact: RetrievalShadowResultV3,
        authority: VerifiedRetrievalRequestV3,
        control_evidence_pack: EvidencePack,
        index: KnowledgeIndex,
        base_config: RetrievalConfig,
        shadow_policy: RetrievalShadowPolicyV3,
        construction_token: object,
    ) -> None:
        if construction_token is not _VERIFIED_SHADOW_RESULT_TOKEN:
            raise TypeError("verified Shadow Results can only be built by the runtime")
        if type(authority) is not VerifiedRetrievalRequestV3:
            raise TypeError("verified Shadow Result requires exact V3 authority")
        if type(control_evidence_pack) is not EvidencePack:
            raise TypeError("verified Shadow Result requires exact V1 EvidencePack")
        if type(index) is not KnowledgeIndex:
            raise TypeError("verified Shadow Result requires exact KnowledgeIndex")
        if type(base_config) is not RetrievalConfig:
            raise TypeError("verified Shadow Result requires exact RetrievalConfig")
        if type(shadow_policy) is not RetrievalShadowPolicyV3:
            raise TypeError("verified Shadow Result requires exact Shadow Policy")
        self._artifact = RetrievalShadowResultV3.model_validate(artifact.model_dump(mode="json"))
        self._authority = authority
        self._control_evidence_pack = EvidencePack.model_validate(
            control_evidence_pack.model_dump(mode="json")
        )
        self._index = KnowledgeIndex.model_validate(index.model_dump(mode="json"))
        self._base_config = RetrievalConfig.model_validate(base_config.model_dump(mode="json"))
        self._policy = RetrievalShadowPolicyV3.model_validate(
            shadow_policy.model_dump(mode="json"),
            context={"base_retrieval_config": self._base_config},
        )
        self._expected_result_id = artifact.result_id
        self._verify_binding()
        self._seal_runtime_object()

    def _verify_binding(self) -> None:
        request = self._authority.request
        baseline = self._authority.baseline_request
        outcomes = self._authority.formal_execution_outcomes
        artifact = RetrievalShadowResultV3.model_validate(self._artifact.model_dump(mode="json"))
        control = EvidencePack.model_validate(self._control_evidence_pack.model_dump(mode="json"))
        index = KnowledgeIndex.model_validate(self._index.model_dump(mode="json"))
        config = RetrievalConfig.model_validate(self._base_config.model_dump(mode="json"))
        policy = RetrievalShadowPolicyV3.model_validate(
            self._policy.model_dump(mode="json"),
            context={"base_retrieval_config": config},
        )
        if (
            artifact.result_id != self._expected_result_id
            or artifact.verified_request_id != request.request_id
            or artifact.v1_control_request_id != baseline.request_id
            or artifact.v1_control_evidence_pack_id != control.evidence_pack_id
            or control.request_id != baseline.request_id
            or request.index_version != index.index_version
            or request.feature_config_version != index.feature_config_version
            or artifact.index_version != index.index_version
            or artifact.index_origin != index.origin
            or artifact.knowledge_build_id != index.published_build_id
            or artifact.source_bundle_id != index.source_bundle_id
            or artifact.embedding_version != index.embedding_version
            or control.index_version != index.index_version
            or control.index_origin != index.origin
            or control.knowledge_build_id != index.published_build_id
            or control.source_bundle_id != index.source_bundle_id
            or control.embedding_version != index.embedding_version
            or control.retrieval_version != config.version
            or artifact.base_retrieval_config_fingerprint != config.fingerprint
            or control.retrieval_config_fingerprint != config.fingerprint
            or artifact.shadow_policy_fingerprint != policy.fingerprint
            or policy.base_retrieval_config_fingerprint != config.fingerprint
            or policy.rrf_k != config.rrf_k
            or policy.result_limit != config.result_limit
            or artifact.formal_attestation_ids != self._authority.formal_attestation_ids
            or len(artifact.units) != len(request.units)
            or len(outcomes) != len(request.units)
        ):
            raise ValueError("verified Shadow Result differs from runtime authority")
        records_by_rule = {record.clause.rule_id: record for record in index.records}
        authority_priorities = config.authority_priority_by_id
        for result_unit, request_unit, outcome in zip(
            artifact.units,
            request.units,
            outcomes,
            strict=True,
        ):
            if (
                result_unit.unit_id != request_unit.unit_id
                or result_unit.profile_id != request_unit.profile_id
                or result_unit.formal_hybrid_analysis_id != request_unit.formal_hybrid_analysis_id
                or result_unit.formal_execution_outcome_id
                != request_unit.formal_execution_outcome_id
                or result_unit.formal_ai_result_id != request_unit.formal_ai_result_id
                or result_unit.trusted_execution_subject_id
                != request_unit.trusted_execution_subject_id
                or result_unit.trusted_runner_attestation_id
                != request_unit.trusted_runner_attestation_id
                or result_unit.formal_execution_status != outcome.status
                or result_unit.exact_tags != request_unit.exact_tags
                or result_unit.routing_tags != request_unit.routing_tags
                or result_unit.ai_inferred_tags != request_unit.ai_inferred_tags
                or result_unit.tag_disagreements != request_unit.tag_disagreements
                or result_unit.formal_dimension_ids != request_unit.retrieval_dimension_ids
                or result_unit.routing_dimension_ids != request_unit.routing_dimension_ids
                or result_unit.candidate_dimension_ids != request_unit.candidate_dimension_ids
                or result_unit.candidate_dimension_policy != policy.candidate_dimension_policy
                or any(
                    pool.rrf_weight != policy.pool_by_id[pool.pool].rrf_weight
                    or len(pool.candidates) > policy.pool_by_id[pool.pool].candidate_limit
                    for pool in result_unit.pools
                )
                or any(
                    arm.rrf_k != policy.rrf_k
                    or arm.result_limit != policy.result_limit
                    or arm.token_budget != request_unit.knowledge_token_budget
                    for arm in (result_unit.static_vector, result_unit.hybrid)
                )
            ):
                raise ValueError("verified Shadow Unit differs from V3 authority")

            expected_structured: dict[ShadowPoolId, tuple[_PoolHit, ...]] = {
                "formal_exact": (),
                "file_hint": (),
                "text_keyword": (),
                "ai_inferred": (),
            }
            if request_unit.dispatchable_review_question_ids:
                expected_structured.update(
                    _structured_pools(
                        index,
                        request_unit,
                        request.target_platform,
                        config,
                        policy,
                    )
                )
            pools_by_id = {pool.pool: pool for pool in result_unit.pools}
            for pool_id in (
                "formal_exact",
                "file_hint",
                "text_keyword",
                "ai_inferred",
            ):
                if pools_by_id[pool_id] != _candidate_pool(
                    pool_id,
                    expected_structured[pool_id],
                    policy,
                ):
                    raise ValueError("verified Shadow structured pool differs from runtime roots")

            semantic_pool = pools_by_id["semantic_vector"]
            if (
                not request_unit.dispatchable_review_question_ids or index.embedding_model is None
            ) and semantic_pool.candidates:
                raise ValueError("verified Shadow semantic pool is unavailable for runtime roots")
            for candidate in semantic_pool.candidates:
                record = records_by_rule.get(candidate.rule_id)
                if record is None:
                    raise ValueError("verified Shadow candidate is absent from KnowledgeIndex")
                applicability = evaluate_applicability(
                    record.clause.applicability,
                    request.target_platform,
                ).decision
                if (
                    applicability == "excluded"
                    or candidate.applicability != applicability
                    or candidate.formal_dimension_overlap
                    != len(
                        set(request_unit.retrieval_dimension_ids).intersection(
                            record.annotation.dimension_ids
                        )
                    )
                    or candidate.authority_priority
                    != authority_priorities.get(record.clause.authority, 0)
                    or tuple(match.value for match in candidate.matched_by)
                    != (index.embedding_model,)
                ):
                    raise ValueError("verified Shadow semantic candidate differs from index roots")

            for arm in (result_unit.static_vector, result_unit.hybrid):
                for clause in arm.ranked_clauses:
                    record = records_by_rule.get(clause.rule_id)
                    if record is None:
                        raise ValueError("verified Shadow Clause is absent from KnowledgeIndex")
                    applicability = evaluate_applicability(
                        record.clause.applicability,
                        request.target_platform,
                    ).decision
                    expected_clause_payload = (
                        record.clause.rule_type,
                        record.clause.status,
                        record.clause.text,
                        record.clause.heading_path,
                        record.clause.parent_context,
                        record.annotation.dimension_ids,
                        record.annotation.tags,
                        record.annotation.apis,
                        record.annotation.components,
                        record.annotation.decorators,
                        record.domains,
                        record.clause.source_ref,
                        applicability,
                        authority_priorities.get(record.clause.authority, 0),
                        len(
                            set(request_unit.retrieval_dimension_ids).intersection(
                                record.annotation.dimension_ids
                            )
                        ),
                        record.token_count,
                    )
                    actual_clause_payload = (
                        clause.rule_type,
                        clause.status,
                        clause.text,
                        clause.heading_path,
                        clause.parent_context,
                        clause.dimension_ids,
                        clause.tags,
                        clause.apis,
                        clause.components,
                        clause.decorators,
                        clause.domains,
                        clause.source_ref,
                        clause.applicability,
                        clause.authority_priority,
                        clause.formal_dimension_overlap,
                        clause.token_count,
                    )
                    if actual_clause_payload != expected_clause_payload:
                        raise ValueError("verified Shadow Clause differs from KnowledgeIndex")

    def __repr__(self) -> str:
        return (
            "VerifiedRetrievalShadowResultV3("
            f"result_id={self._artifact.result_id!r}, authority=<runtime-verified>)"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("verified Retrieval Shadow Results are not serializable")

    @property
    def authority_status(self) -> Literal["runtime_verified"]:
        self._verify_binding()
        return "runtime_verified"

    @property
    def artifact(self) -> RetrievalShadowResultV3:
        self._verify_binding()
        return self._artifact

    @property
    def control_evidence_pack(self) -> EvidencePack:
        self._verify_binding()
        return self._control_evidence_pack


_VERIFIED_SHADOW_RESULT_TOKEN = object()


class RetrievalShadowServiceV3(_ImmutableRuntimeObject):
    """Execute V3 AI-assisted retrieval without changing the V1 visible result."""

    __slots__ = (
        "_allow_evaluation_fixture",
        "_allow_golden_fixture",
        "_config",
        "_embedding_provider",
        "_index",
        "_policy",
    )

    def __init__(
        self,
        index: KnowledgeIndex,
        *,
        base_config: RetrievalConfig | None = None,
        shadow_policy: RetrievalShadowPolicyV3 | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        allow_golden_fixture: bool = False,
        allow_evaluation_fixture: bool = False,
    ) -> None:
        if type(index) is not KnowledgeIndex:
            raise TypeError("shadow index must use exact KnowledgeIndex")
        if base_config is not None and type(base_config) is not RetrievalConfig:
            raise TypeError("base_config must use exact RetrievalConfig")
        config = base_config or load_default_retrieval_config()
        if shadow_policy is not None and type(shadow_policy) is not RetrievalShadowPolicyV3:
            raise TypeError("shadow_policy must use exact RetrievalShadowPolicyV3")
        policy = shadow_policy or load_retrieval_shadow_policy_v3(
            base_config=config,
        )
        if not isinstance(allow_golden_fixture, bool):
            raise TypeError("allow_golden_fixture must be boolean")
        if not isinstance(allow_evaluation_fixture, bool):
            raise TypeError("allow_evaluation_fixture must be boolean")
        if index.origin == "golden_fixture" and not allow_golden_fixture:
            raise ValueError("Golden fixture index requires an explicit test-only opt-in")
        if index.origin == "evaluation_fixture" and not allow_evaluation_fixture:
            raise ValueError("Evaluation fixture index requires an explicit staging opt-in")
        if policy.base_retrieval_config_fingerprint != config.fingerprint:
            raise ValueError("Shadow policy does not bind the active V1 Retrieval config")
        if policy.rrf_k != config.rrf_k or policy.result_limit != config.result_limit:
            raise ValueError("Shadow policy ranking limits differ from V1 Retrieval config")
        self._index = index
        self._config = config
        self._policy = policy
        self._embedding_provider = embedding_provider
        self._allow_golden_fixture = allow_golden_fixture
        self._allow_evaluation_fixture = allow_evaluation_fixture
        self._seal_runtime_object()

    def compare(
        self,
        authority: VerifiedRetrievalRequestV3,
    ) -> VerifiedRetrievalShadowResultV3:
        if type(authority) is not VerifiedRetrievalRequestV3:
            raise TypeError("Phase C requires exact VerifiedRetrievalRequestV3 authority")
        request = authority.request
        baseline = authority.baseline_request
        outcomes = authority.formal_execution_outcomes
        if request.index_version != self._index.index_version:
            raise ValueError("Retrieval V3 request references a different index version")
        if request.feature_config_version != self._index.feature_config_version:
            raise ValueError("Retrieval V3 request and index feature configs disagree")
        if (
            self._index.retrieval_version != self._config.version
            or self._index.retrieval_config_fingerprint != self._config.fingerprint
        ):
            raise ValueError("Knowledge index and base Retrieval config disagree")
        if len(request.units) > self._config.max_units:
            raise ValueError("Retrieval V3 request exceeds configured Unit limit")
        if len(request.units) != len(outcomes):
            raise ValueError("Formal execution outcome coverage differs from V3 Units")

        control = RetrievalService(
            self._index,
            config=self._config,
            embedding_provider=self._embedding_provider,
            allow_golden_fixture=self._allow_golden_fixture,
            allow_evaluation_fixture=self._allow_evaluation_fixture,
        ).retrieve(baseline)
        units = tuple(
            _build_unit_comparison(
                self._index,
                unit,
                outcome,
                request.target_platform,
                self._config,
                self._policy,
                self._embedding_provider,
            )
            for unit, outcome in zip(request.units, outcomes, strict=True)
        )
        if authority.request.request_id != request.request_id:
            raise ValueError("Retrieval V3 authority changed during shadow execution")
        artifact = RetrievalShadowResultV3.create(
            verified_request_id=request.request_id,
            v1_control_request_id=baseline.request_id,
            v1_control_evidence_pack_id=control.evidence_pack_id,
            base_retrieval_config_fingerprint=self._config.fingerprint,
            shadow_policy_fingerprint=self._policy.fingerprint,
            index_version=self._index.index_version,
            index_origin=self._index.origin,
            knowledge_build_id=self._index.published_build_id,
            source_bundle_id=self._index.source_bundle_id,
            embedding_version=self._index.embedding_version,
            formal_attestation_ids=authority.formal_attestation_ids,
            units=units,
        )
        return VerifiedRetrievalShadowResultV3(
            artifact=artifact,
            authority=authority,
            control_evidence_pack=control,
            index=self._index,
            base_config=self._config,
            shadow_policy=self._policy,
            construction_token=_VERIFIED_SHADOW_RESULT_TOKEN,
        )


__all__ = [
    "RetrievalShadowServiceV3",
    "VerifiedRetrievalShadowResultV3",
]
