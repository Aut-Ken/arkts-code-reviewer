from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Any, Literal, cast

import pytest
import test_retrieval_request_v3 as request_v3_helpers
import test_retrieval_shadow_v3 as shadow_helpers
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
from test_retrieval_core import _FakeEmbeddingProvider, _index, _record
from test_retrieval_request_v2 import _EGRESS_POLICY, _TARGET

import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.hybrid_analysis.builders import (
    AnalysisCardBuilder,
    build_ai_tag_model_view,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import AITagDispatchEnvelopeBuilder
from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AITagFormalExecutionEvidenceV2,
    AITagFormalExecutionVerifierV2,
    DeepSeekFormalExecutionRunnerV2,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    build_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
)
from arkts_code_reviewer.retrieval.config import (
    RetrievalConfig,
    load_default_retrieval_config,
)
from arkts_code_reviewer.retrieval.models import EvidencePack, KnowledgeIndex
from arkts_code_reviewer.retrieval.query_planner_v3 import (
    TrustedRetrievalRequestV3Builder,
    VerifiedRetrievalRequestV3,
)
from arkts_code_reviewer.retrieval.shadow_models_v3 import (
    RetrievalShadowResultV3,
    load_retrieval_shadow_result_v3,
)
from arkts_code_reviewer.retrieval.shadow_service_v3 import (
    RetrievalShadowServiceV3,
    VerifiedRetrievalShadowResultV3,
)

Payload = dict[str, Any]
PayloadMutator = Callable[[Payload], None]
FormalMode = Literal["positive", "invalid_output", "unavailable"]


def _runtime_result(
    monkeypatch: pytest.MonkeyPatch,
    *,
    positive_tag: str | None,
    records: tuple[object, ...],
    embedded: bool = False,
    total_budget: int = 128,
) -> VerifiedRetrievalShadowResultV3:
    index = _index(cast("tuple[Any, ...]", records), embedded=embedded)
    authority = shadow_helpers._verified_request(  # noqa: SLF001
        monkeypatch,
        index,
        positive_tag=positive_tag,
        total_budget=total_budget,
    )
    return RetrievalShadowServiceV3(
        index,
        embedding_provider=_FakeEmbeddingProvider() if embedded else None,
        allow_golden_fixture=True,
    ).compare(authority)


def _positive_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    embedded: bool = False,
) -> VerifiedRetrievalShadowResultV3:
    return _runtime_result(
        monkeypatch,
        positive_tag="has_network",
        records=(
            _record(
                "R-BOTH",
                tags=("has_async", "has_network"),
                dimension_ids=("DIM-07",),
                embedding=(1.0, 0.0) if embedded else None,
            ),
        ),
        embedded=embedded,
    )


def _unavailable_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> VerifiedRetrievalShadowResultV3:
    return _runtime_result(
        monkeypatch,
        positive_tag=None,
        records=(
            _record(
                "R-STATIC",
                tags=("has_async",),
                dimension_ids=("DIM-07",),
            ),
        ),
    )


def _two_unit_authority(
    monkeypatch: pytest.MonkeyPatch,
    index: KnowledgeIndex,
    *,
    modes: tuple[FormalMode, FormalMode],
) -> VerifiedRetrievalRequestV3:
    scenario = request_v3_helpers._scenario()  # noqa: SLF001
    cards = AnalysisCardBuilder().build_many(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        policy=_EGRESS_POLICY,
    )
    assert len(cards) == len(modes) == 2
    trust_domain_id = f"ai-shadow-trust-domain:sha256:{'a' * 64}"
    signer, registry = _trust_objects(trust_domain_id)
    responses: dict[str, tuple[int, bytes]] = {}

    def fixed_send(
        _transport: object,
        plan: Any,
        *,
        api_key: str,
    ) -> deepseek_adapter.DeepSeekHttpResponse:
        assert api_key == "retrieval-v3-mixed-fixture-secret"
        status, body = responses[plan.plan_id]
        return deepseek_adapter.DeepSeekHttpResponse(status, body, None, 5)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    evidence: dict[str, AITagFormalExecutionEvidenceV2] = {}
    for card, mode in zip(cards, modes, strict=True):
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
        if mode == "positive":
            judgments = _judgments(envelope)
            visible_line = model_view.code.line_numbers[0]
            for judgment in judgments:
                if judgment["tag_id"] == "has_network":
                    judgment.update(
                        {
                            "decision": "positive",
                            "evidence_lines": [visible_line],
                            "reason_code": "direct_unit_semantic_evidence",
                            "reason": "固定混合 Unit 隔离测试中的直接语义证据。",
                        }
                    )
            status = 200
            raw_body = _provider_body(
                envelope,
                content=_wire_content(judgments),
            )
        elif mode == "invalid_output":
            status = 200
            raw_body = _provider_body(envelope, content='{"judgments":[]}')
        else:
            status = 503
            raw_body = b'{"error":"synthetic server failure"}'
        responses[plan.plan_id] = (status, raw_body)

        credential = _Credential(secret="retrieval-v3-mixed-fixture-secret")
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
        evidence[card.unit_id] = DeepSeekFormalExecutionRunnerV2(
            gate=gate,
            signer=signer,
            registry=registry,
        ).run(
            plan=plan,
            claims=claims,
            capability=capability,
            envelope=envelope,
        )

    return TrustedRetrievalRequestV3Builder(
        formal_execution_verifier=AITagFormalExecutionVerifierV2(registry=registry),
        analysis_context_policy=_EGRESS_POLICY,
    ).build(
        scenario.analysis,
        scenario.context_plan,
        source_snapshots=scenario.snapshots,
        formal_evidence_by_unit=evidence,
        target_platform=_TARGET,
        resolved_index_version=index.index_version,
        knowledge_token_budget=128,
    )


def _recompute_result_id(payload: Payload) -> Payload:
    identity = dict(payload)
    identity.pop("result_id", None)
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    payload["result_id"] = "retrieval-shadow-result:sha256:" + hashlib.sha256(encoded).hexdigest()
    return payload


def _load_rehashed(payload: Payload) -> RetrievalShadowResultV3:
    return load_retrieval_shadow_result_v3(
        json.dumps(
            _recompute_result_id(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    )


def _first_unit(payload: Payload) -> Payload:
    return cast("list[Payload]", payload["units"])[0]


def _hybrid_arm(payload: Payload) -> Payload:
    return cast("Payload", _first_unit(payload)["hybrid"])


def _first_hybrid_clause(payload: Payload) -> Payload:
    return cast("list[Payload]", _hybrid_arm(payload)["ranked_clauses"])[0]


def _first_contribution(payload: Payload) -> Payload:
    return cast("list[Payload]", _first_hybrid_clause(payload)["contributions"])[0]


def _refresh_clause_score(clause: Payload) -> None:
    contributions = cast("list[Payload]", clause["contributions"])
    clause["rrf_score"] = round(
        sum(float(item["rrf_contribution"]) for item in contributions),
        8,
    )


def _tamper_rrf_pool_rank(payload: Payload) -> None:
    contribution = _first_contribution(payload)
    contribution["pool_rank"] = int(contribution["pool_rank"]) + 1


def _tamper_rrf_weight(payload: Payload) -> None:
    contribution = _first_contribution(payload)
    contribution["rrf_weight"] = float(contribution["rrf_weight"]) + 0.5


def _tamper_rrf_formula(payload: Payload) -> None:
    clause = _first_hybrid_clause(payload)
    contribution = cast("list[Payload]", clause["contributions"])[-1]
    contribution["rrf_contribution"] = round(
        float(contribution["rrf_contribution"]) + 0.001,
        8,
    )
    _refresh_clause_score(clause)


def _tamper_contribution_to_pool_without_rule(payload: Payload) -> None:
    unit = _first_unit(payload)
    arm = cast("Payload", unit["hybrid"])
    clause = cast("list[Payload]", arm["ranked_clauses"])[0]
    contribution = cast("list[Payload]", clause["contributions"])[-1]
    pools = {cast("str", pool["pool"]): pool for pool in cast("list[Payload]", unit["pools"])}
    semantic_pool = pools["semantic_vector"]
    assert semantic_pool["candidates"] == []
    contribution["pool"] = "semantic_vector"
    contribution["rrf_weight"] = semantic_pool["rrf_weight"]
    contribution["rrf_contribution"] = round(
        float(semantic_pool["rrf_weight"]) / (int(arm["rrf_k"]) + int(contribution["pool_rank"])),
        8,
    )
    _refresh_clause_score(clause)


@pytest.mark.parametrize(
    "mutator",
    (
        pytest.param(_tamper_rrf_pool_rank, id="pool-rank"),
        pytest.param(_tamper_rrf_weight, id="pool-weight"),
        pytest.param(_tamper_rrf_formula, id="rrf-formula"),
        pytest.param(
            _tamper_contribution_to_pool_without_rule,
            id="rule-absent-from-contribution-pool",
        ),
    ),
)
def test_rehashed_result_rejects_rrf_ledger_tampering(
    monkeypatch: pytest.MonkeyPatch,
    mutator: PayloadMutator,
) -> None:
    artifact = _positive_runtime(monkeypatch).artifact
    payload = artifact.model_dump(mode="json")
    mutator(payload)
    _recompute_result_id(payload)
    assert payload["result_id"] != artifact.result_id

    with pytest.raises(ValueError):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def test_rehashed_result_rejects_ai_provenance_without_formal_ai_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _unavailable_runtime(monkeypatch).artifact
    payload = artifact.model_dump(mode="json")
    unit = _first_unit(payload)
    assert unit["formal_ai_result_id"] is None
    assert unit["formal_execution_status"] == "unavailable"
    assert cast("list[Payload]", unit["pools"])[3]["candidates"] == []
    clause = _first_hybrid_clause(payload)
    matches = cast("list[Payload]", clause["matched_by"])
    matches.append(
        {
            "kind": "tag",
            "scope": "ai_inferred",
            "value": "has_network",
        }
    )
    matches.sort(key=lambda item: (item["kind"], item["scope"], item["value"]))
    _recompute_result_id(payload)

    with pytest.raises(ValueError):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def test_rehashed_result_rejects_more_selected_clauses_than_result_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_result(
        monkeypatch,
        positive_tag=None,
        records=(
            _record("R-A", tags=("has_async",), dimension_ids=("DIM-07",)),
            _record("R-B", tags=("has_async",), dimension_ids=("DIM-07",)),
        ),
    )
    payload = runtime.artifact.model_dump(mode="json")
    unit = _first_unit(payload)
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", unit[arm_name])
        assert len(cast("list[Payload]", arm["selected_rule_ids"])) == 2
        arm["result_limit"] = 1
    _recompute_result_id(payload)

    with pytest.raises(ValueError):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def test_rehashed_result_rejects_coverage_not_derived_from_selected_clauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _positive_runtime(monkeypatch).artifact.model_dump(mode="json")
    unit = _first_unit(payload)
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", unit[arm_name])
        assert arm["covered_dimension_ids"] == ["DIM-07"]
        arm["covered_dimension_ids"] = []
        arm["uncovered_dimension_ids"] = ["DIM-07"]
    _recompute_result_id(payload)

    with pytest.raises(ValueError):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def _rebind_origin(payload: Payload) -> None:
    payload["index_origin"] = "publication"
    payload["knowledge_build_id"] = f"published-knowledge:sha256:{'1' * 64}"


def _rebind_knowledge_build(payload: Payload) -> None:
    payload["knowledge_build_id"] = f"retrieval-fixture:sha256:{'1' * 64}"


def _rebind_source_bundle(payload: Payload) -> None:
    payload["source_bundle_id"] = f"source-bundle:sha256:{'1' * 64}"


def _rebind_embedding_version(payload: Payload) -> None:
    payload["embedding_version"] = "fixture-embedding-v2"


def _rebind_shadow_policy(payload: Payload) -> None:
    payload["shadow_policy_fingerprint"] = f"retrieval-shadow-policy:sha256:{'1' * 64}"


def _rebind_profile(payload: Payload) -> None:
    _first_unit(payload)["profile_id"] = f"feature-profile:sha256:{'1' * 64}"


def _rebind_result_limit(payload: Payload) -> None:
    unit = _first_unit(payload)
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", unit[arm_name])
        arm["result_limit"] = int(arm["result_limit"]) + 1


def _rebind_token_budget(payload: Payload) -> None:
    unit = _first_unit(payload)
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", unit[arm_name])
        arm["token_budget"] = int(arm["token_budget"]) + 1


@pytest.mark.parametrize(
    "mutator",
    (
        pytest.param(_rebind_origin, id="index-origin"),
        pytest.param(_rebind_knowledge_build, id="knowledge-build"),
        pytest.param(_rebind_source_bundle, id="source-bundle"),
        pytest.param(_rebind_embedding_version, id="embedding-version"),
        pytest.param(_rebind_shadow_policy, id="shadow-policy"),
        pytest.param(_rebind_profile, id="unit-profile"),
        pytest.param(_rebind_result_limit, id="result-limit"),
        pytest.param(_rebind_token_budget, id="token-budget"),
    ),
)
def test_runtime_wrapper_rejects_rehashed_provenance_policy_profile_and_budget_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    mutator: PayloadMutator,
) -> None:
    runtime = _positive_runtime(monkeypatch, embedded=True)
    payload = runtime.artifact.model_dump(mode="json")
    mutator(payload)
    forged = _load_rehashed(payload)
    assert forged.result_id != runtime.artifact.result_id
    object.__setattr__(runtime, "_artifact", forged)
    # Move the local identity sentinel as well so this exercises the explicit
    # index/policy/request rebinding checks rather than only the first ID guard.
    object.__setattr__(runtime, "_expected_result_id", forged.result_id)

    with pytest.raises(ValueError):
        _ = runtime.artifact


def test_runtime_wrapper_rejects_rehashed_clause_content_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _positive_runtime(monkeypatch)
    payload = runtime.artifact.model_dump(mode="json")
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", _first_unit(payload)[arm_name])
        for clause in cast("list[Payload]", arm["ranked_clauses"]):
            clause["text"] = "FORGED KNOWLEDGE TEXT"
    forged = _load_rehashed(payload)
    object.__setattr__(runtime, "_artifact", forged)
    object.__setattr__(runtime, "_expected_result_id", forged.result_id)

    with pytest.raises(ValueError, match="Clause differs from KnowledgeIndex"):
        _ = runtime.artifact


def test_runtime_wrapper_rejects_rehashed_control_clause_content_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _positive_runtime(monkeypatch)
    control = runtime.control_evidence_pack
    first_unit = control.units[0]
    assert first_unit.clauses
    forged_clause = first_unit.clauses[0].model_copy(
        update={"text": "FORGED CONTROL KNOWLEDGE TEXT"},
    )
    forged_unit = first_unit.model_copy(
        update={"clauses": (forged_clause, *first_unit.clauses[1:])},
    )
    forged_control = EvidencePack.create(
        request_id=control.request_id,
        retrieval_version=control.retrieval_version,
        retrieval_config_fingerprint=control.retrieval_config_fingerprint,
        index_version=control.index_version,
        index_origin=control.index_origin,
        knowledge_build_id=control.knowledge_build_id,
        production_eligible=control.production_eligible,
        source_bundle_id=control.source_bundle_id,
        embedding_version=control.embedding_version,
        units=(forged_unit, *control.units[1:]),
        diagnostics=control.diagnostics,
    )
    assert forged_control.evidence_pack_id != control.evidence_pack_id

    payload = runtime.artifact.model_dump(mode="json")
    payload["v1_control_evidence_pack_id"] = forged_control.evidence_pack_id
    forged_artifact = _load_rehashed(payload)
    object.__setattr__(runtime, "_control_evidence_pack", forged_control)
    object.__setattr__(runtime, "_artifact", forged_artifact)
    object.__setattr__(runtime, "_expected_result_id", forged_artifact.result_id)

    with pytest.raises(ValueError, match="control EvidencePack"):
        _ = runtime.control_evidence_pack


def test_runtime_wrapper_rejects_rehashed_semantic_path_score_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _positive_runtime(monkeypatch, embedded=True)
    payload = runtime.artifact.model_dump(mode="json")
    pools = {
        cast("str", pool["pool"]): pool
        for pool in cast("list[Payload]", _first_unit(payload)["pools"])
    }
    semantic_candidates = cast("list[Payload]", pools["semantic_vector"]["candidates"])
    assert len(semantic_candidates) == 1
    semantic_candidates[0]["path_score"] = 0.877
    forged = _load_rehashed(payload)
    object.__setattr__(runtime, "_artifact", forged)
    object.__setattr__(runtime, "_expected_result_id", forged.result_id)

    with pytest.raises(ValueError, match="construction snapshot"):
        _ = runtime.artifact


def test_runtime_wrapper_rejects_rehashed_quality_diagnostic_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _positive_runtime(monkeypatch)
    payload = runtime.artifact.model_dump(mode="json")
    diagnostic = {
        "code": "parser_degraded",
        "rule_id": None,
        "detail": "Parser or ReviewUnit context quality limits shadow evidence confidence.",
    }
    unit = _first_unit(payload)
    for arm_name in ("static_vector", "hybrid"):
        arm = cast("Payload", unit[arm_name])
        assert arm["diagnostics"] == []
        arm["diagnostics"] = [diagnostic]
    payload["degraded"] = True
    forged = _load_rehashed(payload)
    object.__setattr__(runtime, "_artifact", forged)
    object.__setattr__(runtime, "_expected_result_id", forged.result_id)

    with pytest.raises(ValueError, match="construction snapshot"):
        _ = runtime.artifact


def test_loader_rejects_stale_result_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _positive_runtime(monkeypatch).artifact.model_dump(mode="json")
    payload["source_bundle_id"] = f"source-bundle:sha256:{'2' * 64}"

    with pytest.raises(ValueError, match="ID does not match content"):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def test_loader_rejects_non_finite_json_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _positive_runtime(monkeypatch).artifact.model_dump(mode="json")
    _first_hybrid_clause(payload)["rrf_score"] = math.nan
    raw = json.dumps(payload, allow_nan=True)

    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_retrieval_shadow_result_v3(raw)


def test_loader_rejects_invalid_utf8() -> None:
    with pytest.raises(ValueError, match="must use UTF-8"):
        load_retrieval_shadow_result_v3(b"\xff")


def test_loader_rejects_top_level_array() -> None:
    with pytest.raises(ValueError, match="top-level value must be an object"):
        load_retrieval_shadow_result_v3("[]")


@pytest.mark.parametrize(
    "field",
    (
        "production_qualified",
        "user_visible",
        "prompt_eligible",
        "finding_evidence_eligible",
    ),
)
def test_loader_rejects_rehashed_true_qualification_flag(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    payload = _positive_runtime(monkeypatch).artifact.model_dump(mode="json")
    payload[field] = True
    _recompute_result_id(payload)

    with pytest.raises(ValueError):
        load_retrieval_shadow_result_v3(json.dumps(payload))


def test_mixed_two_unit_execution_isolates_ai_pool_and_hybrid_gain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        (
            _record("R-STATIC", tags=("has_async",), dimension_ids=("DIM-07",)),
            _record("R-AI", tags=("has_network",), dimension_ids=("DIM-11",)),
        )
    )
    authority = _two_unit_authority(
        monkeypatch,
        index,
        modes=("positive", "unavailable"),
    )
    artifact = (
        RetrievalShadowServiceV3(index, allow_golden_fixture=True).compare(authority).artifact
    )
    by_status = {unit.formal_execution_status: unit for unit in artifact.units}
    assert set(by_status) == {"valid_result", "unavailable"}
    valid = by_status["valid_result"]
    unavailable = by_status["unavailable"]

    assert valid.ai_inferred_tags == ("has_network",)
    assert tuple(
        candidate.rule_id
        for candidate in shadow_helpers._pool(valid, "ai_inferred").candidates  # noqa: SLF001
    ) == ("R-AI",)
    assert set(valid.hybrid.selected_rule_ids) - set(valid.static_vector.selected_rule_ids) == {
        "R-AI"
    }

    assert unavailable.formal_ai_result_id is None
    assert unavailable.ai_inferred_tags == ()
    assert (
        shadow_helpers._pool(  # noqa: SLF001
            unavailable,
            "ai_inferred",
        ).candidates
        == ()
    )
    assert "R-AI" not in unavailable.hybrid.selected_rule_ids
    assert shadow_helpers._without_arm_label(  # noqa: SLF001
        unavailable.static_vector
    ) == shadow_helpers._without_arm_label(unavailable.hybrid)  # noqa: SLF001


def test_invalid_output_and_unavailable_keep_distinct_identity_and_safe_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-STATIC", tags=("has_async",), dimension_ids=("DIM-07",)),))
    authority = _two_unit_authority(
        monkeypatch,
        index,
        modes=("invalid_output", "unavailable"),
    )
    artifact = (
        RetrievalShadowServiceV3(index, allow_golden_fixture=True).compare(authority).artifact
    )
    by_status = {unit.formal_execution_status: unit for unit in artifact.units}
    assert set(by_status) == {"invalid_output", "unavailable"}
    invalid = by_status["invalid_output"]
    unavailable = by_status["unavailable"]

    assert invalid.formal_execution_outcome_id != unavailable.formal_execution_outcome_id
    assert invalid.trusted_execution_subject_id != unavailable.trusted_execution_subject_id
    assert invalid.trusted_runner_attestation_id != unavailable.trusted_runner_attestation_id
    for unit in (invalid, unavailable):
        assert unit.formal_ai_result_id is None
        assert unit.ai_inferred_tags == ()
        assert unit.tag_disagreements == ()
        assert unit.candidate_dimension_ids == ()
        assert (
            shadow_helpers._pool(  # noqa: SLF001
                unit,
                "ai_inferred",
            ).candidates
            == ()
        )
        assert shadow_helpers._without_arm_label(  # noqa: SLF001
            unit.static_vector
        ) == shadow_helpers._without_arm_label(unit.hybrid)  # noqa: SLF001


def test_index_drift_fails_before_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_index = _index((_record("R-REQUEST", tags=("has_async",), embedding=(1.0, 0.0)),))
    runtime_index = _index((_record("R-RUNTIME", tags=("has_async",), embedding=(1.0, 0.0)),))
    assert request_index.index_version != runtime_index.index_version
    authority = shadow_helpers._verified_request(  # noqa: SLF001
        monkeypatch,
        request_index,
        positive_tag="has_network",
    )
    provider = _FakeEmbeddingProvider()
    service = RetrievalShadowServiceV3(
        runtime_index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    )

    with pytest.raises(ValueError, match="different index version"):
        service.compare(authority)
    assert provider.query_calls == 0


def test_config_drift_fails_before_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-CONFIG", tags=("has_async",), embedding=(1.0, 0.0)),))
    authority = shadow_helpers._verified_request(  # noqa: SLF001
        monkeypatch,
        index,
        positive_tag="has_network",
    )
    provider = _FakeEmbeddingProvider()
    service = RetrievalShadowServiceV3(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    )
    drifted_payload = load_default_retrieval_config().model_dump(mode="python")
    drifted_payload["minimum_vector_similarity"] = 0.31
    drifted_config = RetrievalConfig.model_validate(drifted_payload)
    assert drifted_config.fingerprint != index.retrieval_config_fingerprint
    object.__setattr__(service, "_config", drifted_config)

    with pytest.raises(ValueError, match="base Retrieval config disagree"):
        service.compare(authority)
    assert provider.query_calls == 0
