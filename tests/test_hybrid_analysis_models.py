from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import cast

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.hybrid_analysis import (
    AIModelCode,
    AITagAnalysisRequest,
    AITagAnalysisResult,
    AITagContractView,
    AITagExecutionOutcome,
    AITagJudgment,
    AITagModelView,
    AnalysisCode,
    HybridFeatureAnalysisResult,
    HybridTagState,
    OwnerSummary,
    ReviewUnitAnalysisCard,
    StaticDecision,
    load_ai_tag_analysis_request,
    load_ai_tag_analysis_result,
    load_ai_tag_contract_view,
    load_ai_tag_execution_outcome,
    load_ai_tag_model_view,
    load_hybrid_feature_analysis_result,
    load_review_unit_analysis_card,
    project_owner_summary,
    reduce_unit_comparison,
    seal_ai_tag_analysis_request,
    seal_ai_tag_analysis_result,
    seal_ai_tag_contract_view,
    seal_ai_tag_execution_outcome,
    seal_ai_tag_model_view,
    seal_hybrid_feature_analysis_result,
    seal_review_unit_analysis_card,
    taxonomy_fingerprint,
    verify_hybrid_chain,
    verify_model_view_against_card,
    verify_outcome_against_result,
    verify_request_against_model_view,
    verify_result_against_request,
)


def _hash_id(prefix: str, marker: str) -> str:
    return f"{prefix}:sha256:{marker * 64}"


def _active_tag_ids() -> tuple[str, ...]:
    config = load_default_feature_config()
    return tuple(
        sorted(tag_id for tag_id, tag in config.tags_by_id.items() if tag.status == "Active")
    )


def _fact_set(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "apis": [],
        "components": [],
        "decorators": [],
        "attributes": [],
        "symbols": [],
        "syntax": [],
        "calls": [],
        "import_bindings": [],
        "import_uses": [],
        "field_reads": [],
        "field_writes": [],
        "string_literals": [],
        "resource_references": [],
    }
    payload.update(updates)
    return payload


def _scoped_facts() -> dict[str, object]:
    return {
        "unit_exact": _fact_set(
            calls=["connection.createNetConnection", "this.netCon.register"],
            symbols=["Index.addNetworkListener"],
            syntax=["async_fn"],
        ),
        "file_hints": _fact_set(
            apis=["http.createHttp"],
            import_uses=["@ohos.net.connection#default"],
        ),
    }


def _quality(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "parser_layer": "L1",
        "error_nodes": 0,
        "missing_nodes": 0,
        "context_degraded": False,
        "unit_owner_unresolved": False,
    }
    payload.update(updates)
    return payload


def _card_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "review-unit-analysis-card-v1",
        "unit_id": "unit:Index.addNetworkListener",
        "source_ref_id": _hash_id("code-source", "1"),
        "feature_profile_id": _hash_id("feature-profile", "e"),
        "feature_routing_id": _hash_id("feature-routing", "f"),
        "context_plan_id": _hash_id("context-plan", "a"),
        "source_role": "head",
        "unit_kind": "method",
        "unit_symbol": "Index.addNetworkListener",
        "owner_summary": {
            "resolution": "resolved",
            "unit_owner": {
                "kind": "declaration",
                "ref_id": _hash_id("declaration", "c"),
                "owner_kind": "method",
                "qualified_name": "Index.addNetworkListener",
                "quality": "exact",
            },
            "enclosing_owner": {
                "kind": "declaration",
                "ref_id": _hash_id("declaration", "d"),
                "owner_kind": "struct",
                "qualified_name": "Index",
                "quality": "exact",
            },
            "owner_roles": ["arkui_custom_component"],
            "diagnostics": [],
        },
        "code": {
            "mode": "full_unit",
            "text": "async addNetworkListener() {\n  connection.createNetConnection();\n}",
            "line_start": 152,
            "line_end": 154,
            "changed_line_numbers": [153],
            "truncated": False,
        },
        "change_atom_ids": [_hash_id("change-atom", "2")],
        "exact_occurrence_ids": [_hash_id("occurrence", "a")],
        "owner_context_occurrence_ids": [],
        "owner_context_declaration_ids": [],
        "unit_fact_diagnostics": [],
        "facts": _scoped_facts(),
        "static_tags": {
            "exact": ["has_async"],
            "routing": ["has_network"],
            "matches": [
                {
                    "tag_id": "has_network",
                    "status": "Active",
                    "scope": "file_hint",
                    "signals": [
                        {
                            "signal_type": "basic",
                            "kind": "apis",
                            "value": "http.createHttp",
                        }
                    ],
                },
                {
                    "tag_id": "has_async",
                    "status": "Active",
                    "scope": "unit_exact",
                    "signals": [
                        {
                            "signal_type": "basic",
                            "kind": "syntax",
                            "value": "async_fn",
                        }
                    ],
                },
            ],
        },
        "quality": _quality(),
        "available_context_refs": [],
        "code_token_budget": 2400,
        "feature_config_fingerprint": _hash_id("feature-config", "3"),
        "context_policy_fingerprint": _hash_id("analysis-context-policy", "4"),
    }
    payload.update(updates)
    return payload


def _card(**updates: object) -> ReviewUnitAnalysisCard:
    return seal_review_unit_analysis_card(_card_payload(**updates))


def _model_view_payload(
    card: ReviewUnitAnalysisCard,
    **updates: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ai-tag-model-view-v1",
        "card_id": card.card_id,
        "unit_id": card.unit_id,
        "source_ref_id": card.source_ref_id,
        "code": {
            "mode": card.code.mode,
            "numbered_text": (
                "152: async addNetworkListener() {\n"
                "153:   connection.createNetConnection();\n"
                "154: }"
            ),
            "line_numbers": [152, 153, 154],
            "truncated": card.code.truncated,
        },
        "owner_summary": project_owner_summary(card.owner_summary).model_dump(
            mode="json"
        ),
        "scoped_facts": card.facts.model_dump(mode="json"),
        "quality": card.quality.model_dump(mode="json"),
        "projection_policy_fingerprint": _hash_id("ai-model-view-policy", "5"),
    }
    payload.update(updates)
    return payload


def _model_view(
    card: ReviewUnitAnalysisCard,
    **updates: object,
) -> AITagModelView:
    return seal_ai_tag_model_view(_model_view_payload(card, **updates))


def _contracts() -> tuple[AITagContractView, ...]:
    config = load_default_feature_config()
    return tuple(
        seal_ai_tag_contract_view(
            {
                "schema_version": "ai-tag-contract-view-v1",
                "tag_id": tag_id,
                "definition": config.tags_by_id[tag_id].description,
                "inclusions": [f"当前 Unit 的语义满足 {tag_id}"],
                "exclusions": [f"只有文件级提示但当前 Unit 不满足 {tag_id}"],
                "hard_negatives": [f"标识符文字提到 {tag_id} 但没有对应语义"],
            }
        )
        for tag_id in _active_tag_ids()
    )


def _request_payload(
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    contracts: tuple[AITagContractView, ...],
    **updates: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ai-tag-analysis-request-v1",
        "card_id": card.card_id,
        "model_view_id": model_view.model_view_id,
        "taxonomy_delivery_mode": "full_single",
        "active_taxonomy_fingerprint": taxonomy_fingerprint(contracts),
        "tag_contract_views": [item.model_dump(mode="json") for item in contracts],
        "required_tag_count": 24,
        "prompt_version": "deepseek-tag-analysis-v1",
        "prompt_hash": f"sha256:{'6' * 64}",
        "model_policy_fingerprint": _hash_id("ai-tag-policy", "7"),
    }
    payload.update(updates)
    return payload


def _request(
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    contracts: tuple[AITagContractView, ...],
    **updates: object,
) -> AITagAnalysisRequest:
    return seal_ai_tag_analysis_request(
        _request_payload(card, model_view, contracts, **updates)
    )


def _judgments(
    *,
    positive_evidence_line: int = 153,
) -> tuple[dict[str, object], ...]:
    judgments: list[dict[str, object]] = []
    for tag_id in _active_tag_ids():
        if tag_id == "has_network":
            judgments.append(
                {
                    "tag_id": tag_id,
                    "decision": "positive",
                    "evidence_lines": [positive_evidence_line],
                    "reason_code": "direct_network_connection_semantics",
                    "reason": "当前 Unit 创建网络连接。",
                }
            )
        else:
            judgments.append(
                {
                    "tag_id": tag_id,
                    "decision": "not_supported",
                    "evidence_lines": [],
                    "reason_code": "no_support_in_complete_view",
                    "reason": None,
                }
            )
    return tuple(judgments)


def _result_payload(
    request: AITagAnalysisRequest,
    **updates: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ai-tag-analysis-result-v1",
        "request_id": request.request_id,
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "system_fingerprint": "not_reported",
        "thinking": "disabled",
        "reasoning_effort": None,
        "response_format": "json_object",
        "finish_reason": "stop",
        "judgments": list(_judgments()),
        "usage": {
            "input_tokens": 4000,
            "output_tokens": 800,
            "cache_read_input_tokens": 0,
        },
        "latency_ms": 123,
        "attempt_count": 1,
        "output_status": "valid",
    }
    payload.update(updates)
    return payload


def _result(
    request: AITagAnalysisRequest,
    **updates: object,
) -> AITagAnalysisResult:
    return seal_ai_tag_analysis_result(_result_payload(request, **updates))


def _outcome_payload(
    request: AITagAnalysisRequest,
    result: AITagAnalysisResult,
    **updates: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ai-tag-execution-outcome-v1",
        "analysis_run_id": _hash_id("ai-tag-run", "8"),
        "card_id": request.card_id,
        "model_view_id": request.model_view_id,
        "request_id": request.request_id,
        "status": "valid_result",
        "result_id": result.result_id,
        "reason_code": "provider_response_valid",
        "attempt_count": 1,
        "budget_snapshot_id": _hash_id("ai-budget-snapshot", "9"),
    }
    payload.update(updates)
    return payload


def _outcome(
    request: AITagAnalysisRequest,
    result: AITagAnalysisResult,
    **updates: object,
) -> AITagExecutionOutcome:
    return seal_ai_tag_execution_outcome(_outcome_payload(request, result, **updates))


def _hybrid_payload(
    card: ReviewUnitAnalysisCard,
    outcome: AITagExecutionOutcome,
    result: AITagAnalysisResult | None,
    **updates: object,
) -> dict[str, object]:
    decisions = (
        {item.tag_id: item.decision for item in result.judgments}
        if result is not None
        else {}
    )
    states: list[dict[str, object]] = []
    for tag_id in _active_tag_ids():
        exact: StaticDecision = (
            "positive" if tag_id == "has_async" else "unknown"
        )
        routing: StaticDecision = (
            "positive" if tag_id == "has_network" else "unknown"
        )
        ai_decision = decisions.get(tag_id)
        states.append(
            {
                "tag_id": tag_id,
                "static_exact_decision": exact,
                "static_routing_decision": routing,
                "ai_unit_decision": ai_decision,
                "unit_comparison_status": reduce_unit_comparison(exact, ai_decision),
            }
        )
    payload: dict[str, object] = {
        "schema_version": "hybrid-feature-analysis-result-v1",
        "unit_id": card.unit_id,
        "card_id": card.card_id,
        "ai_execution_outcome_id": outcome.outcome_id,
        "ai_result_id": None if result is None else result.result_id,
        "tag_states": states,
        "diagnostics": [],
    }
    payload.update(updates)
    return payload


def _chain() -> tuple[
    ReviewUnitAnalysisCard,
    AITagModelView,
    tuple[AITagContractView, ...],
    AITagAnalysisRequest,
    AITagAnalysisResult,
    AITagExecutionOutcome,
    HybridFeatureAnalysisResult,
]:
    card = _card()
    view = _model_view(card)
    contracts = _contracts()
    request = _request(card, view, contracts)
    result = _result(request)
    outcome = _outcome(request, result)
    hybrid = seal_hybrid_feature_analysis_result(
        _hybrid_payload(card, outcome, result)
    )
    return card, view, contracts, request, result, outcome, hybrid


def _json(model: object) -> str:
    dumped = cast(ReviewUnitAnalysisCard, model).model_dump(mode="json")
    return json.dumps(dumped, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def test_valid_artifact_chain_round_trips_and_is_frozen() -> None:
    card, view, contracts, request, result, outcome, hybrid = _chain()

    verify_model_view_against_card(view, card)
    verify_request_against_model_view(
        request,
        view,
        active_tag_ids=_active_tag_ids(),
        active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
    )
    verify_result_against_request(result, request, view)
    verify_outcome_against_result(outcome, result)
    verify_hybrid_chain(
        hybrid,
        card,
        view,
        request,
        outcome,
        result,
        active_tag_ids=_active_tag_ids(),
        active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
    )

    assert load_review_unit_analysis_card(_json(card)) == card
    assert load_ai_tag_model_view(_json(view)) == view
    assert load_ai_tag_contract_view(_json(contracts[0])) == contracts[0]
    assert load_ai_tag_analysis_request(_json(request)) == request
    assert load_ai_tag_analysis_result(_json(result)) == result
    assert load_ai_tag_execution_outcome(_json(outcome)) == outcome
    assert load_hybrid_feature_analysis_result(_json(hybrid)) == hybrid

    with pytest.raises(ValidationError, match="Instance is frozen"):
        card.unit_id = "mutated"


def test_closed_schema_field_sets_are_frozen() -> None:
    assert set(ReviewUnitAnalysisCard.model_fields) == {
        "schema_version",
        "card_id",
        "unit_id",
        "source_ref_id",
        "feature_profile_id",
        "feature_routing_id",
        "context_plan_id",
        "source_role",
        "unit_kind",
        "unit_symbol",
        "owner_summary",
        "code",
        "change_atom_ids",
        "exact_occurrence_ids",
        "owner_context_occurrence_ids",
        "owner_context_declaration_ids",
        "unit_fact_diagnostics",
        "facts",
        "static_tags",
        "quality",
        "available_context_refs",
        "code_token_budget",
        "feature_config_fingerprint",
        "context_policy_fingerprint",
    }
    assert set(AITagModelView.model_fields) == {
        "schema_version",
        "model_view_id",
        "card_id",
        "unit_id",
        "source_ref_id",
        "code",
        "owner_summary",
        "scoped_facts",
        "quality",
        "projection_policy_fingerprint",
    }
    assert set(AITagAnalysisRequest.model_fields) == {
        "schema_version",
        "request_id",
        "card_id",
        "model_view_id",
        "taxonomy_delivery_mode",
        "active_taxonomy_fingerprint",
        "tag_contract_views",
        "required_tag_count",
        "prompt_version",
        "prompt_hash",
        "model_policy_fingerprint",
    }
    assert set(AITagAnalysisResult.model_fields) == {
        "schema_version",
        "result_id",
        "request_id",
        "provider",
        "model",
        "system_fingerprint",
        "thinking",
        "reasoning_effort",
        "response_format",
        "finish_reason",
        "judgments",
        "usage",
        "latency_ms",
        "attempt_count",
        "output_status",
    }
    assert set(AITagExecutionOutcome.model_fields) == {
        "schema_version",
        "outcome_id",
        "analysis_run_id",
        "card_id",
        "model_view_id",
        "request_id",
        "status",
        "result_id",
        "reason_code",
        "attempt_count",
        "budget_snapshot_id",
    }
    assert set(HybridFeatureAnalysisResult.model_fields) == {
        "schema_version",
        "analysis_id",
        "unit_id",
        "card_id",
        "ai_execution_outcome_id",
        "ai_result_id",
        "tag_states",
        "diagnostics",
    }


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda payload: payload.update({"unexpected": True}), "Extra inputs"),
        (lambda payload: payload.pop("schema_version"), "Field required"),
        (
            lambda payload: cast(dict[str, object], payload["code"]).update(
                {"static_tags": ["has_network"]}
            ),
            "Extra inputs",
        ),
        (lambda payload: payload.update({"code_token_budget": True}), "valid integer"),
        (
            lambda payload: payload.update({"schema_version": "review-unit-analysis-card-v2"}),
            "review-unit-analysis-card-v1",
        ),
    ],
)
def test_card_schema_fails_closed(
    mutate: Callable[[dict[str, object]], object],
    match: str,
) -> None:
    payload = _card_payload()
    mutate(payload)
    with pytest.raises(ValueError, match=match):
        seal_review_unit_analysis_card(payload)


def test_strict_json_loader_rejects_duplicate_nonfinite_and_non_object_input() -> None:
    card = _card()
    raw = _json(card)

    duplicate_top = raw[:-1] + f',"unit_id":"{card.unit_id}"' + "}"
    with pytest.raises(ValueError, match="duplicate JSON key: unit_id"):
        load_review_unit_analysis_card(duplicate_top)

    duplicate_nested = raw.replace('"mode":"full_unit"', '"mode":"full_unit","mode":"full_unit"')
    with pytest.raises(ValueError, match="duplicate JSON key: mode"):
        load_review_unit_analysis_card(duplicate_nested)

    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_review_unit_analysis_card('{"value":NaN}')
    with pytest.raises(ValueError, match="top-level value must be an object"):
        load_review_unit_analysis_card("[]")
    with pytest.raises(ValueError, match="must use UTF-8"):
        load_review_unit_analysis_card(b"\xff")


@pytest.mark.parametrize(
    "mutation",
    [
        {"change_atom_ids": [_hash_id("change-atom", "2"), _hash_id("change-atom", "2")]},
        {
            "static_tags": {
                "exact": ["has_network", "has_async"],
                "routing": [],
                "matches": [],
            }
        },
        {
            "code": {
                "mode": "full_unit",
                "text": "x\ny\nz",
                "line_start": 1,
                "line_end": 3,
                "changed_line_numbers": [2, 1],
                "truncated": False,
            }
        },
    ],
)
def test_set_like_card_sequences_must_be_sorted_and_unique(
    mutation: Mapping[str, object],
) -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        seal_review_unit_analysis_card(_card_payload(**mutation))


def test_content_identity_detects_tampering_for_every_artifact() -> None:
    card, view, contracts, request, result, outcome, hybrid = _chain()
    cases = (
        (load_review_unit_analysis_card, card, "code_token_budget", 2399),
        (
            load_ai_tag_model_view,
            view,
            "projection_policy_fingerprint",
            _hash_id("ai-model-view-policy", "a"),
        ),
        (load_ai_tag_contract_view, contracts[0], "definition", "篡改后的定义"),
        (load_ai_tag_analysis_request, request, "prompt_version", "mutated-prompt-v1"),
        (load_ai_tag_analysis_result, result, "latency_ms", 999),
        (
            load_ai_tag_execution_outcome,
            outcome,
            "budget_snapshot_id",
            _hash_id("ai-budget-snapshot", "b"),
        ),
        (load_hybrid_feature_analysis_result, hybrid, "diagnostics", ["mutated"]),
    )
    for loader, model, field, value in cases:
        payload = model.model_dump(mode="json")
        payload[field] = value
        with pytest.raises(ValueError, match="does not match its complete contents"):
            loader(json.dumps(payload, ensure_ascii=False))


def test_content_identity_known_answers_lock_canonicalization() -> None:
    card, view, contracts, request, result, outcome, hybrid = _chain()

    assert card.card_id == (
        "analysis-card:sha256:"
        "264cf6aa786e6309f59c468606932fc95b4aaaa62f0ef70daae23a9c91338b6f"
    )
    assert view.model_view_id == (
        "ai-tag-model-view:sha256:"
        "5be3c2fac26773e3ec6ed085d29bafc6d2352c02b1eb39a948f5461662f93054"
    )
    assert contracts[0].contract_fingerprint == (
        "ai-tag-contract-view:sha256:"
        "1f975a971ff6b27033e56e774816bc11b305052eee58b81ee233b2eaeffe9655"
    )
    assert request.request_id == (
        "ai-tag-request:sha256:"
        "e9f0f8ebb9d953a613af44c28fcdad13dfbdd3b894440f2c2c8875cd70c3c324"
    )
    assert result.result_id == (
        "ai-tag-result:sha256:"
        "022b976213456ff6c22182e856addc867521947176a4e8c1b28f8e400e5464ff"
    )
    assert outcome.outcome_id == (
        "ai-tag-outcome:sha256:"
        "f83cfccc31e36d03d93611592d3d8c96025b6cb10c197e017133f6fd8fd12a64"
    )
    assert hybrid.analysis_id == (
        "hybrid-analysis:sha256:"
        "6116f011a1a17d3222b3b99043b6d95f65b162ae1867dba3efcfe56ee9f5c44a"
    )


def test_full_24_request_binds_contract_contents_and_registry_snapshot() -> None:
    card = _card()
    view = _model_view(card)
    contracts = _contracts()
    request = _request(card, view, contracts)

    assert request.required_tag_count == len(request.tag_contract_views) == 24
    assert tuple(item.tag_id for item in request.tag_contract_views) == _active_tag_ids()

    missing = contracts[:-1]
    with pytest.raises(ValueError, match="exactly 24"):
        _request(card, view, missing)

    reversed_contracts = tuple(reversed(contracts))
    with pytest.raises(ValueError, match="canonical unique Tag order"):
        _request(card, view, reversed_contracts)

    payload = _request_payload(card, view, contracts)
    payload["active_taxonomy_fingerprint"] = _hash_id("ai-tag-taxonomy", "0")
    with pytest.raises(ValueError, match="does not match contracts"):
        seal_ai_tag_analysis_request(payload)

    with pytest.raises(ValueError, match="registry snapshot"):
        verify_request_against_model_view(
            request,
            view,
            active_tag_ids=tuple(reversed(_active_tag_ids())),
            active_taxonomy_fingerprint=request.active_taxonomy_fingerprint,
        )


@pytest.mark.parametrize(
    ("decision", "evidence", "reason_code", "reason", "valid"),
    [
        ("positive", [153], "direct_network_semantics", "调用网络 API。", True),
        ("not_supported", [], "no_support_in_complete_view", None, True),
        ("abstain", [], "insufficient_context", "需要更多上下文。", True),
        ("positive", [], "direct_network_semantics", "调用网络 API。", False),
        ("positive", [153], "view_truncated", "调用网络 API。", False),
        ("not_supported", [153], "no_support_in_complete_view", None, False),
        ("not_supported", [], "insufficient_context", None, False),
        ("abstain", [153], "insufficient_context", "需要更多上下文。", False),
        ("abstain", [], "no_support_in_complete_view", "需要更多上下文。", False),
    ],
)
def test_judgment_decision_evidence_reason_matrix(
    decision: str,
    evidence: list[int],
    reason_code: str,
    reason: str | None,
    valid: bool,
) -> None:
    payload = {
        "tag_id": "has_network",
        "decision": decision,
        "evidence_lines": evidence,
        "reason_code": reason_code,
        "reason": reason,
    }
    if valid:
        assert AITagJudgment.model_validate(payload).decision == decision
    else:
        with pytest.raises(ValidationError):
            AITagJudgment.model_validate(payload)


def test_result_is_all_or_nothing_and_evidence_is_checked_against_view() -> None:
    card = _card()
    view = _model_view(card)
    contracts = _contracts()
    request = _request(card, view, contracts)

    with pytest.raises(ValueError, match="exactly 24"):
        _result(request, judgments=list(_judgments())[:-1])

    duplicate = list(_judgments())
    duplicate[-1] = deepcopy(duplicate[0])
    with pytest.raises(ValueError, match="canonical unique Tag order"):
        _result(request, judgments=duplicate)

    out_of_range = _result(request, judgments=list(_judgments(positive_evidence_line=999)))
    with pytest.raises(ValueError, match="outside the Model View"):
        verify_result_against_request(out_of_range, request, view)
    out_of_range_outcome = _outcome(request, out_of_range)
    out_of_range_hybrid = seal_hybrid_feature_analysis_result(
        _hybrid_payload(card, out_of_range_outcome, out_of_range)
    )
    with pytest.raises(ValueError, match="outside the Model View"):
        verify_hybrid_chain(
            out_of_range_hybrid,
            card,
            view,
            request,
            out_of_range_outcome,
            out_of_range,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )

    truncated_view = _model_view(
        card,
        code={
            "mode": "full_unit",
            "numbered_text": "153: connection.createNetConnection();",
            "line_numbers": [153],
            "truncated": True,
        },
    )
    truncated_request = _request(card, truncated_view, contracts)
    truncated_result = _result(truncated_request)
    with pytest.raises(ValueError, match="degraded Model View"):
        verify_result_against_request(truncated_result, truncated_request, truncated_view)

    partial_owner = cast(dict[str, object], _card_payload()["owner_summary"])
    partial_owner["resolution"] = "partial"
    partial_owner["diagnostics"] = ["owner_context_symbol_unresolved"]
    partial_card = _card(owner_summary=partial_owner)
    partial_view = _model_view(partial_card)
    partial_request = _request(partial_card, partial_view, contracts)
    partial_result = _result(partial_request)
    with pytest.raises(ValueError, match="degraded Model View"):
        verify_result_against_request(
            partial_result,
            partial_request,
            partial_view,
        )

    unrelated_view = _model_view(
        card,
        projection_policy_fingerprint=_hash_id("ai-model-view-policy", "a"),
    )
    with pytest.raises(ValueError, match="does not reference the supplied Model View"):
        verify_result_against_request(
            result=_result(request),
            request=request,
            model_view=unrelated_view,
        )


def test_l1_quality_requires_explicit_error_and_missing_counts() -> None:
    with pytest.raises(ValueError, match="must carry explicit AST node counts"):
        _card(quality=_quality(error_nodes=None, missing_nodes=None))


def test_owner_summary_represents_method_struct_and_partial_evidence() -> None:
    method = _card().owner_summary
    assert method.resolution == "resolved"
    assert method.unit_owner is not None
    assert method.enclosing_owner is not None
    assert method.unit_owner.ref_id != method.enclosing_owner.ref_id

    struct_identity = {
        "kind": "declaration",
        "ref_id": _hash_id("declaration", "e"),
        "owner_kind": "struct",
        "qualified_name": "Index",
        "quality": "exact",
    }
    struct = OwnerSummary.model_validate(
        {
            "resolution": "resolved",
            "unit_owner": struct_identity,
            "enclosing_owner": struct_identity,
            "owner_roles": ["arkui_custom_component"],
            "diagnostics": [],
        }
    )
    assert project_owner_summary(struct).enclosing_owner_kind == "struct"

    partial_payload = struct.model_dump(mode="json")
    partial_payload["resolution"] = "partial"
    partial_payload["diagnostics"] = ["owner_context_symbol_unresolved"]
    partial = OwnerSummary.model_validate(partial_payload)
    assert partial.resolution == "partial"
    assert project_owner_summary(partial).diagnostics == (
        "owner_context_symbol_unresolved",
    )

    invalid_method_payload = method.model_dump(mode="json")
    invalid_method_payload["enclosing_owner"] = invalid_method_payload["unit_owner"]
    with pytest.raises(ValidationError, match="Only a struct Unit"):
        OwnerSummary.model_validate(invalid_method_payload)

    inconsistent_struct = struct.model_dump(mode="json")
    enclosing = cast(dict[str, object], inconsistent_struct["enclosing_owner"])
    enclosing["qualified_name"] = "Other"
    with pytest.raises(ValidationError, match="identical contents"):
        OwnerSummary.model_validate(inconsistent_struct)


def test_card_owner_summary_must_match_review_unit_kind_and_fallback_boundary() -> None:
    owner = cast(dict[str, object], _card_payload()["owner_summary"])
    wrong_owner = deepcopy(owner)
    unit_owner = cast(dict[str, object], wrong_owner["unit_owner"])
    unit_owner["owner_kind"] = "class"
    with pytest.raises(ValueError, match="owner must match"):
        _card(owner_summary=wrong_owner)

    wrong_symbol = deepcopy(owner)
    unit_owner = cast(dict[str, object], wrong_symbol["unit_owner"])
    unit_owner["qualified_name"] = "Other.addNetworkListener"
    with pytest.raises(ValueError, match="owner must match"):
        _card(owner_summary=wrong_symbol)

    fallback_owner: dict[str, object] = {
        "resolution": "not_applicable",
        "unit_owner": None,
        "enclosing_owner": None,
        "owner_roles": [],
        "diagnostics": [],
    }
    fallback = _card(
        unit_kind="fallback",
        unit_symbol="hunk-L152-L154",
        owner_summary=fallback_owner,
        quality=_quality(context_degraded=True),
    )
    assert fallback.owner_summary.resolution == "not_applicable"

    with pytest.raises(ValueError, match="fallback Analysis Card"):
        _card(
            unit_kind="fallback",
            unit_symbol="hunk-L152-L154",
            owner_summary=owner,
            quality=_quality(context_degraded=True),
        )

    method_not_applicable = deepcopy(owner)
    method_not_applicable["resolution"] = "not_applicable"
    method_not_applicable["enclosing_owner"] = None
    method_not_applicable["owner_roles"] = []
    with pytest.raises(ValueError, match="owner-aware Analysis Card"):
        _card(owner_summary=method_not_applicable)

    function_owner = {
        "kind": "declaration",
        "ref_id": _hash_id("declaration", "e"),
        "owner_kind": "function",
        "qualified_name": "loadData",
        "quality": "exact",
    }
    function_card = _card(
        unit_kind="function",
        unit_symbol="loadData",
        owner_summary={
            "resolution": "not_applicable",
            "unit_owner": function_owner,
            "enclosing_owner": None,
            "owner_roles": [],
            "diagnostics": [],
        },
    )
    assert function_card.owner_summary.unit_owner is not None


@pytest.mark.parametrize(
    ("status", "request_present", "result_present", "attempts", "reason"),
    [
        ("valid_result", True, True, 1, "provider_response_valid"),
        ("invalid_output", True, False, 1, "schema_invalid"),
        ("unavailable", True, False, 1, "provider_timeout"),
        ("skipped_budget", True, False, 0, "budget_exhausted"),
        ("not_run", False, False, 0, "taxonomy_mismatch"),
    ],
)
def test_execution_outcome_status_matrix(
    status: str,
    request_present: bool,
    result_present: bool,
    attempts: int,
    reason: str,
) -> None:
    _, _, _, request, result, _, _ = _chain()
    outcome = seal_ai_tag_execution_outcome(
        {
            "schema_version": "ai-tag-execution-outcome-v1",
            "analysis_run_id": _hash_id("ai-tag-run", "8"),
            "card_id": request.card_id,
            "model_view_id": request.model_view_id,
            "request_id": request.request_id if request_present else None,
            "status": status,
            "result_id": result.result_id if result_present else None,
            "reason_code": reason,
            "attempt_count": attempts,
            "budget_snapshot_id": _hash_id("ai-budget-snapshot", "9"),
        }
    )
    assert outcome.status == status


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "valid_result", "result_id": None},
        {
            "status": "invalid_output",
            "result_id": _hash_id("ai-tag-result", "a"),
            "reason_code": "schema_invalid",
        },
        {
            "status": "skipped_budget",
            "attempt_count": 1,
            "result_id": None,
            "reason_code": "budget_exhausted",
        },
        {
            "status": "not_run",
            "request_id": _hash_id("ai-tag-request", "a"),
            "result_id": None,
            "attempt_count": 0,
            "reason_code": "taxonomy_mismatch",
        },
        {"status": "unavailable", "result_id": None, "reason_code": "provider_response_valid"},
    ],
)
def test_execution_outcome_rejects_illegal_status_cross_products(
    updates: Mapping[str, object],
) -> None:
    _, _, _, request, result, _, _ = _chain()
    with pytest.raises(ValueError):
        _outcome(request, result, **updates)


def test_valid_outcome_attempt_count_must_match_result() -> None:
    _, _, _, request, _, _, _ = _chain()
    result = _result(request, attempt_count=2)
    outcome = _outcome(request, result, attempt_count=1)

    with pytest.raises(ValueError, match="attempt counts differ"):
        verify_outcome_against_result(outcome, result)


@pytest.mark.parametrize(
    ("static_exact", "ai_decision", "expected"),
    [
        ("positive", "positive", "agreement_positive"),
        ("positive", "not_supported", "disagreement"),
        ("positive", "abstain", "static_only"),
        ("unknown", "positive", "ai_only"),
        ("unknown", "not_supported", "no_positive_signal"),
        ("unknown", "abstain", "unresolved"),
        ("positive", None, "static_only_due_execution"),
        ("unknown", None, "unresolved_due_execution"),
    ],
)
@pytest.mark.parametrize("static_routing", ["positive", "unknown"])
def test_exact_ai_reducer_is_independent_of_routing_axis(
    static_exact: str,
    ai_decision: str | None,
    expected: str,
    static_routing: str,
) -> None:
    state = HybridTagState.model_validate(
        {
            "tag_id": "has_network",
            "static_exact_decision": static_exact,
            "static_routing_decision": static_routing,
            "ai_unit_decision": ai_decision,
            "unit_comparison_status": expected,
        }
    )
    assert state.unit_comparison_status == expected


def test_hybrid_rejects_status_tampering_and_ai_promotion_fields() -> None:
    card, view, contracts, request, result, outcome, hybrid = _chain()
    payload = _hybrid_payload(card, outcome, result)
    states = cast(list[dict[str, object]], payload["tag_states"])
    states[0]["unit_comparison_status"] = "agreement_positive"
    with pytest.raises(ValueError, match="does not match exact/AI axes"):
        seal_hybrid_feature_analysis_result(payload)

    payload = _hybrid_payload(card, outcome, result)
    payload["exact_tags"] = ["has_network"]
    with pytest.raises(ValueError, match="Extra inputs"):
        seal_hybrid_feature_analysis_result(payload)

    non_valid = _outcome(
        request,
        result,
        status="invalid_output",
        result_id=None,
        reason_code="schema_invalid",
    )
    payload = _hybrid_payload(card, non_valid, None)
    payload["ai_result_id"] = result.result_id
    with pytest.raises(ValueError, match="requires all AI decisions"):
        seal_hybrid_feature_analysis_result(payload)

    network_state = hybrid.tag_states[_active_tag_ids().index("has_network")]
    assert network_state.static_exact_decision == "unknown"

    payload = _hybrid_payload(card, outcome, result)
    states = cast(list[dict[str, object]], payload["tag_states"])
    network = states[_active_tag_ids().index("has_network")]
    network["static_exact_decision"] = "positive"
    network["unit_comparison_status"] = "agreement_positive"
    forged = seal_hybrid_feature_analysis_result(payload)
    with pytest.raises(ValueError, match="static Tag axes differ"):
        verify_hybrid_chain(
            forged,
            card,
            view,
            request,
            outcome,
            result,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )


def test_verifier_revalidates_model_copy_before_trusting_static_axes() -> None:
    card, view, contracts, request, result, outcome, _ = _chain()
    forged_static = card.static_tags.model_copy(
        update={"exact": ("has_async", "has_network")}
    )
    forged_card = card.model_copy(update={"static_tags": forged_static})
    payload = _hybrid_payload(forged_card, outcome, result)
    states = cast(list[dict[str, object]], payload["tag_states"])
    network = states[_active_tag_ids().index("has_network")]
    network["static_exact_decision"] = "positive"
    network["unit_comparison_status"] = "agreement_positive"
    forged_hybrid = seal_hybrid_feature_analysis_result(payload)

    with pytest.raises(ValueError, match="invalid ReviewUnit Analysis Card"):
        verify_hybrid_chain(
            forged_hybrid,
            forged_card,
            view,
            request,
            outcome,
            result,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )


def test_card_tag_match_provenance_must_exist_in_declared_fact_scope() -> None:
    static_tags = deepcopy(cast(dict[str, object], _card_payload()["static_tags"]))
    matches = cast(list[dict[str, object]], static_tags["matches"])
    signals = cast(list[dict[str, object]], matches[0]["signals"])
    signals[0]["value"] = "http.notInFileHints"

    with pytest.raises(ValueError, match="absent from its declared fact scope"):
        _card(static_tags=static_tags)


def test_owner_role_tag_match_keeps_unit_and_owner_context_provenance_separate() -> None:
    symbol_occurrence_id = _hash_id("occurrence", "a")
    role_occurrence_id = _hash_id("occurrence", "b")
    method_declaration_id = _hash_id("declaration", "c")
    struct_declaration_id = _hash_id("declaration", "d")
    static_tags = deepcopy(cast(dict[str, object], _card_payload()["static_tags"]))
    exact = cast(list[str], static_tags["exact"])
    exact.append("has_lifecycle")
    exact.sort()
    matches = cast(list[dict[str, object]], static_tags["matches"])
    matches.append(
        {
            "tag_id": "has_lifecycle",
            "status": "Active",
            "scope": "unit_exact",
            "signals": [
                {
                    "signal_type": "unit_owner_role_symbol",
                    "kind": "symbols",
                    "value": "Index.addNetworkListener",
                    "operator": "any_unit_symbol_leaf_with_owner_role",
                    "normalized_value": "addNetworkListener",
                    "owner_role": "arkui_custom_component",
                    "symbol_occurrence_id": symbol_occurrence_id,
                    "direct_owner_declaration_id": method_declaration_id,
                    "enclosing_owner_declaration_id": struct_declaration_id,
                    "role_evidence_occurrence_ids": [role_occurrence_id],
                }
            ],
        }
    )
    matches.sort(key=lambda item: (str(item["scope"]), str(item["tag_id"])))

    method_card = _card(
        static_tags=static_tags,
        owner_context_occurrence_ids=[role_occurrence_id],
        owner_context_declaration_ids=[method_declaration_id, struct_declaration_id],
    )
    assert "has_lifecycle" in method_card.static_tags.exact

    struct_identity = {
        "kind": "declaration",
        "ref_id": struct_declaration_id,
        "owner_kind": "struct",
        "qualified_name": "Index",
        "quality": "exact",
    }
    struct_card = _card(
        unit_kind="struct",
        unit_symbol="Index",
        owner_summary={
            "resolution": "resolved",
            "unit_owner": struct_identity,
            "enclosing_owner": struct_identity,
            "owner_roles": ["arkui_custom_component"],
            "diagnostics": [],
        },
        static_tags=static_tags,
        exact_occurrence_ids=[symbol_occurrence_id, role_occurrence_id],
        owner_context_occurrence_ids=[role_occurrence_id],
        owner_context_declaration_ids=[method_declaration_id, struct_declaration_id],
    )
    assert struct_card.owner_summary.unit_owner == struct_card.owner_summary.enclosing_owner

    with pytest.raises(ValueError, match="role evidence is absent from owner context"):
        _card(
            static_tags=static_tags,
            owner_context_occurrence_ids=[],
            owner_context_declaration_ids=[method_declaration_id, struct_declaration_id],
        )

    with pytest.raises(ValueError, match="cannot be a Unit exact occurrence"):
        _card(
            static_tags=static_tags,
            exact_occurrence_ids=[symbol_occurrence_id, role_occurrence_id],
            owner_context_occurrence_ids=[role_occurrence_id],
            owner_context_declaration_ids=[method_declaration_id, struct_declaration_id],
        )


def test_cross_artifact_verifiers_reject_syntactically_valid_wrong_references() -> None:
    card, view, contracts, request, result, outcome, hybrid = _chain()

    other_card = _card(code_token_budget=2399)
    with pytest.raises(ValueError, match="does not reference"):
        verify_model_view_against_card(view, other_card)

    other_view = _model_view(
        card,
        projection_policy_fingerprint=_hash_id("ai-model-view-policy", "a"),
    )
    with pytest.raises(ValueError, match="does not reference"):
        verify_request_against_model_view(
            request,
            other_view,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )

    other_request = _request(card, view, contracts, prompt_version="other-prompt-v1")
    with pytest.raises(ValueError, match="does not reference"):
        verify_result_against_request(result, other_request, view)

    other_result = _result(request, latency_ms=999)
    with pytest.raises(ValueError, match="does not reference"):
        verify_outcome_against_result(outcome, other_result)

    other_outcome = _outcome(
        request,
        result,
        budget_snapshot_id=_hash_id("ai-budget-snapshot", "a"),
    )
    with pytest.raises(ValueError, match="does not reference"):
        verify_hybrid_chain(
            hybrid,
            card,
            view,
            request,
            other_outcome,
            result,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )

    other_card = _card(code_token_budget=2398)
    mixed = seal_hybrid_feature_analysis_result(
        _hybrid_payload(other_card, outcome, result)
    )
    with pytest.raises(ValueError, match="does not reference"):
        verify_hybrid_chain(
            mixed,
            other_card,
            view,
            request,
            outcome,
            result,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )


def test_non_valid_outcome_keeps_static_states_without_fake_ai_decisions() -> None:
    card, view, contracts, request, result, _, _ = _chain()
    outcome = _outcome(
        request,
        result,
        status="invalid_output",
        result_id=None,
        reason_code="schema_invalid",
    )
    hybrid = seal_hybrid_feature_analysis_result(_hybrid_payload(card, outcome, None))

    verify_hybrid_chain(
        hybrid,
        card,
        view,
        request,
        outcome,
        None,
        active_tag_ids=_active_tag_ids(),
        active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
    )
    assert hybrid.ai_result_id is None
    assert all(state.ai_unit_decision is None for state in hybrid.tag_states)
    assert (
        hybrid.tag_states[_active_tag_ids().index("has_async")].unit_comparison_status
        == "static_only_due_execution"
    )


def test_not_run_outcome_cannot_be_reused_for_another_card() -> None:
    card, view, contracts, request, result, _, _ = _chain()
    outcome = _outcome(
        request,
        result,
        status="not_run",
        request_id=None,
        result_id=None,
        reason_code="taxonomy_mismatch",
        attempt_count=0,
    )
    hybrid = seal_hybrid_feature_analysis_result(_hybrid_payload(card, outcome, None))
    verify_hybrid_chain(
        hybrid,
        card,
        view,
        None,
        outcome,
        None,
        active_tag_ids=_active_tag_ids(),
        active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
    )

    other_card = _card(code_token_budget=2397)
    other_view = _model_view(other_card)
    reused = seal_hybrid_feature_analysis_result(
        _hybrid_payload(other_card, outcome, None)
    )
    with pytest.raises(ValueError, match="does not reference the supplied Card"):
        verify_hybrid_chain(
            reused,
            other_card,
            other_view,
            None,
            outcome,
            None,
            active_tag_ids=_active_tag_ids(),
            active_taxonomy_fingerprint=taxonomy_fingerprint(contracts),
        )
    assert (
        hybrid.tag_states[_active_tag_ids().index("has_network")].unit_comparison_status
        == "unresolved_due_execution"
    )


def test_nested_models_reject_unknown_fields_and_noncanonical_sequences() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        AnalysisCode.model_validate(
            {
                "mode": "full_unit",
                "text": "x",
                "line_start": 1,
                "line_end": 1,
                "changed_line_numbers": [1],
                "truncated": False,
                "static_tag": "has_network",
            }
        )
    with pytest.raises(ValidationError, match="sorted and unique"):
        AIModelCode.model_validate(
            {
                "mode": "full_unit",
                "numbered_text": "2: x\n1: y",
                "line_numbers": [2, 1],
                "truncated": False,
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        AITagJudgment.model_validate(
            {
                "tag_id": "has_network",
                "decision": "positive",
                "evidence_lines": [1],
                "reason_code": "direct_network_semantics",
                "reason": "调用网络 API。",
                "finding": "这不是 Finding。",
            }
        )
