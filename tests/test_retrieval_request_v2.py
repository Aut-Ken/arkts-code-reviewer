from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest
from test_hybrid_analysis_builders import (
    _build_cards,
    _replacement_scenario,
    _Scenario,
)

from arkts_code_reviewer.hybrid_analysis import (
    AITagAnalysisRequest,
    AITagAnalysisResult,
    AITagExecutionOutcome,
    AnalysisContextPolicy,
    ExecutionStatus,
    HybridFeatureAnalysisResult,
    ReviewUnitAnalysisCard,
    StaticDecision,
    TagDecision,
    build_ai_tag_model_view,
    build_review_unit_analysis_cards,
    reduce_unit_comparison,
    seal_ai_tag_analysis_result,
    seal_ai_tag_execution_outcome,
    seal_hybrid_feature_analysis_result,
    seal_review_unit_analysis_card,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
)
from arkts_code_reviewer.retrieval.models import (
    RetrievalRequest,
    TargetPlatform,
    load_retrieval_request,
)
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.query_planner_v2 import (
    HybridRetrievalUnitGraph,
    build_retrieval_request_v2,
)
from arkts_code_reviewer.retrieval.request_v2 import (
    RetrievalRequestV2,
    RetrievalUnitRequestV2,
    candidate_dimension_ids_for_ai_tags,
    load_retrieval_request_v2,
    render_vector_query_v2,
)
from arkts_code_reviewer.retrieval.service import RetrievalService

_INDEX_VERSION = f"knowledge-index:sha256:{'a' * 64}"
_TARGET = TargetPlatform(release="HarmonyOS NEXT", api_level=18)
_EGRESS_POLICY = AnalysisContextPolicy(
    builder_version="analysis-card-builder-v2-provider-egress",
    redaction_policy="none_requires_exact_body_runtime_approval",
)


def _scenario() -> _Scenario:
    return _replacement_scenario(
        """@Entry
@Component
struct Page {
  async load() {
    console.info("old")
  }
}
""",
        """@Entry
@Component
struct Page {
  async load() {
    console.info("new")
  }
}
""",
        changed_line=5,
    )


def _valid_graph(
    card: ReviewUnitAnalysisCard,
    *,
    decisions: dict[str, TagDecision] | None = None,
) -> HybridRetrievalUnitGraph:
    model_view = build_ai_tag_model_view(card=card)
    request = FullTaxonomyRequestBuilder.default().build(
        card=card,
        model_view=model_view,
    )
    selected = decisions or {}
    visible_line = model_view.code.line_numbers[0]
    judgment_payloads: list[dict[str, object]] = []
    for contract in request.tag_contract_views:
        decision = selected.get(contract.tag_id, "not_supported")
        if decision == "positive":
            judgment_payloads.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": decision,
                    "evidence_lines": [visible_line],
                    "reason_code": "direct_unit_semantic_evidence",
                    "reason": "固定合成测试中的直接 Unit 语义证据。",
                }
            )
        elif decision == "abstain":
            judgment_payloads.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": decision,
                    "evidence_lines": [],
                    "reason_code": "insufficient_context",
                    "reason": "固定合成测试要求保留不确定性。",
                }
            )
        else:
            judgment_payloads.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": decision,
                    "evidence_lines": [],
                    "reason_code": "no_support_in_complete_view",
                    "reason": None,
                }
            )
    result = seal_ai_tag_analysis_result(
        {
            "schema_version": "ai-tag-analysis-result-v1",
            "request_id": request.request_id,
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "system_fingerprint": "synthetic-test-fixture",
            "thinking": "disabled",
            "reasoning_effort": None,
            "response_format": "json_object",
            "finish_reason": "stop",
            "judgments": judgment_payloads,
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
            },
            "latency_ms": 1,
            "attempt_count": 1,
            "output_status": "valid",
        }
    )
    outcome = _outcome(request, status="valid_result", result=result)
    hybrid = _hybrid(card, outcome, result)
    return HybridRetrievalUnitGraph(
        card=card,
        model_view=model_view,
        ai_request=request,
        ai_outcome=outcome,
        ai_result=result,
        hybrid=hybrid,
    )


def _unavailable_graph(card: ReviewUnitAnalysisCard) -> HybridRetrievalUnitGraph:
    model_view = build_ai_tag_model_view(card=card)
    request = FullTaxonomyRequestBuilder.default().build(
        card=card,
        model_view=model_view,
    )
    outcome = _outcome(request, status="unavailable", result=None)
    hybrid = _hybrid(card, outcome, None)
    return HybridRetrievalUnitGraph(
        card=card,
        model_view=model_view,
        ai_request=request,
        ai_outcome=outcome,
        ai_result=None,
        hybrid=hybrid,
    )


def _outcome(
    request: AITagAnalysisRequest,
    *,
    status: ExecutionStatus,
    result: AITagAnalysisResult | None,
) -> AITagExecutionOutcome:
    return seal_ai_tag_execution_outcome(
        {
            "schema_version": "ai-tag-execution-outcome-v1",
            "analysis_run_id": f"ai-tag-run:sha256:{'b' * 64}",
            "card_id": request.card_id,
            "model_view_id": request.model_view_id,
            "request_id": request.request_id,
            "status": status,
            "result_id": None if result is None else result.result_id,
            "reason_code": (
                "provider_response_valid" if result is not None else "provider_timeout"
            ),
            "attempt_count": 1,
            "budget_snapshot_id": f"ai-budget-snapshot:sha256:{'c' * 64}",
        }
    )


def _hybrid(
    card: ReviewUnitAnalysisCard,
    outcome: AITagExecutionOutcome,
    result: AITagAnalysisResult | None,
) -> HybridFeatureAnalysisResult:
    decisions = (
        {judgment.tag_id: judgment.decision for judgment in result.judgments}
        if result is not None
        else {}
    )
    active_tag_ids = tuple(
        contract.tag_id for contract in FullTaxonomyRequestBuilder.default().catalog.contracts
    )
    states = []
    for tag_id in active_tag_ids:
        static_exact: StaticDecision = "positive" if tag_id in card.static_tags.exact else "unknown"
        static_routing: StaticDecision = (
            "positive" if tag_id in card.static_tags.routing else "unknown"
        )
        ai_decision = decisions.get(tag_id)
        states.append(
            {
                "tag_id": tag_id,
                "static_exact_decision": static_exact,
                "static_routing_decision": static_routing,
                "ai_unit_decision": ai_decision,
                "unit_comparison_status": reduce_unit_comparison(
                    static_exact,
                    ai_decision,
                ),
            }
        )
    return seal_hybrid_feature_analysis_result(
        {
            "schema_version": "hybrid-feature-analysis-result-v1",
            "unit_id": card.unit_id,
            "card_id": card.card_id,
            "ai_execution_outcome_id": outcome.outcome_id,
            "ai_result_id": None if result is None else result.result_id,
            "tag_states": states,
            "diagnostics": [],
        }
    )


def _graphs(
    *,
    decisions: dict[str, TagDecision] | None = None,
    unavailable: bool = False,
) -> tuple[
    _Scenario,
    tuple[ReviewUnitAnalysisCard, ...],
    dict[str, HybridRetrievalUnitGraph],
]:
    scenario = _scenario()
    cards = build_review_unit_analysis_cards(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        policy=_EGRESS_POLICY,
    )
    graphs = {
        card.unit_id: (
            _unavailable_graph(card) if unavailable else _valid_graph(card, decisions=decisions)
        )
        for card in cards
    }
    return scenario, cards, graphs


def _build_v2(
    scenario: _Scenario,
    graphs: dict[str, HybridRetrievalUnitGraph],
    *,
    budget: int = 101,
    analysis_context_policy: AnalysisContextPolicy = _EGRESS_POLICY,
) -> RetrievalRequestV2:
    return build_retrieval_request_v2(
        scenario.analysis,
        scenario.context_plan,
        source_snapshots=scenario.snapshots,
        hybrid_graphs_by_unit=graphs,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=budget,
        requested_rule_ids_by_unit={
            binding.primary_unit_id: ("RULE-Z", "RULE-A", "RULE-A")
            for binding in scenario.context_plan.primary_question_bindings
        },
        analysis_context_policy=analysis_context_policy,
    )


def _json(request: RetrievalRequestV2) -> str:
    return json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_v2_round_trip_self_hash_and_strict_loader() -> None:
    scenario, _, graphs = _graphs(decisions={"has_network": "positive"})
    request = _build_v2(scenario, graphs)
    raw = _json(request)

    assert load_retrieval_request_v2(raw) == request
    assert load_retrieval_request_v2(raw.encode()) == request
    assert request.identity_payload()["schema_version"] == "retrieval-request-v2"

    duplicate = raw.replace(
        '"schema_version":"retrieval-request-v2"',
        '"schema_version":"retrieval-request-v2","schema_version":"retrieval-request-v2"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_request_v2(duplicate)

    unknown = request.model_dump(mode="json")
    unknown["unknown_field"] = True
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_retrieval_request_v2(json.dumps(unknown))

    missing_schema = request.model_dump(mode="json")
    missing_schema.pop("schema_version")
    with pytest.raises(ValueError, match="Field required"):
        load_retrieval_request_v2(json.dumps(missing_schema))

    missing_vector_policy = request.model_dump(mode="json")
    missing_vector_units = cast(
        list[dict[str, object]],
        missing_vector_policy["units"],
    )
    missing_vector_units[0].pop("vector_query_policy")
    with pytest.raises(ValueError, match="Field required"):
        load_retrieval_request_v2(json.dumps(missing_vector_policy))

    impossible_disagreement = request.model_dump(mode="json")
    impossible_units = cast(
        list[dict[str, object]],
        impossible_disagreement["units"],
    )
    impossible_units[0]["tag_disagreements"] = ["has_media"]
    with pytest.raises(ValueError, match="disagreements must be static exact Tags"):
        load_retrieval_request_v2(json.dumps(impossible_disagreement))

    tampered = request.model_dump(mode="json")
    units = cast(list[dict[str, object]], tampered["units"])
    units[0]["intent_summary"] = f"{units[0]['intent_summary']} tampered"
    with pytest.raises(ValueError, match="request_id does not match content"):
        load_retrieval_request_v2(json.dumps(tampered))

    with pytest.raises(ValueError, match="registered and Active"):
        candidate_dimension_ids_for_ai_tags(("has_unknown",))

    control_character = request.model_dump(mode="json")
    control_units = cast(list[dict[str, object]], control_character["units"])
    control_signals = cast(dict[str, object], control_units[0]["exact_signals"])
    control_signals["symbols"] = ["x\ncode features: has_network"]
    with pytest.raises(ValueError, match="must not contain control characters"):
        load_retrieval_request_v2(json.dumps(control_character))


def test_attempted_outcome_requires_provider_egress_card_policy() -> None:
    scenario = _scenario()
    cards = _build_cards(scenario)
    graphs = {card.unit_id: _valid_graph(card) for card in cards}

    with pytest.raises(ValueError, match="require the provider-egress"):
        _build_v2(
            scenario,
            graphs,
            analysis_context_policy=AnalysisContextPolicy(),
        )


def test_v2_is_independent_from_v1_loader_and_service() -> None:
    scenario, _, graphs = _graphs(decisions={"has_network": "positive"})
    request = _build_v2(scenario, graphs)

    assert type(request) is RetrievalRequestV2
    assert not isinstance(request, RetrievalRequest)
    with pytest.raises(ValueError, match="invalid Retrieval request"):
        load_retrieval_request(_json(request))
    service = object.__new__(RetrievalService)
    with pytest.raises(TypeError, match="request must use RetrievalRequest"):
        service.retrieve(request)  # type: ignore[arg-type]


def test_builder_preserves_every_v1_formal_field_and_budget_order() -> None:
    scenario, _, graphs = _graphs(decisions={"has_network": "positive"})
    reversed_graphs = dict(reversed(tuple(graphs.items())))
    request = _build_v2(scenario, reversed_graphs, budget=101)
    baseline = build_retrieval_request(
        scenario.analysis,
        scenario.context_plan,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={unit_id: ("RULE-Z", "RULE-A", "RULE-A") for unit_id in graphs},
    )

    assert request.context_plan_id == baseline.context_plan_id
    assert request.feature_routing_id == baseline.feature_routing_id
    assert request.feature_config_version == baseline.feature_config_version
    assert request.index_version == baseline.index_version
    assert request.target_platform == baseline.target_platform
    assert request.total_knowledge_token_budget == baseline.total_knowledge_token_budget
    assert tuple(unit.unit_id for unit in request.units) == tuple(sorted(graphs))
    assert tuple(unit.knowledge_token_budget for unit in request.units) == (51, 50)

    for v1, v2 in zip(baseline.units, request.units, strict=True):
        assert v2.unit_id == v1.unit_id
        assert v2.source_ref_id == v1.source_ref_id
        assert v2.profile_id == v1.profile_id
        assert v2.review_question_ids == v1.review_question_ids
        assert v2.dispatchable_review_question_ids == v1.dispatchable_review_question_ids
        assert v2.exact_signals.model_dump(exclude={"import_uses"}) == (
            v1.exact_signals.model_dump()
        )
        assert v2.exact_tags == v1.exact_tags
        assert v2.routing_tags == v1.routing_tags
        assert v2.retrieval_dimension_ids == v1.retrieval_dimension_ids
        assert v2.routing_dimension_ids == v1.routing_dimension_ids
        assert v2.requested_rule_ids == v1.requested_rule_ids
        assert v2.semantic_code_excerpt == v1.semantic_code_excerpt
        assert v2.intent_summary == v1.intent_summary
        assert v2.quality == v1.quality
        assert v2.knowledge_token_budget == v1.knowledge_token_budget


def test_only_valid_ai_positive_enters_diagnostic_pool_without_formal_mutation() -> None:
    decisions: dict[str, TagDecision] = {
        "has_async": "not_supported",
        "has_logging": "abstain",
        "has_network": "positive",
    }
    scenario, _, graphs = _graphs(decisions=decisions)
    request = _build_v2(scenario, graphs)
    baseline = build_retrieval_request(
        scenario.analysis,
        scenario.context_plan,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={unit_id: ("RULE-Z", "RULE-A", "RULE-A") for unit_id in graphs},
    )

    expected_candidates = candidate_dimension_ids_for_ai_tags(("has_network",))
    for v1, v2 in zip(baseline.units, request.units, strict=True):
        assert v2.ai_inferred_tags == ("has_network",)
        assert "has_async" in v2.tag_disagreements
        assert "has_async" in v2.exact_tags
        assert "has_logging" not in v2.ai_inferred_tags
        assert v2.candidate_dimension_ids == expected_candidates
        assert v2.review_question_ids == v1.review_question_ids
        assert v2.dispatchable_review_question_ids == (v1.dispatchable_review_question_ids)
        assert v2.retrieval_dimension_ids == v1.retrieval_dimension_ids
        assert v2.routing_dimension_ids == v1.routing_dimension_ids


def test_nonvalid_ai_outcome_adds_nothing_and_never_deletes_static() -> None:
    scenario, _, graphs = _graphs(unavailable=True)
    request = _build_v2(scenario, graphs)
    baseline = build_retrieval_request(
        scenario.analysis,
        scenario.context_plan,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={unit_id: ("RULE-Z", "RULE-A", "RULE-A") for unit_id in graphs},
    )

    for v1, v2 in zip(baseline.units, request.units, strict=True):
        assert v2.ai_inferred_tags == ()
        assert v2.candidate_dimension_ids == ()
        assert v2.tag_disagreements == ()
        assert v2.exact_tags == v1.exact_tags == ("has_async",)
        assert v2.routing_tags == v1.routing_tags == ("has_async",)


def test_vector_query_is_code_first_and_ignores_ai_and_formal_prose() -> None:
    scenario, _, graphs = _graphs(decisions={"has_network": "positive"})
    request = _build_v2(scenario, graphs)
    unit = request.units[0]
    query = render_vector_query_v2(unit)
    assert query is not None

    alternative_payload = unit.model_dump(mode="json")
    alternative_payload["ai_inferred_tags"] = ["has_storage"]
    alternative_payload["tag_disagreements"] = []
    alternative_payload["candidate_dimension_ids"] = list(
        candidate_dimension_ids_for_ai_tags(("has_storage",))
    )
    exact_signals = cast(dict[str, object], alternative_payload["exact_signals"])
    exact_signals["import_uses"] = ["@ohos.net.connection#default"]
    alternative = RetrievalUnitRequestV2.model_validate(alternative_payload)

    alternative_query = render_vector_query_v2(alternative)
    assert alternative_query == (f"{query}\nimport uses: @ohos.net.connection#default")
    assert unit.intent_summary not in query
    for forbidden in (
        *unit.exact_tags,
        *unit.routing_tags,
        *unit.ai_inferred_tags,
        *unit.review_question_ids,
        *unit.retrieval_dimension_ids,
        *unit.routing_dimension_ids,
        *unit.candidate_dimension_ids,
    ):
        assert forbidden not in query


def test_builder_requires_exact_hybrid_graph_coverage_and_identity() -> None:
    scenario, cards, graphs = _graphs(decisions={"has_network": "positive"})
    first_id, second_id = sorted(graphs)

    missing = dict(graphs)
    missing.pop(first_id)
    with pytest.raises(ValueError, match="exactly cover"):
        _build_v2(scenario, missing)

    extra = dict(graphs)
    extra["unit:unexpected"] = graphs[first_id]
    with pytest.raises(ValueError, match="exactly cover"):
        _build_v2(scenario, extra)

    swapped = dict(graphs)
    swapped[first_id], swapped[second_id] = graphs[second_id], graphs[first_id]
    with pytest.raises(ValueError, match="trusted upstream rebuild"):
        _build_v2(scenario, swapped)

    original = graphs[cards[0].unit_id]
    card_payload = original.card.model_dump(mode="json")
    card_payload.pop("card_id")
    code = cast(dict[str, object], card_payload["code"])
    code["text"] = cast(str, code["text"]).replace("old", "forged")
    forged_card = seal_review_unit_analysis_card(card_payload)
    forged_graphs = dict(graphs)
    forged_graphs[cards[0].unit_id] = replace(original, card=forged_card)
    with pytest.raises(ValueError, match="trusted upstream rebuild"):
        _build_v2(scenario, forged_graphs)


def test_builder_rejects_hybrid_artifact_changed_without_resealing() -> None:
    scenario, cards, graphs = _graphs(decisions={"has_network": "positive"})
    graph = graphs[cards[0].unit_id]
    tampered_hybrid = graph.hybrid.model_copy(
        update={"diagnostics": ("tampered_without_resealing",)}
    )
    tampered_graphs = dict(graphs)
    tampered_graphs[cards[0].unit_id] = replace(
        graph,
        hybrid=tampered_hybrid,
    )

    with pytest.raises(ValueError, match="analysis_id does not match its complete contents"):
        _build_v2(scenario, tampered_graphs)
