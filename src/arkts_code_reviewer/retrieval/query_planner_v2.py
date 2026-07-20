from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import ContextPlanResult
from arkts_code_reviewer.code_analysis.models import AnalysisResult
from arkts_code_reviewer.hybrid_analysis.builders import (
    DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
    DEFAULT_ANALYSIS_CONTEXT_POLICY,
    PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION,
    AIModelViewProjectionPolicy,
    AnalysisCardBuilder,
    AnalysisContextPolicy,
    verify_model_view_against_card_and_policy,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    AITagAnalysisRequest,
    AITagAnalysisResult,
    AITagExecutionOutcome,
    AITagModelView,
    HybridFeatureAnalysisResult,
    ReviewUnitAnalysisCard,
    taxonomy_fingerprint,
    verify_hybrid_chain,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
    project_ai_tag_contract_views,
    verify_hybrid_chain_with_trusted_request,
)
from arkts_code_reviewer.retrieval.models import TargetPlatform
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.request_v2 import (
    VECTOR_QUERY_POLICY_V1,
    RetrievalRequestV2,
    RetrievalUnitRequestV2,
    UnitExactSignalsV2,
    candidate_dimension_ids_for_ai_tags,
)


@dataclass(frozen=True)
class HybridRetrievalUnitGraph:
    """Caller-supplied typed Hybrid graph verified before V2 projection.

    These v1 formal artifacts still lack trusted-runner attestation. This graph
    closes structural and upstream identity only; it must not be populated from
    the current non-formal shadow Campaign summary or ResponseValidation.
    """

    card: ReviewUnitAnalysisCard
    model_view: AITagModelView
    ai_request: AITagAnalysisRequest | None
    ai_outcome: AITagExecutionOutcome
    ai_result: AITagAnalysisResult | None
    hybrid: HybridFeatureAnalysisResult


def _validate_hybrid_graph(
    graph: HybridRetrievalUnitGraph,
    *,
    expected_card: ReviewUnitAnalysisCard,
    projection_policy: AIModelViewProjectionPolicy,
    analysis_context_policy: AnalysisContextPolicy,
    request_builder: FullTaxonomyRequestBuilder,
) -> HybridFeatureAnalysisResult:
    if not isinstance(graph, HybridRetrievalUnitGraph):
        raise TypeError("hybrid graph values must use HybridRetrievalUnitGraph")
    if graph.card != expected_card:
        raise ValueError("Hybrid Retrieval Card differs from trusted upstream rebuild")
    verify_model_view_against_card_and_policy(
        graph.model_view,
        graph.card,
        policy=projection_policy,
    )
    if graph.ai_request is not None:
        verify_hybrid_chain_with_trusted_request(
            graph.hybrid,
            card=graph.card,
            model_view=graph.model_view,
            request=graph.ai_request,
            outcome=graph.ai_outcome,
            result=graph.ai_result,
            feature_config=request_builder.feature_config,
            catalog=request_builder.catalog,
            prompt=request_builder.prompt,
            model_policy=request_builder.model_policy,
        )
    else:
        feature_config = request_builder.feature_config
        contracts = project_ai_tag_contract_views(
            request_builder.catalog,
            feature_config,
        )
        active_tag_ids = tuple(
            tag_id
            for tag_id, definition in feature_config.tags_by_id.items()
            if definition.status == "Active"
        )
        verify_hybrid_chain(
            graph.hybrid,
            graph.card,
            graph.model_view,
            None,
            graph.ai_outcome,
            graph.ai_result,
            active_tag_ids=active_tag_ids,
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )
    if (
        graph.ai_outcome.status in {"valid_result", "invalid_output", "unavailable"}
        and analysis_context_policy.builder_version != PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION
    ):
        raise ValueError("attempted AI outcomes require the provider-egress Analysis Card policy")
    return HybridFeatureAnalysisResult.model_validate(graph.hybrid.model_dump(mode="json"))


def _normalize_graphs(
    value: Mapping[str, HybridRetrievalUnitGraph],
) -> dict[str, HybridRetrievalUnitGraph]:
    if not isinstance(value, Mapping):
        raise TypeError("hybrid_graphs_by_unit must be a mapping")
    normalized: dict[str, HybridRetrievalUnitGraph] = {}
    for unit_id, graph in value.items():
        if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
            raise ValueError("Hybrid graph Unit IDs must be non-empty and trimmed")
        if unit_id in normalized:
            raise ValueError("Hybrid graph Unit IDs must be unique")
        if not isinstance(graph, HybridRetrievalUnitGraph):
            raise TypeError("hybrid graph values must use HybridRetrievalUnitGraph")
        normalized[unit_id] = graph
    return normalized


def build_retrieval_request_v2(
    analysis_result: AnalysisResult,
    context_plan: ContextPlanResult,
    *,
    source_snapshots: Mapping[str, CodeSourceSnapshot],
    hybrid_graphs_by_unit: Mapping[str, HybridRetrievalUnitGraph],
    target_platform: TargetPlatform,
    resolved_index_version: str,
    knowledge_token_budget: int,
    requested_rule_ids_by_unit: Mapping[str, tuple[str, ...]] | None = None,
    analysis_context_policy: AnalysisContextPolicy = DEFAULT_ANALYSIS_CONTEXT_POLICY,
    model_view_projection_policy: AIModelViewProjectionPolicy = (
        DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY
    ),
) -> RetrievalRequestV2:
    """Build the closed V2 request without making it an executable V1 request.

    The function first rebuilds the complete V1 request, then verifies every
    caller-supplied Card and formal Hybrid graph against the same upstream graph.
    Only AI ``positive`` decisions are projected. No current RetrievalService
    accepts the returned independent type.
    """

    if not isinstance(source_snapshots, Mapping):
        raise TypeError("source_snapshots must be a mapping")
    if not isinstance(analysis_context_policy, AnalysisContextPolicy):
        raise TypeError("analysis_context_policy must use AnalysisContextPolicy")
    if not isinstance(model_view_projection_policy, AIModelViewProjectionPolicy):
        raise TypeError("model_view_projection_policy must use AIModelViewProjectionPolicy")
    baseline = build_retrieval_request(
        analysis_result,
        context_plan,
        target_platform=target_platform,
        resolved_index_version=resolved_index_version,
        knowledge_token_budget=knowledge_token_budget,
        requested_rule_ids_by_unit=requested_rule_ids_by_unit,
    )
    graphs = _normalize_graphs(hybrid_graphs_by_unit)
    unit_ids = tuple(item.unit_id for item in baseline.units)
    if set(graphs) != set(unit_ids):
        raise ValueError("Hybrid graphs must exactly cover Retrieval primary Units")

    expected_cards = AnalysisCardBuilder().build_many(
        analysis_result=analysis_result,
        context_plan=context_plan,
        source_snapshots=source_snapshots,
        unit_ids=unit_ids,
        policy=analysis_context_policy,
    )
    cards_by_unit = {item.unit_id: item for item in expected_cards}
    if set(cards_by_unit) != set(unit_ids):
        raise ValueError("trusted Analysis Card rebuild does not cover Retrieval Units")
    scopes_by_unit = {item.unit_id: item for item in analysis_result.unit_fact_scopes}
    request_builder = FullTaxonomyRequestBuilder.default()
    units: list[RetrievalUnitRequestV2] = []
    for baseline_unit in baseline.units:
        unit_id = baseline_unit.unit_id
        graph = graphs[unit_id]
        card = cards_by_unit[unit_id]
        hybrid = _validate_hybrid_graph(
            graph,
            expected_card=card,
            projection_policy=model_view_projection_policy,
            analysis_context_policy=analysis_context_policy,
            request_builder=request_builder,
        )
        if (
            card.unit_id != unit_id
            or card.source_ref_id != baseline_unit.source_ref_id
            or card.feature_profile_id != baseline_unit.profile_id
            or card.feature_routing_id != baseline.feature_routing_id
            or card.context_plan_id != baseline.context_plan_id
            or card.feature_config_fingerprint != baseline.feature_config_version
        ):
            raise ValueError("Hybrid Card identities differ from the V1 Retrieval baseline")
        scope = scopes_by_unit.get(unit_id)
        if scope is None or scope.source_ref_id != baseline_unit.source_ref_id:
            raise ValueError("V2 Retrieval Unit has no aligned UnitFactScope")
        exact = scope.unit_exact
        exact_signals = UnitExactSignalsV2(
            apis=exact.apis,
            components=exact.components,
            decorators=exact.decorators,
            attributes=exact.attributes,
            symbols=exact.symbols,
            syntax=exact.syntax,
            calls=exact.calls,
            import_uses=exact.import_uses,
            resource_references=exact.resource_references,
        )
        if exact_signals.model_dump(exclude={"import_uses"}) != (
            baseline_unit.exact_signals.model_dump()
        ):
            raise ValueError("V2 exact facts do not preserve the V1 Retrieval baseline")

        ai_inferred_tags = tuple(
            state.tag_id for state in hybrid.tag_states if state.ai_unit_decision == "positive"
        )
        tag_disagreements = tuple(
            state.tag_id
            for state in hybrid.tag_states
            if state.unit_comparison_status == "disagreement"
        )
        candidate_dimension_ids = candidate_dimension_ids_for_ai_tags(ai_inferred_tags)
        units.append(
            RetrievalUnitRequestV2(
                unit_id=unit_id,
                source_ref_id=baseline_unit.source_ref_id,
                profile_id=baseline_unit.profile_id,
                hybrid_analysis_id=hybrid.analysis_id,
                review_question_ids=baseline_unit.review_question_ids,
                dispatchable_review_question_ids=(baseline_unit.dispatchable_review_question_ids),
                exact_signals=exact_signals,
                exact_tags=baseline_unit.exact_tags,
                routing_tags=baseline_unit.routing_tags,
                ai_inferred_tags=ai_inferred_tags,
                tag_disagreements=tag_disagreements,
                retrieval_dimension_ids=baseline_unit.retrieval_dimension_ids,
                routing_dimension_ids=baseline_unit.routing_dimension_ids,
                candidate_dimension_ids=candidate_dimension_ids,
                requested_rule_ids=baseline_unit.requested_rule_ids,
                semantic_code_excerpt=baseline_unit.semantic_code_excerpt,
                intent_summary=baseline_unit.intent_summary,
                vector_query_policy=VECTOR_QUERY_POLICY_V1,
                quality=baseline_unit.quality,
                knowledge_token_budget=baseline_unit.knowledge_token_budget,
            )
        )

    return RetrievalRequestV2.create(
        context_plan_id=baseline.context_plan_id,
        feature_routing_id=baseline.feature_routing_id,
        feature_config_version=baseline.feature_config_version,
        index_version=baseline.index_version,
        target_platform=baseline.target_platform,
        total_knowledge_token_budget=baseline.total_knowledge_token_budget,
        units=tuple(units),
    )


__all__ = ["HybridRetrievalUnitGraph", "build_retrieval_request_v2"]
