from __future__ import annotations

import json
import pickle

import pytest
from test_deepseek_shadow_provider import (
    _BudgetLedger,
    _claims,
    _Credential,
    _EgressVerifier,
    _provider_body,
    _trusted_inputs,
)
from test_hybrid_analysis_dispatch import _judgments, _wire_content
from test_hybrid_formal_execution import _trust_objects
from test_retrieval_request_v2 import _EGRESS_POLICY, _INDEX_VERSION, _TARGET, _scenario

import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
import arkts_code_reviewer.retrieval.query_planner_v3 as query_planner_v3
from arkts_code_reviewer.hybrid_analysis.builders import build_ai_tag_model_view
from arkts_code_reviewer.hybrid_analysis.dispatch import AITagDispatchEnvelopeBuilder
from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AITagFormalExecutionEvidenceV2,
    AITagFormalExecutionVerifierV2,
    DeepSeekFormalExecutionRunnerV2,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    build_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import FullTaxonomyRequestBuilder
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
)
from arkts_code_reviewer.retrieval.models import RetrievalRequest, load_retrieval_request
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.query_planner_v3 import (
    TrustedRetrievalRequestV3Builder,
    VerifiedRetrievalRequestV3,
)
from arkts_code_reviewer.retrieval.request_v2 import load_retrieval_request_v2
from arkts_code_reviewer.retrieval.request_v3 import (
    RetrievalRequestV3,
    load_retrieval_request_v3,
    render_vector_query_v3,
)
from arkts_code_reviewer.retrieval.service import RetrievalService


def _formal_evidence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    positive_tag: str | None,
) -> tuple[  # type: ignore[no-untyped-def]
    object,
    dict[str, AITagFormalExecutionEvidenceV2],
    AITagFormalExecutionVerifierV2,
]:
    scenario = _scenario()
    from arkts_code_reviewer.hybrid_analysis.builders import AnalysisCardBuilder

    cards = AnalysisCardBuilder().build_many(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        policy=_EGRESS_POLICY,
    )
    trust_domain_id = f"ai-shadow-trust-domain:sha256:{'a' * 64}"
    signer, registry = _trust_objects(trust_domain_id)
    response_by_plan: dict[str, bytes] = {}

    def fixed_send(
        _transport: object,
        plan,  # type: ignore[no-untyped-def]
        *,
        api_key: str,
    ):  # type: ignore[no-untyped-def]
        assert api_key == "retrieval-v3-fixture-secret"
        body = response_by_plan[plan.plan_id]
        if positive_tag is None:
            return deepseek_adapter.DeepSeekHttpResponse(503, body, None, 5)
        return deepseek_adapter.DeepSeekHttpResponse(200, body, None, 5)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    result: dict[str, AITagFormalExecutionEvidenceV2] = {}
    for card in cards:
        model_view = build_ai_tag_model_view(card=card)
        request_builder = FullTaxonomyRequestBuilder.default()
        request = request_builder.build(card=card, model_view=model_view)
        envelope = AITagDispatchEnvelopeBuilder(request_builder=request_builder).build(
            card=card,
            model_view=model_view,
            request=request,
        )
        plan = build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=_EGRESS_POLICY,
            max_output_tokens=4_096,
        )
        if positive_tag is None:
            raw_body = b'{"error":"synthetic server failure"}'
        else:
            judgments = _judgments(envelope)
            visible_line = model_view.code.line_numbers[0]
            for judgment in judgments:
                if judgment["tag_id"] == positive_tag:
                    judgment.update(
                        {
                            "decision": "positive",
                            "evidence_lines": [visible_line],
                            "reason_code": "direct_unit_semantic_evidence",
                            "reason": "固定 Formal V2 检索合同中的直接 Unit 语义证据。",
                        }
                    )
            raw_body = _provider_body(
                envelope,
                content=_wire_content(judgments),
            )
        response_by_plan[plan.plan_id] = raw_body
        credential = _Credential(secret="retrieval-v3-fixture-secret")
        claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
        trusted_inputs = _trusted_inputs(
            card=card,
            envelope=envelope,
            policy=_EGRESS_POLICY,
        )
        events: list[str] = []
        gate = AITagShadowAuthorizationGate(
            trust_domain_id=claims.trust_domain_id,
            credential_provider=credential,
            trusted_plan_inputs=trusted_inputs,
            egress_verifier=_EgressVerifier(events),
            budget_ledger=_BudgetLedger(events),
        )
        capability = gate.authorize(plan=plan, claims=claims)
        result[card.unit_id] = DeepSeekFormalExecutionRunnerV2(
            gate=gate,
            signer=signer,
            registry=registry,
        ).run(
            plan=plan,
            claims=claims,
            capability=capability,
            envelope=envelope,
        )
    return scenario, result, AITagFormalExecutionVerifierV2(registry=registry)


def _build(
    scenario,  # type: ignore[no-untyped-def]
    evidence: dict[str, AITagFormalExecutionEvidenceV2],
    verifier: AITagFormalExecutionVerifierV2,
) -> VerifiedRetrievalRequestV3:
    return TrustedRetrievalRequestV3Builder(
        formal_execution_verifier=verifier,
        analysis_context_policy=_EGRESS_POLICY,
    ).build(
        scenario.analysis,
        scenario.context_plan,
        source_snapshots=scenario.snapshots,
        formal_evidence_by_unit=evidence,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={
            binding.primary_unit_id: ("RULE-Z", "RULE-A", "RULE-A")
            for binding in scenario.context_plan.primary_question_bindings
        },
    )


def test_verified_v3_projects_only_signed_positive_without_formal_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    verified = _build(scenario, evidence, verifier)
    request = verified.request
    baseline = build_retrieval_request(
        scenario.analysis,
        scenario.context_plan,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={
            binding.primary_unit_id: ("RULE-Z", "RULE-A", "RULE-A")
            for binding in scenario.context_plan.primary_question_bindings
        },
    )

    assert isinstance(request, RetrievalRequestV3)
    assert request.request_id.startswith("retrieval-request-v3:sha256:")
    assert verified.baseline_request == baseline
    assert len(verified.formal_attestation_ids) == len(request.units)
    assert tuple(item.outcome_id for item in verified.formal_execution_outcomes) == tuple(
        item.formal_execution_outcome_id for item in request.units
    )
    for v1, v3 in zip(baseline.units, request.units, strict=True):
        formal = evidence[v3.unit_id].bundle
        assert v3.ai_inferred_tags == ("has_network",)
        assert v3.formal_hybrid_analysis_id == formal.hybrid.analysis_id
        assert v3.formal_execution_outcome_id == formal.outcome.outcome_id
        assert formal.result is not None
        assert v3.formal_ai_result_id == formal.result.result_id
        assert v3.trusted_execution_subject_id == formal.subject.subject_id
        assert v3.trusted_runner_attestation_id == formal.attestation.attestation_id
        assert v3.exact_tags == v1.exact_tags
        assert v3.routing_tags == v1.routing_tags
        assert v3.review_question_ids == v1.review_question_ids
        assert v3.dispatchable_review_question_ids == v1.dispatchable_review_question_ids
        assert v3.retrieval_dimension_ids == v1.retrieval_dimension_ids
        assert v3.routing_dimension_ids == v1.routing_dimension_ids
        assert "has_network" not in render_vector_query_v3(v3)


def test_unavailable_signed_execution_falls_back_without_ai_deleting_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(monkeypatch, positive_tag=None)
    request = _build(scenario, evidence, verifier).request
    baseline = build_retrieval_request(
        scenario.analysis,
        scenario.context_plan,
        target_platform=_TARGET,
        resolved_index_version=_INDEX_VERSION,
        knowledge_token_budget=101,
        requested_rule_ids_by_unit={
            binding.primary_unit_id: ("RULE-Z", "RULE-A", "RULE-A")
            for binding in scenario.context_plan.primary_question_bindings
        },
    )
    for v1, v3 in zip(baseline.units, request.units, strict=True):
        assert evidence[v3.unit_id].bundle.outcome.status == "unavailable"
        assert v3.formal_ai_result_id is None
        assert v3.ai_inferred_tags == ()
        assert v3.tag_disagreements == ()
        assert v3.candidate_dimension_ids == ()
        assert v3.exact_tags == v1.exact_tags
        assert v3.routing_tags == v1.routing_tags


def test_v3_strict_loader_is_version_separate_and_serialized_json_is_not_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    verified = _build(scenario, evidence, verifier)
    request = verified.request
    raw = request.model_dump_json()
    assert load_retrieval_request_v3(raw) == request
    with pytest.raises(ValueError, match="invalid Retrieval V2 request"):
        load_retrieval_request_v2(raw)
    with pytest.raises(ValueError, match="invalid Retrieval request"):
        load_retrieval_request(raw)
    with pytest.raises(TypeError):
        pickle.dumps(verified)
    with pytest.raises(AttributeError, match="immutable"):
        verified._request = request  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        del verified._runtime_sealed  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        verifier.verify = lambda evidence: evidence  # type: ignore[method-assign]

    service = object.__new__(RetrievalService)
    with pytest.raises(TypeError, match="request must use RetrievalRequest"):
        service.retrieve(request)  # type: ignore[arg-type]
    assert not isinstance(request, RetrievalRequest)

    duplicate = raw.replace(
        '"schema_version":"retrieval-request-v3"',
        '"schema_version":"retrieval-request-v3","schema_version":"retrieval-request-v3"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_request_v3(duplicate)
    payload = request.model_dump(mode="json")
    payload["unknown"] = True
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_retrieval_request_v3(json.dumps(payload))

    # A recomputed request hash can contain a format-valid false formal reference;
    # only the opaque wrapper's proof binding rejects it.
    first = request.units[0]
    unit_payload = first.model_dump(mode="json")
    unit_payload["formal_execution_outcome_id"] = f"ai-tag-outcome-v2:sha256:{'4' * 64}"
    forged_unit = type(first).model_validate(unit_payload)
    forged_request = RetrievalRequestV3.create(
        context_plan_id=request.context_plan_id,
        feature_routing_id=request.feature_routing_id,
        feature_config_version=request.feature_config_version,
        index_version=request.index_version,
        target_platform=request.target_platform,
        total_knowledge_token_budget=request.total_knowledge_token_budget,
        units=(forged_unit, *request.units[1:]),
    )
    assert load_retrieval_request_v3(forged_request.model_dump_json()) == forged_request
    with pytest.raises(ValueError, match="proof identity differs"):
        VerifiedRetrievalRequestV3(
            request=forged_request,
            baseline_request=verified._baseline_request,  # noqa: SLF001
            eligibilities=verified._eligibilities,  # noqa: SLF001
            construction_token=query_planner_v3._VERIFIED_REQUEST_TOKEN,  # noqa: SLF001
        )

    baseline_forged_payload = first.model_dump(mode="json")
    baseline_forged_payload["semantic_code_excerpt"] = "FORGED"
    baseline_forged_unit = type(first).model_validate(baseline_forged_payload)
    baseline_forged_request = RetrievalRequestV3.create(
        context_plan_id=request.context_plan_id,
        feature_routing_id=request.feature_routing_id,
        feature_config_version=request.feature_config_version,
        index_version=request.index_version,
        target_platform=request.target_platform,
        total_knowledge_token_budget=request.total_knowledge_token_budget,
        units=(baseline_forged_unit, *request.units[1:]),
    )
    with pytest.raises(ValueError, match="differs from V1 baseline"):
        VerifiedRetrievalRequestV3(
            request=baseline_forged_request,
            baseline_request=verified._baseline_request,  # noqa: SLF001
            eligibilities=verified._eligibilities,  # noqa: SLF001
            construction_token=query_planner_v3._VERIFIED_REQUEST_TOKEN,  # noqa: SLF001
        )


def test_v3_builder_and_verifier_runtime_roots_are_exact_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, verifier = _formal_evidence(monkeypatch, positive_tag="has_network")
    builder = TrustedRetrievalRequestV3Builder(
        formal_execution_verifier=verifier,
        analysis_context_policy=_EGRESS_POLICY,
    )
    with pytest.raises(AttributeError, match="immutable"):
        builder._formal_execution_verifier = verifier  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        del builder._runtime_sealed  # noqa: SLF001

    class DerivedVerifier(AITagFormalExecutionVerifierV2):
        pass

    derived = DerivedVerifier(registry=verifier._registry)  # noqa: SLF001
    with pytest.raises(TypeError, match="formal execution verifier"):
        TrustedRetrievalRequestV3Builder(
            formal_execution_verifier=derived,
            analysis_context_policy=_EGRESS_POLICY,
        )


def test_verified_v3_revalidates_internal_request_identity_on_every_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    verified = _build(scenario, evidence, verifier)
    forged = verified.request.model_copy(
        update={"request_id": f"retrieval-request-v3:sha256:{'0' * 64}"}
    )
    object.__setattr__(verified, "_request", forged)

    with pytest.raises(ValueError, match="request_id does not match content"):
        _ = verified.request


def test_verified_v3_rejects_resigned_import_use_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    verified = _build(scenario, evidence, verifier)
    original = verified.request
    first = original.units[0]
    forged_unit = first.model_copy(
        update={
            "exact_signals": first.exact_signals.model_copy(
                update={"import_uses": ("forged.untrusted.Import",)}
            )
        }
    )
    forged = RetrievalRequestV3.create(
        context_plan_id=original.context_plan_id,
        feature_routing_id=original.feature_routing_id,
        feature_config_version=original.feature_config_version,
        index_version=original.index_version,
        target_platform=original.target_platform,
        total_knowledge_token_budget=original.total_knowledge_token_budget,
        units=(forged_unit, *original.units[1:]),
    )
    object.__setattr__(verified, "_request", forged)
    object.__setattr__(verified, "_expected_request_id", forged.request_id)

    with pytest.raises(ValueError, match="differs from trusted V3 exact facts"):
        _ = verified.request


def test_verified_v3_rebuilds_positive_tags_from_formal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    verified = _build(scenario, evidence, verifier)
    eligibility = verified._eligibilities[0]  # noqa: SLF001
    assert eligibility.positive_tags == ("has_network",)
    object.__setattr__(eligibility, "_positive_tags", ("has_storage",))

    with pytest.raises(ValueError, match="AI-positive projection changed"):
        _ = verified.request


def test_v3_builder_requires_complete_matching_formal_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, evidence, verifier = _formal_evidence(
        monkeypatch,
        positive_tag="has_network",
    )
    first, second = sorted(evidence)
    missing = dict(evidence)
    missing.pop(first)
    with pytest.raises(ValueError, match="exactly cover"):
        _build(scenario, missing, verifier)

    swapped = dict(evidence)
    swapped[first], swapped[second] = evidence[second], evidence[first]
    with pytest.raises(ValueError, match="eligibility differs"):
        _build(scenario, swapped, verifier)

    with pytest.raises(TypeError, match="Formal Execution Evidence V2"):
        _build(scenario, {key: value.bundle for key, value in evidence.items()}, verifier)  # type: ignore[arg-type]
